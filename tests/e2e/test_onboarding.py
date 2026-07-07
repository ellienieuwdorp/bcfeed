"""WP-23 · onboarding — the first-run checklist regression net.

Drives the real app in headless Chromium against BOTH a FRESH (empty) data dir
and a seeded one, locking in the UXP-3/4/6/7 acceptance:

- a fresh data dir boots into the provider-branching checklist (both cards, honest
  time estimates) with NO modal and NO "No releases match" table;
- killing mid-setup + relaunching restores the DETECTED step (client config /
  token / IMAP config / data present) — exercised via mocked /config.json +
  /provider-config so no real credentials are ever needed;
- switching provider paths mid-setup loses nothing;
- the Gmail path pre-announces the consent tab, shows a supervised waiting state
  with Cancel, and is recoverable in one click — WITHOUT ever triggering real
  OAuth (the upload + status endpoints are stubbed);
- ✕ / backdrop never open the file picker; only "Choose access file…" does;
- the IMAP app-password requirement is VISIBLE text, not a hover title;
- the delete-data dialog enumerates scope, defaults the stars/seen checkbox OFF,
  and a default delete keeps stars (star → delete → re-fetch → star intact);
- one click from the post-connect state starts a 30-day fetch with the calendar
  visibly selected;
- Settings opens showing per-provider connection state with no action taken.

These tests launch their OWN app subprocesses (fresh + seeded) so they never
contaminate the session-scoped smoke/feedback/a11y data dir. Run with the
prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_onboarding.py
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_PYTHON = os.environ.get("BCFEED_APP_PYTHON", sys.executable)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(base_url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"app process exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"app never became healthy at {base_url}: {last_err}")


@contextlib.contextmanager
def _app(data_dir: Path):
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
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _seed(data_dir: Path) -> dict:
    first = datetime.date.today().replace(day=1)
    prev = (first - datetime.timedelta(days=1)).replace(day=1)
    date_a, date_b = prev.replace(day=10), prev.replace(day=20)
    url_a = "https://midnighttapes.bandcamp.com/album/neon-fields"
    url_b = "https://harbourlights.bandcamp.com/album/tidal"

    def rel(url, dt, artist, title, page, rid):
        return {
            "img_url": None,
            "date": dt.isoformat(),
            "artist": artist,
            "title": title,
            "page_name": page,
            "url": url,
            "release_id": rid,
            "is_track": False,
        }

    rel_a = rel(url_a, date_a, "Aria Vale", "Neon Fields", "Midnight Tapes", 111)
    rel_b = rel(url_b, date_b, "Coastal Static", "Tidal", "Harbour Lights", 222)
    (data_dir / "release_cache.json").write_text(
        json.dumps({date_a.isoformat(): [rel_a], date_b.isoformat(): [rel_b]}), encoding="utf-8"
    )
    (data_dir / "scrape_status.json").write_text(
        json.dumps([date_a.isoformat(), date_b.isoformat()]), encoding="utf-8"
    )
    (data_dir / "embed_cache.json").write_text(
        json.dumps(
            {
                url_a: {
                    "release_id": 111,
                    "is_track": False,
                    "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=111/size=large/",
                    "description": "A",
                },
                url_b: {
                    "release_id": 222,
                    "is_track": False,
                    "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=222/size=large/",
                    "description": "B",
                },
            }
        ),
        encoding="utf-8",
    )
    return {"url_a": url_a, "url_b": url_b, "rel_a": rel_a, "rel_b": rel_b}


@pytest.fixture(scope="module")
def _browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture(scope="module")
def fresh_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("bcfeed-onboarding-fresh")
    with _app(data_dir) as base_url:
        yield base_url


@pytest.fixture
def fresh_page(_browser, fresh_server):
    context = _browser.new_context()
    page = context.new_page()
    try:
        yield page, fresh_server
    finally:
        context.close()


@pytest.fixture
def seeded_page(_browser, tmp_path_factory):
    """A per-test server over its own seeded dir — the delete-data test mutates it."""
    data_dir = tmp_path_factory.mktemp("bcfeed-onboarding-seed")
    seed = _seed(data_dir)
    with _app(data_dir) as base_url:
        context = _browser.new_context()
        page = context.new_page()
        try:
            yield page, base_url, seed
        finally:
            context.close()


def _fulfill_json(route, payload):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def test_fresh_boot_shows_checklist(fresh_page):
    page, base = fresh_page
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")

    # Both provider cards, with honest time estimates.
    titles = page.eval_on_selector_all(
        ".provider-card .provider-card-title", "els => els.map(e => e.textContent.trim())"
    )
    times = page.eval_on_selector_all(
        ".provider-card .provider-card-time", "els => els.map(e => e.textContent.trim())"
    )
    assert any("IMAP" in t for t in titles), titles
    assert any("Google" in t for t in titles), titles
    assert any("5 min" in t for t in times), times
    assert any("20 min" in t for t in times), times

    # No "Credentials Needed" modal exists, and no modal is open.
    assert page.eval_on_selector_all("#missing-token-backdrop", "els => els.length") == 0
    for backdrop in (
        "settings-backdrop",
        "load-creds-backdrop",
        "delete-data-backdrop",
        "max-results-backdrop",
    ):
        disp = page.eval_on_selector(f"#{backdrop}", "e => getComputedStyle(e).display")
        assert disp == "none", (backdrop, disp)

    # The empty "No releases match" table is not shown — the checklist replaces it.
    assert page.eval_on_selector(".table-wrapper", "e => getComputedStyle(e).display") == "none"
    assert page.eval_on_selector("body", "e => e.classList.contains('has-onboarding')")


def test_imap_app_password_hint_is_visible_text(fresh_page):
    page, base = fresh_page
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")

    # Stated on the card (step 1) as visible text, not a hover tooltip.
    card_text = page.eval_on_selector('.provider-card[data-provider="imap"]', "e => e.textContent")
    assert "app-specific password" in card_text.lower()

    # And in the re-hosted panel (step 2): visible .help-text, no title= carrying it.
    page.click('.provider-card[data-provider="imap"]')
    page.wait_for_selector("#onboarding-imap-connect:not([hidden])")
    visible_hint = page.eval_on_selector_all(
        "#imap-config-panel .help-text",
        "els => els.map(e => e.textContent.trim()).filter(t => /app-specific password/i.test(t))",
    )
    assert visible_hint, "app-password requirement must be visible text in the IMAP panel"
    title_hint = page.eval_on_selector_all(
        "#imap-config-panel [title]",
        "els => els.map(e => e.getAttribute('title')).filter(t => t && /app-specific/i.test(t))",
    )
    assert title_hint == [], "app-password hint must not be a hover title"

    # The panel is genuinely re-hosted (moved), not rebuilt.
    assert page.eval_on_selector(
        "#onboarding-imap-slot", "e => !!e.querySelector('#imap-config-panel')"
    )


def test_switching_provider_paths_loses_nothing(fresh_page):
    page, base = fresh_page
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")

    page.click('.provider-card[data-provider="imap"]')
    page.wait_for_selector("#onboarding-imap-connect:not([hidden])")
    page.fill("#imap-host", "imap.example.com")
    page.fill("#imap-user", "me@example.com")
    page.fill("#imap-pass", "app-pass-123")

    # Bounce to the Gmail path and back.
    page.click("#onboarding-change-provider")
    page.wait_for_selector("#onboarding-step-1.is-current")
    page.click('.provider-card[data-provider="gmail"]')
    page.wait_for_selector("#onboarding-gmail-connect:not([hidden])")
    page.click("#onboarding-change-provider")
    page.wait_for_selector("#onboarding-step-1.is-current")
    page.click('.provider-card[data-provider="imap"]')
    page.wait_for_selector("#onboarding-imap-connect:not([hidden])")

    assert page.input_value("#imap-host") == "imap.example.com"
    assert page.input_value("#imap-user") == "me@example.com"
    assert page.input_value("#imap-pass") == "app-pass-123"


def _route_state(page, base, *, has_credentials, provider):
    _cfg = {
        "title": "bcfeed",
        "embed_proxy_url": f"{base}/embed-meta",
        "has_token": has_credentials and provider.get("provider") == "gmail",
        "has_credentials": has_credentials,
        "default_theme": "light",
        "clear_status_on_load": False,
        "show_dev_settings": False,
    }
    page.route("**/config.json", lambda r: _fulfill_json(r, _cfg))

    def _prov(route):
        if route.request.method == "GET":
            _fulfill_json(route, provider)
        else:
            _fulfill_json(route, {"ok": True})

    page.route("**/provider-config", _prov)


def test_detected_step_restores_on_relaunch(fresh_page):
    page, base = fresh_page

    imap_empty = {"host": "", "username": "", "folder": "", "use_ssl": True, "has_password": False}

    # (a) Gmail client config uploaded but no token yet → resume at step 2, Gmail.
    _route_state(
        page,
        base,
        has_credentials=False,
        provider={"provider": "gmail", "has_gmail_credentials": True, "imap_config": imap_empty},
    )
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding-step-2.is-current")
    assert page.eval_on_selector("#onboarding-gmail-connect", "e => !e.hidden")

    # (b) IMAP config partially entered → resume at step 2, IMAP.
    page.unroute("**/config.json")
    page.unroute("**/provider-config")
    _route_state(
        page,
        base,
        has_credentials=False,
        provider={
            "provider": "imap",
            "has_gmail_credentials": False,
            "imap_config": {
                "host": "imap.x",
                "username": "u",
                "folder": "",
                "use_ssl": True,
                "has_password": False,
            },
        },
    )
    page.reload()
    page.wait_for_selector("#onboarding-step-2.is-current")
    assert page.eval_on_selector("#onboarding-imap-connect", "e => !e.hidden")

    # (c) Fully connected, no data yet → step 3 (get first releases).
    page.unroute("**/config.json")
    page.unroute("**/provider-config")
    _route_state(
        page,
        base,
        has_credentials=True,
        provider={
            "provider": "imap",
            "has_gmail_credentials": False,
            "imap_config": {
                "host": "imap.x",
                "username": "u",
                "folder": "INBOX",
                "use_ssl": True,
                "has_password": True,
            },
        },
    )
    page.reload()
    page.wait_for_selector("#onboarding-step-3.is-current")


def test_gmail_consent_announced_and_supervised(fresh_page):
    page, base = fresh_page
    # Stub the connect endpoints so real Google OAuth is NEVER triggered.
    page.route(
        "**/load-credentials",
        lambda r: _fulfill_json(r, {"ok": True, "status": "waiting", "logs": []}),
    )
    page.route(
        "**/connect-status",
        lambda r: _fulfill_json(r, {"status": "waiting", "message": "waiting"}),
    )
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")

    page.click('.provider-card[data-provider="gmail"]')
    page.wait_for_selector("#onboarding-gmail-connect:not([hidden])")
    page.click("#onboarding-gmail-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display !== 'none'"
    )

    # The consent hop is announced BEFORE the file picker.
    intro = page.eval_on_selector('.load-creds-view[data-view="intro"]', "e => e.textContent")
    assert "Google sign-in tab" in intro
    assert "unverified" in intro

    # Choosing a file switches to the supervised waiting state with a Cancel.
    fake = REPO_ROOT / "tests" / "e2e" / "__pycache__" / "fake_client_secret.json"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text('{"installed":{"client_id":"x"}}', encoding="utf-8")
    page.set_input_files("#load-creds-file", str(fake))
    page.wait_for_selector('.load-creds-view[data-view="waiting"]:not([hidden])')
    assert page.eval_on_selector(
        "#load-creds-cancel", "e => getComputedStyle(e).display !== 'none'"
    )

    # Abandoning is recoverable in one click.
    page.click("#load-creds-cancel")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display === 'none'"
    )


def test_close_and_backdrop_never_open_file_picker(fresh_page):
    page, base = fresh_page
    choosers = {"n": 0}
    page.on("filechooser", lambda fc: choosers.__setitem__("n", choosers["n"] + 1))
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding:not([hidden])")
    page.click('.provider-card[data-provider="gmail"]')
    page.click("#onboarding-gmail-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display !== 'none'"
    )

    # Backdrop click closes without a picker.
    page.eval_on_selector("#load-creds-backdrop", "e => e.click()")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display === 'none'"
    )
    assert choosers["n"] == 0

    # ✕ closes without a picker.
    page.click("#onboarding-gmail-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display !== 'none'"
    )
    page.click("#load-creds-close")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display === 'none'"
    )
    assert choosers["n"] == 0

    # Only "Choose access file…" opens the picker.
    page.click("#onboarding-gmail-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('load-creds-backdrop')).display !== 'none'"
    )
    page.click("#load-creds-continue")
    page.wait_for_timeout(300)
    assert choosers["n"] == 1


def test_post_connect_30_day_starter(fresh_page):
    page, base = fresh_page
    _route_state(
        page,
        base,
        has_credentials=True,
        provider={
            "provider": "imap",
            "has_gmail_credentials": False,
            "imap_config": {
                "host": "imap.x",
                "username": "u",
                "folder": "INBOX",
                "use_ssl": True,
                "has_password": True,
            },
        },
    )
    pop_hits = {"n": 0}

    def _pop(route):
        pop_hits["n"] += 1
        route.fulfill(
            status=200,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
            body='event: done\ndata: {"v":1,"new_releases":0}\n\n',
        )

    page.route("**/populate-range-stream*", _pop)
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#onboarding-step-3.is-current")

    page.click("#onboarding-check-30")
    # The calendar visibly reflects the selection before the run.
    page.wait_for_function(
        "() => document.querySelectorAll('#calendar-range .calendar-day.in-range').length > 0"
    )
    assert pop_hits["n"] == 1, "one click must start exactly one fetch"

    # 30-day window: yesterday-29 .. yesterday.
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=29)
    assert page.input_value("#date-filter-from") == start.isoformat()
    assert page.input_value("#date-filter-to") == end.isoformat()


def test_delete_data_dialog_keeps_stars_by_default(seeded_page):
    page, base, seed = seeded_page
    url_a = seed["url_a"]
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    # Data present → no checklist.
    assert page.eval_on_selector("#onboarding", "e => e.hidden")

    # Star url_a (persists server-side).
    row = f'#release-rows tr.data-row[data-key="{url_a}"]'
    page.eval_on_selector(row, "e => e.focus()")
    page.keyboard.press("s")
    page.wait_for_function(
        "(sel) => document.querySelector(sel + ' [data-star-btn]')"
        ".getAttribute('aria-pressed') === 'true'",
        arg=row,
    )

    # Open the confirm dialog from Settings.
    page.click("#settings-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('settings-backdrop')).display !== 'none'"
    )
    assert "button-danger" in page.get_attribute("#settings-reset", "class")
    page.click("#settings-reset")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('delete-data-backdrop')).display !== 'none'"
    )

    # Scope enumerated; stars/seen checkbox defaults OFF; label reflects scope.
    body_text = page.eval_on_selector("#delete-data-backdrop", "e => e.textContent")
    assert "releases" in body_text and "players" in body_text
    assert page.eval_on_selector("#delete-data-include-stars", "e => e.checked") is False
    assert page.eval_on_selector("#delete-data-confirm", "e => e.textContent.trim()") == (
        "Delete downloaded data"
    )
    page.check("#delete-data-include-stars")
    assert page.eval_on_selector("#delete-data-confirm", "e => e.textContent.trim()") == (
        "Delete everything"
    )
    page.uncheck("#delete-data-include-stars")

    # Confirm the DEFAULT delete (keep stars/seen).
    page.click("#delete-data-confirm")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('delete-data-backdrop')).display === 'none'"
    )

    # Server: the star survived; the release data is gone.
    starred = page.evaluate("async () => (await (await fetch('/starred-state')).json()).starred")
    assert url_a in starred, starred
    rel_count = page.evaluate(
        "async () => (await (await fetch('/releases')).json()).releases.length"
    )
    assert rel_count == 0, rel_count

    # A post-action toast reported what was removed.
    toasts = page.eval_on_selector_all("#toast-region .toast", "els => els.map(e => e.textContent)")
    assert any("stars and history were kept" in t for t in toasts), toasts

    # Re-fetch (mocked) the same releases → the star is intact on the restored row.
    page.route(
        "**/releases",
        lambda r: _fulfill_json(r, {"releases": [seed["rel_a"], seed["rel_b"]]}),
    )
    page.reload()
    page.wait_for_selector("#release-rows tr.data-row")
    page.wait_for_function(
        "(sel) => { const row = document.querySelector(sel);"
        " return !!row && row.classList.contains('starred'); }",
        arg=row,
    )


def test_settings_shows_connection_state_without_action(seeded_page):
    page, base, seed = seeded_page
    page.goto(f"{base}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")

    page.click("#settings-btn")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('settings-backdrop')).display !== 'none'"
    )
    # The connection-status line is first in the Email section and reflects state
    # with NO action taken (this dir has no provider configured → Not connected).
    status_text = page.eval_on_selector(
        "#settings-connection-status .connection-text", "e => e.textContent.trim()"
    )
    assert "Not connected" in status_text, status_text
    assert page.eval_on_selector(
        "#settings-connection-status", "e => e.classList.contains('is-disconnected')"
    )

    # Switching the provider select updates the per-provider status line.
    page.select_option("#provider-select", "imap")
    page.wait_for_timeout(150)
    imap_status = page.eval_on_selector(
        "#settings-connection-status .connection-text", "e => e.textContent.trim()"
    )
    assert "Not connected" in imap_status, imap_status
