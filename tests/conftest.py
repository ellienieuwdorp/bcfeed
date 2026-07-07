"""Shared pytest harness for bcfeed (WP-03a).

This is the substrate the backend test suites (WP-03b/04/05/…) plug into. It
provides three things:

1. **Per-test data-dir isolation.** An autouse fixture points ``BCFEED_DATA_DIR``
   at a fresh temp directory and reloads the path-bound modules so no test can
   ever read or write the real application data dir
   (``~/Library/Application Support/bcfeed``).

2. **A frozen "today".** ``util.today`` is the single choke point for "now"
   (WP-02); ``frozen_today`` / ``freeze_today`` monkeypatch it — and the alias
   ``session_store._today`` captured at import — so the pervasive
   exclude-today logic is deterministic.

3. **Seeders + fixture loaders.** Helpers to write the JSON stores directly and
   to run the saved email fixtures through BOTH provider extraction paths
   (Gmail ``get_html_from_message`` and IMAP ``_extract_html``).

Import discipline for test authors: reference store modules as
``import session_store`` / ``session_store.func(...)``, never
``from session_store import func`` at module top — the autouse reload rebinds
the module's functions to the temp-dir paths, and a name imported at module
import time would keep pointing at the pre-reload (real-dir) function.
"""

from __future__ import annotations

import base64
import datetime
import email
import email.message
import importlib
import json
import sys
from email.header import decode_header
from pathlib import Path

import pytest

# Make the repo root importable so `import session_store` (etc.) works under a
# bare `pytest` invocation, not only `python -m pytest`. conftest.py is imported
# before any test module, so this runs first.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
EMAILS_DIR = FIXTURES_DIR / "emails"
PAGES_DIR = FIXTURES_DIR / "pages"

# A stable, arbitrary "today" for the whole suite. Chosen to sit just after the
# June-2025 dates in the email fixtures so exclude-today logic never drops them.
FIXED_TODAY = datetime.date(2025, 7, 1)

# Modules that bind data-dir paths at import time and must be reloaded (in this
# order) after BCFEED_DATA_DIR changes.
_PATH_BOUND_MODULES = ("paths", "session_store")


def _reload_path_bound_modules():
    """Reload paths then session_store so both see the current BCFEED_DATA_DIR."""
    for name in _PATH_BOUND_MODULES:
        module = importlib.import_module(name)
        importlib.reload(module)


# ---------------------------------------------------------------------------
# Data-dir isolation
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Point BCFEED_DATA_DIR at a fresh temp dir for every test.

    Autouse so isolation is guaranteed even for tests that never ask for it —
    the real data dir is untouchable. Yields the temp data-dir path.
    """
    data_dir = tmp_path / "bcfeed-data"
    data_dir.mkdir()
    monkeypatch.setenv("BCFEED_DATA_DIR", str(data_dir))
    _reload_path_bound_modules()
    yield data_dir
    # monkeypatch restores the env var; leave modules reloaded for the next
    # test's autouse pass to rebind cleanly.


@pytest.fixture
def data_dir(isolated_data_dir):
    """Explicit alias for the isolated temp data dir (autouse already applied)."""
    return isolated_data_dir


# ---------------------------------------------------------------------------
# Keychain isolation (WP-13)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_keyring(monkeypatch):
    """Route all keyring traffic to an in-memory dict for every test.

    credential_store reads AND — since WP-13's legacy-scope invalidation —
    can *delete* secrets from read-looking paths like ``/config.json``, so no
    test may ever touch the developer's real keychain. Returns the backing
    dict (keyed by ``(service, name)``) so tests can seed/inspect secrets.
    """
    import keyring
    import keyring.errors

    store: dict[tuple[str, str], str] = {}

    def _get(service, name):
        return store.get((service, name))

    def _set(service, name, value):
        store[(service, name)] = value

    def _delete(service, name):
        if (service, name) not in store:
            raise keyring.errors.PasswordDeleteError(name)
        del store[(service, name)]

    monkeypatch.setattr(keyring, "get_password", _get)
    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "delete_password", _delete)
    return store


# ---------------------------------------------------------------------------
# Frozen "today"
# ---------------------------------------------------------------------------
@pytest.fixture
def freeze_today(isolated_data_dir, monkeypatch):
    """Return a callable that freezes ``util.today`` to a given date.

    Patches both ``util.today`` and ``session_store._today`` (the alias captured
    at import) so every "now" consumer agrees.
    """

    def _freeze(day: datetime.date) -> datetime.date:
        import session_store
        import util

        monkeypatch.setattr(util, "today", lambda: day)
        monkeypatch.setattr(session_store, "_today", lambda: day)
        return day

    return _freeze


@pytest.fixture
def frozen_today(freeze_today):
    """Freeze today to FIXED_TODAY and return it."""
    return freeze_today(FIXED_TODAY)


# ---------------------------------------------------------------------------
# JSON store seeders
# ---------------------------------------------------------------------------
def _iso(day) -> str:
    if isinstance(day, datetime.date):
        return day.isoformat()
    return str(day)


class StoreSeeder:
    """Write bcfeed's JSON stores directly, bypassing the pipeline.

    Reads path constants live (via ``import paths``) so it always targets the
    current test's isolated data dir.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def _write(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def releases(self, mapping: dict) -> None:
        """Seed release_cache.json as ``{'YYYY-MM-DD': [release, ...]}``."""
        import paths

        normalized = {_iso(day): list(items) for day, items in mapping.items()}
        self._write(paths.RELEASE_CACHE_PATH, normalized)

    def scrape_status(self, days, *, empty: bool = False, source: str | None = None) -> None:
        """Seed the per-day ledger (schema v2: {date: {empty, source}})."""
        import paths

        ledger = {_iso(d): {"empty": empty, "source": source} for d in days}
        self._write(paths.SCRAPE_STATUS_PATH, {day: ledger[day] for day in sorted(ledger)})

    def viewed(self, urls) -> None:
        import paths

        self._write(paths.VIEWED_PATH, list(urls))

    def starred(self, urls) -> None:
        import paths

        self._write(paths.STARRED_PATH, list(urls))

    def embed_cache(self, mapping: dict) -> None:
        import paths

        self._write(paths.EMBED_CACHE_PATH, dict(mapping))


@pytest.fixture
def seed(isolated_data_dir):
    """A StoreSeeder bound to the isolated data dir."""
    return StoreSeeder(isolated_data_dir)


@pytest.fixture
def make_release():
    """Return a factory building release dicts in the canonical shape."""
    import util

    def _make(**overrides):
        base = dict(
            release_url="https://artist.bandcamp.com/album/example",
            date="2025-06-16",
            artist_name="Example Artist",
            release_title="Example Album",
            page_name="Example Page",
            is_track=False,
        )
        base.update(overrides)
        return util.construct_release(**base)

    return _make


# ---------------------------------------------------------------------------
# Email fixtures — exercisable through BOTH provider extraction paths
# ---------------------------------------------------------------------------
def _decode_mime_header(raw: str) -> str:
    if not raw:
        return ""
    parts = []
    for chunk, charset in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _message_to_gmail_payload(part: email.message.Message) -> dict:
    """Build a Gmail-API-style payload node from a parsed email part.

    Mirrors what the Gmail API returns: each part carries its decoded body
    base64url-wrapped, multipart parts carry a ``parts`` list.
    """
    node: dict = {"mimeType": part.get_content_type()}
    headers = [{"name": k, "value": v} for k, v in part.items()]
    node["headers"] = headers
    if part.is_multipart():
        node["body"] = {}
        node["parts"] = [_message_to_gmail_payload(p) for p in part.get_payload()]
    else:
        decoded = part.get_payload(decode=True) or b""
        node["body"] = {"data": base64.urlsafe_b64encode(decoded).decode("ascii")}
    return node


class EmailFixtures:
    """Load saved ``.eml`` fixtures and expose both provider extraction paths."""

    def __init__(self, emails_dir: Path):
        self.dir = emails_dir

    def path(self, name: str) -> Path:
        p = self.dir / (name if name.endswith(".eml") else f"{name}.eml")
        if not p.exists():
            raise FileNotFoundError(f"No email fixture named {name!r} in {self.dir}")
        return p

    def raw(self, name: str) -> bytes:
        return self.path(name).read_bytes()

    def message(self, name: str) -> email.message.Message:
        return email.message_from_bytes(self.raw(name))

    def subject(self, name: str) -> str:
        return _decode_mime_header(self.message(name).get("Subject", ""))

    def gmail_message(self, name: str) -> dict:
        """A Gmail 'full' message dict: ``{'payload': ...}`` with headers/body."""
        return {"payload": _message_to_gmail_payload(self.message(name))}

    def html_via_gmail(self, name: str) -> str | None:
        """HTML as the real Gmail provider path would extract it."""
        import gmail_client

        return gmail_client.get_html_from_message(self.gmail_message(name))

    def html_via_imap(self, name: str) -> str:
        """HTML as the real IMAP provider path would extract it."""
        from imap_client import ImapConfig
        from imap_provider import ImapProvider

        provider = ImapProvider(ImapConfig(host="fixture.invalid", username="u", password="p"))
        return provider._extract_html(self.message(name))


@pytest.fixture
def emails():
    """Access to the saved email fixtures (both provider paths)."""
    return EmailFixtures(EMAILS_DIR)


# ---------------------------------------------------------------------------
# Page fixtures
# ---------------------------------------------------------------------------
class PageFixtures:
    """Load saved Bandcamp release-page HTML fixtures."""

    def __init__(self, pages_dir: Path):
        self.dir = pages_dir

    def path(self, name: str) -> Path:
        p = self.dir / (name if name.endswith(".html") else f"{name}.html")
        if not p.exists():
            raise FileNotFoundError(f"No page fixture named {name!r} in {self.dir}")
        return p

    def html(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")


@pytest.fixture
def pages():
    """Access to the saved Bandcamp page fixtures."""
    return PageFixtures(PAGES_DIR)
