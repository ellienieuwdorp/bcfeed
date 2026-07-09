from __future__ import annotations

import os
import sys
from pathlib import Path

# Single source of truth for the application version. Surfaced at runtime via
# /config.json (server.py) and Settings→About (web/js/settings.js), and read as
# the packaging version by pyproject.toml ([tool.setuptools.dynamic]). Keep this
# the ONLY place the version literal appears in the repo (ARCH-9 · WP-28).
__version__ = "1.1.0"


def resource_path(name: str) -> Path:
    """
    Resolve a bundled read-only asset (frontend files, in-app docs) that ships
    alongside the code.

    In a normal install/dev tree the assets sit next to this module. Under a
    PyInstaller onedir/onefile bundle they are unpacked into ``sys._MEIPASS``;
    prefer that root when present so a future macOS ``.app`` (WP-29) just works
    without any call site changing. This is the ONLY legitimate ``sys._MEIPASS``
    reference in the codebase — asset resolution, not credentials.
    """
    bundle_root = getattr(sys, "_MEIPASS", None)
    base = Path(bundle_root) if bundle_root else Path(__file__).resolve().parent
    return base / name


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
# The former no_results_dates.json store is gone (WP-14 · LOG-11): empty days
# are recorded in the scrape_status ledger itself. migrations.py folds any
# legacy file in (and deletes it) by literal filename — no path constant here.
SCRAPE_STATUS_PATH = DATA_DIR / "scrape_status.json"
# Sidecar recording the store schema version (WP-14 · LOG-21).
SCHEMA_META_PATH = DATA_DIR / "meta.json"
EMBED_CACHE_PATH = DATA_DIR / "embed_cache.json"
# Legacy import source only (never written): a pre-keychain credentials.json
# is migrated into the keychain on first use, then deleted. The legacy pickle
# token constant is fully retired (WP-13 · SEC-10/CQ-70).
CREDENTIALS_PATH = DATA_DIR / GMAIL_CREDENTIALS_FILE
# Bundled asset paths route through resource_path() so a PyInstaller/.app bundle
# resolves them from the unpacked bundle root (WP-28/WP-29). server.py derives
# FRONTEND_DIR / WEB_JS_DIR from DASHBOARD_PATH.parent — no asset path is built
# ad hoc outside this module.
DASHBOARD_PATH = resource_path("dashboard.html")
DASHBOARD_CSS_PATH = resource_path("dashboard.css")
DASHBOARD_JS_PATH = resource_path("dashboard.js")
README_PATH = resource_path("README.md")
SETUP_PATH = resource_path("SETUP.md")
IMAP_SETUP_PATH = resource_path("IMAP_SETUP.md")
GMAIL_SETUP_PATH = resource_path("GMAIL_SETUP.md")
