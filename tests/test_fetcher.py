"""WP-12 · Polite Bandcamp fetcher + SSRF allowlist (LOG-7/ARC-5f/SEC-2/PERF-3/LOG-19).

All network access is mocked (``bandcamp.requests`` replaced, DNS via the
``bandcamp._getaddrinfo`` seam) and all timing runs on a FAKE clock injected
through ``bandcamp._rate_limiter`` — no test ever sleeps for real seconds or
touches bandcamp.com.

Covered:
* http://, private-IP hosts, and uncached custom domains are all rejected 400
  with IDENTICAL generic bodies and zero fetches; nothing is cached;
* a custom-domain URL present in the release cache is allowed;
* a hostname resolving to a private/loopback IP is blocked even when it looks
  like a bandcamp.com subdomain (resolve-and-block);
* a synthetic 429 with ``Retry-After: 2`` waits (fake clock) then succeeds;
  a persistent 429 records a bounded, retryable failure;
* sustained calls stay ≤ 1 req/s over a fake 60 s window (scheduled delays);
* exactly ONE BeautifulSoup parse per fetched page (PERF-3);
* redirects: one re-validated hop allowed; a redirect to a disallowed host is
  never followed to a fetch; a second hop is refused;
* oversized bodies (declared or streamed) are aborted;
* ``art_url`` appears on the ok record when the page has ``og:image`` and is
  absent otherwise (LOG-19 capture half).
"""

from __future__ import annotations

import importlib
import json
import socket

import pytest

OK_PAGE = """<!doctype html>
<html><head>
<meta name="bc-page-properties" content='{"item_type": "album", "item_id": 4242}'>
</head><body>
<div id="tralbum-about">A test album about text.</div>
</body></html>
"""

URL = "https://artist.bandcamp.com/album/example"

PUBLIC_IP = "151.101.65.30"

DISALLOWED_URLS = [
    "http://bandcamp.com/album/x",  # not https
    "https://192.168.1.1/album/x",  # private-IP host, not cached
    "https://10.0.0.1/album/x",  # private-IP host, not cached
    "https://example.com/album/x",  # custom domain NOT in the release cache
]


# ---------------------------------------------------------------------------
# Fakes: clock, DNS, network
# ---------------------------------------------------------------------------
class FakeClock:
    """Monotonic fake clock; sleeping advances time instead of blocking."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = RuntimeError(f"HTTP {self.status_code}")
            exc.response = self
            raise exc


class StreamingResponse(FakeResponse):
    """A response whose body arrives in chunks via iter_content."""

    def __init__(self, chunks: list[bytes], **kwargs):
        super().__init__(**kwargs)
        self._chunks = chunks
        self.encoding = "utf-8"

    def iter_content(self, chunk_size=None):
        yield from self._chunks


class FakeRequests:
    """Stands in for the ``requests`` module inside bandcamp.py.

    Serves the queued responses in order, repeating the last one; records
    every requested URL and the kwargs of each call.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        self.kwargs.append(kwargs)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
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


@pytest.fixture
def fake_clock(bandcamp_mod, monkeypatch):
    """Install a fake-clock token bucket as THE shared rate limiter."""
    clock = FakeClock()
    monkeypatch.setattr(
        bandcamp_mod,
        "_rate_limiter",
        bandcamp_mod._TokenBucket(clock=clock.monotonic, sleep=clock.sleep),
    )
    return clock


@pytest.fixture
def public_dns(bandcamp_mod, monkeypatch):
    """Every hostname resolves to a public IP; returns the resolved hosts."""
    resolved: list[str] = []

    def fake_getaddrinfo(host, *args, **kwargs):
        resolved.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]

    monkeypatch.setattr(bandcamp_mod, "_getaddrinfo", fake_getaddrinfo)
    return resolved


def _fake_network(monkeypatch, bandcamp_mod, *responses) -> FakeRequests:
    fake = FakeRequests(responses)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)
    return fake


def _embed_cache_on_disk() -> dict:
    import paths

    if not paths.EMBED_CACHE_PATH.exists():
        return {}
    return json.loads(paths.EMBED_CACHE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# SSRF allowlist: rejects are uniform, nothing fetched, nothing cached
# ---------------------------------------------------------------------------
def test_disallowed_urls_rejected_identical_bodies_no_fetch(
    server_mod, bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    client = server_mod.app.test_client()

    # WP-14 · LOG-9: the route canonicalizes its url param before validation,
    # which upgrades an http:// bandcamp link to its canonical https form —
    # so the "not https" case is only a *direct-function* reject now (see
    # test_disallowed_urls_raise_without_fetch). Private-IP hosts and
    # non-cached custom domains stay rejected at the route: canonicalization
    # never changes their host.
    route_disallowed = [u for u in DISALLOWED_URLS if not u.startswith("http://")]
    responses = [client.get("/embed-meta", query_string={"url": u}) for u in route_disallowed]

    for resp in responses:
        assert resp.status_code == 400
        assert resp.is_json
        assert set(resp.get_json()) == {"error"}
    bodies = {resp.get_data() for resp in responses}
    assert len(bodies) == 1, "rejected URLs must get IDENTICAL generic bodies"
    assert fake.calls == [], "a rejected URL must never reach the network"
    assert _embed_cache_on_disk() == {}, "blocked URLs must never be cached"


def test_disallowed_urls_raise_without_fetch(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    for url in DISALLOWED_URLS:
        with pytest.raises(bandcamp_mod.FetchBlockedError):
            bandcamp_mod.fetch_release_page(url)
    assert fake.calls == []


def test_cached_custom_domain_url_allowed(
    server_mod, bandcamp_mod, seed, make_release, fake_clock, public_dns, monkeypatch
):
    url = "https://music.customlabel.example/album/x"
    seed.releases({"2025-06-16": [make_release(release_url=url)]})
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))

    resp = server_mod.app.test_client().get("/embed-meta", query_string={"url": url})

    assert resp.status_code == 200
    assert resp.get_json()["release_id"] == 4242
    assert fake.calls == [url]
    assert "music.customlabel.example" in public_dns  # resolve-and-block still ran


@pytest.mark.parametrize("blocked_ip", ["127.0.0.1", "10.1.2.3", "169.254.5.5", "192.168.1.10"])
def test_bandcamp_subdomain_resolving_private_ip_blocked(
    server_mod, bandcamp_mod, fake_clock, monkeypatch, blocked_ip
):
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (blocked_ip, 443))]

    monkeypatch.setattr(bandcamp_mod, "_getaddrinfo", fake_getaddrinfo)
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    url = "https://evil.bandcamp.com/album/x"

    with pytest.raises(bandcamp_mod.FetchBlockedError):
        bandcamp_mod.fetch_release_page(url)

    resp = server_mod.app.test_client().get("/embed-meta", query_string={"url": url})
    assert resp.status_code == 400
    assert fake.calls == []
    assert _embed_cache_on_disk() == {}


# ---------------------------------------------------------------------------
# Politeness: Retry-After, bounded retries, sustained ≤ 1 rps (fake clock)
# ---------------------------------------------------------------------------
def test_429_retry_after_waits_then_succeeds(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    fake = _fake_network(
        monkeypatch,
        bandcamp_mod,
        FakeResponse("slow down", status_code=429, headers={"Retry-After": "2"}),
        FakeResponse(OK_PAGE),
    )

    record = bandcamp_mod.get_embed_meta(URL, now=1_000)

    assert record["status"] == "ok"
    assert record["release_id"] == 4242
    assert len(fake.calls) == 2
    assert 2.0 in fake_clock.sleeps, "Retry-After: 2 must schedule a 2 s wait"
    assert fake_clock.now >= 2.0


def test_persistent_429_records_bounded_retryable_failure(
    bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    fake = _fake_network(
        monkeypatch,
        bandcamp_mod,
        FakeResponse("slow down", status_code=429, headers={"Retry-After": "1"}),
    )

    record = bandcamp_mod.get_embed_meta(URL, now=1_000)

    assert record == {"status": "error", "code": "http_429", "fetched_at": 1_000}
    assert len(fake.calls) == bandcamp_mod.FETCH_MAX_RETRIES + 1
    # Negative-cached (retryable after TTL), so a preload run isn't aborted
    # and the page isn't hammered again immediately.
    bandcamp_mod.get_embed_meta(URL, now=1_001)
    assert len(fake.calls) == bandcamp_mod.FETCH_MAX_RETRIES + 1


def test_sustained_calls_stay_at_or_under_1_rps_over_60s(
    bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))
    burst = bandcamp_mod._rate_limiter._capacity

    calls = 63
    for _ in range(calls):
        bandcamp_mod.fetch_release_page(URL)

    # Beyond the small burst allowance, every request costs a full second of
    # scheduled delay: ≥ 60 fake seconds must have elapsed for 63 calls.
    assert fake_clock.now >= calls - burst
    assert fake_clock.now >= 60.0
    assert sum(fake_clock.sleeps) == fake_clock.now, "all waiting was scheduled, never real"
    assert all(delay <= 1.0 + 1e-9 for delay in fake_clock.sleeps)


# ---------------------------------------------------------------------------
# PERF-3: exactly one BeautifulSoup parse per fetched page
# ---------------------------------------------------------------------------
def test_exactly_one_soup_parse_per_page(bandcamp_mod, pages, fake_clock, public_dns, monkeypatch):
    import bs4

    class CountingSoup(bs4.BeautifulSoup):
        instances = 0

        def __init__(self, *args, **kwargs):
            CountingSoup.instances += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(bandcamp_mod, "BeautifulSoup", CountingSoup)
    _fake_network(monkeypatch, bandcamp_mod, FakeResponse(pages.html("release_page")))

    record = bandcamp_mod.get_embed_meta(URL)

    assert CountingSoup.instances == 1, "meta + description + art must share ONE soup"
    assert record["status"] == "ok"
    assert record["release_id"] == 100200300
    assert "synthetic album about text" in record["description"]
    assert record["art_url"] == "https://f4.bcbits.example/img/a100200300_10.jpg"


# ---------------------------------------------------------------------------
# LOG-19 capture half: art_url from og:image
# ---------------------------------------------------------------------------
def test_art_url_present_when_page_has_og_image(
    bandcamp_mod, pages, fake_clock, public_dns, monkeypatch
):
    _fake_network(monkeypatch, bandcamp_mod, FakeResponse(pages.html("release_page")))

    record = bandcamp_mod.get_embed_meta(URL, now=42)

    assert record["art_url"] == "https://f4.bcbits.example/img/a100200300_10.jpg"
    # And it is persisted on the LOG-20-shaped record.
    on_disk = _embed_cache_on_disk()[URL]
    assert on_disk["status"] == "ok"
    assert on_disk["art_url"] == "https://f4.bcbits.example/img/a100200300_10.jpg"
    assert on_disk["fetched_at"] == 42


def test_art_url_absent_when_page_has_no_og_image(
    bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))

    record = bandcamp_mod.get_embed_meta(URL)

    assert record["status"] == "ok"
    assert "art_url" not in record
    # Positive records never refetch, so absence is stable — no refetch loop.
    bandcamp_mod.get_embed_meta(URL)
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Redirects: one re-validated hop, disallowed targets never fetched
# ---------------------------------------------------------------------------
def test_redirect_to_disallowed_host_not_followed(
    server_mod, bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    redirect = {"Location": "https://attacker.example.com/x"}
    fake = _fake_network(
        monkeypatch,
        bandcamp_mod,
        FakeResponse("", status_code=302, headers=redirect),
        FakeResponse("", status_code=302, headers=redirect),
        FakeResponse(OK_PAGE),
    )

    with pytest.raises(bandcamp_mod.FetchBlockedError):
        bandcamp_mod.fetch_release_page(URL)
    assert fake.calls == [URL], "the disallowed redirect target must never be fetched"

    resp = server_mod.app.test_client().get("/embed-meta", query_string={"url": URL})
    assert resp.status_code == 400
    assert all(call == URL for call in fake.calls), "no fetch ever left the original URL"
    assert _embed_cache_on_disk() == {}


def test_redirect_one_validated_hop_allowed(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    target = "https://other.bandcamp.com/album/x"
    fake = _fake_network(
        monkeypatch,
        bandcamp_mod,
        FakeResponse("", status_code=301, headers={"Location": target}),
        FakeResponse(OK_PAGE),
    )

    html = bandcamp_mod.fetch_release_page(URL)

    assert html == OK_PAGE
    assert fake.calls == [URL, target]


def test_second_redirect_hop_refused(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    fake = _fake_network(
        monkeypatch,
        bandcamp_mod,
        FakeResponse("", status_code=302, headers={"Location": "https://a.bandcamp.com/x"}),
        FakeResponse("", status_code=302, headers={"Location": "https://b.bandcamp.com/x"}),
        FakeResponse(OK_PAGE),
    )

    with pytest.raises(bandcamp_mod.FetchBlockedError):
        bandcamp_mod.fetch_release_page(URL)
    assert len(fake.calls) == 2


# ---------------------------------------------------------------------------
# Size cap: oversized bodies are aborted
# ---------------------------------------------------------------------------
def test_oversized_streamed_body_aborted(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    monkeypatch.setattr(bandcamp_mod, "FETCH_MAX_BYTES", 64)
    resp = StreamingResponse([b"x" * 40, b"y" * 40, b"z" * 40])
    _fake_network(monkeypatch, bandcamp_mod, resp)

    with pytest.raises(bandcamp_mod.OversizedResponseError):
        bandcamp_mod.fetch_release_page(URL)
    assert resp.closed, "the oversized download must be aborted, not drained"


def test_oversized_declared_content_length_aborted(
    bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    monkeypatch.setattr(bandcamp_mod, "FETCH_MAX_BYTES", 64)
    resp = StreamingResponse([b"x" * 16], headers={"Content-Length": "1000000"})
    _fake_network(monkeypatch, bandcamp_mod, resp)

    with pytest.raises(bandcamp_mod.OversizedResponseError):
        bandcamp_mod.fetch_release_page(URL)
    assert resp.closed


def test_oversized_body_records_network_error(bandcamp_mod, fake_clock, public_dns, monkeypatch):
    monkeypatch.setattr(bandcamp_mod, "FETCH_MAX_BYTES", 64)
    _fake_network(monkeypatch, bandcamp_mod, StreamingResponse([b"x" * 100]))

    record = bandcamp_mod.get_embed_meta(URL, now=7)

    assert record == {"status": "error", "code": "network", "fetched_at": 7}


# ---------------------------------------------------------------------------
# Fetch mechanics: UA, timeout, no auto-redirects on every request
# ---------------------------------------------------------------------------
def test_fetch_sends_ua_timeout_and_disables_auto_redirects(
    bandcamp_mod, fake_clock, public_dns, monkeypatch
):
    fake = _fake_network(monkeypatch, bandcamp_mod, FakeResponse(OK_PAGE))

    bandcamp_mod.fetch_release_page(URL)

    (kwargs,) = fake.kwargs
    assert kwargs["headers"]["User-Agent"] == "bcfeed/1.0"
    assert kwargs["timeout"] == bandcamp_mod.FETCH_TIMEOUT_SECONDS
    assert kwargs["allow_redirects"] is False
