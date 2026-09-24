"""Selenium browser creation."""

from __future__ import annotations

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from scraper.config import BrowserSettings


def create_browser(settings: BrowserSettings) -> webdriver.Chrome:
    options = Options()
    if settings.headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(settings.page_timeout_seconds)
    return driver
