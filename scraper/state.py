"""SQLite checkpoint state for safe, resumable runs."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Record:
    key: str
    detail_url: str
    title: str
    pdf_name: str
    text_name: str
    download_status: str
    extraction_status: str
    attempts: int
    error: str | None
    updated_at: str


def filenames_for(key: str, title: str = "") -> tuple[str, str]:
    """Return deterministic, filesystem-safe names without exposing raw keys."""
    label = title.strip() or "document"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip(".-_") or "document"
    label = label[:60].rstrip(".-_") or "document"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    stem = f"doc-{digest}-{label}"
    return f"{stem}.pdf", f"{stem}.txt"


class State:
    """The single source of truth for discovered and processed records."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                key TEXT PRIMARY KEY,
                detail_url TEXT NOT NULL,
                title TEXT NOT NULL,
                pdf_name TEXT NOT NULL UNIQUE,
                text_name TEXT NOT NULL UNIQUE,
                download_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (download_status IN ('pending', 'downloaded', 'failed')),
                extraction_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (extraction_status IN ('pending', 'extracted', 'no_text', 'failed')),
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> State:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert(self, key: str, detail_url: str, title: str = "") -> None:
        if not key.strip() or not detail_url.strip():
            raise ValueError("record key and detail URL are required")
        pdf_name, text_name = filenames_for(key, title)
        self.connection.execute(
            """
            INSERT INTO records (key, detail_url, title, pdf_name, text_name)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                detail_url = excluded.detail_url,
                title = excluded.title,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, detail_url, title, pdf_name, text_name),
        )
        self.connection.commit()

    def get(self, key: str) -> Record | None:
        row = self.connection.execute(
            "SELECT * FROM records WHERE key = ?", (key,)
        ).fetchone()
        return Record(**dict(row)) if row else None

    def records_for_download(self, max_attempts: int) -> list[Record]:
        rows = self.connection.execute(
            """SELECT * FROM records
               WHERE download_status != 'downloaded' AND attempts < ?
               ORDER BY key""",
            (max_attempts,),
        ).fetchall()
        return [Record(**dict(row)) for row in rows]

    def records_for_extraction(self) -> list[Record]:
        rows = self.connection.execute(
            """SELECT * FROM records
               WHERE download_status = 'downloaded'
                 AND extraction_status IN ('pending', 'failed')
               ORDER BY key"""
        ).fetchall()
        return [Record(**dict(row)) for row in rows]

    def mark_downloaded(self, key: str) -> None:
        self._update(key, "download_status = 'downloaded', error = NULL")

    def mark_download_failed(self, key: str, error: str) -> None:
        self._update(
            key,
            "download_status = 'failed', attempts = attempts + 1, error = ?",
            (error[:1000],),
        )

    def mark_extracted(self, key: str) -> None:
        self._update(key, "extraction_status = 'extracted', error = NULL")

    def mark_no_text(self, key: str, error: str = "PDF contains no extractable text") -> None:
        self._update(
            key, "extraction_status = 'no_text', error = ?", (error[:1000],)
        )

    def mark_extraction_failed(self, key: str, error: str) -> None:
        self._update(
            key, "extraction_status = 'failed', error = ?", (error[:1000],)
        )

    def reset_missing_pdf(self, key: str) -> None:
        self._update(
            key,
            "download_status = 'pending', extraction_status = 'pending', error = ?",
            ("Downloaded file was missing; queued again",),
        )

    def reset_missing_text(self, key: str) -> None:
        self._update(
            key,
            "extraction_status = 'pending', error = ?",
            ("Extracted text was missing; queued again",),
        )

    def counts(self) -> dict[str, int]:
        result = {"total": 0, "pending": 0, "downloaded": 0, "extracted": 0, "failed": 0, "no_text": 0}
        result["total"] = self.connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        result["pending"] = self.connection.execute(
            "SELECT COUNT(*) FROM records WHERE download_status = 'pending'"
        ).fetchone()[0]
        result["downloaded"] = self.connection.execute(
            "SELECT COUNT(*) FROM records WHERE download_status = 'downloaded'"
        ).fetchone()[0]
        result["extracted"] = self.connection.execute(
            "SELECT COUNT(*) FROM records WHERE extraction_status = 'extracted'"
        ).fetchone()[0]
        result["failed"] = self.connection.execute(
            """SELECT COUNT(*) FROM records
               WHERE download_status = 'failed' OR extraction_status = 'failed'"""
        ).fetchone()[0]
        result["no_text"] = self.connection.execute(
            "SELECT COUNT(*) FROM records WHERE extraction_status = 'no_text'"
        ).fetchone()[0]
        return result

    def all_records(self) -> list[Record]:
        rows = self.connection.execute("SELECT * FROM records ORDER BY key").fetchall()
        return [Record(**dict(row)) for row in rows]

    def _update(self, key: str, assignment: str, values: tuple[object, ...] = ()) -> None:
        cursor = self.connection.execute(
            f"UPDATE records SET {assignment}, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
            (*values, key),
        )
        if cursor.rowcount != 1:
            raise KeyError(key)
        self.connection.commit()
