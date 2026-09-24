"""Command-line orchestration. The launcher delegates here and stays stable."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from selenium.common.exceptions import WebDriverException

from scraper.browser import create_browser
from scraper.config import ConfigError, Settings, load_settings
from scraper.locking import RunLock
from scraper.pipeline import (
    discover,
    process_downloads,
    process_extractions,
    reconcile_files,
    write_manifest,
)
from scraper.site import load_adapter
from scraper.state import State


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="document-scraper",
        description="Resume-safe browser collection and PDF text extraction.",
    )
    parser.add_argument(
        "--config",
        default="private/config.json",
        help="private JSON configuration (default: private/config.json)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "discover", "download", "extract", "status", "manifest"),
    )
    return parser


def _print_counts(state: State) -> dict[str, int]:
    counts = state.counts()
    print(
        " | ".join(
            f"{name}: {counts[name]}"
            for name in ("total", "pending", "downloaded", "extracted", "no_text", "failed")
        )
    )
    return counts


def _print_guidance(counts: dict[str, int]) -> None:
    if counts["pending"]:
        print("Next: run 'download' to process pending records.")
    if counts["failed"]:
        print(
            "Some records failed. Run again to retry up to max_retries; "
            "see manifest.jsonl for details."
        )
    if counts["no_text"]:
        print("Some PDFs had no extractable text and may require OCR; originals were kept.")


def _with_browser(command: str, settings: Settings, state: State) -> None:
    adapter = load_adapter(settings)
    with create_browser(settings.browser) as driver:
        adapter.prepare(driver)
        if command in {"run", "discover"}:
            count = discover(driver, adapter, state)
            print(f"Discovered {count} records.")
        if command in {"run", "download"}:
            reconcile_files(state, settings.output_dir)
            process_downloads(driver, adapter, settings, state)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = load_settings(args.config)
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        with RunLock(settings.output_dir / ".scraper.lock"):
            with State(settings.output_dir / "state.sqlite3") as state:
                reconcile_files(state, settings.output_dir)
                if args.command in {"run", "discover", "download"}:
                    _with_browser(args.command, settings, state)
                if args.command in {"run", "extract"}:
                    process_extractions(settings, state)
                if args.command in {"run", "discover", "download", "extract", "manifest"}:
                    write_manifest(state, settings.output_dir)
                counts = _print_counts(state)
                _print_guidance(counts)
                if args.command == "run" and (
                    counts["pending"] or counts["failed"] or counts["no_text"]
                ):
                    return 2
        return 0
    except (ConfigError, RuntimeError, OSError, WebDriverException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Unexpected error ({type(exc).__name__}): {exc}. "
            "No completed files were removed; rerun 'status' before retrying.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
