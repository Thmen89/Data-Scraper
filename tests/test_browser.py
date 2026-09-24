import unittest
from unittest import mock

from selenium.common.exceptions import WebDriverException

from scraper.browser import create_browser
from scraper.config import BrowserSettings


class BrowserTests(unittest.TestCase):
    def test_startup_failure_has_actionable_message(self):
        with mock.patch(
            "scraper.browser.webdriver.Chrome",
            side_effect=WebDriverException("long driver diagnostics"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "check that Chrome and its driver are available"
            ):
                create_browser(BrowserSettings())


if __name__ == "__main__":
    unittest.main()
