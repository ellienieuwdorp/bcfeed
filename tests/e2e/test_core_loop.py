"""WP-24 · core loop — one fetch concept + ambient enrichment.

Drives the real app in headless Chromium (reusing the session app subprocess +
seeded data dir from conftest) and mocks the network with ``page.route`` to lock
in the UXP-8/9/11/21 acceptance:

- ONE fetch concept: the primary button reads "Get releases" and there is no
  separate "Preload release data" button; expanding a row plays music with no
  second fetch concept visible;
- starring an unloaded release jumps it to the FRONT of the queue and starts its
  enrichment promptly (a single ``/embed-meta`` fetch, the polite path);
- the background queue PAUSES while a Gmail/IMAP fetch runs — a queue requested
  mid-populate never opens ``/preload-range-stream`` until the populate ends —
  and never uses a second fetch path (only ``/preload-range-stream``);
- Pause stops the queue with a ``POST /preload-cancel`` (within one in-flight
  item, WP-17) and Resume re-opens the job (the server skips already-loaded);
- a 404 / gone release shows *unavailable* + "Open on Bandcamp" and is never
  auto-retried this session;
- a fully-checked range shows "Up to date" + a "Check again" that re-queries with
  ``refresh=1`` and keeps stars intact;
- hitting the result cap turns into a one-click half-range continuation whose two
  contiguous halves tile the whole selection (the union equals an uncapped fetch;
  the server dedupes by canonical URL, LOG-9).

Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_core_loop.py
"""

from __future__ import annotations

import datetime
import json
import time
from urllib.parse import parse_qs, urlparse


def _wait_hits(page, counter, n, timeout_ms=3000):
    """Poll a driver-side request counter until it reaches n."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if counter["n"] >= n:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"expected >= {n} hits, saw {counter['n']}")


# --- helpers ---------------------------------------------------------------
def _release(url, date, artist, title, page_name, release_id, embed=None):
    rel = {
        "img_url": None,
        "date": date if isinstance(date, str) else date.isoformat(),
        "artist": artist,
        "title": title,
        "page_name": page_name,
        "url": url,
        "release_id": release_id,
        "is_track": False,
    }
    if embed:
        rel["embed_url"] = embed
        rel["has_description"] = True
    return rel


def _prev_month_day(day):
    first = datetime.date.today().replace(day=1)
    prev_first = (first - datetime.timedelta(days=1)).replace(day=1)
    return prev_first.replace(day=day)


def _prev_month_all_days():
    first = datetime.date.today().replace(day=1)
    last_prev = first - datetime.timedelta(days=1)
    days = []
    d = last_prev.replace(day=1)
    while d <= last_prev:
        days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return days


def _json(route, payload, status=200):
    route.fulfill(
        status=status,
        content_type="application/json",
        body=json.dumps(payload),
    )


def _setup(page, base, *, releases, scraped=None):
    """Route the boot endpoints so the page loads our fixture data, no modal."""
    scraped = scraped or []

    page.route(
        "**/config.json*",
        lambda route: _json(
            route,
            {
                "title": "bcfeed",
                "embed_proxy_url": f"{base}/embed-meta",
                "has_credentials": True,
                "default_theme": "light",
                "clear_status_on_load": False,
                "show_dev_settings": False,
            },
        ),
    )
    page.route("**/releases*", lambda route: _json(route, {"releases": releases}))
    page.route("**/viewed-state*", lambda route: _json(route, {"viewed": []}))
    page.route(
        "**/starred-state*",
        lambda route: _json(
            route,
            {"ok": True} if route.request.method == "POST" else {"starred": []},
        ),
    )
    page.route(
        "**/scrape-status*",
        lambda route: _json(route, {"scraped": scraped, "not_scraped": []}),
    )


def _boot(page, base):
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")


def _select_range(page, start_iso, end_iso):
    page.evaluate(
        """([s, e]) => {
            const from = document.getElementById('date-filter-from');
            const to = document.getElementById('date-filter-to');
            from.value = s;
            to.value = e;
            from.dispatchEvent(new Event('input', { bubbles: true }));
            to.dispatchEvent(new Event('input', { bubbles: true }));
        }""",
        [start_iso, end_iso],
    )


# --- tests -----------------------------------------------------------------
def test_one_fetch_concept_no_preload_button(page, app_server, seed_data):
    """New-user path: Get releases is the only fetch concept; expand plays music."""
    # This test uses the REAL seeded (enriched) data but expanding a row marks it
    # seen; keep that write off the session-shared server so later specs still see
    # a pristine seen/starred state.
    page.route(
        "**/viewed-state*",
        lambda route: _json(
            route, {"ok": True} if route.request.method == "POST" else {"viewed": []}
        ),
    )
    page.route(
        "**/starred-state*",
        lambda route: _json(
            route, {"ok": True} if route.request.method == "POST" else {"starred": []}
        ),
    )
    _boot(page, app_server)
    page.evaluate(
        "() => { const m = document.getElementById('missing-token-backdrop');"
        " if (m) m.style.display = 'none'; }"
    )

    # Exactly one primary fetch button, labelled "Get releases"; the old second
    # concept ("Preload release data") is gone entirely.
    assert page.locator("#preload-range").count() == 0
    assert (page.text_content("#populate-range") or "").strip() == "Get releases"

    # Browse → expand a row → the player loads (no second fetch concept in sight).
    url_a = seed_data["url_a"]
    page.locator(f'#release-rows tr.data-row[data-key="{url_a}"]').click()
    page.wait_for_selector("tr.detail-row iframe")

    # The seeded (enriched) rows carry the quiet "ready" glyph — the CACHED badge
    # is gone from the default view.
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            const g = row && row.querySelector('[data-enrich-glyph]');
            return !!g && g.classList.contains('is-ready') && !g.hidden;
        }""",
        arg=url_a,
    )
    assert page.locator("#release-rows .cached-badge").count() == 0


def test_star_moves_to_front_and_starts_enrichment(page, app_server):
    """Starring an unloaded release fetches its player promptly, front of queue."""
    url = "https://frontqueue.bandcamp.com/album/first"
    rel = _release(url, _prev_month_day(12), "Queue Jumper", "First", "Front Queue", 501)
    _setup(page, app_server, releases=[rel])

    embed_hits = {"n": 0}

    def embed_handler(route):
        embed_hits["n"] += 1
        _json(
            route,
            {
                "release_id": 501,
                "is_track": False,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=501/size=large/",
                "description": "A description.",
                "art_url": None,
            },
        )

    page.route("**/embed-meta*", embed_handler)
    _boot(page, app_server)

    row = f'#release-rows tr.data-row[data-key="{url}"]'
    page.eval_on_selector(row, "e => e.focus()")
    page.keyboard.press("s")

    # The star fired the single-release enrichment (the polite /embed-meta path)…
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.lastPrioritized", timeout=3000)
    assert page.evaluate("() => window.__bcfeedEnrich.lifecycle.lastPrioritized") == url
    # …and the glyph settles on ready once the player is loaded.
    page.wait_for_function(
        """(url) => {
            const g = document.querySelector(
                `#release-rows tr.data-row[data-key="${url}"] [data-enrich-glyph]`);
            return !!g && g.classList.contains('is-ready');
        }""",
        arg=url,
    )
    assert embed_hits["n"] == 1


def test_queue_defers_during_populate_and_resumes_after(page, app_server):
    """The enrichment queue never competes with a running Gmail/IMAP fetch."""
    url = "https://ambient.bandcamp.com/album/loops"
    rel = _release(url, _prev_month_day(12), "Ambient", "Loops", "Ambient", 601)
    _setup(page, app_server, releases=[rel])

    preload_hits = {"n": 0}
    page.route("**/preload-range-stream*", lambda route: _count_keepalive(route, preload_hits))

    held = {"route": None}
    page.route("**/populate-range-stream*", lambda route: held.__setitem__("route", route))

    _boot(page, app_server)

    # Start a fetch; it stays in flight (its route is captured, not fulfilled).
    page.click("#populate-range")
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.populateActive === true")

    # Ask for the whole-range queue mid-populate → it must DEFER, not open a
    # second fetch path.
    page.click("#enrich-load-all")
    page.wait_for_timeout(150)
    assert preload_hits["n"] == 0, "the queue opened /preload-range-stream during a populate"
    assert page.evaluate("() => window.__bcfeedEnrich.lifecycle.pendingStart") is True

    # Finish the populate → the deferred queue starts, and ONLY through
    # /preload-range-stream (the polite server job).
    held["route"].fulfill(
        status=200,
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
        body='event: done\ndata: {"new_releases":0,"days_scraped":1}\n\n',
    )
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.populateActive === false")
    _wait_hits(page, preload_hits, 1)
    assert preload_hits["n"] == 1


def test_pause_cancels_and_resume_reopens(page, app_server):
    """Pause stops the job (POST /preload-cancel); Resume re-opens it."""
    url = "https://pausable.bandcamp.com/album/x"
    rel = _release(url, _prev_month_day(12), "Pausable", "X", "Pausable", 701)
    _setup(page, app_server, releases=[rel])

    preload_hits = {"n": 0}
    page.route("**/preload-range-stream*", lambda route: _count_keepalive(route, preload_hits))
    cancel_hits = {"n": 0}
    page.route(
        "**/preload-cancel*",
        lambda route: (
            cancel_hits.__setitem__("n", cancel_hits["n"] + 1),
            _json(route, {"ok": True}),
        ),
    )

    _boot(page, app_server)

    # Kick the whole-range queue → it opens the job and shows a Pause control.
    page.click("#enrich-load-all")
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.queueState === 'running'")
    _wait_hits(page, preload_hits, 1)
    assert preload_hits["n"] == 1
    page.wait_for_selector("#enrich-pause:not([hidden])")
    assert (page.text_content("#enrich-pause") or "").strip() == "Pause"

    # Pause → the server job is asked to stop (within one in-flight item, WP-17).
    page.click("#enrich-pause")
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.queueState === 'paused'")
    _wait_hits(page, cancel_hits, 1)
    assert cancel_hits["n"] == 1
    assert (page.text_content("#enrich-pause") or "").strip() == "Resume"

    # Resume → re-opens the job (the server skips already-loaded releases).
    page.click("#enrich-pause")
    page.wait_for_function("() => window.__bcfeedEnrich.lifecycle.queueState === 'running'")
    _wait_hits(page, preload_hits, 2)
    assert preload_hits["n"] == 2


def test_unavailable_404_shows_open_on_bandcamp_no_retry(page, app_server):
    """A gone release shows unavailable + Open on Bandcamp and is never retried."""
    url = "https://gone.bandcamp.com/album/deleted"
    rel = _release(url, _prev_month_day(12), "Gone", "Deleted", "Gone", 801)
    _setup(page, app_server, releases=[rel])

    embed_hits = {"n": 0}

    def embed_502(route):
        embed_hits["n"] += 1
        _json(route, {"error": "Couldn't reach the Bandcamp page for this release."}, status=502)

    page.route("**/embed-meta*", embed_502)
    _boot(page, app_server)

    row = f'#release-rows tr.data-row[data-key="{url}"]'
    page.locator(row).click()

    # The detail row offers the Bandcamp fallback…
    page.wait_for_selector("tr.detail-row a.link")
    fallback = page.locator("tr.detail-row a.link")
    assert "Open on Bandcamp" in (fallback.text_content() or "")
    assert fallback.get_attribute("href") == url
    # …and the row glyph reads unavailable.
    page.wait_for_function(
        """(url) => {
            const g = document.querySelector(
                `#release-rows tr.data-row[data-key="${url}"] [data-enrich-glyph]`);
            return !!g && g.classList.contains('is-unavailable');
        }""",
        arg=url,
    )
    hits_after_first = embed_hits["n"]

    # Never auto-retried: hovering the row again schedules no new /embed-meta.
    page.eval_on_selector(row, "e => e.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))")
    page.wait_for_timeout(400)
    assert embed_hits["n"] == hits_after_first, "an unavailable release was auto-retried"


def test_up_to_date_check_again_refresh(page, app_server):
    """A fully-checked range → Up to date + Check again (refresh=1), stars kept."""
    url = "https://checked.bandcamp.com/album/done"
    rel = _release(
        url,
        _prev_month_day(15),
        "Checked",
        "Done",
        "Checked",
        901,
        embed="https://bandcamp.com/EmbeddedPlayer/album=901/size=large/",
    )
    _setup(page, app_server, releases=[rel], scraped=_prev_month_all_days())
    _boot(page, app_server)

    # Select a fully-checked range → the primary is "Check again" (never a dead
    # end) with the "Up to date" confirmation.
    start = _prev_month_day(10).isoformat()
    end = _prev_month_day(20).isoformat()
    _select_range(page, start, end)
    page.wait_for_function(
        "() => document.getElementById('populate-range').textContent.trim() === 'Check again'"
    )
    assert page.get_attribute("#up-to-date-line", "hidden") is None
    assert page.get_attribute("#populate-range", "disabled") is None

    # Star the release before re-checking.
    row = f'#release-rows tr.data-row[data-key="{url}"]'
    page.eval_on_selector(row, "e => e.focus()")
    page.keyboard.press("s")
    page.wait_for_selector(f'{row}[class*="starred"]')

    refresh_urls = []

    def pop_handler(route):
        refresh_urls.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body='event: done\ndata: {"new_releases":0,"days_scraped":11}\n\n',
        )

    page.route("**/populate-range-stream*", pop_handler)

    page.click("#populate-range")
    # It re-queries with refresh=1 (WP-15) …
    page.wait_for_selector("#toast-region .toast")
    assert refresh_urls, "Check again issued no populate request"
    assert "refresh=1" in refresh_urls[0], refresh_urls
    # …completes with a zero-new completion toast …
    toast = page.locator("#toast-region .toast .toast-message").first.text_content() or ""
    assert "0" in toast or "No new" in toast, toast
    # …and the star survives.
    assert page.get_attribute("#populate-range", "disabled") is None
    page.wait_for_function(
        """(url) => {
            const btn = document.querySelector(
                `#release-rows tr.data-row[data-key="${url}"] [data-star-btn]`);
            return !!btn && btn.getAttribute('aria-pressed') === 'true';
        }""",
        arg=url,
    )


def test_max_results_split_continuation(page, app_server):
    """The quota wall becomes a one-click split; the halves tile the selection."""
    releases = [
        _release(
            f"https://split.bandcamp.com/album/{i}",
            _prev_month_day(1 + i),
            f"Artist {i}",
            f"Title {i}",
            "Split",
            1000 + i,
            embed=f"https://bandcamp.com/EmbeddedPlayer/album={1000 + i}/size=large/",
        )
        for i in range(3)
    ]
    _setup(page, app_server, releases=releases)
    _boot(page, app_server)

    full_start = _prev_month_day(1).isoformat()
    full_end = _prev_month_day(10).isoformat()
    _select_range(page, full_start, full_end)

    calls = []

    def pop_handler(route):
        q = parse_qs(urlparse(route.request.url).query)
        calls.append((q.get("start", [""])[0], q.get("end", [""])[0]))
        if len(calls) == 1:
            body = (
                "event: error\n"
                'data: {"code":"max_results","message":"Maximum results reached (2500/2000)."}\n\n'
            )
        else:
            nr = 2 if len(calls) == 2 else 1
            body = f'event: done\ndata: {{"new_releases":{nr},"days_scraped":5}}\n\n'
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body=body,
        )

    page.route("**/populate-range-stream*", pop_handler)

    # Hit the cap → a warn banner proposing the first half, one click away.
    page.click("#populate-range")
    banner = '#banner-region [data-banner-id="populate-error"]'
    page.wait_for_selector(f"{banner} .banner-action")
    action = page.locator(f"{banner} .banner-action")
    assert "Check" in (action.text_content() or "")

    # One click runs the continuation: first half, then the rest.
    action.click()
    page.wait_for_selector("#toast-region .toast")

    # Three populate calls total: the capped full range, then two halves that
    # tile it exactly (contiguous, no gap, no overlap) — the union equals an
    # uncapped fetch (the server dedupes by canonical URL, LOG-9).
    deadline = time.time() + 3
    while len(calls) < 3 and time.time() < deadline:
        page.wait_for_timeout(50)
    assert len(calls) == 3, calls
    assert calls[0] == (full_start, full_end)
    (h1s, h1e), (h2s, h2e) = calls[1], calls[2]
    assert h1s == full_start and h2e == full_end, calls
    d1_end = datetime.date.fromisoformat(h1e)
    d2_start = datetime.date.fromisoformat(h2s)
    assert d2_start == d1_end + datetime.timedelta(days=1), calls

    toast = page.locator("#toast-region .toast .toast-message").first.text_content() or ""
    assert "Added 3 releases" in toast, toast


def _count_keepalive(route, counter):
    """Record a hit and answer with a never-terminating stream.

    A huge ``retry`` means the EventSource, once this response ends, waits ~forever
    before reconnecting — so it never re-hits the route (stable counts) yet the
    queue stays 'running' (no terminal event) until close().
    """
    counter["n"] += 1
    route.fulfill(
        status=200,
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
        body="retry: 999999\n\n",
    )
