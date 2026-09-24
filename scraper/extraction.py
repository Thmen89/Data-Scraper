"""PDF validation and plain-text extraction."""

from __future__ import annotations

import os
from pathlib import Path

from pypdf import PdfReader


class NoTextError(RuntimeError):
    """Raised when a valid PDF has no extractable text."""


def validate_pdf(path: Path) -> None:
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError("response is not a PDF")
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF is encrypted")
    if not reader.pages:
        raise ValueError("PDF contains no pages")


def extract_text(pdf_path: Path, text_path: Path) -> None:
    reader = PdfReader(pdf_path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF is encrypted")
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    if not any(pages):
        raise NoTextError("PDF contains no extractable text; it may need OCR")
    body = "\n\n".join(
        f"--- Page {number} ---\n{text}" for number, text in enumerate(pages, 1)
    )
    temporary = text_path.with_suffix(text_path.suffix + ".part")
    temporary.write_text(body + "\n", encoding="utf-8")
    os.replace(temporary, text_path)
