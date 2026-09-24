import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scraper.cli import main
from scraper.locking import AlreadyRunningError, RunLock


class CliTests(unittest.TestCase):
    def test_status_uses_private_config_without_starting_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "output_dir": "results",
                        "site": {
                            "start_url": "https://synthetic.example.test/list",
                            "item_selector": ".item",
                        },
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["--config", str(config), "status"])
            self.assertEqual(result, 0)
            self.assertIn("total: 0", output.getvalue())
            self.assertTrue((root / "results" / "state.sqlite3").is_file())

    def test_bad_config_returns_error_without_traceback(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = main(["--config", "missing.json", "status"])
        self.assertEqual(result, 1)
        self.assertIn("configuration not found", errors.getvalue())

    def test_output_lock_rejects_concurrent_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".scraper.lock"
            with RunLock(path):
                with self.assertRaises(AlreadyRunningError):
                    with RunLock(path):
                        pass


if __name__ == "__main__":
    unittest.main()
