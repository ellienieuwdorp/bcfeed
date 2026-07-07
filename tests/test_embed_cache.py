"""WP-11 · Cache-first embed metadata with negative caching (CQ-20/PY-10/LOG-5/PERF-2).

All network access is mocked: ``bandcamp.requests`` is replaced by a counting
fake, so no test can ever reach bandcamp.com.

Covered:
* two ``get_embed_meta`` calls (and two ``/embed-meta`` requests) for one URL
  perform exactly ONE fetch — the cache is finally read (CQ-20/PERF-2);
* a 404 page is fetched once, recorded ``status: "error"`` with an ``http_404``
  code, and not refetched before the retry TTL; after the TTL one retry runs
  (LOG-5 negative caching);
* legacy flat cache entries lazily upgrade on read and still serve their
  metadata/``description`` without a fetch (LOG-5 migration; ``embed_url``
  is derived since WP-14);
* a page whose ``bc-page-properties`` content is neither JSON nor a Python
  literal yields ``None`` from ``extract_bc_meta`` — no exception — and the
  route answers with a generic JSON ``{error}`` 502, never an HTML 500
  (CQ-20/PY-10);
* ``description: ""`` records fetched-but-none, so such pages are never
  refetched.
"""

from __future__ import annotations

import importlib
import json

import pytest

OK_PAGE = """<!doctype html>
<html><head>
<meta name="bc-page-properties" content='{"item_type": "album", "item_id": 4242}'>
</head><body>
<div id="tralbum-about">A test album about text.</div>
</body></html>
"""

NON_LITERAL_META_PAGE = """<!doctype html>
<html><head>
<meta name="bc-page-properties" content="function() { not a literal }">
</head><body></body></html>
"""

NO_DESCRIPTION_PAGE = """<!doctype html>
<html><head>
<meta name="bc-page-properties" content='{"item_type": "track", "item_id": 777}'>
</head><body></body></html>
"""

URL = "https://artist.bandcamp.com/album/example"


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = RuntimeError(f"HTTP {self.status_code}")
            exc.response = self
            raise exc


class FakeRequests:
    """Stands in for the ``requests`` module inside bandcamp.py."""

    def __init__(self, response: FakeResponse | Exception):
        self.response = response
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def bandcamp_mod(isolated_data_dir):
    import bandcamp

    importlib.reload(bandcamp)
    return bandcamp


@pytest.fixture
def server_mod(bandcamp_mod):
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


def _fake_network(monkeypatch, bandcamp_mod, response) -> FakeRequests:
    fake = FakeRequests(response)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)
    return fake


# ---------------------------------------------------------------------------
# Cache-first: one URL → one fetch
# ---------------------------------------------------------------------------
def test_two_get_embed_meta_calls_one_fetch(bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))

    first = bandcamp_mod.get_embed_meta(URL)
    second = bandcamp_mod.get_embed_meta(URL)

    assert fake.calls == 1
    assert first["status"] == "ok"
    assert second == first
    assert first["release_id"] == 4242
    assert first["is_track"] is False
    # WP-14 · LOG-20: embed_url is derived at response time, never stored.
    assert "embed_url" not in first
    assert "test album about text" in first["description"]
    assert isinstance(first["fetched_at"], int)


def test_two_embed_meta_requests_one_fetch(server_mod, bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    client = server_mod.app.test_client()

    first = client.get(f"/embed-meta?url={URL}")
    second = client.get(f"/embed-meta?url={URL}")

    assert fake.calls == 1
    assert first.status_code == 200
    assert second.get_json() == first.get_json()
    data = first.get_json()
    assert data["release_id"] == 4242
    assert data["is_track"] is False
    assert "album=4242" in data["embed_url"]
    assert "test album about text" in data["description"]


# ---------------------------------------------------------------------------
# Negative caching: failures recorded, not refetched before TTL
# ---------------------------------------------------------------------------
def test_404_recorded_and_not_refetched_before_ttl(bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse("gone", status_code=404))

    record = bandcamp_mod.get_embed_meta(URL, now=1_000_000)
    assert record == {"status": "error", "code": "http_404", "fetched_at": 1_000_000}

    # Within the TTL: served from the negative cache, no second fetch.
    ttl = bandcamp_mod.EMBED_ERROR_RETRY_TTL_SECONDS
    again = bandcamp_mod.get_embed_meta(URL, now=1_000_000 + ttl - 1)
    assert fake.calls == 1
    assert again == record

    # After the TTL: exactly one retry is allowed.
    bandcamp_mod.get_embed_meta(URL, now=1_000_000 + ttl)
    assert fake.calls == 2


def test_404_route_returns_generic_502_from_cache(server_mod, bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse("gone", status_code=404))
    client = server_mod.app.test_client()

    first = client.get(f"/embed-meta?url={URL}")
    second = client.get(f"/embed-meta?url={URL}")

    assert fake.calls == 1  # the second answer came from the negative cache
    for resp in (first, second):
        assert resp.status_code == 502
        assert resp.is_json
        assert set(resp.get_json()) == {"error"}
        assert "404" not in resp.get_json()["error"]


def test_network_failure_recorded_as_network_error(bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, ConnectionError("boom"))

    record = bandcamp_mod.get_embed_meta(URL, now=5_000)
    assert record == {"status": "error", "code": "network", "fetched_at": 5_000}
    bandcamp_mod.get_embed_meta(URL, now=5_001)
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# Legacy flat entries: lazy upgrade on read, still served, never refetched
# ---------------------------------------------------------------------------
def test_legacy_entry_lazily_upgrades_and_serves_without_fetch(bandcamp_mod, seed, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    seed.embed_cache(
        {
            URL: {
                "release_id": 555,
                "is_track": False,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=555/",
                "description": "A fine record.",
            }
        }
    )

    record = bandcamp_mod.get_embed_meta(URL)
    assert fake.calls == 0
    assert record["status"] == "ok"
    assert record["release_id"] == 555
    assert record["description"] == "A fine record."
    assert record["fetched_at"] is None


def test_legacy_entry_served_by_route(server_mod, bandcamp_mod, seed, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    seed.embed_cache(
        {
            URL: {
                "release_id": 555,
                "is_track": True,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/track=555/",
                "description": "A fine record.",
            }
        }
    )

    resp = server_mod.app.test_client().get(f"/embed-meta?url={URL}")
    assert fake.calls == 0
    assert resp.status_code == 200
    data = resp.get_json()
    # The route derives embed_url from release_id + is_track (WP-14 · LOG-20).
    assert "track=555" in data["embed_url"]
    assert data["description"] == "A fine record."
    assert data["is_track"] is True


# ---------------------------------------------------------------------------
# Guarded literal_eval (CQ-20/PY-10): non-literal meta → None → JSON 502
# ---------------------------------------------------------------------------
def test_extract_bc_meta_returns_none_for_non_literal_content(bandcamp_mod):
    assert bandcamp_mod.extract_bc_meta(NON_LITERAL_META_PAGE) is None


def test_extract_bc_meta_returns_none_for_non_dict_literal(bandcamp_mod):
    page = '<meta name="bc-page-properties" content="[1, 2, 3]">'
    assert bandcamp_mod.extract_bc_meta(page) is None


def test_non_literal_meta_route_returns_json_502_not_html_500(
    server_mod, bandcamp_mod, monkeypatch
):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(NON_LITERAL_META_PAGE))
    client = server_mod.app.test_client()

    resp = client.get(f"/embed-meta?url={URL}")
    assert resp.status_code == 502
    assert resp.is_json
    assert set(resp.get_json()) == {"error"}

    # Recorded as a no_meta error and negatively cached: no refetch.
    client.get(f"/embed-meta?url={URL}")
    assert fake.calls == 1


def test_literal_meta_page_still_parses(bandcamp_mod, pages):
    meta = bandcamp_mod.extract_bc_meta(pages.html("release_page_literal_meta"))
    assert meta is not None
    assert meta["item_type"] == "track"
    assert meta["item_id"] == 100200301


# ---------------------------------------------------------------------------
# description "" = fetched-but-none: never refetched
# ---------------------------------------------------------------------------
def test_empty_description_cached_and_not_refetched(bandcamp_mod, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(NO_DESCRIPTION_PAGE))

    record = bandcamp_mod.get_embed_meta(URL)
    assert record["status"] == "ok"
    assert record["description"] == ""
    assert record["is_track"] is True

    bandcamp_mod.get_embed_meta(URL)
    assert fake.calls == 1


# ---------------------------------------------------------------------------
# On-disk record shape (LOG-20: no stored embed_url — derived state)
# ---------------------------------------------------------------------------
def test_written_record_shape(bandcamp_mod, monkeypatch, isolated_data_dir):
    import paths

    _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    bandcamp_mod.get_embed_meta(URL, now=42)

    cache = json.loads(paths.EMBED_CACHE_PATH.read_text(encoding="utf-8"))
    assert cache == {
        URL: {
            "status": "ok",
            "release_id": 4242,
            "is_track": False,
            "description": "A test album about text.",
            "fetched_at": 42,
        }
    }
