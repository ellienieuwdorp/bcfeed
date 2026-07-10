"""WPX-A · UIP-1/UIP-2 — the layout-stability floor.

Drives the real app in headless Chromium and pins the "no background state change
may resize a layout element" contract for the two surfaces WPX-A stabilises:

- ``#activity-strip`` is a CONSTANT-HEIGHT single line. A fetch's progress bar is
  a thin edge-attached overlay (out of flow), the enrich chip is an inline
  segment of the meta row, and every conditional element swaps in place — so the
  strip's height is pixel-identical across idle / fetching (progress) /
  loading-players (chip) / error / combinations. Only the Details disclosure may
  change its height.
- ``.action-group`` height is pixel-identical across not-checked / checked /
  partially-checked selections: the "Up to date" line's slot is permanently
  reserved (visibility, not display), so toggling it never grows the card.

Network is mocked with ``page.route`` so the spec never touches the session-shared
server data dir. Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_layout_stability.py
"""

from __future__ import annotations

import datetime
import json


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
    page.route(
        "**/starred-state*",
        lambda route: _json(
            route, {"ok": True} if route.request.method == "POST" else {"starred": []}
        ),
    )
    page.route(
        "**/scrape-status*",
        lambda route: _json(route, {"scraped": scraped, "not_scraped": []}),
    )
    page.route(
        "**/viewed-state**",
        lambda route: _json(
            route, {"ok": True} if route.request.method == "POST" else {"viewed": []}
        ),
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


def _height(page, selector):
    return page.evaluate(
        "(sel) => document.querySelector(sel).getBoundingClientRect().height", selector
    )


def _sse(events: str):
    def handler(route):
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body=events,
        )

    return handler


def test_activity_strip_height_constant(page, app_server):
    """The strip must be pixel-identical across idle / progress / chip / error."""
    rel = _release(
        "https://s.bandcamp.com/album/a",
        _prev_month_day(15),
        "Aria",
        "One",
        "Alpha",
        1,
        embed="https://bandcamp.com/EmbeddedPlayer/album=1/size=large/",
    )
    _setup(page, app_server, releases=[rel], scraped=[_prev_month_day(15).isoformat()])
    _boot(page, app_server)
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    # Wait for the count/sort to render into the strip's reserved slot.
    page.wait_for_function(
        "() => (document.getElementById('activity-count').textContent || '').includes('sorted by')"
    )

    strip = "#activity-strip"
    # Details starts collapsed, so this is the true single-line baseline.
    h_idle = _height(page, strip)

    # (1) Fetching: unhide the progress overlay exactly as the run does.
    page.evaluate("() => { document.getElementById('activity-progress').hidden = false; }")
    h_progress = _height(page, strip)
    page.evaluate("() => { document.getElementById('activity-progress').hidden = true; }")

    # (2) Loading players: reveal the inline enrich chip as renderChip would.
    page.evaluate(
        """() => {
            document.getElementById('enrich-chip').hidden = false;
            document.getElementById('enrich-chip-label').textContent =
                '3 players not loaded yet';
            document.getElementById('enrich-load-all').hidden = false;
        }"""
    )
    h_chip = _height(page, strip)

    # (3) Progress + chip together (the worst combination).
    page.evaluate("() => { document.getElementById('activity-progress').hidden = false; }")
    h_combo = _height(page, strip)
    page.evaluate(
        """() => {
            document.getElementById('activity-progress').hidden = true;
            document.getElementById('enrich-chip').hidden = true;
            document.getElementById('enrich-load-all').hidden = true;
        }"""
    )

    # (4) Error: icon + line as handleTerminalError leaves them.
    page.evaluate(
        """() => {
            const icon = document.getElementById('activity-icon');
            icon.hidden = false;
            icon.classList.add('state-error');
            document.getElementById('activity-line').textContent = "Couldn't load releases.";
        }"""
    )
    h_error = _height(page, strip)

    for label, value in [
        ("progress", h_progress),
        ("chip", h_chip),
        ("progress+chip", h_combo),
        ("error", h_error),
    ]:
        assert abs(value - h_idle) < 0.5, (
            f"activity strip grew in the {label} state: {value} vs idle {h_idle}"
        )

    # A REAL run (mocked SSE, no terminal event) must not grow the strip either.
    page.route(
        "**/populate-range-stream*",
        _sse(
            'data: {"v":1,"phase":"download","current":30,"total":40,"text":"Downloading 30 of 40"}\n\n'
        ),
    )
    page.click("#populate-range")
    page.wait_for_function(
        "() => document.getElementById('progress-bar').getAttribute('aria-valuenow') === '75'"
    )
    h_real_run = _height(page, strip)
    assert abs(h_real_run - h_idle) < 0.5, (
        f"activity strip grew during a real run: {h_real_run} vs idle {h_idle}"
    )


def test_action_group_height_constant(page, app_server):
    """.action-group is identical across not-checked / partial / fully-checked."""
    rel = _release(
        "https://s.bandcamp.com/album/b",
        _prev_month_day(15),
        "Bex",
        "Two",
        "Bravo",
        2,
        embed="https://bandcamp.com/EmbeddedPlayer/album=2/size=large/",
    )
    # Days 10–20 are checked; 1–9 and 21–28 are not.
    scraped = [_prev_month_day(d).isoformat() for d in range(10, 21)]
    _setup(page, app_server, releases=[rel], scraped=scraped)
    _boot(page, app_server)

    group = ".action-group"

    # Fully-checked selection → "Check again" + the "Up to date" line visible.
    _select_range(page, _prev_month_day(10).isoformat(), _prev_month_day(20).isoformat())
    page.wait_for_function(
        "() => document.getElementById('populate-range').textContent.trim() === 'Check again'"
    )
    assert page.get_attribute("#up-to-date-line", "hidden") is None
    h_checked = _height(page, group)

    # Partially-checked selection → "Get releases", up-to-date hidden (reserved).
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.getElementById('populate-range').textContent.trim() === 'Get releases'"
    )
    assert page.get_attribute("#up-to-date-line", "hidden") is not None
    h_partial = _height(page, group)

    # Entirely-unchecked selection → same "Get releases" state.
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(5).isoformat())
    page.wait_for_function(
        "() => document.getElementById('populate-range').textContent.trim() === 'Get releases'"
    )
    h_unchecked = _height(page, group)

    assert abs(h_partial - h_checked) < 0.5, (
        f"action group changed height (partial {h_partial} vs checked {h_checked})"
    )
    assert abs(h_unchecked - h_checked) < 0.5, (
        f"action group changed height (unchecked {h_unchecked} vs checked {h_checked})"
    )
