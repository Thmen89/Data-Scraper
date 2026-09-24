# Document Scraper

A small, source-neutral Python tool that uses Selenium to discover documents,
downloads PDFs with the browser session, and creates UTF-8 text files for later
search or AI ingestion.

The repository intentionally contains no real source URL, selectors,
credentials, captured pages, or collected data. Keep those only under
`private/`, which Git ignores.

## What it does

- Discovers records across paginated listing pages.
- Resumes safely using `state.sqlite3` in the output folder.
- Streams bounded PDF downloads to temporary files before publishing them.
- Reuses domain-aware Selenium cookies and rejects unexpected download hosts.
- Keeps each original PDF and writes a matching page-marked `.txt` file.
- Writes `manifest.jsonl` for downstream ingestion and provenance.
- Runs one process per output folder and stops on an apparent expired session.

It deliberately does not include OCR, parallel downloading, browser profile
persistence, or Word conversion. Scanned PDFs are retained and reported as
`no_text` so OCR can be added later if the real documents require it.

## Setup

Use Python 3.10 or newer and an installed Chrome/Chromium browser:

```bash
python -m venv .venv
# Linux/macOS
.venv/bin/python -m pip install -r requirements.txt
# Windows PowerShell
.venv\Scripts\python -m pip install -r requirements.txt
```

Selenium Manager normally locates the browser and driver. A corporate machine
may require an approved Chrome installation or an administrator-provided
driver.

## Private configuration

Create the ignored directory and copy the safe example:

```bash
mkdir private
cp config.example.json private/config.json
```

On Windows PowerShell, use:

```powershell
New-Item -ItemType Directory -Force private
Copy-Item config.example.json private/config.json
```

Edit only `private/config.json`. The generic adapter expects each listing item
to have a stable key attribute and a link to its detail page. The detail page
must expose a normal HTTP(S) PDF link. Add every legitimate download/CDN host
to `allowed_download_hosts`.

Start with `browser.headless` set to `false` while inspecting selectors. Never
place passwords in the JSON file. For a conventional site, authenticate in a
site-specific adapter or through an approved existing mechanism.

## Commands

The launcher defaults to the complete resumable run:

```bash
.venv/bin/python launcher.py run
.venv/bin/python launcher.py status
.venv/bin/python launcher.py extract
```

Windows PowerShell equivalents use `.venv\Scripts\python launcher.py`.

Available commands:

- `run`: discover, download, extract text, and refresh the manifest.
- `discover`: scan listings only.
- `download`: process already discovered pending downloads.
- `extract`: retry text extraction without re-downloading PDFs.
- `status`: print checkpoint totals without starting a browser.
- `manifest`: regenerate `manifest.jsonl` from the checkpoint.

Use another ignored configuration path with
`launcher.py --config private/other.json run`. Exit code `2` from `run` means
some work remains failed, pending, or requires OCR.

## Complex sites: one private adapter

Do not encode arbitrary click sequences in JSON or modify the launcher. If the
site needs login, intermediate clicks, JavaScript, or custom pagination, create
the ignored file `private/site.py`, set `"adapter_path": "site.py"` in the
private configuration, and define this small boundary:

```python
from scraper.site import RecordRef


class Adapter:
    def __init__(self, settings):
        self.settings = settings

    def prepare(self, driver):
        # Called for every new browser. Open the site and log in here.
        driver.get(self.settings.site.start_url)

    def discover(self, driver):
        # Traverse listings and yield plain durable values.
        yield RecordRef("stable-private-key", "https://detail-url", "Title")

    def resolve_document(self, driver, record):
        # Navigate/click as needed and return a fresh HTTP(S) download URL.
        return "https://allowed-download-host/document.pdf"


def create_adapter(settings):
    return Adapter(settings)
```

Use a durable record key, not a row number, title alone, or expiring download
URL. The core continues to own filenames, retries, checkpointing, downloads,
extraction, and manifests. Standard direct GET downloads are supported; POST,
`blob:` URLs, CAPTCHA, and browser-only downloads need a narrow custom change.

## Validation

Fast tests:

```bash
.venv/bin/python -m unittest discover -v
```

Full synthetic browser pipeline:

```bash
RUN_SELENIUM_TESTS=1 .venv/bin/python -m unittest discover -v
```

Set `RUN_SELENIUM_TESTS=1` in PowerShell with
`$env:RUN_SELENIUM_TESTS = "1"`. All fixtures are generated locally and contain
no source data.

Before a large collection, confirm authorization and applicable terms, test
5–10 documents, inspect their PDFs and text, then verify resume behavior. Keep
the output folder and `private/` out of backups or AI systems that are not
approved for the data.
