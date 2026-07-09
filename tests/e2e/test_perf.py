"""WP-27 · performance polish — fast smoke-level assertions.

The heavy 5,000-release benchmark (payload size + first-paint timing) lives in
``scripts/bench_5k.py`` and is run by hand (its numbers are recorded in
``docs/current-state/perf-baseline.md``): generating and driving a 5k library is
too slow/heavy for the CI net. What CI *does* guard here are the behavioural
invariants the perf work introduced, each cheap to assert against a small routed
fixture:

- PERF-4: the DEFAULT date filter on load is the MOST RECENT MONTH of data (not
  min→max over the whole library), so first paint never builds the entire
  library's rows;
- an explicit saved calendar selection (localStorage ``bc_calendar_state_v1``)
  still WINS over that default;
- PERF-8: the in-flight embed dedupe holds — a hover + click + star on one row
  issues exactly ONE ``/embed-meta`` fetch for that URL.

Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_perf.py
"""

from __future__ import annotations

import datetime
import json
import time


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


def _month_day(months_ago, day):
    """A date `months_ago` calendar months back, on `day`.

    months_ago=1 is the previous calendar month, which is entirely in the past,
    so every day is selectable (the calendar blocks today and the future).
    """
    first = datetime.date.today().replace(day=1)
    for _ in range(months_ago):
        first = (first - datetime.timedelta(days=1)).replace(day=1)
    return first.replace(day=day)


def _json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _setup(page, base, *, releases, scraped=None):
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
    page.route("**/viewed-state**", lambda route: _json(route, {"viewed": []}))
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


def _three_months_fixture():
    """7 releases across 3 distinct months; 3 of them in the most recent month."""
    recent = [
        _release(
            f"https://recent{d}.bandcamp.com/album/a",
            _month_day(1, d),
            f"Recent {d}",
            f"Song {d}",
            "Recent Label",
            1000 + d,
        )
        for d in (5, 12, 20)
    ]
    older = [
        _release(
            f"https://mid{d}.bandcamp.com/album/a",
            _month_day(2, d),
            f"Mid {d}",
            f"Song {d}",
            "Mid Label",
            2000 + d,
        )
        for d in (8, 22)
    ]
    oldest = [
        _release(
            f"https://old{d}.bandcamp.com/album/a",
            _month_day(3, d),
            f"Old {d}",
            f"Song {d}",
            "Old Label",
            3000 + d,
        )
        for d in (10, 15)
    ]
    return recent, older, oldest


# --- tests -----------------------------------------------------------------
def test_default_view_is_most_recent_month(page, app_server):
    """PERF-4: the default filter selects the most recent month, not min→max."""
    recent, older, oldest = _three_months_fixture()
    _setup(page, app_server, releases=recent + older + oldest)
    _boot(page, app_server)

    # 7 releases exist, but the default view shows only the 3 most-recent-month
    # rows — the whole library is never rendered on first paint.
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 3"
    )
    keys = page.evaluate(
        "() => [...document.querySelectorAll('#release-rows tr.data-row')].map(r => r.dataset.key)"
    )
    assert all("recent" in k for k in keys), keys
    assert len(keys) == 3

    # The defaulted date inputs cover exactly the most recent month.
    month = _month_day(1, 1)
    from_val = page.eval_on_selector("#date-filter-from", "e => e.value")
    to_val = page.eval_on_selector("#date-filter-to", "e => e.value")
    assert from_val == month.isoformat(), from_val
    assert to_val.startswith(month.strftime("%Y-%m")), to_val


def test_saved_range_wins_over_default(page, app_server):
    """An explicit saved calendar selection overrides the most-recent-month default."""
    recent, older, oldest = _three_months_fixture()
    _setup(page, app_server, releases=recent + older + oldest)

    # Seed the persisted calendar range (localStorage) BEFORE the app boots, to
    # the OLDEST month — the opposite of what the default would pick.
    oldest_month = _month_day(3, 1)
    last_day = (_month_day(2, 1) - datetime.timedelta(days=1)).isoformat()
    page.add_init_script(
        f"""
        window.localStorage.setItem(
            'bc_calendar_state_v1',
            JSON.stringify({{ from: '{oldest_month.isoformat()}', to: '{last_day}' }})
        );
        """
    )
    _boot(page, app_server)

    # The saved (oldest) range wins: only the 2 oldest-month rows show, and the
    # default's most-recent-month rows are absent.
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 2"
    )
    keys = page.evaluate(
        "() => [...document.querySelectorAll('#release-rows tr.data-row')].map(r => r.dataset.key)"
    )
    assert all("old" in k for k in keys), keys
    from_val = page.eval_on_selector("#date-filter-from", "e => e.value")
    assert from_val == oldest_month.isoformat(), from_val


def test_hover_click_dedupe_single_fetch(page, app_server):
    """PERF-8: hover + click + star on one row issue exactly one /embed-meta fetch."""
    url = "https://recent5.bandcamp.com/album/a"
    recent, older, oldest = _three_months_fixture()
    _setup(page, app_server, releases=recent + older + oldest)

    hits = {"n": 0}

    def embed_handler(route):
        hits["n"] += 1
        # Hold the response so any un-deduped concurrent triggers would show up
        # as a second in-flight request rather than a cache hit off the first.
        time.sleep(0.3)
        _json(
            route,
            {
                "release_id": 1005,
                "is_track": False,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=1005/size=large/",
                "description": "A description.",
                "art_url": None,
            },
        )

    page.route("**/embed-meta*", embed_handler)
    _boot(page, app_server)

    row = f'#release-rows tr.data-row[data-key="{url}"]'
    # Hover (schedules the 200ms preload), then click (immediate expand fetch),
    # then star (star-triggers-enrichment) — three ensureEmbed triggers for the
    # same URL in quick succession.
    page.eval_on_selector(row, "e => e.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}))")
    page.locator(row).click()
    page.eval_on_selector(row, "e => e.focus()")
    page.keyboard.press("s")

    # Let the preload debounce fire and the held response resolve.
    page.wait_for_timeout(700)
    assert hits["n"] == 1, f"hover+click+star triple-fetched the same URL: {hits['n']}"
