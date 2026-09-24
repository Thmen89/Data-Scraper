import tempfile
import unittest
from pathlib import Path

from scraper.state import State, filenames_for


class StateTests(unittest.TestCase):
    def test_filenames_are_safe_deterministic_and_keyed(self):
        first = filenames_for("private/key:1", "Quarterly / Report")
        second = filenames_for("private/key:1", "Quarterly / Report")
        other = filenames_for("private_key:1", "Quarterly / Report")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertRegex(first[0], r"^doc-[a-f0-9]{12}-Quarterly-Report\.pdf$")

    def test_filenames_avoid_windows_device_names(self):
        for title in ("CON.summary", "NUL.report", "LPT1.notes"):
            pdf_name, _ = filenames_for(title, title)
            self.assertTrue(pdf_name.startswith("doc-"))
            self.assertNotEqual(pdf_name.split(".", 1)[0].upper(), title.split(".", 1)[0])

    def test_upsert_preserves_successful_progress(self):
        with tempfile.TemporaryDirectory() as temporary:
            with State(Path(temporary) / "state.sqlite3") as state:
                state.upsert("id-1", "https://example.test/old", "First")
                state.mark_downloaded("id-1")
                state.mark_extracted("id-1")
                state.upsert("id-1", "https://example.test/new", "Renamed")
                record = state.get("id-1")
                self.assertIsNotNone(record)
                self.assertEqual(record.detail_url, "https://example.test/new")
                self.assertEqual(record.download_status, "downloaded")
                self.assertEqual(record.extraction_status, "extracted")

    def test_download_and_extraction_failures_are_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            with State(Path(temporary) / "state.sqlite3") as state:
                state.upsert("id-1", "https://example.test/1", "One")
                state.mark_downloaded("id-1")
                state.mark_extraction_failed("id-1", "broken PDF")
                self.assertEqual(state.records_for_download(3), [])
                self.assertEqual([r.key for r in state.records_for_extraction()], ["id-1"])

    def test_failed_download_is_bounded_by_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            with State(Path(temporary) / "state.sqlite3") as state:
                state.upsert("id-1", "https://example.test/1", "One")
                state.mark_download_failed("id-1", "temporary")
                self.assertEqual([r.key for r in state.records_for_download(2)], ["id-1"])
                state.mark_download_failed("id-1", "again")
                self.assertEqual(state.records_for_download(2), [])


if __name__ == "__main__":
    unittest.main()
