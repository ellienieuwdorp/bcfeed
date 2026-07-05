"""WP-03b · ARC-7c Playwright smoke — its own launch substrate.

This conftest is scoped to ``tests/e2e/`` and is deliberately separate from the
unit ``tests/conftest.py`` (which stays hermetic). It launches the *real* app as
a subprocess on an ephemeral port against a throwaway, seeded ``BCFEED_DATA_DIR``
so the browser drives the same server a user would — no in-process patching, no
network (the one cached embed means no Bandcamp scrape is ever triggered).

Interpreter split: the process that runs pytest here needs Playwright; the app
subprocess needs Flask + the app deps. In CI a single venv has both, so the
default ``sys.executable`` works. Locally the Playwright venv and the app venv
differ, so set ``BCFEED_APP_PYTHON`` to the interpreter that can import the app
(e.g. the project ``.venv``); it defaults to ``sys.executable``.
"""

from __future__ import annotations

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


def _seed_dates() -> tuple[datetime.date, datetime.date]:
    """Two dates in the previous calendar month.

    The month before today is entirely in the past, so both days are always
    selectable in the calendar (which blocks today and the future) regardless of
    when the suite runs.
    """
    first_of_month = datetime.date.today().replace(day=1)
    prev_first = (first_of_month - datetime.timedelta(days=1)).replace(day=1)
    return prev_first.replace(day=10), prev_first.replace(day=20)


def _release(url, date, artist, title, page_name, release_id, is_track=False):
    return {
        "img_url": None,
        "date": date.isoformat(),
        "artist": artist,
        "title": title,
        "page_name": page_name,
        "url": url,
        "release_id": release_id,
        "is_track": is_track,
    }


@pytest.fixture(scope="session")
def seed_data():
    """The releases the smoke seeds, so the test can assert against them."""
    date_a, date_b = _seed_dates()
    url_a = "https://midnighttapes.bandcamp.com/album/neon-fields"
    url_b = "https://harbourlights.bandcamp.com/album/tidal"
    rel_a = _release(url_a, date_a, "Aria Vale", "Neon Fields", "Midnight Tapes", 111)
    rel_b = _release(url_b, date_b, "Coastal Static", "Tidal", "Harbour Lights", 222)
    return {
        "date_a": date_a,
        "date_b": date_b,
        "url_a": url_a,
        "url_b": url_b,
        "rel_a": rel_a,
        "rel_b": rel_b,
    }


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
        except (urllib.error.URLError, ConnectionError, OSError) as exc:  # not up yet
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"app never became healthy at {base_url}: {last_err}")


@pytest.fixture(scope="session")
def app_server(tmp_path_factory, seed_data):
    """Launch the app on an ephemeral port over a seeded temp data dir."""
    data_dir = tmp_path_factory.mktemp("bcfeed-e2e-data")

    (data_dir / "release_cache.json").write_text(
        json.dumps(
            {
                seed_data["date_a"].isoformat(): [seed_data["rel_a"]],
                seed_data["date_b"].isoformat(): [seed_data["rel_b"]],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "scrape_status.json").write_text(
        json.dumps([seed_data["date_a"].isoformat(), seed_data["date_b"].isoformat()]),
        encoding="utf-8",
    )
    # One real cached embed per release so the UI never needs to scrape Bandcamp
    # (ensureEmbed short-circuits on a present embed_url).
    (data_dir / "embed_cache.json").write_text(
        json.dumps(
            {
                seed_data["url_a"]: {
                    "release_id": 111,
                    "is_track": False,
                    "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=111/size=large/",
                    "description": "Cached description A.",
                },
                seed_data["url_b"]: {
                    "release_id": 222,
                    "is_track": False,
                    "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=222/size=large/",
                    "description": "Cached description B.",
                },
            }
        ),
        encoding="utf-8",
    )

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


@pytest.fixture
def page(app_server):
    """A fresh Chromium page (headless) pointed at the launched app."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        pg = context.new_page()
        try:
            yield pg
        finally:
            context.close()
            browser.close()
