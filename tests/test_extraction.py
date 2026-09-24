import tempfile
import unittest
from pathlib import Path

from scraper.extraction import NoTextError, extract_text, validate_pdf
from tests.pdf_factory import pdf_bytes


class ExtractionTests(unittest.TestCase):
    def test_extracts_utf8_text_with_page_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "input.pdf"
            text = root / "output.txt"
            pdf.write_bytes(pdf_bytes("Asymmetric expected value 42"))
            validate_pdf(pdf)
            extract_text(pdf, text)
            self.assertEqual(
                text.read_text(encoding="utf-8"),
                "--- Page 1 ---\nAsymmetric expected value 42\n",
            )

    def test_distinguishes_valid_scanned_pdf_from_invalid_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            blank = root / "blank.pdf"
            blank.write_bytes(pdf_bytes(None))
            validate_pdf(blank)
            with self.assertRaises(NoTextError):
                extract_text(blank, root / "blank.txt")
            invalid = root / "invalid.pdf"
            invalid.write_text("login page", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not a PDF"):
                validate_pdf(invalid)


if __name__ == "__main__":
    unittest.main()
