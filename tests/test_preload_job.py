"""WP-17 · Server-side preload job + batch viewed endpoint (LOG-6/PERF-3/PERF-1/ARCH-6).

All network access is mocked (``bandcamp.requests`` replaced, DNS via the
``bandcamp._getaddrinfo`` seam) and all pacing runs on a FAKE clock injected
through ``bandcamp._rate_limiter`` — no test ever sleeps for real seconds or
touches bandcamp.com. Fetch gating uses semaphores the test releases, so
worker interleavings are deterministic where the assertions need them to be.

Covered:
* a 50-release preload run issues ≤ 3 concurrent fetches, paces globally at
  ≤ 1 req/s through the shared WP-12 token bucket, and flushes the embed
  cache in batches of ~10 (5 writes for 50 items, not 50);
* killing the client mid-run persists every completed fetch (including a
  partial batch < 10) and a re-run skips them — resumable by construction;
* an explicit cancel stops scheduling within one in-flight request and the
  terminal ``done`` event carries accurate ok/failed/skipped counts;
* failures are counted and negative-cached; fresh records (ok, or error
  within TTL) are skipped without a fetch;
* the job holds its OWN non-reentrant lock: a second preload is rejected
  ``busy`` while a populate runs to completion concurrently (disjoint stores,
  by design), and a disconnect never releases the lock before the worker ends;
* POST /viewed-state/batch marks 300 URLs (canonicalized) in ONE store write,
  loses nothing under concurrent single-toggle POSTs, validates its payload,
  and is WP-08 CSRF-guarded.
"""

from __future__ import annotations

import datetime
import importlib
import json
import socket
import threading
import time

import pytest
from werkzeug.test import Client

OK_PAGE = """<!doctype html>
<html><head>
<meta name="bc-page-properties" content='{"item_type": "album", "item_id": 4242}'>
</head><body>
<div id="tralbum-about">A test album about text.</div>
</body></html>
"""

PUBLIC_IP = "151.101.65.30"

STREAM_URL = "/preload-range-stream?start=2025-06-01&end=2025-06-30"


# ---------------------------------------------------------------------------
# Fakes: clock, DNS, gated network
# ---------------------------------------------------------------------------
class FakeClock:
    """Thread-safe monotonic fake clock; sleeping advances time, never blocks."""

    def __init__(self):
        self._lock = threading.Lock()
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds


class FakeResponse:
    def __init__(self, text: str = OK_PAGE, status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            exc = RuntimeError(f"HTTP {self.status_code}")
            exc.response = self
            raise exc


class GatedRequests:
    """Thread-safe fake ``requests`` with an optional per-fetch permit gate.

    While ``gated`` is True every ``get`` must acquire one permit before it
    returns, so the test controls exactly how many fetches complete and can
    observe workers piled up in flight. ``calls`` records every requested
    URL; ``max_in_flight`` tracks the concurrency high-water mark.
    """

    def __init__(self, *, gated: bool = False, responses: dict | None = None):
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self.gated = gated
        self.permits = threading.Semaphore(0)
        self.responses = responses or {}

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append(url)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.gated:
                assert self.permits.acquire(timeout=10), "test never released a fetch permit"
            response = self.responses.get(url)
            if isinstance(response, Exception):
                raise response
            return response or FakeResponse()
        finally:
            with self._lock:
                self.in_flight -= 1


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
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]

    monkeypatch.setattr(bandcamp_mod, "_getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def embed_write_counter(monkeypatch):
    """Count json_store.update_json calls that hit the embed cache."""
    import json_store
    import paths

    counts = {"embed": 0, "viewed": 0}
    real = json_store.update_json

    def counting(path, mutator, default, **kwargs):
        import pathlib

        resolved = pathlib.Path(path)
        if resolved == paths.EMBED_CACHE_PATH:
            counts["embed"] += 1
        if resolved == paths.VIEWED_PATH:
            counts["viewed"] += 1
        return real(path, mutator, default, **kwargs)

    monkeypatch.setattr(json_store, "update_json", counting)
    return counts


def seed_range_releases(seed, make_release, count: int) -> list[str]:
    """Seed ``count`` releases across June 2025 and return their URLs."""
    mapping: dict = {}
    urls: list[str] = []
    for i in range(count):
        day = datetime.date(2025, 6, 1 + (i % 25))
        url = f"https://artist{i}.bandcamp.com/album/a{i}"
        release = make_release(release_url=url, date=day.isoformat(), release_title=f"Album {i}")
        mapping.setdefault(day, []).append(release)
        urls.append(url)
    seed.releases(mapping)
    return urls


# ---------------------------------------------------------------------------
# SSE helpers (same protocol as test_sse_protocol)
# ---------------------------------------------------------------------------
def parse_sse(body: str) -> list[tuple[str | None, str]]:
    events: list[tuple[str | None, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        events.append((name, "\n".join(data_lines)))
    return events


def message_events(events) -> list[dict]:
    return [json.loads(data) for name, data in events if name is None]


def terminal_events(events) -> list[tuple[str, dict]]:
    return [(name, json.loads(data)) for name, data in events if name is not None]


def run_stream(server_mod, url: str = STREAM_URL):
    resp = server_mod.app.test_client().get(url)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    return parse_sse(resp.get_data(as_text=True))


class StreamReader:
    """Incrementally read SSE events from an unbuffered test-client response."""

    def __init__(self, server_mod, url: str = STREAM_URL):
        self.resp = server_mod.app.test_client().get(url, buffered=False)
        assert self.resp.status_code == 200
        self._iter = iter(self.resp.response)

    def next_event(self) -> tuple[str | None, str]:
        chunk = next(self._iter)
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
        events = parse_sse(chunk)
        assert len(events) == 1, f"expected one SSE block per chunk, got {chunk!r}"
        return events[0]

    def read_completions(self, count: int) -> list[dict]:
        """Read events until ``count`` per-item completion events were seen."""
        seen: list[dict] = []
        while len(seen) < count:
            name, data = self.next_event()
            assert name is None, f"unexpected terminal event {name}: {data}"
            payload = json.loads(data)
            if payload.get("current"):
                seen.append(payload)
        return seen

    def read_until_terminal(self) -> tuple[str, dict]:
        while True:
            name, data = self.next_event()
            if name is not None:
                return name, json.loads(data)

    def close(self):
        self.resp.close()


def wait_for(predicate, timeout: float = 10.0) -> bool:
    """Poll a condition with tiny real sleeps (never seconds-long blocks)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _embed_cache_on_disk() -> dict:
    import paths

    if not paths.EMBED_CACHE_PATH.exists():
        return {}
    return json.loads(paths.EMBED_CACHE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 50-release run: ≤3 concurrent, ≤1 rps globally, batched cache writes
# ---------------------------------------------------------------------------
def test_50_release_preload_concurrency_rate_and_batched_writes(
    server_mod,
    bandcamp_mod,
    seed,
    make_release,
    fake_clock,
    public_dns,
    embed_write_counter,
    monkeypatch,
):
    urls = seed_range_releases(seed, make_release, 50)
    fake = GatedRequests(gated=True)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)

    results: list = []
    thread = threading.Thread(target=lambda: results.append(run_stream(server_mod)))
    thread.start()
    try:
        # All three workers end up with exactly one fetch in flight each.
        assert wait_for(lambda: fake.in_flight == 3), "3 workers never got in flight"
        assert fake.max_in_flight == 3
    finally:
        # Open the gate and let the run drain.
        fake.gated = False
        fake.permits.release(3)
        thread.join(timeout=30)
    assert not thread.is_alive(), "preload stream never finished"

    events = results[0]
    # ≤3 concurrent fetches, each URL fetched exactly once.
    assert fake.max_in_flight <= 3
    assert sorted(fake.calls) == sorted(urls)

    # Global pacing ≤ ~1 rps: the token bucket grants at most
    # capacity + rate·elapsed tokens, so 50 fetches need ≥ 47 fake seconds —
    # all of it scheduled through the fake clock, none slept for real.
    capacity = bandcamp_mod._rate_limiter._capacity
    assert fake_clock.now >= 50 - capacity
    assert sum(fake_clock.sleeps) == pytest.approx(fake_clock.now)

    # Typed protocol: enrich-phase progress events with determinate counts.
    messages = message_events(events)
    assert messages
    for payload in messages:
        assert payload["v"] == 1
        assert payload["phase"] == "enrich"
        assert payload["level"] in ("info", "warn", "error")
        assert payload["message"] == payload["text"]
    assert max(p["current"] for p in messages if p["current"] is not None) == 50
    assert all(p["total"] == 50 for p in messages if p["total"] is not None)

    # Exactly one terminal event with the full summary, last in the stream.
    assert terminal_events(events) == [
        ("done", {"ok": 50, "failed": 0, "skipped": 0, "total": 50, "cancelled": False})
    ]
    assert events[-1][0] == "done"

    # PERF-3: 50 items flushed in batches of 10 → exactly 5 embed-cache
    # writes (O(cache × n/10)), never one whole-cache rewrite per release.
    assert embed_write_counter["embed"] == 5
    on_disk = _embed_cache_on_disk()
    assert set(on_disk) == set(urls)
    assert all(record["status"] == "ok" for record in on_disk.values())


# ---------------------------------------------------------------------------
# Kill the client mid-run: completed fetches persist, a re-run skips them
# ---------------------------------------------------------------------------
def test_client_kill_mid_run_persists_completed_and_rerun_skips(
    server_mod, bandcamp_mod, seed, make_release, fake_clock, public_dns, monkeypatch
):
    urls = seed_range_releases(seed, make_release, 12)
    fake = GatedRequests(gated=True)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)

    reader = StreamReader(server_mod)
    fake.permits.release(5)
    completions = reader.read_completions(5)
    assert [p["total"] for p in completions] == [12] * 5

    # The client dies: tearing down the SSE generator sets the cancel event,
    # but the WORKER owns the lock (WP-05 pattern) — it must still be held.
    reader.close()
    assert server_mod.PRELOAD_LOCK.locked(), "disconnect must not release the job lock"

    # Let the (≤3) in-flight fetches finish; no new ones may be scheduled.
    fake.permits.release(3)
    assert wait_for(lambda: not server_mod.PRELOAD_LOCK.locked()), "worker never released lock"

    first_run_calls = list(fake.calls)
    assert 5 <= len(first_run_calls) <= 8, "cancel must stop within the in-flight requests"
    # Everything fetched (a partial batch < 10) was flushed and persisted.
    on_disk = _embed_cache_on_disk()
    assert set(on_disk) == set(first_run_calls)
    assert all(record["status"] == "ok" for record in on_disk.values())

    # Re-run: only the remainder is fetched; already-enriched items skipped.
    fake.gated = False
    events = run_stream(server_mod)
    second_run_calls = fake.calls[len(first_run_calls) :]
    assert sorted(first_run_calls + second_run_calls) == sorted(urls)
    assert not set(second_run_calls) & set(first_run_calls), "re-run must skip enriched items"
    remainder = 12 - len(first_run_calls)
    assert terminal_events(events) == [
        (
            "done",
            {
                "ok": remainder,
                "failed": 0,
                "skipped": len(first_run_calls),
                "total": 12,
                "cancelled": False,
            },
        )
    ]


# ---------------------------------------------------------------------------
# Explicit cancel: stops within one in-flight request, accurate terminal counts
# ---------------------------------------------------------------------------
def test_cancel_stops_within_one_in_flight_request_with_accurate_counts(
    server_mod, bandcamp_mod, seed, make_release, fake_clock, public_dns, monkeypatch
):
    monkeypatch.setattr(bandcamp_mod, "PRELOAD_WORKER_COUNT", 1)
    seed_range_releases(seed, make_release, 10)
    fake = GatedRequests(gated=True)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)

    reader = StreamReader(server_mod)
    fake.permits.release(3)
    reader.read_completions(3)
    # The single worker is now in flight on item 4 (blocked at the gate).
    assert wait_for(lambda: len(fake.calls) == 4), "worker never started its 4th fetch"

    cancel = server_mod.app.test_client().post("/preload-cancel")
    assert cancel.status_code == 200
    assert cancel.get_json() == {"ok": True, "cancelling": True}

    # The one in-flight request finishes; nothing further is scheduled.
    fake.permits.release(1)
    name, payload = reader.read_until_terminal()
    assert name == "done"
    assert payload == {"ok": 4, "failed": 0, "skipped": 6, "total": 10, "cancelled": True}
    assert wait_for(lambda: not server_mod.PRELOAD_LOCK.locked())
    assert len(fake.calls) == 4, "cancel must stop scheduling within one in-flight request"
    assert set(_embed_cache_on_disk()) == set(fake.calls)
    reader.close()


def test_cancel_with_no_active_job_is_a_noop(server_mod):
    resp = server_mod.app.test_client().post("/preload-cancel")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "cancelling": False}


# ---------------------------------------------------------------------------
# Candidate selection: failures counted + negative-cached, fresh records skipped
# ---------------------------------------------------------------------------
def test_failures_counted_and_fresh_records_skipped_without_fetch(
    server_mod, bandcamp_mod, seed, make_release, fake_clock, public_dns, monkeypatch
):
    urls = seed_range_releases(seed, make_release, 8)
    now = int(time.time())
    # urls[6] has a fresh ok record, urls[7] a fresh error record (within its
    # retry TTL): neither may be fetched again (resumable by construction).
    seed.embed_cache(
        {
            urls[6]: {
                "status": "ok",
                "release_id": 1,
                "is_track": False,
                "description": "",
                "fetched_at": now,
            },
            urls[7]: {"status": "error", "code": "network", "fetched_at": now},
        }
    )
    fake = GatedRequests(
        responses={
            urls[1]: FakeResponse("server error", status_code=500),
            urls[3]: FakeResponse("server error", status_code=500),
        }
    )
    monkeypatch.setattr(bandcamp_mod, "requests", fake)

    events = run_stream(server_mod)

    assert sorted(fake.calls) == sorted(urls[:6])
    assert terminal_events(events) == [
        ("done", {"ok": 4, "failed": 2, "skipped": 2, "total": 8, "cancelled": False})
    ]
    warns = [p for p in message_events(events) if p["level"] == "warn"]
    assert len(warns) == 2
    on_disk = _embed_cache_on_disk()
    assert on_disk[urls[1]]["status"] == "error"
    assert on_disk[urls[1]]["code"] == "http_500"


def test_preflight_rejections_are_typed_error_events(server_mod):
    events = run_stream(server_mod, "/preload-range-stream")
    assert terminal_events(events) == [
        ("error", {"code": "internal", "message": "Missing start/end"})
    ]
    events = run_stream(server_mod, "/preload-range-stream?start=2025-06-02&end=2025-06-01")
    assert terminal_events(events)[0][1]["code"] == "internal"


# ---------------------------------------------------------------------------
# Locking: own non-reentrant lock; preload + populate coexist by design
# ---------------------------------------------------------------------------
def test_second_preload_rejected_busy_while_populate_coexists(
    server_mod, bandcamp_mod, seed, make_release, fake_clock, public_dns, monkeypatch
):
    assert server_mod.PRELOAD_LOCK is not server_mod.POPULATE_LOCK
    seed_range_releases(seed, make_release, 5)
    fake = GatedRequests(gated=True)
    monkeypatch.setattr(bandcamp_mod, "requests", fake)

    reader = StreamReader(server_mod)
    assert wait_for(lambda: fake.in_flight >= 1), "preload job never started fetching"
    assert server_mod.PRELOAD_LOCK.locked()

    # A second preload is rejected with a typed busy error…
    events = run_stream(server_mod)
    assert terminal_events(events) == [
        ("error", {"code": "busy", "message": "Release details are already being loaded."})
    ]

    # …while a populate runs to completion concurrently: the two jobs hold
    # separate locks and write disjoint stores (embed cache vs release cache
    # + ledger), each through json_store's per-path locks.
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "imap")
    monkeypatch.setattr(server_mod, "_has_credentials_for_provider", lambda: True)
    monkeypatch.setattr(server_mod, "populate_release_cache", lambda *a, **k: None)
    populate_events = parse_sse(
        server_mod.app.test_client()
        .get("/populate-range-stream?start=2025-06-01&end=2025-06-30")
        .get_data(as_text=True)
    )
    assert terminal_events(populate_events) == [("done", {"new_releases": 0, "days_scraped": 0})]
    assert server_mod.PRELOAD_LOCK.locked(), "the preload job must still be running"

    fake.gated = False
    fake.permits.release(5)
    name, payload = reader.read_until_terminal()
    assert name == "done"
    assert payload["ok"] == 5
    assert wait_for(lambda: not server_mod.PRELOAD_LOCK.locked())
    reader.close()


# ---------------------------------------------------------------------------
# POST /viewed-state/batch: one lock, one write, zero lost marks
# ---------------------------------------------------------------------------
def test_batch_marks_300_urls_in_one_store_write(server_mod, embed_write_counter):
    # Non-canonical spellings prove LOG-9 normalization on the way in.
    urls = [f"https://Artist{i}.bandcamp.com/album/x{i}/" for i in range(300)]
    expected = {f"https://artist{i}.bandcamp.com/album/x{i}" for i in range(300)}

    resp = server_mod.app.test_client().post(
        "/viewed-state/batch", json={"urls": urls, "viewed": True}
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "count": 300}
    assert embed_write_counter["viewed"] == 1, "300 URLs must be ONE store write"
    listed = server_mod.app.test_client().get("/viewed-state").get_json()["viewed"]
    assert set(listed) == expected

    # And a batch unmark removes them in one write too.
    resp = server_mod.app.test_client().post(
        "/viewed-state/batch", json={"urls": urls[:100], "viewed": False}
    )
    assert resp.status_code == 200
    assert embed_write_counter["viewed"] == 2
    listed = server_mod.app.test_client().get("/viewed-state").get_json()["viewed"]
    assert len(listed) == 200


def test_batch_loses_nothing_under_concurrent_single_toggles(server_mod):
    batch_urls = [f"https://batch{i}.bandcamp.com/album/b{i}" for i in range(300)]
    single_urls = [f"https://single{i}.bandcamp.com/album/s{i}" for i in range(20)]
    barrier = threading.Barrier(6)
    errors: list[str] = []

    def post_batch():
        client = server_mod.app.test_client()
        barrier.wait(timeout=10)
        resp = client.post("/viewed-state/batch", json={"urls": batch_urls, "viewed": True})
        if resp.status_code != 200:
            errors.append(f"batch: HTTP {resp.status_code}")

    def post_singles(chunk):
        client = server_mod.app.test_client()
        barrier.wait(timeout=10)
        for url in chunk:
            resp = client.post("/viewed-state", json={"url": url, "read": True})
            if resp.status_code != 200:
                errors.append(f"single: HTTP {resp.status_code}")

    threads = [threading.Thread(target=post_batch)]
    for i in range(5):
        threads.append(
            threading.Thread(target=post_singles, args=(single_urls[i * 4 : i * 4 + 4],))
        )
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors

    listed = server_mod.app.test_client().get("/viewed-state").get_json()["viewed"]
    assert set(listed) == set(batch_urls) | set(single_urls), "no mark may be lost"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"urls": [], "viewed": True},
        {"urls": "https://a.bandcamp.com/album/x", "viewed": True},
        {"urls": ["https://a.bandcamp.com/album/x", 5], "viewed": True},
        {"urls": ["https://a.bandcamp.com/album/x"], "viewed": "yes"},
        {"urls": ["https://a.bandcamp.com/album/x"]},
    ],
)
def test_batch_rejects_malformed_payloads(server_mod, payload):
    resp = server_mod.app.test_client().post("/viewed-state/batch", json=payload)
    assert resp.status_code == 400


def test_batch_requires_csrf_header(server_mod):
    # A cross-site "simple" request cannot carry the custom header → 403.
    plain = Client(server_mod.app)
    resp = plain.post(
        "/viewed-state/batch", json={"urls": ["https://a.bandcamp.com/album/x"], "viewed": True}
    )
    assert resp.status_code == 403
