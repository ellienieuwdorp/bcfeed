"""WP-13 · OAuth: read-only scope, async connect job, legacy-pickle retirement.

Covers, with a fully STUBBED OAuth flow (no browser, no network, no real
keychain — see conftest's autouse ``isolated_keyring``):

* ARC-5d/SEC-3 — exactly one scope string in the code and it is
  ``gmail.readonly``; a stored legacy full-scope authorization is invalidated
  on load and ``has_token`` becomes false so the reconnect flow triggers.
* SEC-10/CQ-70 — the pickle migration path is gone (source greps); a stale
  ``token.pickle`` on disk is deleted, never read.
* SEC-6/CQ-72/UXP-4-backend — ``/load-credentials`` validates the
  client-secret shape, returns immediately (< 1 s), and never runs
  ``run_local_server`` on the request thread; ``/connect-status`` reports
  waiting|done|failed; an abandoned consent times out to ``failed`` with no
  stuck thread.
* SEC-9/CQ-70 — raw ``CredentialStoreError`` text never reaches a client
  body.
* CQ-08-residual — ``/clear-credentials`` clears BOTH keychain entries and a
  full auth cycle leaves no secret bytes on disk.
"""

from __future__ import annotations

import importlib
import io
import json
import threading
import time
from pathlib import Path

import pytest

import credential_store

REPO_ROOT = Path(__file__).resolve().parent.parent
READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
FULL_SCOPE = "https://mail.google.com/"

CLIENT_SECRET = {
    "installed": {
        "client_id": "abc.apps.googleusercontent.com",
        "project_id": "bcfeed",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "hush-hush-client-secret",
        "redirect_uris": ["http://localhost"],
    }
}

# A sentinel that must never appear in any client-facing body.
LEAK = "SECRET-KEYCHAIN-GUTS-/Users/nobody/keyring.backends"


def token_payload(scopes):
    return {
        "token": "ya29.fake-access-token",
        "refresh_token": "1//fake-refresh-token",
        "client_id": "abc.apps.googleusercontent.com",
        "client_secret": "hush-hush-client-secret",
        "scopes": scopes,
        "expiry": "2099-01-01T00:00:00Z",
    }


@pytest.fixture
def gmail_client_mod(isolated_data_dir):
    """gmail_client reloaded after the autouse paths reload (DATA_DIR bind)."""
    import gmail_client

    importlib.reload(gmail_client)
    return gmail_client


@pytest.fixture
def server_mod(gmail_client_mod):
    """server reloaded against the freshly-reloaded gmail_client."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


@pytest.fixture
def client(server_mod):
    """Same-origin test client (injects the anti-CSRF header like the UI)."""
    return server_mod.app.test_client()


def _upload(client, payload=None, raw: bytes | None = None):
    body = raw if raw is not None else json.dumps(payload or CLIENT_SECRET).encode("utf-8")
    return client.post(
        "/load-credentials",
        data={"file": (io.BytesIO(body), "client_secret.json")},
        content_type="multipart/form-data",
    )


def _wait_for_status(client, expected, timeout=5.0):
    status = None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get("/connect-status").get_json()["status"]
        if status == expected:
            return status
        time.sleep(0.02)
    raise AssertionError(f"/connect-status never became {expected!r} (last: {status!r})")


class FakeCreds:
    valid = True

    def to_json(self):
        return json.dumps(token_payload([READONLY_SCOPE]))


def _install_fake_flow(monkeypatch, gmail_client_mod, record, release=None):
    """Stub InstalledAppFlow + build so gmail_authenticate never blocks/dials."""

    class FakeFlow:
        @classmethod
        def from_client_config(cls, config, scopes):
            record["scopes"] = scopes
            return cls()

        def run_local_server(self, port=0, timeout_seconds=None, **kwargs):
            record["thread"] = threading.current_thread()
            record["timeout_seconds"] = timeout_seconds
            if release is not None and not release.wait(5):
                raise TimeoutError("consent abandoned")
            return FakeCreds()

    monkeypatch.setattr(gmail_client_mod, "InstalledAppFlow", FakeFlow)
    monkeypatch.setattr(gmail_client_mod, "build", lambda *a, **k: "gmail-service")


# ---------------------------------------------------------------------------
# ARC-5d/SEC-3 — one scope string, and it is read-only
# ---------------------------------------------------------------------------
def _code_files():
    files = sorted(REPO_ROOT.glob("*.py"))
    files += [REPO_ROOT / "dashboard.js", REPO_ROOT / "dashboard.html"]
    return [f for f in files if f.exists()]


def test_no_full_mailbox_scope_anywhere_in_code_or_top_level_docs():
    for f in _code_files() + sorted(REPO_ROOT.glob("*.md")):
        assert "mail.google.com" not in f.read_text(encoding="utf-8"), (
            f"{f.name} still references the full-access Gmail scope"
        )


def test_exactly_one_scope_string_in_code_and_it_is_readonly():
    hits = []
    for f in _code_files():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "googleapis.com/auth/gmail" in line:
                hits.append((f.name, line.strip()))
    assert len(hits) == 1, f"expected exactly one scope string in code, got: {hits}"
    assert hits[0][0] == "gmail_client.py"
    assert READONLY_SCOPE in hits[0][1]

    import gmail_client

    assert gmail_client.GMAIL_SCOPE == READONLY_SCOPE


def test_authenticate_requests_only_the_readonly_scope(gmail_client_mod, monkeypatch):
    credential_store.save_gmail_client_config_json(json.dumps(CLIENT_SECRET))
    record = {}
    _install_fake_flow(monkeypatch, gmail_client_mod, record)

    service = gmail_client_mod.gmail_authenticate(oauth_timeout_seconds=7)

    assert service == "gmail-service"
    assert record["scopes"] == [READONLY_SCOPE]
    assert record["timeout_seconds"] == 7
    stored = json.loads(credential_store.get_gmail_token_json())
    assert stored["scopes"] == [READONLY_SCOPE]


# ---------------------------------------------------------------------------
# ARC-5d — legacy full-scope authorization invalidated on load
# ---------------------------------------------------------------------------
def test_legacy_full_scope_token_is_cleared_on_load(gmail_client_mod):
    credential_store.save_gmail_token_json(json.dumps(token_payload([FULL_SCOPE])))

    assert gmail_client_mod.gmail_token_available() is False
    assert credential_store.get_gmail_token_json() is None, (
        "legacy full-scope authorization must be cleared from the keychain"
    )


def test_token_without_readonly_scope_is_cleared_on_load(gmail_client_mod):
    credential_store.save_gmail_token_json(
        json.dumps(token_payload(["https://www.googleapis.com/auth/gmail.labels"]))
    )
    assert gmail_client_mod.gmail_token_available() is False
    assert credential_store.get_gmail_token_json() is None


def test_readonly_token_stays_valid(gmail_client_mod):
    credential_store.save_gmail_token_json(json.dumps(token_payload([READONLY_SCOPE])))
    assert gmail_client_mod.gmail_token_available() is True
    assert credential_store.get_gmail_token_json() is not None


def test_config_json_reports_has_token_false_for_legacy_token(client):
    credential_store.save_gmail_token_json(json.dumps(token_payload([FULL_SCOPE])))

    data = client.get("/config.json").get_json()

    assert data["has_token"] is False, "legacy token must surface as has_token:false"
    assert credential_store.get_gmail_token_json() is None, (
        "the reconnect path requires the stale authorization to be gone"
    )


# ---------------------------------------------------------------------------
# SEC-10/CQ-70 — pickle path retired
# ---------------------------------------------------------------------------
def test_no_pickle_import_or_token_path_references():
    for f in sorted(REPO_ROOT.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        assert "import pickle" not in text, f"{f.name} still imports pickle"
        assert "pickle.load" not in text, f"{f.name} still deserializes pickle"
        assert "TOKEN_PATH" not in text, f"{f.name} still references TOKEN_PATH"


def test_token_pickle_survives_only_as_the_noted_cleanup_literal():
    for f in sorted(REPO_ROOT.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        if f.name == "gmail_client.py":
            # Exactly one occurrence: the unlink-by-literal-filename cleanup.
            assert text.count("token.pickle") == 1
        else:
            assert "token.pickle" not in text, f"{f.name} references token.pickle"


def test_stale_token_pickle_is_deleted_never_read(gmail_client_mod, isolated_data_dir, monkeypatch):
    stale = isolated_data_dir / "token.pickle"
    stale.write_bytes(b"\x80\x04MALICIOUS-PICKLE-BYTES")
    credential_store.save_gmail_client_config_json(json.dumps(CLIENT_SECRET))
    _install_fake_flow(monkeypatch, gmail_client_mod, {})

    gmail_client_mod.gmail_authenticate()

    assert not stale.exists(), "stale legacy pickle must be unlinked on auth"


def test_clear_credentials_removes_stale_pickle_and_both_keychain_entries(
    gmail_client_mod, isolated_data_dir
):
    stale = isolated_data_dir / "token.pickle"
    stale.write_bytes(b"junk")
    credential_store.save_gmail_client_config_json(json.dumps(CLIENT_SECRET))
    credential_store.save_gmail_token_json(json.dumps(token_payload([READONLY_SCOPE])))

    gmail_client_mod.clear_gmail_credentials()

    assert not stale.exists(), "a stale pickle must never be able to re-authorize"
    assert not credential_store.has_gmail_token()
    assert not credential_store.has_gmail_client_config()


# ---------------------------------------------------------------------------
# SEC-6/CQ-72 — /load-credentials is immediate; flow runs off-thread
# ---------------------------------------------------------------------------
def test_load_credentials_returns_immediately_and_flow_runs_off_request_thread(
    server_mod, client, gmail_client_mod, monkeypatch
):
    release = threading.Event()
    record = {}
    _install_fake_flow(monkeypatch, gmail_client_mod, record, release=release)

    start = time.monotonic()
    resp = _upload(client)
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert elapsed < 1.0, f"/load-credentials blocked for {elapsed:.2f}s"
    body = resp.get_json()
    assert body["ok"] is True
    assert body["status"] == "waiting"
    assert client.get("/connect-status").get_json()["status"] == "waiting"

    release.set()
    _wait_for_status(client, "done")

    # run_local_server executed on the background worker, never the request
    # thread (the Flask test client runs handlers on this very thread).
    assert record["thread"] is not None
    assert record["thread"] is not threading.current_thread()
    assert record["timeout_seconds"] == server_mod.GMAIL_CONNECT_TIMEOUT_SECONDS
    assert record["scopes"] == [READONLY_SCOPE]
    assert client.get("/config.json").get_json()["has_token"] is True


def test_abandoned_consent_times_out_to_failed_with_no_stuck_thread(
    server_mod, client, monkeypatch
):
    def fake_authenticate(oauth_timeout_seconds=None):
        # Stands in for run_local_server's own timeout firing (fast in tests).
        raise TimeoutError(f"no consent arrived: {LEAK}")

    monkeypatch.setattr(server_mod, "gmail_authenticate", fake_authenticate)

    resp = _upload(client)
    assert resp.status_code == 200

    _wait_for_status(client, "failed")
    with server_mod._gmail_connect_lock:
        worker = server_mod._gmail_connect_job["thread"]
    worker.join(2)
    assert not worker.is_alive(), "the connect worker must exit after the timeout"

    body = json.dumps(client.get("/connect-status").get_json())
    assert LEAK not in body
    assert "TimeoutError" not in body


def test_second_upload_while_connect_is_waiting_is_rejected(
    server_mod, client, gmail_client_mod, monkeypatch
):
    release = threading.Event()
    _install_fake_flow(monkeypatch, gmail_client_mod, {}, release=release)

    assert _upload(client).status_code == 200
    second = _upload(client)
    assert second.status_code == 409

    release.set()
    _wait_for_status(client, "done")


def test_connect_status_starts_idle(client):
    data = client.get("/connect-status").get_json()
    assert data["status"] == "idle"


# ---------------------------------------------------------------------------
# SEC-6 — client-secret shape validation
# ---------------------------------------------------------------------------
def test_load_credentials_rejects_arbitrary_json(server_mod, client, monkeypatch):
    flow_started = []
    monkeypatch.setattr(server_mod, "gmail_authenticate", lambda **kw: flow_started.append(True))

    resp = _upload(client, payload={"foo": "bar"})

    assert resp.status_code == 400
    assert not credential_store.has_gmail_client_config(), (
        "arbitrary JSON must never be stored as the OAuth client config"
    )
    assert flow_started == [], "no consent flow may start for a rejected upload"
    assert client.get("/connect-status").get_json()["status"] == "idle"


def test_load_credentials_rejects_client_secret_missing_fields(client):
    resp = _upload(client, payload={"installed": {"client_id": "only-an-id"}})
    assert resp.status_code == 400
    assert not credential_store.has_gmail_client_config()


def test_load_credentials_accepts_web_client_shape(
    server_mod, client, gmail_client_mod, monkeypatch
):
    _install_fake_flow(monkeypatch, gmail_client_mod, {})
    payload = {"web": {"client_id": "abc", "client_secret": "xyz"}}

    resp = _upload(client, payload=payload)

    assert resp.status_code == 200
    assert credential_store.has_gmail_client_config()
    _wait_for_status(client, "done")


def test_load_credentials_rejects_non_utf8_and_empty_uploads(client):
    assert _upload(client, raw=b"\xff\xfe\x00garbage").status_code == 400
    assert _upload(client, raw=b"").status_code == 400


# ---------------------------------------------------------------------------
# SEC-9/CQ-70 — CredentialStoreError text never reaches a client
# ---------------------------------------------------------------------------
def test_keychain_error_on_upload_is_generic(server_mod, client, monkeypatch):
    def explode(raw_json):
        raise credential_store.CredentialStoreError(f"System keychain error: {LEAK}")

    monkeypatch.setattr(server_mod, "save_gmail_client_config_json", explode)

    resp = _upload(client)

    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert LEAK not in body
    assert "keychain error" not in body


def test_keychain_error_on_clear_is_generic(server_mod, client, monkeypatch):
    def explode():
        raise credential_store.CredentialStoreError(f"System keychain error: {LEAK}")

    monkeypatch.setattr(server_mod, "clear_gmail_credentials", explode)

    resp = client.post("/clear-credentials")

    assert resp.status_code == 500
    body = resp.get_data(as_text=True)
    assert LEAK not in body


def test_keychain_error_in_connect_job_yields_generic_status(server_mod, client, monkeypatch):
    def fake_authenticate(oauth_timeout_seconds=None):
        raise credential_store.CredentialStoreError(f"System keychain error: {LEAK}")

    monkeypatch.setattr(server_mod, "gmail_authenticate", fake_authenticate)

    _upload(client)
    _wait_for_status(client, "failed")

    body = json.dumps(client.get("/connect-status").get_json())
    assert LEAK not in body
    assert "CredentialStoreError" not in body


# ---------------------------------------------------------------------------
# CQ-08-residual — full clear semantics; no secret bytes on disk
# ---------------------------------------------------------------------------
def test_clear_credentials_route_clears_token_and_client_config(server_mod, client):
    credential_store.save_gmail_client_config_json(json.dumps(CLIENT_SECRET))
    credential_store.save_gmail_token_json(json.dumps(token_payload([READONLY_SCOPE])))

    resp = client.post("/clear-credentials")

    assert resp.status_code == 200
    assert not credential_store.has_gmail_token()
    assert not credential_store.has_gmail_client_config()
    data = client.get("/config.json").get_json()
    assert data["has_token"] is False
    assert data["has_credentials"] is False


def test_full_auth_cycle_leaves_no_secret_bytes_on_disk(
    server_mod, client, gmail_client_mod, monkeypatch, isolated_data_dir
):
    _install_fake_flow(monkeypatch, gmail_client_mod, {})

    resp = _upload(client)
    assert resp.status_code == 200
    _wait_for_status(client, "done")

    leftovers = [p for p in isolated_data_dir.rglob("*") if p.is_file()]
    for p in leftovers:
        content = p.read_text(encoding="utf-8", errors="ignore")
        for secret in ("hush-hush-client-secret", "ya29.", "1//fake-refresh-token"):
            assert secret not in content, f"secret bytes found on disk in {p.name}"
    assert not (isolated_data_dir / "token.pickle").exists()
    assert not (isolated_data_dir / "credentials.json").exists()
