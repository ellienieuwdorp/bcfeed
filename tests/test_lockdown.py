"""Localhost lockdown tests (WP-08 — SEC-4/SEC-11/SEC-9, ARC-5b/5c).

Covers the three coordinated guards:

1. **Host validation** (DNS-rebinding defense): every route — including SSE and
   static files — rejects any request whose Host header is not
   localhost/127.0.0.1[:port] with 403.
2. **Anti-CSRF custom header**: every state-mutating (non-GET) route requires
   ``X-BCFeed-Request: 1``. A cross-site form or "simple" fetch cannot set
   custom headers, so drive-by CSRF is blocked — critically for
   ``/imap/discover`` and ``/provider-config``, which make the server open
   outbound IMAP connections. The SSE GET stays header-free (EventSource
   cannot set headers) and is covered by the Host check.
3. **No CORS, generic errors**: no ``Access-Control-Allow-Origin`` on any
   response; error bodies never leak filesystem paths, exception class names,
   or keyring internals.

Two client flavors are used deliberately:

- ``server_mod.app.test_client()`` models the app's own browser: the
  ``_SameOriginTestClient`` default injects the anti-CSRF header exactly like
  the dashboard's fetch wrapper does.
- ``werkzeug.test.Client(app)`` is the raw WSGI surface — no header injection —
  standing in for a cross-site attacker or a bare curl.
"""

from __future__ import annotations

import importlib
import io

import pytest
from werkzeug.test import Client

from credential_store import CredentialStoreError
from email_provider import AuthenticationError, ProviderError

# A sentinel that must never appear in a response body. Used as the "secret"
# detail inside injected exceptions (paths, keyring internals, server text).
LEAK = "SECRET-LEAK-/Users/nobody/private/keyring.backends.macOS"

MUTATING_POSTS = (
    "/viewed-state",
    "/starred-state",
    "/reset-caches",
    "/clear-credentials",
    "/load-credentials",
    "/provider-config",
    "/imap/discover",
)


@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


@pytest.fixture
def raw_client(server_mod):
    """A raw WSGI client with NO default headers — the attacker's-eye view."""
    return Client(server_mod.app)


def _assert_no_leak(resp, *, forbidden=()):
    body = resp.get_data(as_text=True)
    assert LEAK not in body
    for marker in (
        "Traceback",
        "/Users/",
        "/private/",
        "keyring",
        "Errno",
        "Exception",
        "Error(",
    ):
        assert marker not in body, f"error body leaks {marker!r}: {body}"
    for marker in forbidden:
        assert marker not in body, f"error body leaks {marker!r}: {body}"


# ---------------------------------------------------------------------------
# Host validation (SEC-4 rebinding defense)
# ---------------------------------------------------------------------------
def test_foreign_host_rejected_on_every_surface(raw_client):
    for path in ("/health", "/releases", "/dashboard", "/populate-range-stream"):
        resp = raw_client.get(path, base_url="http://evil.example")
        assert resp.status_code == 403, f"{path} accepted a foreign Host header"
        _assert_no_leak(resp)


def test_foreign_host_rejected_even_with_csrf_header(raw_client):
    resp = raw_client.post(
        "/reset-caches",
        base_url="http://evil.example",
        json={"clear_cache": True},
        headers={"X-BCFeed-Request": "1"},
    )
    assert resp.status_code == 403


def test_localhost_hosts_allowed(server_mod):
    client = server_mod.app.test_client()
    for base in ("http://localhost:5050", "http://127.0.0.1:8123", "http://localhost"):
        resp = client.get("/health", base_url=base)
        assert resp.status_code == 200, f"Host from {base} was wrongly rejected"
        assert resp.get_json() == {"ok": True}


# ---------------------------------------------------------------------------
# Anti-CSRF header on mutating routes (SEC-11)
# ---------------------------------------------------------------------------
def test_mutating_posts_without_header_are_403(raw_client):
    for path in MUTATING_POSTS:
        resp = raw_client.post(path, json={})
        assert resp.status_code == 403, f"POST {path} accepted a header-less request"
        _assert_no_leak(resp)


def test_headerless_imap_discover_opens_no_outbound_connection(server_mod, raw_client, monkeypatch):
    constructed = []

    class SpyImapClient:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))

        def __getattr__(self, name):  # pragma: no cover - should never run
            raise AssertionError("IMAP client must never be used for a rejected request")

    monkeypatch.setattr(server_mod, "ImapClient", SpyImapClient)
    resp = raw_client.post(
        "/imap/discover",
        json={
            "imap_config": {
                "host": "attacker.example",
                "port": 143,
                "username": "u",
                "password": "p",
            }
        },
    )
    assert resp.status_code == 403
    assert constructed == [], "rejected /imap/discover still constructed an IMAP client"


def test_headerless_provider_config_post_does_not_change_config(server_mod, raw_client):
    resp = raw_client.post("/provider-config", json={"provider": "imap"})
    assert resp.status_code == 403
    # The stored provider is untouched (still the default).
    get_resp = server_mod.app.test_client().get("/provider-config")
    assert get_resp.status_code == 200
    assert get_resp.get_json()["provider"] == "gmail"


def test_wrong_header_value_is_rejected(raw_client):
    resp = raw_client.post("/reset-caches", json={}, headers={"X-BCFeed-Request": "0"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# With the header + a valid Host, everything works normally
# ---------------------------------------------------------------------------
def test_reset_caches_with_header_works(server_mod, seed):
    seed.releases({"2025-06-16": []})
    resp = server_mod.app.test_client().post("/reset-caches", json={"clear_cache": True})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_provider_config_roundtrip_with_header(server_mod):
    client = server_mod.app.test_client()
    resp = client.post("/provider-config", json={"provider": "imap"})
    assert resp.status_code == 200
    assert client.get("/provider-config").get_json()["provider"] == "imap"


def test_imap_discover_with_header_reaches_handler(server_mod, monkeypatch):
    class DummyClient:
        def close(self):
            pass

    monkeypatch.setattr(server_mod, "_open_imap_client", lambda imap, **kw: DummyClient())
    monkeypatch.setattr(server_mod, "_discover_imap_folders", lambda client: (["INBOX"], "INBOX"))
    resp = server_mod.app.test_client().post(
        "/imap/discover",
        json={"imap_config": {"host": "imap.example.com", "username": "u", "password": "p"}},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"folders": ["INBOX"], "recommended_folder": "INBOX"}


def test_viewed_state_toggle_still_works_same_origin(server_mod):
    client = server_mod.app.test_client()
    url = "https://artist.bandcamp.com/album/example"
    resp = client.post("/viewed-state", json={"url": url, "read": True})
    assert resp.status_code == 200
    assert url in client.get("/viewed-state").get_json()["viewed"]


# ---------------------------------------------------------------------------
# SSE stays header-free (EventSource cannot set headers)
# ---------------------------------------------------------------------------
def test_populate_stream_reachable_without_custom_header(server_mod, raw_client, monkeypatch):
    # No provider credentials: the stream must answer with an in-band SSE
    # error — but it must NOT be blocked by the header guard (403).
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "gmail")
    monkeypatch.setattr(server_mod, "gmail_credentials_configured", lambda: False)
    resp = raw_client.get("/populate-range-stream?start=2025-06-01&end=2025-06-02")
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    assert "event: error" in resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# CORS is gone (SEC-4/ARC-5b)
# ---------------------------------------------------------------------------
def test_no_access_control_allow_origin_on_any_response(server_mod, raw_client, monkeypatch):
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "gmail")
    monkeypatch.setattr(server_mod, "gmail_credentials_configured", lambda: False)
    client = server_mod.app.test_client()

    responses = [
        client.get("/health"),
        client.get("/releases"),
        client.get("/viewed-state"),
        client.get("/starred-state"),
        client.get("/scrape-status?start=2025-06-01&end=2025-06-02"),
        client.get("/dashboard"),
        client.get("/dashboard.js"),
        client.get("/provider-config"),
        client.get("/populate-range-stream?start=2025-06-01&end=2025-06-02"),
        client.post("/reset-caches", json={}),
        client.post("/viewed-state", json={}),  # 400 path
        raw_client.post("/reset-caches", json={}),  # 403 path
        raw_client.get("/health", base_url="http://evil.example"),  # Host-403 path
    ]
    for resp in responses:
        assert "Access-Control-Allow-Origin" not in resp.headers, (
            f"CORS header present on {resp.request.path if resp.request else resp}"
        )


# ---------------------------------------------------------------------------
# Generic error bodies (SEC-9): no paths, exception classes, keyring internals
# ---------------------------------------------------------------------------
def test_imap_discover_auth_failure_body_is_generic(server_mod, monkeypatch):
    def explode(imap, **kwargs):
        raise AuthenticationError(f"IMAP error: {LEAK}")

    monkeypatch.setattr(server_mod, "_open_imap_client", explode)
    resp = server_mod.app.test_client().post(
        "/imap/discover",
        json={"imap_config": {"host": "imap.example.com", "username": "u", "password": "p"}},
    )
    assert resp.status_code == 400
    _assert_no_leak(resp, forbidden=("AuthenticationError",))


def test_imap_discover_connection_failure_body_is_generic(server_mod, monkeypatch):
    def explode(imap, **kwargs):
        raise ProviderError(f"IMAP connection failed: {LEAK}")

    monkeypatch.setattr(server_mod, "_open_imap_client", explode)
    resp = server_mod.app.test_client().post(
        "/imap/discover",
        json={"imap_config": {"host": "imap.example.com", "username": "u", "password": "p"}},
    )
    assert resp.status_code == 400
    _assert_no_leak(resp, forbidden=("ProviderError",))


def test_provider_config_credential_store_error_body_is_generic(server_mod, monkeypatch):
    def explode(config):
        raise CredentialStoreError(f"System keychain error: {LEAK}")

    monkeypatch.setattr(server_mod, "save_provider_config", explode)
    resp = server_mod.app.test_client().post("/provider-config", json={"provider": "gmail"})
    assert resp.status_code == 500
    _assert_no_leak(resp, forbidden=("CredentialStoreError", "keychain"))


def test_clear_credentials_failure_body_is_generic(server_mod, monkeypatch):
    def explode():
        raise RuntimeError(f"unlink failed: {LEAK}")

    monkeypatch.setattr(server_mod, "clear_gmail_credentials", explode)
    resp = server_mod.app.test_client().post("/clear-credentials")
    assert resp.status_code == 500
    _assert_no_leak(resp, forbidden=("RuntimeError", "unlink"))


def test_load_credentials_bad_json_body_is_generic(server_mod):
    resp = server_mod.app.test_client().post(
        "/load-credentials",
        data={"file": (io.BytesIO(b"this is not json"), "credentials.json")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    _assert_no_leak(resp, forbidden=("JSONDecodeError", "Expecting value"))


def test_releases_failure_body_is_generic(server_mod, monkeypatch):
    def explode():
        raise RuntimeError(LEAK)

    monkeypatch.setattr(server_mod, "get_full_release_cache", explode)
    resp = server_mod.app.test_client().get("/releases")
    assert resp.status_code == 500
    _assert_no_leak(resp, forbidden=("RuntimeError",))


def test_missing_static_file_is_404_with_generic_body(server_mod, monkeypatch, tmp_path):
    missing = tmp_path / "nope" / "dashboard.html"
    monkeypatch.setattr(server_mod, "DASHBOARD_PATH", missing)
    resp = server_mod.app.test_client().get("/dashboard")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert str(missing) not in body
    assert str(tmp_path) not in body
    _assert_no_leak(resp)
