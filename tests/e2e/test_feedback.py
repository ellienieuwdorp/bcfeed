"""WP-22 · feedback primitives — progress, toasts, banners, Details, server-down.

Drives the real app in headless Chromium to lock in the UXP-2/UXP-13/UXP-19/
UXP-20 acceptance:

- populate WITHOUT credentials → a persistent, actionable banner that SURVIVES
  calendar clicks and filter changes (the exact JS-3 overwrite path);
- a determinate progress bar during the download phase (aria-valuenow advances),
  and a calendar interaction mid-run that cannot erase progress or re-enable the
  primary button;
- a completion toast ("Added N releases · …") on `event: done`;
- a transient mid-run disconnect that produces NO failure banner and still ends
  in the normal completion toast;
- the full-screen server-down backdrop is gone; the activity strip + Details
  disclosure (collapsed + empty by default) replace the raw Status LOG BOX.

The completion / progress / disconnect cases mock the SSE stream with
``page.route`` (the seeded data dir has no email provider, so a real run can only
produce the auth error — which is exactly what the banner-persistence case
wants). Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_feedback.py
"""

from __future__ import annotations


def _dismiss_startup_modal(page):
    """Hide the no-provider credentials modal so it stops intercepting clicks."""
    page.evaluate(
        """() => {
            const m = document.getElementById('missing-token-backdrop');
            if (m) m.style.display = 'none';
        }"""
    )


def _sse(events: str):
    """Build a page.route handler that fulfils an SSE request with a fixed body."""

    def handler(route):
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body=events,
        )

    return handler


def _boot(page, app_server):
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    _dismiss_startup_modal(page)


def test_server_down_backdrop_gone_and_activity_strip(page, app_server, seed_data):
    _boot(page, app_server)

    # The full-screen server-down modal is deleted (UXP-20 / JS-2-finish).
    assert page.locator("#server-down-backdrop").count() == 0

    # The activity strip replaced the raw 200px Status LOG BOX.
    assert page.locator("#activity-strip").count() == 1

    # Details disclosure: collapsed + empty by default (UI-9-replacement).
    detail = page.locator("#populate-log")
    assert detail.get_attribute("hidden") is not None, "Details must start collapsed"
    assert (detail.text_content() or "").strip() == "", "Details must start empty"
    assert page.get_attribute("#status-toggle", "aria-expanded") == "false"

    # Toggling the disclosure opens it (aria-expanded flips, content unhidden).
    page.click("#status-toggle")
    page.wait_for_function(
        "() => document.getElementById('populate-log').hasAttribute('hidden') === false"
    )
    assert page.get_attribute("#status-toggle", "aria-expanded") == "true"


def test_populate_without_credentials_persistent_banner(page, app_server, seed_data):
    _boot(page, app_server)

    banner = '#banner-region [data-banner-id="populate-error"]'

    # No provider is configured, so a real populate ends in a typed auth error.
    page.click("#populate-range")
    page.wait_for_selector(banner)
    text = page.locator(f"{banner} .banner-message").text_content()
    assert "Connect your email" in text, text
    # It is actionable (a Connect action) and assertive (role=alert).
    assert page.locator(f"{banner} .banner-action").count() == 1
    assert page.get_attribute(banner, "role") == "alert"

    # JS-3 overwrite path: clicking a calendar day must NOT erase the banner.
    page.evaluate(
        """() => {
            const cell = document.querySelector(
                '#calendar-range .calendar-day:not(.other-month):not(.disabled)');
            if (cell) cell.click();
        }"""
    )
    page.wait_for_timeout(100)
    assert page.locator(banner).count() == 1, "banner erased by a calendar click"

    # …and neither must toggling the date filter.
    page.click("#filter-by-date")
    page.wait_for_timeout(100)
    assert page.locator(banner).count() == 1, "banner erased by a filter change"

    # It is dismissible and stays gone once dismissed.
    page.click(f"{banner} .banner-dismiss")
    page.wait_for_selector(banner, state="detached")


def test_determinate_progress_and_button_locked_mid_run(page, app_server, seed_data):
    _boot(page, app_server)

    # A progress-only stream (no terminal event): the run stays in flight so the
    # button stays locked and the bar stays visible + determinate.
    page.route(
        "**/populate-range-stream*",
        _sse(
            'data: {"v":1,"phase":"query","current":0,"total":1,"text":"Searching your mail…"}\n\n'
            'data: {"v":1,"phase":"download","current":10,"total":40,"text":"Downloading 10 of 40"}\n\n'
            'data: {"v":1,"phase":"download","current":30,"total":40,"text":"Downloading 30 of 40"}\n\n'
        ),
    )
    page.click("#populate-range")

    # Determinate during download: aria-valuenow reflects 30/40 = 75%.
    page.wait_for_function(
        "() => document.getElementById('progress-bar').getAttribute('aria-valuenow') === '75'"
    )
    assert page.get_attribute("#activity-progress", "hidden") is None, "progress bar hidden mid-run"

    # The primary button is a single-owner: it is disabled + "Checking for releases…".
    assert page.get_attribute("#populate-range", "disabled") is not None
    assert (page.text_content("#populate-range") or "").strip() == "Checking for releases…"

    # Calendar interaction mid-run cannot erase progress or re-enable the button.
    page.evaluate(
        """() => {
            const cell = document.querySelector(
                '#calendar-range .calendar-day:not(.other-month):not(.disabled)');
            if (cell) cell.click();
        }"""
    )
    page.wait_for_timeout(150)
    assert page.get_attribute("#progress-bar", "aria-valuenow") == "75", (
        "calendar click erased progress"
    )
    assert page.get_attribute("#populate-range", "disabled") is not None, (
        "calendar click re-enabled the button"
    )


def test_completion_toast(page, app_server, seed_data):
    _boot(page, app_server)

    page.route(
        "**/populate-range-stream*",
        _sse(
            'data: {"v":1,"phase":"download","current":40,"total":40,"text":"Downloading 40 of 40"}\n\n'
            'event: done\ndata: {"new_releases":3,"days_scraped":2}\n\n'
        ),
    )
    page.click("#populate-range")

    page.wait_for_selector("#toast-region .toast")
    toast_text = page.locator("#toast-region .toast .toast-message").first.text_content()
    assert "Added 3 releases" in toast_text, toast_text
    # A successful completion never leaves a failure banner.
    assert page.locator('#banner-region [data-banner-id="populate-error"]').count() == 0
    # The button is released after completion.
    page.wait_for_function(
        "() => document.getElementById('populate-range').disabled === false"
        " || document.getElementById('populate-range').textContent.trim() !== 'Checking for releases…'"
    )


def test_transient_disconnect_is_not_a_failure(page, app_server, seed_data):
    _boot(page, app_server)

    # First connection ends mid-run without a terminal event (a transient blip);
    # EventSource reconnects (retry: 200ms) and the second connection completes.
    calls = {"n": 0}

    def handler(route):
        calls["n"] += 1
        if calls["n"] == 1:
            body = (
                "retry: 200\n"
                'data: {"v":1,"phase":"download","current":5,"total":10,"text":"Downloading 5 of 10"}\n\n'
            )
        else:
            body = 'event: done\ndata: {"new_releases":2,"days_scraped":1}\n\n'
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body=body,
        )

    page.route("**/populate-range-stream*", handler)
    page.click("#populate-range")

    # The blip resolves into a truthful completion toast — never a failure banner.
    page.wait_for_selector("#toast-region .toast", timeout=8000)
    toast_text = page.locator("#toast-region .toast .toast-message").first.text_content()
    assert "Added 2 releases" in toast_text, toast_text
    assert page.locator('#banner-region [data-banner-id="populate-error"]').count() == 0, (
        "a transient disconnect wrongly raised a failure banner"
    )
    assert calls["n"] >= 2, "the stream never reconnected after the blip"
