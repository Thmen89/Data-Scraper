import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from scraper.config import BrowserSettings, Settings, SiteSettings
from scraper.pipeline import (
    AuthenticationError,
    _cookie_jar,
    download_pdf,
    process_downloads,
    process_extractions,
    reconcile_files,
)
from scraper.state import State
from tests.pdf_factory import pdf_bytes


class DownloadHandler(BaseHTTPRequestHandler):
    document = pdf_bytes("Cookie protected text")

    def do_GET(self):
        if self.path == "/redirect-away":
            self.send_response(302)
            self.send_header("Location", "http://localhost:1/not-allowed")
            self.end_headers()
            return
        if self.path == "/oversized":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(self.document) + 10_000))
            self.end_headers()
            return
        if self.path == "/unauthorized":
            self.send_response(401)
            self.end_headers()
            return
        if "session=allowed" not in self.headers.get("Cookie", ""):
            body = b"<html>login</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(self.document)))
        self.end_headers()
        self.wfile.write(self.document)

    def log_message(self, *_):
        pass


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DownloadHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def settings(self, output: Path, max_bytes: int = 10_000_000) -> Settings:
        return Settings(
            output_dir=output,
            site=SiteSettings("https://example.test", ".item"),
            browser=BrowserSettings(),
            allowed_download_hosts=("127.0.0.1",),
            max_download_bytes=max_bytes,
        )

    def test_uses_domain_aware_browser_cookie(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "saved.pdf"
            url = f"http://127.0.0.1:{self.server.server_port}/document"
            cookies = [
                {
                    "name": "session",
                    "value": "allowed",
                    "domain": "127.0.0.1",
                    "path": "/",
                }
            ]
            download_pdf(url, destination, cookies, self.settings(root))
            self.assertEqual(destination.read_bytes(), DownloadHandler.document)
            self.assertFalse((root / "saved.pdf.part").exists())

    def test_rejects_login_html_and_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "saved.pdf"
            url = f"http://127.0.0.1:{self.server.server_port}/document"
            with self.assertRaises(AuthenticationError):
                download_pdf(url, destination, [], self.settings(root))
            self.assertFalse(destination.exists())
            self.assertFalse((root / "saved.pdf.part").exists())

    def test_treats_http_401_as_authentication_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"http://127.0.0.1:{self.server.server_port}/unauthorized"
            with self.assertRaises(AuthenticationError):
                download_pdf(url, root / "saved.pdf", [], self.settings(root))

    def test_rejects_disallowed_host_before_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self.settings(Path(temporary))
            with self.assertRaisesRegex(ValueError, "host is not allowed"):
                download_pdf("https://other.example.test/file.pdf", Path(temporary) / "x.pdf", [], settings)

    def test_rejects_redirect_to_disallowed_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"http://127.0.0.1:{self.server.server_port}/redirect-away"
            with self.assertRaisesRegex(ValueError, "host is not allowed"):
                download_pdf(url, root / "x.pdf", [], self.settings(root))

    def test_rejects_size_from_header_without_partial_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = f"http://127.0.0.1:{self.server.server_port}/oversized"
            with self.assertRaisesRegex(ValueError, "max_download_bytes"):
                download_pdf(url, root / "x.pdf", [], self.settings(root, max_bytes=100))
            self.assertFalse((root / "x.pdf.part").exists())

    def test_reconciles_file_published_before_checkpoint_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with State(root / "state.sqlite3") as state:
                state.upsert("interrupted", "https://example.test/detail", "Recovered")
                record = state.get("interrupted")
                self.assertIsNotNone(record)
                (root / record.pdf_name).write_bytes(DownloadHandler.document)
                reconcile_files(state, root)
                self.assertEqual(state.get("interrupted").download_status, "downloaded")

    def test_recreates_missing_text_without_redownloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self.settings(root)
            with State(root / "state.sqlite3") as state:
                state.upsert("complete", "https://example.test/detail", "Complete")
                record = state.get("complete")
                (root / record.pdf_name).write_bytes(DownloadHandler.document)
                state.mark_downloaded(record.key)
                (root / record.text_name).write_text("old text", encoding="utf-8")
                state.mark_extracted(record.key)
                (root / record.text_name).unlink()

                reconcile_files(state, root)
                self.assertEqual(state.records_for_download(3), [])
                self.assertEqual([r.key for r in state.records_for_extraction()], ["complete"])
                process_extractions(settings, state)
                self.assertIn("Cookie protected text", (root / record.text_name).read_text())

    def test_authentication_failure_stops_remaining_records(self):
        class Adapter:
            resolved: list[str] = []

            def resolve_document(self, driver, record):
                self.resolved.append(record.key)
                return "http://127.0.0.1/document"

        class Driver:
            def get_cookies(self):
                return []

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = self.settings(root)
            settings = Settings(
                output_dir=settings.output_dir,
                site=settings.site,
                browser=settings.browser,
                allowed_download_hosts=settings.allowed_download_hosts,
                request_delay_seconds=0,
            )
            adapter = Adapter()
            with State(root / "state.sqlite3") as state:
                state.upsert("first", "https://example.test/1")
                state.upsert("second", "https://example.test/2")
                with mock.patch(
                    "scraper.pipeline.download_pdf",
                    side_effect=AuthenticationError("expired"),
                ):
                    with self.assertRaises(AuthenticationError):
                        process_downloads(Driver(), adapter, settings, state)
                self.assertEqual(adapter.resolved, ["first"])

    def test_host_only_cookie_is_not_sent_to_child_host(self):
        jar = _cookie_jar(
            [{"name": "session", "value": "secret", "domain": "app.example.test", "path": "/"}]
        )
        exact = urllib.request.Request("https://app.example.test/file")
        child = urllib.request.Request("https://cdn.app.example.test/file")
        jar.add_cookie_header(exact)
        jar.add_cookie_header(child)
        self.assertEqual(exact.get_header("Cookie"), "session=secret")
        self.assertIsNone(child.get_header("Cookie"))

    def test_explicit_domain_cookie_can_be_sent_to_child_host(self):
        jar = _cookie_jar(
            [{"name": "shared", "value": "yes", "domain": ".example.test", "path": "/"}]
        )
        child = urllib.request.Request("https://cdn.example.test/file")
        jar.add_cookie_header(child)
        self.assertEqual(child.get_header("Cookie"), "shared=yes")


if __name__ == "__main__":
    unittest.main()
