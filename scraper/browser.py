"""Selenium browser creation."""

from __future__ import annotations

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

from scraper.config import BrowserSettings


def create_browser(settings: BrowserSettings) -> webdriver.Chrome:
    options = Options()
    if settings.headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as exc:
        raise RuntimeError(
            "could not start Chrome; check that Chrome and its driver are available"
        ) from exc
    driver.set_page_load_timeout(settings.page_timeout_seconds)
    return driver
