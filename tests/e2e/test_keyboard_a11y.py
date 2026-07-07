"""WP-21 · accessibility — the pointer-free keyboard + ARIA regression net.

Drives the real app in headless Chromium and exercises the WP-21 semantics with
NO pointer: arrow-key calendar traversal + Enter selection across a week
boundary, keyboard column sorting with a flipping ``aria-sort``, a focus-trapped
settings dialog that Escape closes and restores focus from, and keyboard row
triage. Plus static ARIA assertions (day-cell role/name/aria-pressed, dialog
markup) and an a11y sanity sweep (no positive tabindex; every interactive
control has an accessible name).

Run alongside the smoke test with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_keyboard_a11y.py
"""

from __future__ import annotations


def _dismiss_startup_modal(page):
    """The seeded data dir has no provider, so the informational credentials
    modal auto-opens and would trap Tab. Hide it the way a user dismissing it
    would leave the page; the modal manager self-heals its stale stack entry on
    the next keydown."""
    page.evaluate(
        """() => {
            const m = document.getElementById('missing-token-backdrop');
            if (m) m.style.display = 'none';
        }"""
    )


def _active(page):
    """A compact description of document.activeElement for assertions."""
    return page.evaluate(
        """() => {
            const a = document.activeElement;
            if (!a) return null;
            const day = a.classList && a.classList.contains('calendar-day')
                ? (a.querySelector('.date-label') || {}).textContent : null;
            return {
                tag: a.tagName,
                id: a.id || null,
                cls: a.className || '',
                day: day ? Number(day) : null,
                role: a.getAttribute && a.getAttribute('role'),
            };
        }"""
    )


def _focus(page, selector):
    """Programmatically land focus on an element — the same place a Tab would
    put it — then let the test drive it with the real keyboard."""
    ok = page.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.focus();
            return document.activeElement === el;
        }""",
        selector,
    )
    assert ok, f"could not focus {selector}"


def test_keyboard_calendar_grid(page, app_server, seed_data):
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    page.wait_for_function("document.querySelectorAll('#calendar-range .calendar-day').length > 0")
    _dismiss_startup_modal(page)

    # The grid is a real role=grid with exactly ONE tab stop (roving tabindex).
    grid_role = page.get_attribute("#calendar-range", "role")
    assert grid_role == "grid", grid_role
    tab_stops = page.eval_on_selector_all(
        "#calendar-range .calendar-day", "els => els.filter(e => e.tabIndex === 0).length"
    )
    assert tab_stops == 1, f"grid must have one roving tab stop, saw {tab_stops}"

    # Day cells expose role + accessible name + aria-pressed.
    sample = page.evaluate(
        """() => {
            const c = document.querySelector('#calendar-range .calendar-day[tabindex="0"]');
            return {
                role: c.getAttribute('role'),
                name: c.getAttribute('aria-label'),
                pressed: c.getAttribute('aria-pressed'),
                tag: c.tagName,
            };
        }"""
    )
    assert sample["tag"] == "BUTTON", sample
    assert sample["role"] == "gridcell", sample
    assert sample["name"] and any(ch.isdigit() for ch in sample["name"]), sample
    assert sample["pressed"] in ("true", "false"), sample

    # Land on the roving cell (as a Tab would), then navigate with arrows only.
    _focus(page, '#calendar-range .calendar-day[tabindex="0"]')
    start = _active(page)
    assert start["day"] is not None, start

    # Down = +7 days: crosses a week-row boundary deterministically.
    page.keyboard.press("ArrowDown")
    after_down = _active(page)
    assert after_down["day"] == start["day"] + 7, (start, after_down)

    # Right/Left move a single day and stay within the grid.
    page.keyboard.press("ArrowRight")
    assert _active(page)["day"] == start["day"] + 8
    page.keyboard.press("ArrowLeft")
    assert _active(page)["day"] == start["day"] + 7
    page.keyboard.press("ArrowUp")
    assert _active(page)["day"] == start["day"]

    # Enter selects the focused day; Shift+Enter after moving right extends the
    # range — the selected range then shows as in-range cells.
    page.keyboard.press("Enter")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Shift+Enter")
    in_range = page.eval_on_selector_all(
        "#calendar-range .calendar-day.in-range", "els => els.length"
    )
    assert in_range >= 3, f"keyboard range selection should span multiple days, saw {in_range}"
    pressed = page.eval_on_selector_all(
        "#calendar-range .calendar-day[aria-pressed='true']", "els => els.length"
    )
    assert pressed >= 3, f"selected cells must report aria-pressed=true, saw {pressed}"


def test_keyboard_sort_header(page, app_server, seed_data):
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    _dismiss_startup_modal(page)

    # aria-sort is present on every sortable column; the default sort is date.
    assert page.get_attribute('th[data-sort="date"]', "aria-sort") == "descending"
    assert page.get_attribute('th[data-sort="artist"]', "aria-sort") == "none"

    # Tab-land on the Artist header button and sort with Enter (no pointer).
    _focus(page, 'th[data-sort="artist"] .th-inner')
    page.keyboard.press("Enter")
    assert page.get_attribute('th[data-sort="artist"]', "aria-sort") == "ascending"
    # The previously-sorted column resets.
    assert page.get_attribute('th[data-sort="date"]', "aria-sort") == "none"
    # A second activation flips ascending → descending.
    page.keyboard.press("Enter")
    assert page.get_attribute('th[data-sort="artist"]', "aria-sort") == "descending"


def test_keyboard_settings_dialog_focus_trap(page, app_server, seed_data):
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    _dismiss_startup_modal(page)

    panel = page.locator("#settings-backdrop .settings-panel")
    assert panel.get_attribute("role") == "dialog"
    assert panel.get_attribute("aria-modal") == "true"
    assert panel.get_attribute("aria-labelledby") == "settings-title"

    # Open Settings from the gear via the keyboard.
    _focus(page, "#settings-btn")
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('settings-backdrop')).display !== 'none'"
    )
    # Focus moved INTO the dialog.
    assert page.evaluate(
        "() => document.querySelector('#settings-backdrop .settings-panel')"
        ".contains(document.activeElement)"
    )

    # Tab many times: focus never escapes the dialog (the trap holds both ways).
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate(
            "() => document.querySelector('#settings-backdrop .settings-panel')"
            ".contains(document.activeElement)"
        ), "Tab escaped the modal"
    for _ in range(4):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(
            "() => document.querySelector('#settings-backdrop .settings-panel')"
            ".contains(document.activeElement)"
        ), "Shift+Tab escaped the modal"

    # Escape closes and restores focus to the trigger (the gear).
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('settings-backdrop')).display === 'none'"
    )
    assert _active(page)["id"] == "settings-btn", _active(page)


def test_keyboard_row_triage(page, app_server, seed_data):
    url_a = seed_data["url_a"]
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    _dismiss_startup_modal(page)

    row_sel = f'#release-rows tr.data-row[data-key="{url_a}"]'
    # Rows carry aria-expanded; the read-dot is a real, named toggle button.
    assert page.get_attribute(row_sel, "aria-expanded") == "false"
    dot = page.evaluate(
        """(sel) => {
            const b = document.querySelector(sel + ' .row-dot');
            return { tag: b.tagName, name: b.getAttribute('aria-label'),
                     pressed: b.getAttribute('aria-pressed') };
        }""",
        row_sel,
    )
    assert dot["tag"] == "BUTTON" and dot["name"] and dot["pressed"] == "false", dot

    # Enter expands the row (opens the detail panel, flips aria-expanded).
    _focus(page, row_sel)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "(sel) => document.querySelector(sel).getAttribute('aria-expanded') === 'true'",
        arg=row_sel,
    )
    assert page.evaluate(
        "(sel) => { const d = document.querySelector(sel).nextElementSibling;"
        " return !!d && d.classList.contains('detail-row'); }",
        row_sel,
    )
    # Expanding marked it seen; 'u' restores unseen and re-shows the dot.
    _focus(page, row_sel)
    page.keyboard.press("u")
    page.wait_for_function(
        "(sel) => document.querySelector(sel).classList.contains('unseen')", arg=row_sel
    )
    assert page.get_attribute(f"{row_sel} .row-dot", "aria-pressed") == "false"
    # 's' stars it — the star button reflects the pressed state.
    _focus(page, row_sel)
    page.keyboard.press("s")
    page.wait_for_function(
        "(sel) => document.querySelector(sel + ' [data-star-btn]')"
        ".getAttribute('aria-pressed') === 'true'",
        arg=row_sel,
    )
    # The e2e app_server + seed_data fixtures are session-scoped, so starred /
    # viewed state persists to the shared data dir. Restore url_a to its unstarred
    # baseline so the (session-shared) smoke test's star-persistence step is not
    # contaminated by this run.
    _focus(page, row_sel)
    page.keyboard.press("s")
    page.wait_for_function(
        "(sel) => document.querySelector(sel + ' [data-star-btn]')"
        ".getAttribute('aria-pressed') === 'false'",
        arg=row_sel,
    )


def test_static_a11y_sanity(page, app_server, seed_data):
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    page.wait_for_function("document.querySelectorAll('#calendar-range .calendar-day').length > 0")
    _dismiss_startup_modal(page)

    # No positive tabindex anywhere (roving grids use 0/-1 only).
    positive = page.evaluate(
        """() => [...document.querySelectorAll('[tabindex]')]
            .map(e => e.tabIndex).filter(t => t > 0)"""
    )
    assert positive == [], f"found positive tabindex values: {positive}"

    # Every interactive control has an accessible name (text, aria-label, title,
    # or an associated <label>). Skip the intentionally-hidden sprite/inputs.
    unnamed = page.evaluate(
        """() => {
            const sel = 'button, a[href], [role="gridcell"], [role="dialog"]';
            const out = [];
            for (const el of document.querySelectorAll(sel)) {
                if (el.closest('.hidden')) continue;
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') continue;
                const labelledby = el.getAttribute('aria-labelledby');
                let name = (el.getAttribute('aria-label') || '').trim()
                    || (el.textContent || '').trim()
                    || (el.getAttribute('title') || '').trim();
                if (!name && labelledby) {
                    const t = document.getElementById(labelledby);
                    if (t) name = (t.textContent || '').trim();
                }
                if (!name) out.push(el.tagName + '.' + (el.className || '') + '#' + (el.id || ''));
            }
            return out;
        }"""
    )
    assert unnamed == [], f"interactive controls without an accessible name: {unnamed}"

    # Every modal is a role=dialog with aria-modal and an accessible name.
    dialogs = page.evaluate(
        """() => [...document.querySelectorAll('[role="dialog"]')].map(d => ({
            modal: d.getAttribute('aria-modal'),
            named: !!(d.getAttribute('aria-label') || d.getAttribute('aria-labelledby')),
        }))"""
    )
    assert len(dialogs) == 5, f"expected 5 dialogs, saw {len(dialogs)}"
    assert all(d["modal"] == "true" and d["named"] for d in dialogs), dialogs
