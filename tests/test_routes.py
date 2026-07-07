"""WP-03b · CQ-46 Flask test-client smokes of CURRENT route contracts.

Light contract smokes over the HTTP surface — the shapes the dashboard depends
on, exercised through the same-origin test client (so the anti-CSRF header the
dashboard's fetch wrapper sends is present by default). The heavy lockdown
matrix lives in test_lockdown.py; here we only smoke the two WP-08 guards so a
regression shows up in the route suite too.

Covered:
* ``/config.json`` payload shape (has_token / has_credentials / default_theme…);
* ``/releases`` returns the cache with embed enrichment merged in;
* ``/viewed-state`` and ``/starred-state`` GET/POST round-trips;
* ``/scrape-status`` shape;
* the doc routes (200 + internal-link rewrite);
* WP-08 guards at a smoke level: bad Host → 403, header-less POST → 403.
"""

from __future__ import annotations

import importlib

import pytest
from werkzeug.test import Client


@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


@pytest.fixture
def client(server_mod):
    """Same-origin client — injects X-BCFeed-Request like the dashboard does."""
    return server_mod.app.test_client()


# ---------------------------------------------------------------------------
# /config.json
# ---------------------------------------------------------------------------
def test_config_json_shape(client):
    resp = client.get("/config.json")
    assert resp.status_code == 200
    data = resp.get_json()
    for key in (
        "title",
        "embed_proxy_url",
        "has_token",
        "has_credentials",
        "default_theme",
        "clear_status_on_load",
        "show_dev_settings",
    ):
        assert key in data, f"/config.json missing {key}"
    assert data["title"] == "bcfeed"
    assert data["embed_proxy_url"].endswith("/embed-meta")
    assert data["default_theme"] == "light"
    assert isinstance(data["has_token"], bool)
    assert isinstance(data["has_credentials"], bool)


# ---------------------------------------------------------------------------
# /releases — cache + embed merge
# ---------------------------------------------------------------------------
def test_releases_merges_embed_enrichment(client, seed, make_release, frozen_today):
    # WP-14 · LOG-20/PERF-5: the overlay adds only light fields — embed_url is
    # DERIVED from release_id + is_track, description bodies never ride the
    # payload (has_description flags them), and every row carries source.
    url = "https://a.bandcamp.com/album/x"
    seed.releases({"2025-06-16": [make_release(release_url=url, date="2025-06-16")]})
    seed.embed_cache(
        {
            url: {
                "status": "ok",
                "release_id": 555,
                "is_track": False,
                "description": "A fine record.",
                "fetched_at": 42,
            }
        }
    )
    resp = client.get("/releases")
    assert resp.status_code == 200
    releases = resp.get_json()["releases"]
    assert len(releases) == 1
    rel = releases[0]
    assert rel["url"] == url
    assert "album=555" in rel["embed_url"]
    assert rel["release_id"] == 555
    assert "description" not in rel
    assert rel["has_description"] is True
    assert "source" in rel


def test_releases_empty_when_no_cache(client):
    resp = client.get("/releases")
    assert resp.status_code == 200
    assert resp.get_json() == {"releases": []}


# ---------------------------------------------------------------------------
# /viewed-state and /starred-state round-trips
# ---------------------------------------------------------------------------
def test_viewed_state_get_post_roundtrip(client):
    url = "https://a.bandcamp.com/album/x"
    assert client.get("/viewed-state").get_json() == {"viewed": []}

    resp = client.post("/viewed-state", json={"url": url, "read": True})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert url in client.get("/viewed-state").get_json()["viewed"]

    # Toggle back off.
    client.post("/viewed-state", json={"url": url, "read": False})
    assert url not in client.get("/viewed-state").get_json()["viewed"]


def test_viewed_state_rejects_bad_payload(client):
    resp = client.post("/viewed-state", json={"url": "u"})  # missing 'read' bool
    assert resp.status_code == 400


def test_starred_state_get_post_roundtrip(client):
    url = "https://a.bandcamp.com/album/y"
    assert client.get("/starred-state").get_json() == {"starred": []}

    resp = client.post("/starred-state", json={"url": url, "starred": True})
    assert resp.status_code == 200
    assert url in client.get("/starred-state").get_json()["starred"]

    client.post("/starred-state", json={"url": url, "starred": False})
    assert url not in client.get("/starred-state").get_json()["starred"]


def test_starred_state_rejects_bad_payload(client):
    resp = client.post("/starred-state", json={"url": "u", "starred": "yes"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /scrape-status
# ---------------------------------------------------------------------------
def test_scrape_status_shape(client, seed, frozen_today):
    seed.scrape_status(["2025-06-16"])
    resp = client.get("/scrape-status", query_string={"start": "2025-06-15", "end": "2025-06-17"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data) == {"scraped", "not_scraped"}
    assert "2025-06-16" in data["scraped"]
    assert "2025-06-15" in data["not_scraped"]
    assert "2025-06-17" in data["not_scraped"]


def test_scrape_status_invalid_range_is_400(client):
    resp = client.get("/scrape-status", query_string={"start": "2025-06-17", "end": "2025-06-15"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Doc routes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("route", ["/readme", "/setup", "/setup-gmail", "/setup-imap"])
def test_doc_routes_render_200(client, route):
    resp = client.get(route)
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "<a " in body


def test_readme_route_rewrites_internal_links(client):
    body = client.get("/readme").get_data(as_text=True)
    assert 'href="setup"' in body
    assert 'href="setup-gmail"' in body
    assert 'href="SETUP.md"' not in body
    assert 'rel="noopener"' in body


# ---------------------------------------------------------------------------
# WP-08 guards — smoke level (full matrix in test_lockdown.py)
# ---------------------------------------------------------------------------
def test_foreign_host_rejected_smoke(server_mod):
    raw = Client(server_mod.app)
    resp = raw.get("/config.json", base_url="http://evil.example")
    assert resp.status_code == 403


def test_header_less_post_rejected_smoke(server_mod):
    raw = Client(server_mod.app)  # no anti-CSRF header
    resp = raw.post("/viewed-state", json={"url": "u", "read": True})
    assert resp.status_code == 403
