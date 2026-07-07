"""WP-25 · table & calendar flows — the UXP-14/15/16/17/18/12 regression net.

Drives the real app in headless Chromium (reusing the session app subprocess +
seeded data dir from conftest) and mocks the network with ``page.route`` so the
specs are isolated from — and never contaminate — the session-shared seen/starred
state. Locks in:

- three distinct empty states, each with its own copy + action, and the removal
  of "No releases match the current filter." from a fresh install (UXP-14);
- the single-column label filter: exclusion-set persistence across a range
  change (JS-5), one-click ``only``, and All / None (UXP-15);
- mark-seen honesty: a live count equal to the affected visible rows under active
  filters, exactly ONE batch write, and an undo that restores the EXACT prior
  per-row seen set — verified via a stateful /viewed-state mock (UXP-16);
- a persistent count/filter status line that survives load / sort / filter / mark
  and a chip that names active filters (UXP-16);
- the four calendar states (checked, new releases, selected, unchecked-in-
  selection) with the gap the loudest, and a legend built from real day cells
  (UXP-17);
- the calendar button reads "Latest" and today's cell carries an explanatory
  tooltip/aria (UXP-18);
- a "?" help listing exactly the implemented keyboard shortcuts (UXP-12).

Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_table_calendar.py
"""

from __future__ import annotations

import datetime
import json


# --- fixtures ---------------------------------------------------------------
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


def _setup(page, base, *, releases, scraped=None, viewed_seed=None, has_credentials=True):
    """Route the boot endpoints. /viewed-state is STATEFUL so an undo can be
    verified by reading the set back (the acceptance's "verify via /viewed-state"),
    all without touching the session-shared server data dir."""
    scraped = scraped or []
    state = {"viewed": set(viewed_seed or []), "batch_posts": 0}

    page.route(
        "**/config.json*",
        lambda route: _json(
            route,
            {
                "title": "bcfeed",
                "embed_proxy_url": f"{base}/embed-meta",
                "has_credentials": has_credentials,
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
            route,
            {"ok": True} if route.request.method == "POST" else {"starred": []},
        ),
    )
    page.route(
        "**/scrape-status*",
        lambda route: _json(route, {"scraped": scraped, "not_scraped": []}),
    )

    def viewed_handler(route):
        req = route.request
        if req.method == "GET":
            _json(route, {"viewed": sorted(state["viewed"])})
            return
        body = json.loads(req.post_data or "{}")
        if "urls" in body:  # WP-17 batch write
            state["batch_posts"] += 1
            for u in body["urls"]:
                if body.get("viewed"):
                    state["viewed"].add(u)
                else:
                    state["viewed"].discard(u)
        else:  # single toggle
            if body.get("read"):
                state["viewed"].add(body["url"])
            else:
                state["viewed"].discard(body["url"])
        _json(route, {"ok": True})

    # `**` (not `*`) so the glob spans the `/batch` sub-path too — a `*` would
    # not cross the slash, letting the batch write leak to the real server.
    page.route("**/viewed-state**", viewed_handler)
    return state


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


def _label_checked(page, label):
    return page.evaluate(
        """(label) => {
            const items = [...document.querySelectorAll('#label-filters .filter-item')];
            const item = items.find(it => it.querySelector('.filter-label')
                && it.querySelector('.filter-label').textContent === label);
            return item ? item.querySelector('.filter-checkbox').checked : null;
        }""",
        label,
    )


def _toggle_label(page, label):
    page.evaluate(
        """(label) => {
            const items = [...document.querySelectorAll('#label-filters .filter-item')];
            const item = items.find(it => it.querySelector('.filter-label')
                && it.querySelector('.filter-label').textContent === label);
            if (item) item.querySelector('.filter-checkbox').click();
        }""",
        label,
    )


# --- empty states (UXP-14) --------------------------------------------------
def test_three_empty_states_and_old_string_gone(page, app_server):
    a1 = _release("https://a.bandcamp.com/album/1", _prev_month_day(10), "AA", "One", "Alpha", 1)
    a2 = _release("https://a.bandcamp.com/album/2", _prev_month_day(12), "AB", "Two", "Alpha", 2)
    empty_day = _prev_month_day(5).isoformat()
    _setup(
        page,
        app_server,
        releases=[a1, a2],
        scraped=[_prev_month_day(10).isoformat(), _prev_month_day(12).isoformat(), empty_day],
    )
    _boot(page, app_server)

    # The fresh-install wrong-for-both string is gone from the app entirely.
    assert "No releases match the current filter" not in page.content()

    full_start = _prev_month_day(1).isoformat()
    full_end = _prev_month_day(28).isoformat()
    _select_range(page, full_start, full_end)
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 2"
    )

    # (b) Filters hide everything → its own copy + a Clear-filters action.
    page.click("#filter-none")
    page.wait_for_selector("#empty-state-action[data-empty-mode='clear']")
    assert "No releases match your filters" in page.text_content("#empty-state-title")
    assert (page.text_content("#empty-state-action") or "").strip() == "Clear filters"
    # The action restores the table (single click).
    page.click("#empty-state-action")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 2"
    )

    # (c) A checked range with zero releases → "No new releases …" + Check again.
    _select_range(page, empty_day, empty_day)
    page.wait_for_selector("#empty-state-action[data-empty-mode='fetch']")
    assert "No new releases" in page.text_content("#empty-state-title")
    assert (page.text_content("#empty-state-action") or "").strip() == "Check again"


def test_fresh_install_has_no_stale_empty_string(page, app_server):
    _setup(page, app_server, releases=[], has_credentials=False)
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")
    # First-run is the onboarding checklist — not an empty-table string.
    assert "No releases match the current filter" not in page.content()


# --- label filter (UXP-15 / JS-5) -------------------------------------------
def test_label_filter_persists_across_range_change(page, app_server):
    a = _release("https://x.bandcamp.com/album/a", _prev_month_day(10), "A", "a", "Alpha", 1)
    b = _release("https://x.bandcamp.com/album/b", _prev_month_day(11), "B", "b", "Bravo", 2)
    c = _release("https://x.bandcamp.com/album/c", _prev_month_day(15), "C", "c", "Charlie", 3)
    _setup(page, app_server, releases=[a, b, c])
    _boot(page, app_server)
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#label-filters .filter-item').length === 3"
    )

    # Uncheck two labels.
    _toggle_label(page, "Alpha")
    _toggle_label(page, "Bravo")
    assert _label_checked(page, "Alpha") is False
    assert _label_checked(page, "Bravo") is False

    # Navigate to a sub-range whose visible labels differ (only Charlie), then
    # back to the full range: the exclusion set survives the label-signature
    # change (the JS-5 bug reset it to "all").
    _select_range(page, _prev_month_day(15).isoformat(), _prev_month_day(15).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#label-filters .filter-item').length === 1"
    )
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#label-filters .filter-item').length === 3"
    )
    assert _label_checked(page, "Alpha") is False, "Alpha was re-checked by navigation (JS-5)"
    assert _label_checked(page, "Bravo") is False, "Bravo was re-checked by navigation (JS-5)"


def test_only_and_all_none(page, app_server):
    a = _release("https://x.bandcamp.com/album/a", _prev_month_day(10), "A", "a", "Alpha", 1)
    b = _release("https://x.bandcamp.com/album/b", _prev_month_day(11), "B", "b", "Bravo", 2)
    c = _release("https://x.bandcamp.com/album/c", _prev_month_day(12), "C", "c", "Charlie", 3)
    _setup(page, app_server, releases=[a, b, c])
    _boot(page, app_server)
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 3"
    )

    # "only" on Bravo → exactly Bravo's releases, in one click.
    page.evaluate(
        """() => {
            const items = [...document.querySelectorAll('#label-filters .filter-item')];
            const item = items.find(it => it.querySelector('.filter-label').textContent === 'Bravo');
            item.querySelector('.filter-only').click();
        }"""
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 1"
    )
    pages = page.eval_on_selector_all(
        "#release-rows tr.data-row", "els => els.map(e => e.dataset.page)"
    )
    assert set(pages) == {"Bravo"}, pages

    # All → everything back.
    page.click("#filter-all")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 3"
    )

    # None → empty table + the (b) filtered-empty state (never an auto-reset).
    page.click("#filter-none")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 0"
    )
    assert page.eval_on_selector("#empty-state", "e => getComputedStyle(e).display") != "none"
    assert "No releases match your filters" in page.text_content("#empty-state-title")


# --- mark seen honesty + undo (UXP-16) --------------------------------------
def test_mark_seen_count_batch_and_undo(page, app_server):
    a1 = _release("https://x.bandcamp.com/album/a1", _prev_month_day(10), "A", "a1", "Alpha", 1)
    a2 = _release("https://x.bandcamp.com/album/a2", _prev_month_day(11), "A", "a2", "Alpha", 2)
    b1 = _release("https://x.bandcamp.com/album/b1", _prev_month_day(12), "B", "b1", "Bravo", 3)
    # Prior per-row set is MIXED: a1 already seen, a2 unseen.
    state = _setup(page, app_server, releases=[a1, a2, b1], viewed_seed=[a1["url"]])
    _boot(page, app_server)
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 3"
    )

    # Hide Bravo → the count reflects the affected (visible) rows only.
    _toggle_label(page, "Bravo")
    page.wait_for_function(
        "() => document.getElementById('mark-seen').textContent.trim() === 'Mark 2 shown as seen'"
    )

    # Mark shown seen → exactly ONE batch write, over exactly the two Alpha urls.
    assert state["batch_posts"] == 0
    page.click("#mark-seen")
    page.wait_for_selector("#toast-region .toast .toast-action")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row.unseen').length === 0"
    )
    assert state["batch_posts"] == 1, state["batch_posts"]
    # Bravo (hidden) was NOT touched; both Alpha rows are now seen.
    assert state["viewed"] == {a1["url"], a2["url"]}, state["viewed"]

    # Undo restores the EXACT prior per-row set: a1 seen, a2 unseen (b1 untouched).
    page.click("#toast-region .toast .toast-action")
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            return !!row && row.classList.contains('unseen');
        }""",
        arg=a2["url"],
    )
    server_viewed = page.evaluate(
        "async () => (await (await fetch('/viewed-state')).json()).viewed"
    )
    assert set(server_viewed) == {a1["url"]}, server_viewed


def test_count_filter_status_line_survives(page, app_server):
    a = _release("https://x.bandcamp.com/album/a", _prev_month_day(10), "Zed", "a", "Alpha", 1)
    b = _release("https://x.bandcamp.com/album/b", _prev_month_day(11), "Ann", "b", "Bravo", 2)
    _setup(page, app_server, releases=[a, b])
    _boot(page, app_server)
    _select_range(page, _prev_month_day(1).isoformat(), _prev_month_day(28).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 2"
    )

    def count_text():
        return (page.text_content("#table-count") or "").strip()

    # load
    assert "2 releases" in count_text()
    assert "sorted by date" in count_text()
    # sort
    page.click('th[data-sort="artist"] .th-inner')
    assert "sorted by artist" in count_text()
    # filter → chip appears, count reflects the filter
    _toggle_label(page, "Bravo")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 1"
    )
    assert "filtered from 2" in count_text()
    assert page.get_attribute("#filter-chip", "hidden") is None
    assert "hidden" in (page.text_content("#filter-chip") or "").lower()
    # mark
    page.click("#mark-seen")
    page.wait_for_selector("#toast-region .toast")
    assert "1 release" in count_text()
    # Clear via the chip resets the filters (not the dates).
    page.click(".filter-chip-clear")
    page.wait_for_function(
        "() => document.querySelectorAll('#release-rows tr.data-row').length === 2"
    )
    assert "2 releases" in count_text()


# --- calendar coverage map (UXP-17) -----------------------------------------
def test_calendar_states_and_legend_from_real_cells(page, app_server):
    # A release with an unseen state on a checked day; a second checked day; and
    # unchecked days inside the selection (the gap).
    d_checked_new = _prev_month_day(10)
    d_checked = _prev_month_day(11)
    rel = _release("https://x.bandcamp.com/album/a", d_checked_new, "A", "a", "Alpha", 1)
    _setup(
        page,
        app_server,
        releases=[rel],
        scraped=[d_checked_new.isoformat(), d_checked.isoformat()],
    )
    _boot(page, app_server)
    _select_range(page, _prev_month_day(8).isoformat(), _prev_month_day(14).isoformat())
    page.wait_for_function(
        "() => document.querySelectorAll('#calendar-range .calendar-day.gap').length > 0"
    )

    counts = page.evaluate(
        """() => ({
            checked: document.querySelectorAll('#calendar-range .calendar-day.populated-day').length,
            selected: document.querySelectorAll('#calendar-range .calendar-day.in-range').length,
            gap: document.querySelectorAll('#calendar-range .calendar-day.gap').length,
            newrel: document.querySelectorAll('#calendar-range .calendar-day .dot.unseen').length,
        })"""
    )
    assert counts["checked"] >= 2, counts
    assert counts["selected"] >= 5, counts
    assert counts["gap"] >= 3, counts
    assert counts["newrel"] >= 1, counts

    # A combined cell (checked + selected + new releases) keeps all three channels.
    combined = page.evaluate(
        """(key) => {
            const c = document.querySelector(`#calendar-range .calendar-day[data-key="${key}"]`);
            if (!c) return null;
            return {
                checked: c.classList.contains('populated-day'),
                selected: c.classList.contains('in-range') || c.classList.contains('selected'),
                dot: !!c.querySelector('.dot.unseen'),
            };
        }""",
        d_checked_new.isoformat(),
    )
    assert combined == {"checked": True, "selected": True, "dot": True}, combined

    # The gap is the loudest: a dashed warn ring (not a quiet coverage number).
    gap_style = page.evaluate(
        """() => {
            const c = document.querySelector('#calendar-range .calendar-day.gap');
            const s = getComputedStyle(c);
            return { border: s.borderTopStyle };
        }"""
    )
    assert gap_style["border"] == "dashed", gap_style

    # The legend is built from REAL day-cell components carrying the state classes.
    legend = page.evaluate(
        """() => ({
            checked: !!document.querySelector('.calendar-legend .legend-cell.populated-day'),
            newrel: !!document.querySelector('.calendar-legend .legend-cell .dot.unseen'),
            selected: !!document.querySelector('.calendar-legend .legend-cell.in-range'),
            gap: !!document.querySelector('.calendar-legend .legend-cell.gap'),
        })"""
    )
    assert all(legend.values()), legend


# --- Latest button + today's-cell tooltip (UXP-18) --------------------------
def test_latest_button_and_today_tooltip(page, app_server):
    rel = _release("https://x.bandcamp.com/album/a", _prev_month_day(10), "A", "a", "Alpha", 1)
    _setup(page, app_server, releases=[rel])
    _boot(page, app_server)

    # The button no longer lies about selecting "Today".
    assert (page.text_content(".calendar-today-btn") or "").strip() == "Latest"

    # Navigate to the current month (next capped at the current month) so today's
    # cell is rendered, then assert it explains why it isn't selectable.
    page.click('[data-cal-nav="range-next"]')
    today_iso = datetime.date.today().isoformat()
    page.wait_for_selector(f'#calendar-range .calendar-day[data-key="{today_iso}"]')
    cell = page.evaluate(
        """(key) => {
            const c = document.querySelector(`#calendar-range .calendar-day[data-key="${key}"]`);
            return { title: c.getAttribute('title'), label: c.getAttribute('aria-label'),
                     disabled: c.classList.contains('disabled') };
        }""",
        today_iso,
    )
    assert cell["disabled"] is True, cell
    assert "still arriving" in (cell["title"] or ""), cell
    assert "not selectable yet" in (cell["label"] or ""), cell


# --- shortcuts help (UXP-12) ------------------------------------------------
def test_shortcuts_help_lists_exactly_implemented(page, app_server):
    rel = _release("https://x.bandcamp.com/album/a", _prev_month_day(10), "A", "a", "Alpha", 1)
    _setup(page, app_server, releases=[rel])
    _boot(page, app_server)

    # Not a dialog (the a11y dialog count must stay at four).
    assert page.locator("#shortcuts-help[role='dialog']").count() == 0

    # Open the "?" popover and read every listed key.
    page.click("#shortcuts-help .shortcuts-summary")
    page.wait_for_selector("#shortcuts-help[open] .shortcuts-panel")
    keys = page.eval_on_selector_all(
        "#shortcuts-help .shortcut kbd", "els => els.map(e => e.textContent.trim())"
    )
    # Every listed key is one the app actually implements; nothing else appears.
    implemented = {"↑", "↓", "←", "→", "Enter", "Space", "Shift", "Home", "End", "s", "u", "Esc"}
    assert set(keys) <= implemented, set(keys) - implemented
    # And the full triage/navigation loop is represented.
    for expected in ("↑", "↓", "Enter", "Space", "s", "u", "Esc"):
        assert expected in keys, (expected, keys)
