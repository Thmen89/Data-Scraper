"""Load and validate the private scraper configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when a configuration file is incomplete or unsafe."""


@dataclass(frozen=True)
class BrowserSettings:
    headless: bool = True
    page_timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SiteSettings:
    start_url: str
    item_selector: str
    item_key_attribute: str = "data-id"
    item_link_selector: str | None = None
    next_page_selector: str | None = None
    download_selector: str = "a[href]"


@dataclass(frozen=True)
class Settings:
    output_dir: Path
    site: SiteSettings
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    adapter_path: Path | None = None
    allowed_download_hosts: tuple[str, ...] = ()
    request_delay_seconds: float = 1.0
    request_timeout_seconds: float = 60.0
    max_download_bytes: int = 100 * 1024 * 1024
    max_retries: int = 3


def _http_url(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(f"{name} must use http or https")
    return value


def _positive_number(value: object, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number")
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ConfigError(f"{name} must be {qualifier}")
    return float(value)


def load_settings(path: str | Path) -> Settings:
    """Load settings from an ignored JSON file."""
    config_path = Path(path).resolve()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {config_path}: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be an object")

    site_raw = raw.get("site")
    if not isinstance(site_raw, dict):
        raise ConfigError("site must be an object")
    start_url = _http_url(site_raw.get("start_url"), "site.start_url")
    item_selector = site_raw.get("item_selector")
    if not isinstance(item_selector, str) or not item_selector.strip():
        raise ConfigError("site.item_selector must be a non-empty CSS selector")

    browser_raw = raw.get("browser", {})
    if not isinstance(browser_raw, dict):
        raise ConfigError("browser must be an object")
    headless = browser_raw.get("headless", True)
    if not isinstance(headless, bool):
        raise ConfigError("browser.headless must be true or false")

    output_value = raw.get("output_dir", "output")
    if not isinstance(output_value, str) or not output_value.strip():
        raise ConfigError("output_dir must be a non-empty path")
    output_dir = Path(output_value).expanduser()
    if not output_dir.is_absolute():
        output_dir = (config_path.parent / output_dir).resolve()

    adapter_path = None
    adapter_value = raw.get("adapter_path")
    if adapter_value is not None:
        if not isinstance(adapter_value, str) or not adapter_value.strip():
            raise ConfigError("adapter_path must be a non-empty path")
        adapter_path = Path(adapter_value).expanduser()
        if not adapter_path.is_absolute():
            adapter_path = (config_path.parent / adapter_path).resolve()

    default_host = urlparse(start_url).hostname
    host_values = raw.get("allowed_download_hosts", [default_host])
    if not isinstance(host_values, list) or not host_values:
        raise ConfigError("allowed_download_hosts must be a non-empty list")
    hosts: list[str] = []
    for host in host_values:
        if not isinstance(host, str) or not host.strip() or "/" in host:
            raise ConfigError("allowed_download_hosts entries must be hostnames")
        hosts.append(host.lower())

    max_retries = raw.get("max_retries", 3)
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
        raise ConfigError("max_retries must be a positive integer")
    max_bytes = raw.get("max_download_bytes", 100 * 1024 * 1024)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ConfigError("max_download_bytes must be a positive integer")

    def optional_selector(name: str) -> str | None:
        value = site_raw.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError(f"site.{name} must be a non-empty CSS selector")
        return value

    return Settings(
        output_dir=output_dir,
        site=SiteSettings(
            start_url=start_url,
            item_selector=item_selector,
            item_key_attribute=str(site_raw.get("item_key_attribute", "data-id")),
            item_link_selector=optional_selector("item_link_selector"),
            next_page_selector=optional_selector("next_page_selector"),
            download_selector=str(site_raw.get("download_selector", "a[href]")),
        ),
        browser=BrowserSettings(
            headless=headless,
            page_timeout_seconds=_positive_number(
                browser_raw.get("page_timeout_seconds", 30),
                "browser.page_timeout_seconds",
            ),
        ),
        adapter_path=adapter_path,
        allowed_download_hosts=tuple(dict.fromkeys(hosts)),
        request_delay_seconds=_positive_number(
            raw.get("request_delay_seconds", 1),
            "request_delay_seconds",
            allow_zero=True,
        ),
        request_timeout_seconds=_positive_number(
            raw.get("request_timeout_seconds", 60), "request_timeout_seconds"
        ),
        max_download_bytes=max_bytes,
        max_retries=max_retries,
    )
