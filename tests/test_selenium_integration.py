import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scraper.browser import create_browser
from scraper.cli import main
from scraper.config import BrowserSettings, Settings, SiteSettings
from scraper.pipeline import discover, process_downloads, process_extractions, reconcile_files
from scraper.site import GenericSiteAdapter
from scraper.state import State
from tests.pdf_factory import pdf_bytes


class FakeSiteHandler(BaseHTTPRequestHandler):
    document = pdf_bytes("Full browser pipeline text")

    def do_GET(self):
        if self.path == "/list":
            self._html(
                '<div class="item" data-id="alpha"><a class="details" href="/detail/alpha">Alpha</a></div>'
                '<div class="item" data-id="beta"><a class="details" href="/detail/beta">Beta</a></div>'
                '<a class="next" href="/list?page=2">Next</a>',
                cookie=True,
            )
        elif self.path == "/list?page=2":
            self._html('<div class="item" data-id="gamma"><a class="details" href="/detail/gamma">Gamma</a></div>')
        elif self.path.startswith("/detail/"):
            key = self.path.rsplit("/", 1)[-1]
            self._html(f'<a class="download" href="/pdf/{key}">PDF</a>')
        elif self.path.startswith("/pdf/") and "session=active" in self.headers.get("Cookie", ""):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(self.document)))
            self.end_headers()
            self.wfile.write(self.document)
        else:
            self._html("<h1>Login</h1>")

    def _html(self, content: str, cookie: bool = False):
        body = f"<!doctype html><html><body>{content}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if cookie:
            self.send_header("Set-Cookie", "session=active; Path=/; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.environ.get("RUN_SELENIUM_TESTS") == "1", "set RUN_SELENIUM_TESTS=1")
class SeleniumIntegrationTests(unittest.TestCase):
    def test_paginated_discovery_download_and_extraction(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSiteHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                root = f"http://127.0.0.1:{server.server_port}"
                settings = Settings(
                    output_dir=output,
                    site=SiteSettings(
                        start_url=f"{root}/list",
                        item_selector=".item",
                        item_link_selector="a.details",
                        next_page_selector="a.next",
                        download_selector="a.download",
                    ),
                    browser=BrowserSettings(headless=True, page_timeout_seconds=10),
                    allowed_download_hosts=("127.0.0.1",),
                    request_delay_seconds=0,
                )
                adapter = GenericSiteAdapter(settings)
                with create_browser(settings.browser) as driver, State(output / "state.sqlite3") as state:
                    adapter.prepare(driver)
                    self.assertEqual(discover(driver, adapter, state), 3)
                    reconcile_files(state, output)
                    process_downloads(driver, adapter, settings, state)
                    process_extractions(settings, state)
                    self.assertEqual(state.counts()["downloaded"], 3)
                    self.assertEqual(state.counts()["extracted"], 3)
                    self.assertEqual(len(list(output.glob("*.pdf"))), 3)
                    self.assertEqual(len(list(output.glob("*.txt"))), 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_full_run_and_separate_resume_commands(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSiteHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                base_url = f"http://127.0.0.1:{server.server_port}"

                def config_for(folder: str) -> Path:
                    path = root / f"{folder}.json"
                    path.write_text(
                        json.dumps(
                            {
                                "output_dir": folder,
                                "site": {
                                    "start_url": f"{base_url}/list",
                                    "item_selector": ".item",
                                    "item_link_selector": "a.details",
                                    "next_page_selector": "a.next",
                                    "download_selector": "a.download",
                                },
                                "allowed_download_hosts": ["127.0.0.1"],
                                "request_delay_seconds": 0,
                            }
                        ),
                        encoding="utf-8",
                    )
                    return path

                full_config = config_for("full")
                self.assertEqual(main(["--config", str(full_config), "run"]), 0)
                self.assertEqual(len(list((root / "full").glob("*.pdf"))), 3)
                self.assertEqual(len(list((root / "full").glob("*.txt"))), 3)
                self.assertEqual(
                    len((root / "full" / "manifest.jsonl").read_text().splitlines()), 3
                )

                resumed_config = config_for("resumed")
                self.assertEqual(main(["--config", str(resumed_config), "discover"]), 0)
                self.assertEqual(main(["--config", str(resumed_config), "download"]), 0)
                self.assertEqual(main(["--config", str(resumed_config), "extract"]), 0)
                self.assertEqual(len(list((root / "resumed").glob("*.txt"))), 3)
                self.assertEqual(
                    len((root / "resumed" / "manifest.jsonl").read_text().splitlines()), 3
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
