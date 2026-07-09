#!/usr/bin/env python3
"""WP-27 · re-baseline the audit's 5,000-release performance numbers.

Generates a throwaway schema-v2 data dir holding ~5,000 releases (a fraction
enriched with embed records + descriptions), launches the *real* app on an
ephemeral port over that dir, and measures:

  1. /releases payload size for the whole library (PERF-5 — descriptions must
     no longer ride the table payload, so ~5k rows stay under ~1.5 MB);
  2. first paint — navigation → the DEFAULT view's rows rendered — via headless
     Chromium (PERF-4 — the default is the most recent month, so first paint
     builds one month of rows, not all 5,000);
  3. mark-N-shown-as-seen — one batch POST + the main-thread time to mutate and
     re-render the default view (PERF-1/JS-11 render discipline held post-Phase-3).

This is a HAND-RUN script, not a CI test: generating and driving a 5k library is
too slow/heavy for the smoke net (that keeps the behavioural invariants only, in
tests/e2e/test_perf.py). Record its output in docs/current-state/perf-baseline.md.

Interpreter split (same contract as tests/e2e/conftest.py): run THIS driver with
an interpreter that has Playwright; point the app subprocess at a Flask-capable
interpreter via BCFEED_APP_PYTHON (defaults to sys.executable). Example::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python scripts/bench_5k.py
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_PYTHON = os.environ.get("BCFEED_APP_PYTHON", sys.executable)

TOTAL_RELEASES = 5000
# Releases placed in the most recent (previous calendar) month — the size of the
# DEFAULT view, and the count that mark-N-shown-as-seen operates on.
RECENT_MONTH_RELEASES = 300
# Fraction of the library that is enriched (has an embed record + description).
ENRICHED_FRACTION = 0.3

PAGES = [
    "Midnight Tapes",
    "Harbour Lights",
    "Aurora Sound",
    "Deep Field Records",
    "Slow Static",
    "Northern Drift",
    "Velvet Signal",
    "Coastal Echo",
]
DESCRIPTION = (
    "A full-length description body of the sort that used to ride the table "
    "payload for every single release before WP-14 moved it off the /releases "
    "response and behind a lazy /embed-meta fetch on expand. " * 3
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _prev_month_first() -> datetime.date:
    first = datetime.date.today().replace(day=1)
    return (first - datetime.timedelta(days=1)).replace(day=1)


def _generate_fixture(data_dir: Path) -> dict:
    """Write a schema-v2 store with TOTAL_RELEASES releases; return some stats."""
    recent_first = _prev_month_first()
    # The most recent month is entirely in the past (fully selectable). Spread
    # the older releases backwards over prior months so the library spans ~2 yrs.
    release_cache: dict[str, list] = {}
    embed_cache: dict[str, dict] = {}
    scraped: list[str] = []

    enriched_target = int(TOTAL_RELEASES * ENRICHED_FRACTION)
    enriched = 0
    made = 0

    def add_release(day_date: datetime.date, idx: int) -> None:
        nonlocal enriched, made
        page = PAGES[idx % len(PAGES)]
        slug = f"release-{idx}"
        host = page.lower().replace(" ", "") + str(idx % 97)
        url = f"https://{host}.bandcamp.com/album/{slug}"
        rel = {
            "date": day_date.isoformat(),
            "artist": f"Artist {idx % 900}",
            "title": f"Release Title Number {idx}",
            "page_name": page,
            "url": url,
            "release_id": 100000 + idx,
            "is_track": False,
            "source": "gmail",
        }
        key = day_date.isoformat()
        release_cache.setdefault(key, []).append(rel)
        if key not in scraped:
            scraped.append(key)
        if enriched < enriched_target:
            embed_cache[url] = {
                "status": "ok",
                "release_id": 100000 + idx,
                "is_track": False,
                "description": DESCRIPTION,
                "art_url": f"https://f4.bcbits.com/img/a{idx}_16.jpg",
                "fetched_at": 1700000000,
            }
            enriched += 1
        made += 1

    # 1) The recent month.
    for i in range(RECENT_MONTH_RELEASES):
        day = recent_first + datetime.timedelta(days=i % 27)
        add_release(day, made)

    # 2) The remaining releases, spread over the ~23 months before that.
    remaining = TOTAL_RELEASES - RECENT_MONTH_RELEASES
    older_start = (recent_first - datetime.timedelta(days=1)).replace(day=1)
    for i in range(remaining):
        # Walk backwards a day at a time from the end of the older span.
        day = older_start - datetime.timedelta(days=i % 690)
        add_release(day, made)

    (data_dir / "release_cache.json").write_text(json.dumps(release_cache), encoding="utf-8")
    (data_dir / "embed_cache.json").write_text(json.dumps(embed_cache), encoding="utf-8")
    (data_dir / "scrape_status.json").write_text(json.dumps(scraped), encoding="utf-8")
    # Mark the store already at schema v2 so boot runs no migration over it.
    (data_dir / "meta.json").write_text(json.dumps({"schema": 2}), encoding="utf-8")

    return {
        "total": made,
        "enriched": enriched,
        "recent_month": RECENT_MONTH_RELEASES,
        "recent_first": recent_first.isoformat(),
    }


def _wait_for_health(base_url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"app exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"app never became healthy at {base_url}")


def _measure_payload(base_url: str) -> dict:
    t0 = time.perf_counter()
    with urllib.request.urlopen(f"{base_url}/releases", timeout=30) as resp:
        body = resp.read()
    dt = time.perf_counter() - t0
    data = json.loads(body)
    n = len(data.get("releases", []))
    has_desc = any("description" in r for r in data.get("releases", []))
    return {
        "bytes": len(body),
        "mb": len(body) / (1024 * 1024),
        "server_ms": dt * 1000,
        "count": n,
        "any_description_field": has_desc,
    }


def _measure_browser(base_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    result: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()

        batch_posts = {"n": 0}

        def _on_request(req):
            if req.method == "POST" and "/viewed-state/batch" in req.url:
                batch_posts["n"] += 1

        page.on("request", _on_request)

        # First paint: navigation → the DEFAULT view's rows rendered.
        t0 = time.perf_counter()
        page.goto(f"{base_url}/dashboard", wait_until="commit")
        page.wait_for_selector("#release-rows tr.data-row")
        result["first_paint_ms"] = (time.perf_counter() - t0) * 1000

        result["default_rows"] = page.evaluate(
            "() => document.querySelectorAll('#release-rows tr.data-row').length"
        )

        # mark-N-shown-as-seen: measure the synchronous click handler + the
        # coalesced render flush (awaiting one microtask lets the scheduled
        # render run before we stop the clock — no requestAnimationFrame idle).
        page.evaluate("() => { window.__bcfeedRenderCounts.table = 0; }")
        timing = page.evaluate(
            """async () => {
                const t0 = performance.now();
                document.getElementById('mark-seen').click();
                await Promise.resolve();  // flush the coalesced render microtask
                return {
                    ms: performance.now() - t0,
                    tableRenders: window.__bcfeedRenderCounts.table,
                };
            }"""
        )
        page.wait_for_timeout(200)  # let the batch POST fly
        result["mark_seen_ms"] = timing["ms"]
        result["mark_seen_table_renders"] = timing["tableRenders"]
        result["mark_seen_batch_posts"] = batch_posts["n"]

        browser.close()
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bcfeed-bench-") as tmp:
        data_dir = Path(tmp)
        stats = _generate_fixture(data_dir)

        port = _free_port()
        env = dict(os.environ)
        env["BCFEED_DATA_DIR"] = str(data_dir)
        proc = subprocess.Popen(
            [APP_PYTHON, "bcfeed.py", "--no-browser", "--port", str(port)],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_health(base_url, proc)
            payload = _measure_payload(base_url)
            browser = _measure_browser(base_url)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    print("=" * 62)
    print("bcfeed 5k performance re-baseline (WP-27)")
    print("=" * 62)
    print(f"fixture:            {stats['total']} releases, {stats['enriched']} enriched")
    print(f"recent month:       {stats['recent_month']} releases ({stats['recent_first']})")
    print("-" * 62)
    print(f"/releases payload:  {payload['mb']:.2f} MB  ({payload['bytes']:,} bytes)")
    print(f"  releases in body: {payload['count']}")
    print(f"  description field present: {payload['any_description_field']}  (expect False)")
    print(f"  server build time:{payload['server_ms']:.0f} ms")
    print("-" * 62)
    print(f"first paint:        {browser['first_paint_ms']:.0f} ms  (nav → default rows rendered)")
    print(f"  default rows:     {browser['default_rows']}  (of {stats['total']})")
    print("-" * 62)
    print(f"mark-{browser['default_rows']}-seen:")
    print(f"  main-thread:      {browser['mark_seen_ms']:.1f} ms  (click handler + render flush)")
    print(f"  table renders:    {browser['mark_seen_table_renders']}  (expect 1)")
    print(f"  batch POSTs:      {browser['mark_seen_batch_posts']}  (expect 1)")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
