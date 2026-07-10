"""WPX-B · UIP-9 — theme-aware Bandcamp players.

Drives the real app in headless Chromium (session app subprocess + seeded data
dir from conftest) with the boot endpoints mocked via ``page.route`` so the spec
is isolated from the session-shared state and never hits Bandcamp. The player
iframe is cross-origin, so it can only follow the app theme through its
``bgcol=`` / ``linkcol=`` URL segments (``api.applyEmbedTheme``); this locks in:

- expanding a row in DARK theme yields an iframe whose src carries the dark
  surface bgcol (171b23) and the dark accent linkcol (52d0ff), INSERTED even
  though the source embed URL carried neither segment;
- toggling to LIGHT re-tints the OPEN player in place (bgcol ffffff, linkcol
  0b6e99) — the MutationObserver hook re-sets the src, no reload of the row;
- ``transparent=true`` survives both themes.

Run with the prebuilt Playwright venv, e.g.::

    BCFEED_APP_PYTHON=/path/to/.venv/bin/python \\
        /path/to/pwvenv/bin/python -m pytest tests/e2e/test_player_theming.py
"""

from __future__ import annotations

import datetime
import json


def _prev_month_day(day):
    first = datetime.date.today().replace(day=1)
    prev_first = (first - datetime.timedelta(days=1)).replace(day=1)
    return prev_first.replace(day=day)


def _json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


# A cached embed URL that carries NO bgcol/linkcol segments — proves the client
# INSERTS them (the seeded/real cache shape is exactly this bare form).
_BARE_EMBED = "https://bandcamp.com/EmbeddedPlayer/album=901/size=large/transparent=true/"


def _release():
    return {
        "img_url": None,
        "date": _prev_month_day(12).isoformat(),
        "artist": "Theme Test",
        "title": "Dark Surface",
        "page_name": "Calm Slate",
        "url": "https://themetest.bandcamp.com/album/dark-surface",
        "release_id": 901,
        "is_track": False,
        "embed_url": _BARE_EMBED,
        # description present ⇒ the expand path needs no /embed-meta fetch.
        "description": "A themed player.",
        "has_description": True,
    }


def _setup(page, base):
    page.route(
        "**/config.json*",
        lambda route: _json(
            route,
            {
                "title": "bcfeed",
                "embed_proxy_url": f"{base}/embed-meta",
                "has_credentials": True,
                "default_theme": "dark",
                "clear_status_on_load": False,
                "show_dev_settings": False,
            },
        ),
    )
    page.route("**/releases*", lambda route: _json(route, {"releases": [_release()]}))
    page.route("**/viewed-state*", lambda route: _json(route, {"viewed": []}))
    page.route(
        "**/starred-state*",
        lambda route: _json(
            route, {"ok": True} if route.request.method == "POST" else {"starred": []}
        ),
    )
    page.route(
        "**/scrape-status*",
        lambda route: _json(route, {"scraped": [], "not_scraped": []}),
    )
    # Defensive: the expand path shouldn't need /embed-meta (description present),
    # but keep it off the network if it ever does.
    page.route(
        "**/embed-meta*",
        lambda route: _json(
            route,
            {"release_id": 901, "is_track": False, "embed_url": _BARE_EMBED, "description": "x"},
        ),
    )


def _iframe_src(page):
    return page.get_attribute("tr.detail-row iframe", "src") or ""


def test_player_follows_theme_and_retints_on_toggle(page, app_server):
    # Boot in DARK: seed the persisted theme before any app script runs.
    page.add_init_script("localStorage.setItem('bc_dashboard_theme', 'dark');")
    _setup(page, app_server)
    page.goto(f"{app_server}/dashboard")
    page.wait_for_selector("#release-rows tr.data-row")
    page.evaluate(
        "() => { const m = document.getElementById('missing-token-backdrop');"
        " if (m) m.style.display = 'none'; }"
    )
    assert page.evaluate("() => !document.body.classList.contains('theme-light')")

    # Expand the row → the player loads with the DARK theme colours INSERTED.
    page.locator("#release-rows tr.data-row").first.click()
    page.wait_for_selector("tr.detail-row iframe")
    page.wait_for_function(
        "() => (document.querySelector('tr.detail-row iframe').src || '').includes('bgcol=171b23')"
    )
    dark_src = _iframe_src(page)
    assert "bgcol=171b23" in dark_src, dark_src  # --surface (dark)
    assert "linkcol=52d0ff" in dark_src, dark_src  # --accent (dark)
    assert "transparent=true" in dark_src, dark_src
    assert "bgcol=ffffff" not in dark_src, dark_src

    # Toggle to LIGHT via the real theme control → the OPEN player re-tints in
    # place (the MutationObserver hook re-sets the src; no manual re-expand).
    page.evaluate(
        "() => { const t = document.getElementById('theme-toggle');"
        " t.checked = false; t.dispatchEvent(new Event('change', { bubbles: true })); }"
    )
    page.wait_for_function("() => document.body.classList.contains('theme-light')")
    page.wait_for_function(
        "() => (document.querySelector('tr.detail-row iframe').src || '').includes('bgcol=ffffff')"
    )
    light_src = _iframe_src(page)
    assert "bgcol=ffffff" in light_src, light_src  # --surface (light)
    assert "linkcol=0b6e99" in light_src, light_src  # --accent (light)
    assert "transparent=true" in light_src, light_src
    assert "bgcol=171b23" not in light_src, light_src
