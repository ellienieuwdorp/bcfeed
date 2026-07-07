from __future__ import annotations

import os
from pathlib import Path


def _resolve_base_dir() -> Path:
    """
    Return the data directory location WITHOUT creating it.

    Honors the ``BCFEED_DATA_DIR`` environment variable (used by tests to
    isolate state into a temp dir). Otherwise, on macOS prefer
    ~/Library/Application Support/bcfeed; elsewhere fall back to ~/.bcfeed.
    """
    override = os.environ.get("BCFEED_DATA_DIR")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    app_support = home / "Library" / "Application Support" / "bcfeed"
    if app_support.parent.exists():  # likely macOS
        return app_support
    return home / ".bcfeed"


def get_data_dir() -> Path:
    """Return the data directory, creating it on first use."""
    base = _resolve_base_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base


# Path constants are resolved at import (no directory side effect); the dir is
# created lazily by get_data_dir()/writers. Tests set BCFEED_DATA_DIR before import.
DATA_DIR = _resolve_base_dir()
GMAIL_CREDENTIALS_FILE = "credentials.json"
VIEWED_PATH = DATA_DIR / "viewed_state.json"
STARRED_PATH = DATA_DIR / "starred_state.json"
RELEASE_CACHE_PATH = DATA_DIR / "release_cache.json"
EMPTY_DATES_PATH = DATA_DIR / "no_results_dates.json"
SCRAPE_STATUS_PATH = DATA_DIR / "scrape_status.json"
EMBED_CACHE_PATH = DATA_DIR / "embed_cache.json"
# Legacy import source only (never written): a pre-keychain credentials.json
# is migrated into the keychain on first use, then deleted. The legacy pickle
# token constant is fully retired (WP-13 · SEC-10/CQ-70).
CREDENTIALS_PATH = DATA_DIR / GMAIL_CREDENTIALS_FILE
DASHBOARD_PATH = Path(__file__).resolve().with_name("dashboard.html")
DASHBOARD_CSS_PATH = Path(__file__).resolve().with_name("dashboard.css")
DASHBOARD_JS_PATH = Path(__file__).resolve().with_name("dashboard.js")
README_PATH = Path(__file__).resolve().with_name("README.md")
SETUP_PATH = Path(__file__).resolve().with_name("SETUP.md")
IMAP_SETUP_PATH = Path(__file__).resolve().with_name("IMAP_SETUP.md")
GMAIL_SETUP_PATH = Path(__file__).resolve().with_name("GMAIL_SETUP.md")
