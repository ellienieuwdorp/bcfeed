"""WP-06 · Backend quick wins (mechanical S-fixes).

Covers the acceptance for the WP-06 fixes:

- CQ-03/PY-13: ``/reset-caches`` honors ``clear_viewed`` and ``clear_starred``
  independently — a flag clears ONLY its own store (viewed-only leaves starred,
  starred-only leaves viewed, cache-only leaves both).
- CQ-04/PY-14: ``max_results`` is validated and clamped — a non-integer or
  non-positive value is a JSON 400 (never an unhandled ValueError → 500), and an
  absurdly large value is clamped down to ``GMAIL_MAX_RESULTS_HARD``.
- CQ-32/LOG-19 (delete half): the always-null parse-time ``img_url`` capture is
  gone from the parser, yet the live decode/parse path still yields identical
  release output. Since WP-14 the release dict carries no ``img_url`` key at
  all (LOG-19 finish).
- CQ-01/PY-14: the dead blocks are gone (str-email fallback, always-true all-None
  guard, ``--batch`` untruthful message, ``type(exc) ==`` comparisons) — and
  ``mark_dates_not_scraped`` is deliberately RETAINED for WP-15/LOG-3.

Import discipline (see conftest): reference modules as ``import x`` so the
autouse data-dir reload is honored; server.py binds store paths at import, so
the ``server_mod`` fixture reloads it per test.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


# ---------------------------------------------------------------------------
# CQ-03 — /reset-caches flag independence
# ---------------------------------------------------------------------------
def _seed_all(seed):
    seed.viewed(["https://a.bandcamp.com/album/x"])
    seed.starred(["https://b.bandcamp.com/album/y"])
    seed.releases({"2025-06-16": []})
    seed.scrape_status(["2025-06-16"])


def test_reset_viewed_only_leaves_starred(server_mod, seed):
    import paths

    _seed_all(seed)
    resp = server_mod.app.test_client().post("/reset-caches", json={"clear_viewed": True})
    assert resp.status_code == 200
    assert not paths.VIEWED_PATH.exists(), "viewed store should be cleared"
    assert paths.STARRED_PATH.exists(), "starred store must survive a viewed-only clear"


def test_reset_starred_only_leaves_viewed(server_mod, seed):
    import paths

    _seed_all(seed)
    resp = server_mod.app.test_client().post("/reset-caches", json={"clear_starred": True})
    assert resp.status_code == 200
    assert not paths.STARRED_PATH.exists(), "starred store should be cleared"
    assert paths.VIEWED_PATH.exists(), "viewed store must survive a starred-only clear"


def test_reset_cache_only_leaves_viewed_and_starred(server_mod, seed):
    import paths

    _seed_all(seed)
    resp = server_mod.app.test_client().post("/reset-caches", json={"clear_cache": True})
    assert resp.status_code == 200
    assert not paths.RELEASE_CACHE_PATH.exists(), "release cache should be cleared"
    assert paths.VIEWED_PATH.exists(), "viewed store must survive a cache-only clear"
    assert paths.STARRED_PATH.exists(), "starred store must survive a cache-only clear"


def test_reset_all_flags_clears_everything(server_mod, seed):
    import paths

    _seed_all(seed)
    resp = server_mod.app.test_client().post(
        "/reset-caches",
        json={"clear_cache": True, "clear_viewed": True, "clear_starred": True},
    )
    assert resp.status_code == 200
    assert not paths.VIEWED_PATH.exists()
    assert not paths.STARRED_PATH.exists()
    assert not paths.RELEASE_CACHE_PATH.exists()


# ---------------------------------------------------------------------------
# CQ-04 — max_results validation + clamp
# ---------------------------------------------------------------------------
def test_max_results_non_integer_is_json_400(server_mod):
    resp = server_mod.app.test_client().get(
        "/populate-range-stream",
        query_string={"start": "2025-06-01", "end": "2025-06-02", "max_results": "abc"},
    )
    assert resp.status_code == 400
    assert resp.mimetype == "application/json"
    assert "max_results" in resp.get_json()["error"]


def test_max_results_non_positive_is_json_400(server_mod):
    resp = server_mod.app.test_client().get(
        "/populate-range-stream",
        query_string={"start": "2025-06-01", "end": "2025-06-02", "max_results": "0"},
    )
    assert resp.status_code == 400
    assert resp.mimetype == "application/json"


def _drive_populate_capturing_max(server_mod, monkeypatch, max_results_qs):
    """Drive /populate-range-stream past the credential gate with a stubbed
    pipeline, returning the max_results the pipeline was actually called with."""
    captured: list[int] = []

    def fake_populate(after, before, max_results, batch_size, log=print):
        captured.append(max_results)

    # Route around real credential/keychain checks so the worker actually runs.
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "imap")
    monkeypatch.setattr(server_mod, "_has_credentials_for_provider", lambda: True)
    monkeypatch.setattr(server_mod, "populate_release_cache", fake_populate)

    resp = server_mod.app.test_client().get(
        "/populate-range-stream",
        query_string={"start": "2025-06-01", "end": "2025-06-02", **max_results_qs},
    )
    # Fully consume the stream so the worker thread finishes and releases the lock.
    body = resp.get_data(as_text=True)
    return resp, body, captured


def test_max_results_absurdly_large_is_clamped_no_500(server_mod, monkeypatch):
    resp, body, captured = _drive_populate_capturing_max(
        server_mod, monkeypatch, {"max_results": "999999"}
    )
    assert resp.status_code == 200, "clamped value must not raise a 500 traceback"
    assert resp.mimetype == "text/event-stream"
    assert captured == [server_mod.GMAIL_MAX_RESULTS_HARD]
    assert "event: done" in body


def test_max_results_absent_defaults_to_hard_cap(server_mod, monkeypatch):
    resp, _body, captured = _drive_populate_capturing_max(server_mod, monkeypatch, {})
    assert resp.status_code == 200
    assert captured == [server_mod.GMAIL_MAX_RESULTS_HARD]


# ---------------------------------------------------------------------------
# CQ-32 — live parse path unchanged; img_url no longer parsed
# ---------------------------------------------------------------------------
def _legacy_email(emails, name, date):
    return {"html": emails.html_via_imap(name), "date": date, "subject": emails.subject(name)}


def test_album_fixture_parses_unchanged_through_construct_release_list(emails):
    import pipeline

    batch = {"1": _legacy_email(emails, "album_release", "2025-06-16")}
    lines: list[str] = []
    releases = pipeline.construct_release_list(batch, log=lines.append)

    assert len(releases) == 1
    rel = releases[0]
    assert rel["url"] == "https://midnighttapes.bandcamp.com/album/neon-fields"
    assert rel["title"] == "Neon Fields"
    assert rel["artist"] == "Aria Vale"
    assert rel["page_name"] == "Midnight Tapes"
    assert rel["is_track"] is False
    assert rel["date"] == "2025-06-16"
    # Release-dict shape (schema v2): the always-null img_url key is gone
    # (WP-14 · LOG-19) — artwork is enrichment state, never a parse-time field.
    assert "img_url" not in rel


def test_track_fixture_parses_unchanged_through_construct_release_list(emails):
    import pipeline

    batch = {"1": _legacy_email(emails, "track_release", "2025-06-17")}
    releases = pipeline.construct_release_list(batch, log=lambda *_: None)

    assert len(releases) == 1
    assert releases[0]["is_track"] is True
    assert "img_url" not in releases[0]


def test_parser_returns_none_placeholder_not_a_parsed_img(emails):
    from bandcamp_email_parser import parse_release_email

    html = emails.html_via_imap("album_release")
    subject = emails.subject("album_release")
    placeholder, url, *_ = parse_release_email(html, subject)
    assert placeholder is None
    assert url == "https://midnighttapes.bandcamp.com/album/neon-fields"


# ---------------------------------------------------------------------------
# CQ-01 — dead code removed; mark_dates_not_scraped retained for WP-15
# ---------------------------------------------------------------------------
def test_mark_dates_not_scraped_retained_with_pointer_comment():
    import session_store

    assert hasattr(session_store, "mark_dates_not_scraped")
    assert callable(session_store.mark_dates_not_scraped)
    src = inspect.getsource(session_store.mark_dates_not_scraped)
    assert "WP-15" in src, "retention pointer comment must survive"


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_no_batch_flag_string_anywhere():
    for name in ("gmail_client.py", "bcfeed.py", "server.py", "pipeline.py"):
        assert "--batch" not in _read(name), f"{name} still references a nonexistent --batch flag"


def test_no_type_exc_equality_comparison_in_gmail_client():
    assert "type(exc) ==" not in _read("gmail_client.py")


def test_no_str_email_fallback_or_all_none_guard_in_pipeline():
    src = _read("pipeline.py")
    assert "str(email)" not in src, "unreachable str-email fallback must be gone"
    assert "x is None\n" not in src and "all(\n            x is None" not in src
    assert "if not all(" not in src, "always-true all-None guard must be gone"


def test_no_parse_time_img_url_capture_in_parser():
    # The parser no longer names img_url at all (capture removed, CQ-32).
    assert "img_url" not in _read("bandcamp_email_parser.py")
