"""Discovery, safe downloading, extraction, and manifest generation."""

from __future__ import annotations

import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from selenium.webdriver.remote.webdriver import WebDriver

from scraper.config import Settings
from scraper.extraction import NoTextError, extract_text, validate_pdf
from scraper.site import RecordRef, SiteAdapter
from scraper.state import State


class AuthenticationError(RuntimeError):
    """Raised when a download returns an apparent login page."""


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_download_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_download_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("download URL must use http or https")
    if parsed.hostname.lower() not in allowed_hosts:
        raise ValueError("download URL host is not allowed")


def _cookie_jar(cookies: list[dict[str, object]]) -> http.cookiejar.CookieJar:
    policy = http.cookiejar.DefaultCookiePolicy(
        strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain
    )
    jar = http.cookiejar.CookieJar(policy=policy)
    for item in cookies:
        domain = str(item.get("domain", ""))
        name = str(item.get("name", ""))
        if not domain or not name:
            continue
        expires_value = item.get("expiry")
        expires = int(expires_value) if isinstance(expires_value, (int, float)) else None
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=name,
                value=str(item.get("value", "")),
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=str(item.get("path", "/")),
                path_specified=True,
                secure=bool(item.get("secure", False)),
                expires=expires,
                discard=expires is None,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": item.get("httpOnly", False)},
                rfc2109=False,
            )
        )
    return jar


def download_pdf(
    url: str,
    destination: Path,
    cookies: list[dict[str, object]],
    settings: Settings,
) -> None:
    _validate_download_url(url, settings.allowed_download_hosts)
    opener = urllib.request.build_opener(
        _AllowedRedirects(settings.allowed_download_hosts),
        urllib.request.HTTPCookieProcessor(_cookie_jar(cookies)),
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 Document Collector"}
    )
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    try:
        try:
            response = opener.open(request, timeout=settings.request_timeout_seconds)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise AuthenticationError(
                    f"download returned HTTP {exc.code}; authentication or access has failed"
                ) from exc
            raise
        with response:
            _validate_download_url(response.url, settings.allowed_download_hosts)
            content_type = response.headers.get_content_type()
            if content_type in {"text/html", "application/xhtml+xml"}:
                raise AuthenticationError(
                    "download returned HTML; the browser session may have expired"
                )
            length = response.headers.get("Content-Length")
            if length and int(length) > settings.max_download_bytes:
                raise ValueError("download exceeds max_download_bytes")
            total = 0
            with temporary.open("wb") as output:
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > settings.max_download_bytes:
                        raise ValueError("download exceeds max_download_bytes")
                    output.write(chunk)
        validate_pdf(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def discover(driver: WebDriver, adapter: SiteAdapter, state: State) -> int:
    count = 0
    for record in adapter.discover(driver):
        state.upsert(record.key, record.detail_url, record.title)
        count += 1
    if count == 0:
        raise RuntimeError("discovery returned no records; check login and selectors")
    return count


def reconcile_files(state: State, output_dir: Path) -> None:
    for record in state.all_records():
        pdf_path = output_dir / record.pdf_name
        text_path = output_dir / record.text_name
        if record.download_status == "downloaded" and not pdf_path.is_file():
            state.reset_missing_pdf(record.key)
        elif record.download_status != "downloaded" and pdf_path.is_file():
            try:
                validate_pdf(pdf_path)
            except Exception:
                pdf_path.unlink(missing_ok=True)
            else:
                state.mark_downloaded(record.key)
        if record.extraction_status == "extracted" and not text_path.is_file():
            state.reset_missing_text(record.key)


def process_downloads(
    driver: WebDriver, adapter: SiteAdapter, settings: Settings, state: State
) -> None:
    for record in state.records_for_download(settings.max_retries):
        try:
            url = adapter.resolve_document(
                driver, RecordRef(record.key, record.detail_url, record.title)
            )
            download_pdf(
                url,
                settings.output_dir / record.pdf_name,
                driver.get_cookies(),
                settings,
            )
            state.mark_downloaded(record.key)
        except AuthenticationError as exc:
            state.mark_download_failed(record.key, str(exc))
            raise
        except Exception as exc:
            state.mark_download_failed(record.key, str(exc))
        finally:
            if settings.request_delay_seconds:
                time.sleep(settings.request_delay_seconds)


def process_extractions(settings: Settings, state: State) -> None:
    for record in state.records_for_extraction():
        try:
            extract_text(
                settings.output_dir / record.pdf_name,
                settings.output_dir / record.text_name,
            )
            state.mark_extracted(record.key)
        except NoTextError as exc:
            state.mark_no_text(record.key, str(exc))
        except Exception as exc:
            state.mark_extraction_failed(record.key, str(exc))


def write_manifest(state: State, output_dir: Path) -> None:
    destination = output_dir / "manifest.jsonl"
    temporary = destination.with_suffix(".jsonl.part")
    with temporary.open("w", encoding="utf-8") as output:
        for record in state.all_records():
            output.write(
                json.dumps(
                    {
                        "key": record.key,
                        "title": record.title,
                        "source_url": record.detail_url,
                        "pdf": record.pdf_name
                        if record.download_status == "downloaded"
                        else None,
                        "text": record.text_name
                        if record.extraction_status == "extracted"
                        else None,
                        "download_status": record.download_status,
                        "extraction_status": record.extraction_status,
                        "error": record.error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    os.replace(temporary, destination)
