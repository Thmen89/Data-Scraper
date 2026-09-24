"""Generic site navigation and the private-adapter loading boundary."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib.parse import urljoin

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait

from scraper.config import Settings


@dataclass(frozen=True)
class RecordRef:
    key: str
    detail_url: str
    title: str = ""


class SiteAdapter(Protocol):
    def prepare(self, driver: WebDriver) -> None: ...

    def discover(self, driver: WebDriver) -> Iterable[RecordRef]: ...

    def resolve_document(self, driver: WebDriver, record: RecordRef) -> str: ...


class GenericSiteAdapter:
    """Adapter for conventional paginated listing and detail pages."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def prepare(self, driver: WebDriver) -> None:
        driver.get(self.settings.site.start_url)

    def discover(self, driver: WebDriver) -> Iterable[RecordRef]:
        site = self.settings.site
        seen_pages: set[tuple[str, tuple[str, ...]]] = set()
        while True:
            WebDriverWait(driver, self.settings.browser.page_timeout_seconds).until(
                lambda browser: browser.find_elements(By.CSS_SELECTOR, site.item_selector)
            )
            elements = driver.find_elements(By.CSS_SELECTOR, site.item_selector)
            page_records: list[RecordRef] = []
            for element in elements:
                key = element.get_attribute(site.item_key_attribute)
                link = (
                    element.find_element(By.CSS_SELECTOR, site.item_link_selector)
                    if site.item_link_selector
                    else element
                )
                detail_url = link.get_attribute("href")
                if not key or not detail_url:
                    raise RuntimeError(
                        "each listing item must provide a stable key and detail link"
                    )
                page_records.append(
                    RecordRef(
                        key=key,
                        detail_url=urljoin(driver.current_url, detail_url),
                        title=element.text.strip(),
                    )
                )

            signature = (driver.current_url, tuple(record.key for record in page_records))
            if signature in seen_pages:
                raise RuntimeError("pagination repeated a page; stopping to avoid a loop")
            seen_pages.add(signature)
            yield from page_records

            if not site.next_page_selector:
                return
            next_links = driver.find_elements(By.CSS_SELECTOR, site.next_page_selector)
            if not next_links:
                return
            next_url = next_links[0].get_attribute("href")
            if not next_url:
                raise RuntimeError("the next-page element must have an href")
            driver.get(urljoin(driver.current_url, next_url))

    def resolve_document(self, driver: WebDriver, record: RecordRef) -> str:
        driver.get(record.detail_url)
        selector = self.settings.site.download_selector
        element = WebDriverWait(
            driver, self.settings.browser.page_timeout_seconds
        ).until(lambda browser: browser.find_element(By.CSS_SELECTOR, selector))
        url = element.get_attribute("href")
        if not url:
            raise RuntimeError("the download element must have an href")
        return urljoin(driver.current_url, url)


def load_adapter(settings: Settings) -> SiteAdapter:
    """Load an explicitly configured, ignored adapter or use the generic one."""
    if settings.adapter_path is None:
        return GenericSiteAdapter(settings)
    path = settings.adapter_path
    if not path.is_file():
        raise RuntimeError(f"private adapter not found: {path}")
    spec = importlib.util.spec_from_file_location("private_site_adapter", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load private adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "create_adapter", None)
    if not callable(factory):
        raise RuntimeError("private adapter must define create_adapter(settings)")
    return factory(settings)
