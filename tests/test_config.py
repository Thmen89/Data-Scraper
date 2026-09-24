import json
import tempfile
import unittest
from pathlib import Path

from scraper.config import ConfigError, load_settings


class ConfigTests(unittest.TestCase):
    def write_config(self, directory: Path, value: object) -> Path:
        path = directory / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_loads_paths_relative_to_private_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write_config(
                root,
                {
                    "output_dir": "collected",
                    "site": {
                        "start_url": "https://docs.example.test/list",
                        "item_selector": ".item",
                    },
                },
            )
            settings = load_settings(path)
            self.assertEqual(settings.output_dir, root / "collected")
            self.assertEqual(settings.allowed_download_hosts, ("docs.example.test",))
            self.assertTrue(settings.browser.headless)

    def test_rejects_non_http_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_config(
                Path(temporary),
                {"site": {"start_url": "file:///secret", "item_selector": ".item"}},
            )
            with self.assertRaisesRegex(ConfigError, "http or https"):
                load_settings(path)

    def test_rejects_invalid_limits(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_config(
                Path(temporary),
                {
                    "site": {"start_url": "https://example.test", "item_selector": ".item"},
                    "max_download_bytes": 0,
                },
            )
            with self.assertRaisesRegex(ConfigError, "max_download_bytes"):
                load_settings(path)


if __name__ == "__main__":
    unittest.main()
