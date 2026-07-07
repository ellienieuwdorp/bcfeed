"""WP-03b · ARC-7c Playwright smoke — the regression net for the do-not-break list.

Drives the real app in headless Chromium against a seeded data dir and asserts
the reachable "verified strengths": the fast table renders, a row expands, a
star persists across a reload, a calendar day filters, the keyboard triage
shortcuts (s / u) work, and every mutating request carries the anti-CSRF header.

Runs headless in CI (the CI job installs Playwright + chromium). Locally, run it
with the prebuilt Playwright venv and point the app subprocess at a Flask-capable
interpreter, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_smoke.py
"""

from __future__ import annotations


def _mutating_header_log(page):
    """Collect the anti-CSRF header seen on every mutating (non-GET) request."""
    seen: list[tuple[str, str, str | None]] = []

    def _on_request(req):
        if req.method in ("GET", "HEAD"):
            return
        seen.append((req.method, req.url, req.headers.get("x-bcfeed-request")))

    page.on("request", _on_request)
    return seen


def _row(page, url: str):
    return page.locator(f'#release-rows tr.data-row[data-key="{url}"]')


def test_smoke_do_not_break_list(page, app_server, seed_data):
    header_log = _mutating_header_log(page)
    url_a = seed_data["url_a"]
    url_b = seed_data["url_b"]

    page.goto(f"{app_server}/dashboard")

    # 1. Dashboard loads and rows render (fast no-framework table).
    page.wait_for_selector("#release-rows tr.data-row")
    page.wait_for_function("document.querySelectorAll('#release-rows tr.data-row').length === 2")
    assert _row(page, url_a).count() == 1
    assert _row(page, url_b).count() == 1

    # The seeded data dir has no configured provider, so the app shows its
    # informational "no credentials" modal over the page. Dismiss it (as a user
    # would) so it stops intercepting pointer events — the modal itself is not
    # part of the do-not-break list under test here.
    page.evaluate(
        """() => {
            const m = document.getElementById('missing-token-backdrop');
            if (m) m.style.display = 'none';
        }"""
    )

    # 2. Expand a row → a detail row with the cached embed iframe appears.
    _row(page, url_a).click()
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            if (!row || !row.classList.contains('expanded')) return false;
            const detail = row.nextElementSibling;
            return !!(detail && detail.classList.contains('detail-row'));
        }""",
        arg=url_a,
    )
    page.wait_for_selector("tr.detail-row iframe")

    # 3. Keyboard triage: expanding marked the row seen; 'u' restores unseen.
    _row(page, url_a).focus()
    page.keyboard.press("u")
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            return !!row && row.classList.contains('unseen');
        }""",
        arg=url_a,
    )

    # 3b. WP-18 render discipline: a single seen-toggle mutates the row in place
    # and must NOT rebuild the tbody (node-identity) or run a table render — only
    # the calendar re-renders. Capture the live node + reset the render counters,
    # toggle, then assert the same node object is still connected.
    page.evaluate(
        """(url) => {
            window.__smokeRowRef = document.querySelector(
                `#release-rows tr.data-row[data-key="${url}"]`);
            window.__bcfeedRenderCounts.table = 0;
            window.__bcfeedRenderCounts.calendar = 0;
        }""",
        arg=url_a,
    )
    _row(page, url_a).focus()
    page.keyboard.press("u")
    page.wait_for_timeout(50)
    identity = page.evaluate(
        """(url) => {
            const cur = document.querySelector(
                `#release-rows tr.data-row[data-key="${url}"]`);
            return {
                identical: cur === window.__smokeRowRef,
                connected: !!window.__smokeRowRef && window.__smokeRowRef.isConnected,
                tableRenders: window.__bcfeedRenderCounts.table,
            };
        }""",
        arg=url_a,
    )
    assert identity["identical"] and identity["connected"], identity
    assert identity["tableRenders"] == 0, f"a single toggle rebuilt the tbody: {identity}"

    # 4. Keyboard 's' stars the focused row.
    _row(page, url_a).focus()
    page.keyboard.press("s")
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            if (!row || !row.classList.contains('starred')) return false;
            const btn = row.querySelector('[data-star-btn]');
            return !!btn && btn.getAttribute('aria-pressed') === 'true';
        }""",
        arg=url_a,
    )

    # 5. The star persists across a full reload (server owns starred state).
    page.reload()
    page.wait_for_selector("#release-rows tr.data-row")
    page.wait_for_function(
        """(url) => {
            const row = document.querySelector(`#release-rows tr.data-row[data-key="${url}"]`);
            return !!row && row.classList.contains('starred');
        }""",
        arg=url_a,
    )

    # 6. A calendar day filters the table down to that day's release.
    page.wait_for_function("document.querySelectorAll('#calendar-range .calendar-day').length > 0")
    day_a = seed_data["date_a"].day
    clicked = page.evaluate(
        """(day) => {
            const cells = [...document.querySelectorAll(
                '#calendar-range .calendar-day:not(.other-month):not(.disabled)')];
            const cell = cells.find(
                (c) => c.querySelector('.date-label') &&
                       c.querySelector('.date-label').textContent === String(day));
            if (!cell) return false;
            cell.click();
            return true;
        }""",
        day_a,
    )
    assert clicked, "could not find the calendar cell for the seeded day"
    page.wait_for_function("document.querySelectorAll('#release-rows tr.data-row').length === 1")
    assert _row(page, url_a).count() == 1
    assert _row(page, url_b).count() == 0

    # 7. Every mutating request carried the anti-CSRF header (star-triggers-POST).
    starred_posts = [h for (m, u, h) in header_log if "/starred-state" in u and m == "POST"]
    assert starred_posts, "starring never issued a /starred-state POST"
    assert all(h == "1" for h in starred_posts), (
        f"a mutating request rode without the anti-CSRF header: {header_log}"
    )
    # No mutating request anywhere in the run was header-less.
    assert all(h == "1" for (_m, _u, h) in header_log), header_log

    # 8. WP-18 render/ownership discipline: mark-all-seen mutates every visible
    # row's state, persists the whole set with ONE batch POST, and coalesces to
    # at most two renders (one table + one calendar) — never one POST/render per
    # row (PERF-1/JS-11/ARCH-6).
    page.evaluate(
        """() => {
            window.__bcfeedRenderCounts.table = 0;
            window.__bcfeedRenderCounts.calendar = 0;
        }"""
    )
    before_batch = len(
        [u for (m, u, _h) in header_log if "/viewed-state/batch" in u and m == "POST"]
    )
    page.evaluate("() => document.getElementById('mark-seen').click()")
    page.wait_for_timeout(150)
    counts = page.evaluate("() => ({ ...window.__bcfeedRenderCounts })")
    assert counts["table"] <= 1, f"mark-all-seen ran more than one table render: {counts}"
    assert counts["calendar"] <= 1, f"mark-all-seen ran more than one calendar render: {counts}"
    after_batch = len(
        [u for (m, u, _h) in header_log if "/viewed-state/batch" in u and m == "POST"]
    )
    assert after_batch - before_batch == 1, (
        f"mark-all-seen must issue exactly one batch POST, saw {after_batch - before_batch}"
    )
