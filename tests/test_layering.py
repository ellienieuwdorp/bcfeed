"""WP-11 · Backend layering (ARC-3): server.py sheds its non-HTTP concerns.

Covered:
* the relocated IMAP config/discovery helpers live in provider_factory,
  import without Flask, and run against a mocked connection (rank + select);
* the WP-08 password-reuse mitigation survives the move: the stored IMAP
  password is reused ONLY when the posted connection signature matches the
  saved config;
* the /provider-config and /imap/discover routes drive the relocated helpers;
* the single static-asset route serves /dashboard.css and /dashboard.js with
  the same URLs/mimetypes as the former per-file routes (WP-18 prep) and keeps
  the WP-08 generic-404 behavior;
* /releases still surfaces derived embed_url + has_description for ok embed records —
  both new-shape and legacy flat entries;
* grep-proof source guards: no requests/BeautifulSoup/ImapClient usage left in
  server.py, and provider_factory never imports Flask.

No test opens a network connection; IMAP clients are fakes injected at the
seams the relocation created.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server_mod(isolated_data_dir):
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


# ---------------------------------------------------------------------------
# Relocated IMAP helpers: importable + runnable without Flask
# ---------------------------------------------------------------------------
def test_provider_factory_imports_without_flask():
    """provider_factory (and the discovery helpers) must not pull in Flask."""
    code = (
        "import sys\n"
        "import provider_factory\n"
        "assert 'flask' not in sys.modules, 'provider_factory imported Flask'\n"
    )
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=60,
    )


class FakeImapConnection:
    """Duck-typed stand-in for ImapClient: list/select/search, no sockets."""

    def __init__(self, folders, hits_in=()):
        self.folders = list(folders)
        self.hits_in = set(hits_in)
        self.selected: list[str] = []
        self.closed = False

    def list_folders(self):
        return self.folders

    def select_folder(self, name):
        self.selected.append(name)

    def uid_search(self, criteria):
        current = self.selected[-1] if self.selected else None
        return ["1"] if current in self.hits_in else []

    def close(self):
        self.closed = True


def _folder(name, flags=(), selectable=True):
    from imap_client import ImapFolder

    return ImapFolder(name=name, flags=tuple(flags), selectable=selectable)


def test_discover_imap_folders_ranks_and_probes(isolated_data_dir):
    import provider_factory

    conn = FakeImapConnection(
        folders=[
            _folder("Trash", flags=("\\Trash",)),
            _folder("INBOX"),
            _folder("[Gmail]/All Mail", flags=("\\All",)),
            _folder("[Gmail]", selectable=False),
            _folder("Spam", flags=("\\Junk",)),
        ],
        hits_in={"[Gmail]/All Mail"},
    )
    folders, recommended = provider_factory.discover_imap_folders(conn)

    # \All ranks first, then INBOX; junk-ish folders sink; non-selectable gone.
    assert folders[:2] == ["[Gmail]/All Mail", "INBOX"]
    assert "[Gmail]" not in folders
    assert recommended == "[Gmail]/All Mail"
    # Junk/Trash folders are never probed for the recommendation.
    assert set(conn.selected) <= {"[Gmail]/All Mail", "INBOX"}


def test_discover_imap_folders_falls_back_to_best_selectable(isolated_data_dir):
    import provider_factory

    conn = FakeImapConnection(folders=[_folder("INBOX"), _folder("Archive")], hits_in=set())
    folders, recommended = provider_factory.discover_imap_folders(conn)
    assert recommended == folders[0] == "INBOX"


def test_imap_folder_rank_orders_all_inbox_archive_junk(isolated_data_dir):
    import provider_factory

    ranked = sorted(
        [
            _folder("Sent", flags=("\\Sent",)),
            _folder("Archive", flags=("\\Archive",)),
            _folder("INBOX"),
            _folder("All Mail", flags=("\\All",)),
            _folder("Old", selectable=False),
        ],
        key=provider_factory.imap_folder_rank,
    )
    assert [f.name for f in ranked] == ["All Mail", "INBOX", "Archive", "Sent", "Old"]


# ---------------------------------------------------------------------------
# WP-08 mitigation preserved: password reuse only on signature match
# ---------------------------------------------------------------------------
SAVED_IMAP = {
    "host": "imap.example.com",
    "port": 993,
    "username": "u@example.com",
    "folder": "INBOX",
    "use_ssl": True,
}


def test_stored_password_reused_when_signature_matches(isolated_data_dir, monkeypatch):
    import provider_factory

    monkeypatch.setattr(provider_factory, "get_imap_password", lambda: "stored-secret")
    config = provider_factory.build_imap_config(
        {"host": "imap.example.com", "username": "u@example.com"}, SAVED_IMAP
    )
    assert config["password"] == "stored-secret"


def test_stored_password_not_reused_for_different_target(isolated_data_dir, monkeypatch):
    import provider_factory

    def fail():  # pragma: no cover - reaching this IS the failure
        raise AssertionError("stored password must not be read for a foreign target")

    monkeypatch.setattr(provider_factory, "get_imap_password", fail)
    config = provider_factory.build_imap_config(
        {"host": "attacker.example.net", "username": "u@example.com"}, SAVED_IMAP
    )
    assert config["password"] == ""


def test_coerce_helpers(isolated_data_dir):
    import provider_factory

    assert provider_factory.coerce_imap_port("143") == 143
    assert provider_factory.coerce_imap_port("nope") == 993
    assert provider_factory.coerce_imap_port(-1) == 993
    assert provider_factory.coerce_imap_use_ssl("false") is False
    assert provider_factory.coerce_imap_use_ssl(None) is True


# ---------------------------------------------------------------------------
# Routes call into the relocated helpers
# ---------------------------------------------------------------------------
def test_imap_discover_route_uses_relocated_helpers(server_mod, monkeypatch):
    conn = FakeImapConnection(folders=[_folder("INBOX")], hits_in={"INBOX"})
    opened = {}

    def fake_open(imap, **kwargs):
        opened.update(imap)
        return conn

    monkeypatch.setattr(server_mod, "_open_imap_client", fake_open)
    resp = server_mod.app.test_client().post(
        "/imap/discover",
        json={"imap_config": {"host": "imap.example.com", "username": "u", "password": "p"}},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"folders": ["INBOX"], "recommended_folder": "INBOX"}
    assert opened["host"] == "imap.example.com"
    assert conn.closed


def test_provider_config_post_validates_via_relocated_helpers(server_mod, monkeypatch):
    import provider_factory

    conn = FakeImapConnection(folders=[_folder("INBOX")])
    monkeypatch.setattr(server_mod, "_open_imap_client", lambda imap, **kw: conn)
    # Keep the test off the real system keychain.
    stored = {}
    monkeypatch.setattr(
        provider_factory, "save_imap_password", lambda pw: stored.update(password=pw)
    )
    monkeypatch.setattr(server_mod, "has_imap_password", lambda: bool(stored))
    client = server_mod.app.test_client()

    resp = client.post(
        "/provider-config",
        json={
            "provider": "imap",
            "imap_config": {
                "host": "imap.example.com",
                "username": "u",
                "password": "p",
                "folder": "INBOX",
            },
        },
    )
    assert resp.status_code == 200
    assert conn.closed
    saved = client.get("/provider-config").get_json()
    assert saved["provider"] == "imap"
    assert saved["imap_config"]["host"] == "imap.example.com"


# ---------------------------------------------------------------------------
# Static assets: one route, same URLs, same behavior (WP-18 prep)
# ---------------------------------------------------------------------------
def test_static_route_serves_css_and_js(server_mod):
    client = server_mod.app.test_client()

    css = client.get("/dashboard.css")
    assert css.status_code == 200
    assert css.mimetype == "text/css"
    assert len(css.data) > 0

    js = client.get("/dashboard.js")
    assert js.status_code == 200
    assert js.mimetype == "application/javascript"
    assert len(js.data) > 0


def test_static_route_missing_file_is_generic_404(server_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(server_mod, "FRONTEND_DIR", tmp_path / "nowhere")
    resp = server_mod.app.test_client().get("/dashboard.css")
    assert resp.status_code == 404
    assert str(tmp_path) not in resp.get_data(as_text=True)


def test_dashboard_html_route_unchanged(server_mod):
    resp = server_mod.app.test_client().get("/dashboard")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"


# ---------------------------------------------------------------------------
# /releases still surfaces embed enrichment for ok records
# ---------------------------------------------------------------------------
def test_releases_surfaces_ok_record_enrichment(server_mod, seed, make_release, frozen_today):
    # WP-14 · LOG-20: embed_url is derived (release_id + is_track), the
    # description body stays out of the payload (has_description instead).
    url = "https://a.bandcamp.com/album/x"
    seed.releases({"2025-06-16": [make_release(release_url=url, date="2025-06-16")]})
    seed.embed_cache(
        {
            url: {
                "status": "ok",
                "release_id": 555,
                "is_track": False,
                "description": "A fine record.",
                "fetched_at": 1_750_000_000,
            }
        }
    )
    resp = server_mod.app.test_client().get("/releases")
    assert resp.status_code == 200
    rel = resp.get_json()["releases"][0]
    assert "album=555" in rel["embed_url"]
    assert rel["has_description"] is True
    assert "description" not in rel
    assert rel["release_id"] == 555


def test_releases_skips_error_records(server_mod, seed, make_release, frozen_today):
    url = "https://a.bandcamp.com/album/x"
    seed.releases({"2025-06-16": [make_release(release_url=url, date="2025-06-16")]})
    seed.embed_cache({url: {"status": "error", "code": "http_404", "fetched_at": 1}})
    rel = server_mod.app.test_client().get("/releases").get_json()["releases"][0]
    assert "embed_url" not in rel
    assert "description" not in rel
    assert "has_description" not in rel


# ---------------------------------------------------------------------------
# Source guards (ARC-3 acceptance grep, kept honest by the suite)
# ---------------------------------------------------------------------------
def test_server_py_has_no_non_http_concerns():
    src = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"\brequests\.", src), "server.py must not call requests"
    assert "BeautifulSoup" not in src, "server.py must not parse HTML"
    assert not re.search(r"\bImapClient\(", src), "server.py must not open IMAP connections"
    assert not re.search(r"\bimport requests\b", src)


def test_provider_factory_has_no_flask_import():
    src = (REPO_ROOT / "provider_factory.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import flask|from flask)", src, re.MULTILINE)
