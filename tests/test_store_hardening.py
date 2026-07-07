"""WP-05 · Store hardening floor (ARC-2a).

Covers:

- CQ-18/PY-8/ARCH-1/PERF-6: one locked, atomic JSON persistence implementation
  (json_store) — 50 concurrent viewed-state updates lose zero marks, and no
  shared-``.tmp``-name collision artifacts are left behind.
- CQ-17/LOG-4/PY-12: a present-but-corrupt store is quarantined to
  ``<name>.corrupt-<timestamp>`` and the app continues with the empty default;
  a *missing* store stays quiet (no quarantine file, no error).
- CQ-18/ARCH-1 (one implementation): grep-proof that no ``json.dump``/``open(``
  file IO remains in server.py.
- CQ-30/PY-15: /reset-caches deletes stores through the same json_store module.
- JS-10 (server half): POPULATE_LOCK is owned by the worker for its whole
  lifetime — a client disconnect mid-populate keeps the lock held (a second
  populate is rejected) until the worker exits; normal completion releases it.

Import discipline (see conftest): modules are referenced as ``import x`` inside
tests/fixtures so the autouse data-dir reload is always honored; server.py
binds store paths at import so the ``server_mod`` fixture reloads it per test.
"""

from __future__ import annotations

import importlib
import json
import re
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir.

    server.py binds the store path constants at import time, so it must be
    reloaded after the autouse fixture repoints BCFEED_DATA_DIR and reloads
    paths/session_store.
    """
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


def _wait_until(predicate, timeout=5.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------------------
# Concurrency: no lost updates
# ---------------------------------------------------------------------------
def test_50_concurrent_viewed_updates_lose_zero_marks(server_mod, isolated_data_dir):
    """50 threads each mark a distinct URL viewed; every mark must survive."""
    n = 50
    urls = [f"https://artist{i}.bandcamp.com/album/a{i}" for i in range(n)]
    barrier = threading.Barrier(n)
    failures: list[str] = []

    def post_one(url: str):
        client = server_mod.app.test_client()
        barrier.wait(timeout=10)
        resp = client.post("/viewed-state", json={"url": url, "read": True})
        if resp.status_code != 200:
            failures.append(f"{url}: HTTP {resp.status_code}")

    threads = [threading.Thread(target=post_one, args=(url,)) for url in urls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not failures

    resp = server_mod.app.test_client().get("/viewed-state")
    assert resp.status_code == 200
    viewed = set(resp.get_json()["viewed"])
    missing = set(urls) - viewed
    assert not missing, f"lost {len(missing)} of {n} concurrent marks: {sorted(missing)[:5]}"

    # No temp-file artifacts left behind (unique names + os.replace).
    assert not list(isolated_data_dir.glob("*.tmp"))
    assert not list(isolated_data_dir.glob(".*.tmp"))


def test_concurrent_mixed_star_and_unstar_converges(server_mod):
    """Concurrent star/unstar of disjoint URLs applies every update exactly."""
    keep = [f"https://k{i}.bandcamp.com/album/x" for i in range(20)]
    drop = [f"https://d{i}.bandcamp.com/album/x" for i in range(20)]
    seed_client = server_mod.app.test_client()
    for url in drop:
        assert (
            seed_client.post("/starred-state", json={"url": url, "starred": True}).status_code
            == 200
        )

    barrier = threading.Barrier(len(keep) + len(drop))

    def toggle(url: str, starred: bool):
        client = server_mod.app.test_client()
        barrier.wait(timeout=10)
        client.post("/starred-state", json={"url": url, "starred": starred})

    threads = [threading.Thread(target=toggle, args=(u, True)) for u in keep]
    threads += [threading.Thread(target=toggle, args=(u, False)) for u in drop]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    starred = set(seed_client.get("/starred-state").get_json()["starred"])
    assert starred == set(keep)


def test_json_store_update_json_is_atomic_under_threads(isolated_data_dir):
    """json_store.update_json: 50 concurrent read-modify-writes, zero lost."""
    import json_store

    path = isolated_data_dir / "counter_store.json"
    barrier = threading.Barrier(50)

    def add(i: int):
        barrier.wait(timeout=10)
        json_store.update_json(path, lambda items: sorted({*items, f"item-{i}"}), [])

    threads = [threading.Thread(target=add, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == sorted(f"item-{i}" for i in range(50))


# ---------------------------------------------------------------------------
# Corruption quarantine (CQ-17/LOG-4/PY-12)
# ---------------------------------------------------------------------------
def test_corrupt_release_cache_is_quarantined_not_overwritten(isolated_data_dir):
    import paths
    import session_store

    garbage = '{"2025-06-01": [{"artist": "trunc'
    paths.RELEASE_CACHE_PATH.write_text(garbage, encoding="utf-8")

    # The app continues with an empty cache…
    assert session_store.get_full_release_cache() == []

    # …and the corrupt bytes are preserved aside, not silently overwritten.
    assert not paths.RELEASE_CACHE_PATH.exists()
    quarantined = list(isolated_data_dir.glob("release_cache.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == garbage

    # A subsequent write starts a fresh store and leaves the quarantine alone.
    session_store.persist_release_metadata(
        [
            {
                "url": "https://a.bandcamp.com/album/x",
                "date": "2025-06-01",
                "artist": "A",
                "title": "X",
            }
        ]
    )
    assert paths.RELEASE_CACHE_PATH.exists()
    assert quarantined[0].read_text(encoding="utf-8") == garbage


def test_missing_release_cache_stays_quiet(isolated_data_dir):
    import paths
    import session_store

    assert not paths.RELEASE_CACHE_PATH.exists()
    assert session_store.get_full_release_cache() == []
    assert not list(isolated_data_dir.glob("*.corrupt-*"))
    # Reading a missing store must not create the file either.
    assert not paths.RELEASE_CACHE_PATH.exists()


def test_corrupt_viewed_store_survives_via_endpoint(server_mod, isolated_data_dir):
    import paths

    paths.VIEWED_PATH.write_text("[not json", encoding="utf-8")
    resp = server_mod.app.test_client().get("/viewed-state")
    assert resp.status_code == 200
    assert resp.get_json()["viewed"] == []
    assert len(list(isolated_data_dir.glob("viewed_state.json.corrupt-*"))) == 1


# ---------------------------------------------------------------------------
# One persistence implementation (CQ-18/ARCH-1): grep proof
# ---------------------------------------------------------------------------
def test_no_json_or_open_file_io_left_in_server_py():
    src = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"\bjson\.(dump|dumps|load|loads)\b", src), (
        "server.py must not do its own JSON store IO — use json_store"
    )
    assert not re.search(r"(?<![\w.])open\(", src), (
        "server.py must not open files directly — use json_store"
    )
    assert not re.search(r"\bimport json\b(?!_store)", src)


def test_session_store_has_no_direct_file_io():
    src = (REPO_ROOT / "session_store.py").read_text(encoding="utf-8")
    assert not re.search(r"\bjson\.(dump|dumps|load|loads)\b", src)
    assert not re.search(r"(?<![\w.])open\(", src)


# ---------------------------------------------------------------------------
# On-disk formats preserved
# ---------------------------------------------------------------------------
def test_store_formats_unchanged(server_mod, isolated_data_dir):
    import paths

    client = server_mod.app.test_client()
    client.post("/viewed-state", json={"url": "https://a.bc.com/album/x", "read": True})
    assert json.loads(paths.VIEWED_PATH.read_text(encoding="utf-8")) == ["https://a.bc.com/album/x"]

    # Embed cache stays dict-of-dicts keyed by URL on disk; since WP-11 the
    # record shape is the LOG-20 one (status/fetched_at + fields), written by
    # bandcamp._store_embed_record through json_store. Since WP-14 embed_url
    # is derived, never stored. The full record-shape contract is covered in
    # tests/test_embed_cache.py.
    import bandcamp

    url = "https://a.bc.com/album/x"
    bandcamp._store_embed_record(
        url,
        {
            "status": "ok",
            "release_id": "123",
            "is_track": False,
            "description": "",
            "fetched_at": 42,
        },
    )
    cache = json.loads(paths.EMBED_CACHE_PATH.read_text(encoding="utf-8"))
    assert cache == {
        url: {
            "status": "ok",
            "release_id": "123",
            "is_track": False,
            "description": "",
            "fetched_at": 42,
        }
    }


# ---------------------------------------------------------------------------
# /reset-caches consistency (CQ-30/PY-15)
# ---------------------------------------------------------------------------
def test_reset_caches_deletes_stores_through_json_store(server_mod, seed, isolated_data_dir):
    import paths

    seed.releases({"2025-06-01": [{"url": "https://a.bc.com/album/x", "date": "2025-06-01"}]})
    # The v2 ledger records checked-and-empty days itself (LOG-11) — there is
    # no second no_results_dates.json store anymore.
    seed.scrape_status(["2025-06-01", "2025-06-02"])
    seed.embed_cache({"https://a.bc.com/album/x": {"release_id": 1, "status": "ok"}})
    seed.viewed(["https://a.bc.com/album/x"])
    seed.starred(["https://a.bc.com/album/x"])

    resp = server_mod.app.test_client().post(
        "/reset-caches", json={"clear_cache": True, "clear_viewed": True, "clear_starred": True}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["errors"] == []
    assert set(body["cleared"]) == {
        "release_cache.json",
        "scrape_status.json",
        "embed_cache.json",
        "viewed_state.json",
        "starred_state.json",
    }
    for path in (
        paths.RELEASE_CACHE_PATH,
        paths.SCRAPE_STATUS_PATH,
        paths.EMBED_CACHE_PATH,
        paths.VIEWED_PATH,
        paths.STARRED_PATH,
    ):
        assert not path.exists()


# ---------------------------------------------------------------------------
# JS-10 server half: worker-owned POPULATE_LOCK
# ---------------------------------------------------------------------------
@pytest.fixture
def populate_harness(server_mod, monkeypatch):
    """Bypass provider checks and swap in a controllable populate worker."""
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "gmail")
    monkeypatch.setattr(server_mod, "gmail_credentials_configured", lambda: True)
    monkeypatch.setattr(server_mod, "gmail_token_available", lambda: True)

    started = threading.Event()
    unblock = threading.Event()

    def fake_populate(start, end, max_results, batch_size=20, log=None, refresh=False):
        # Emit one line first: the test client fetches the stream's first
        # chunk eagerly even in non-buffered mode, so a wholly silent worker
        # would block the request instead of returning a lazy stream.
        if log:
            log("worker running")
        started.set()
        assert unblock.wait(timeout=10), "test never unblocked the fake populate worker"
        if log:
            log("fake populate ran")

    monkeypatch.setattr(server_mod, "populate_release_cache", fake_populate)
    return started, unblock


POPULATE_URL = "/populate-range-stream?start=2025-06-01&end=2025-06-02"


def test_disconnect_mid_populate_keeps_lock_until_worker_exits(server_mod, populate_harness):
    started, unblock = populate_harness

    # Start a populate; the response stream is NOT consumed (buffered=False),
    # then closed — simulating a client that connected and disconnected.
    resp1 = server_mod.app.test_client().get(POPULATE_URL, buffered=False)
    assert started.wait(timeout=5), "populate worker never started"
    resp1.close()  # client disconnect: SSE generator torn down, worker lives on

    # The worker is still running, so the lock must still be held…
    assert server_mod.POPULATE_LOCK.locked()

    # …and a second populate attempt is rejected while it runs.
    resp2 = server_mod.app.test_client().get(POPULATE_URL)
    body2 = resp2.get_data(as_text=True)
    assert "event: error" in body2
    assert "Another populate is already running" in body2
    assert server_mod.POPULATE_LOCK.locked()

    # When the worker finishes, it releases the lock even though the client
    # that started it is long gone.
    unblock.set()
    assert _wait_until(lambda: not server_mod.POPULATE_LOCK.locked()), (
        "worker exit did not release POPULATE_LOCK"
    )

    # A fresh populate can now run to completion normally.
    resp3 = server_mod.app.test_client().get(POPULATE_URL)
    body3 = resp3.get_data(as_text=True)
    assert "fake populate ran" in body3
    assert "Populate completed." in body3
    assert "event: done" in body3
    assert not server_mod.POPULATE_LOCK.locked()


def test_normal_completion_releases_lock(server_mod, populate_harness):
    started, unblock = populate_harness
    unblock.set()  # worker completes immediately

    resp = server_mod.app.test_client().get(POPULATE_URL)
    body = resp.get_data(as_text=True)
    assert "Populate completed." in body
    assert "event: done" in body
    assert _wait_until(lambda: not server_mod.POPULATE_LOCK.locked())


def test_worker_failure_still_releases_lock(server_mod, populate_harness, monkeypatch):
    def exploding_populate(start, end, max_results, batch_size=20, log=None, refresh=False):
        raise RuntimeError("boom")

    monkeypatch.setattr(server_mod, "populate_release_cache", exploding_populate)
    resp = server_mod.app.test_client().get(POPULATE_URL)
    body = resp.get_data(as_text=True)
    assert "ERROR: Unexpected error: boom" in body
    assert _wait_until(lambda: not server_mod.POPULATE_LOCK.locked())
