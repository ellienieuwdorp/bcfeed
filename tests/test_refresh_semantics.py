"""WP-15 · Refresh semantics (frozen clock, mocked providers).

Covers:

- LOG-2: the trailing settling window (``SETTLING_WINDOW_DAYS = 3``). The last
  three calendar days (today included) are queried and their releases
  persisted, but they never enter the scrape ledger — a re-populate re-queries
  exactly those days, so late-arriving emails self-heal. The ledger is the
  sole authority for "checked" in ``cached_releases_for_range``; stale window
  records on disk are ignored on read and dropped on the next write.
- LOG-3 / UX-12 / UXP-11 (backend): ``refresh=1`` on the stream endpoint
  forces a re-query of a fully-checked range through the resurrected
  ``mark_dates_not_scraped``. A newly-arrived email appears, canonical-URL
  merge yields zero duplicate rows, stars/seen/embeds are untouched, and an
  empty-again range is recorded empty-again (never left stale).
- LOG-12: both providers bucket every email onto the user's LOCAL calendar
  date through the shared ``EmailProvider.local_date_from_header`` helper;
  refresh scans pad the provider QUERY by ±1 day (marking is never padded) so
  boundary-hour emails in the account's timezone are picked up.
- LOG-22 / PY-16 (behavior half, decision (a)): days recorded by the OTHER
  provider read as unchecked for the active one — a provider switch offers the
  range for re-check instead of silently serving the old provider's coverage.

Import discipline (see conftest): store modules are referenced as ``import x``
inside tests so the autouse data-dir reload is honored.
"""

from __future__ import annotations

import base64
import datetime
import importlib
import json
import os
import time
from pathlib import Path

import pytest

from email_provider import EmailMessage, SearchQuery

REPO_ROOT = Path(__file__).resolve().parent.parent

# The acceptance scenario's frozen clock: populate June on July 2.
JUL_2 = datetime.date(2025, 7, 2)


def _silent(*_args, **_kwargs) -> None:
    """A no-op log sink for pipeline runs whose output we don't inspect."""


def june(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


def release_html(slug: str) -> str:
    return (
        f"<html><body><p>Greetings friend, <b>Page {slug}</b> just released "
        f"<i>Album {slug}</i> by Artist {slug}, check it out here: "
        f'<a href="https://{slug}.bandcamp.com/album/{slug}">listen</a></p></body></html>'
    )


def release_message(slug: str, date: str) -> EmailMessage:
    return EmailMessage(html=release_html(slug), date=date, subject=f"New release from Page {slug}")


def release_url(slug: str) -> str:
    return f"https://{slug}.bandcamp.com/album/{slug}"


class FakeProvider:
    """EmailProvider double driven by a {date: [EmailMessage]} plan.

    ``search`` records the exact (after, before) bounds it was queried with —
    the settling-window/pad assertions key off them — and returns one id per
    planned message inside the queried window (before-date exclusive, matching
    the pipeline's end+1 convention).
    """

    def __init__(self, emails_by_day):
        self.emails_by_day = {day: list(messages) for day, messages in emails_by_day.items()}
        self.search_calls: list[tuple[str, str]] = []
        self.closed = False

    def authenticate(self) -> None:
        pass

    def search(self, query: SearchQuery, max_results: int = 100, log=None) -> list[str]:
        self.search_calls.append((query.after_date, query.before_date))
        start = datetime.datetime.strptime(query.after_date, "%Y/%m/%d").date()
        end_exclusive = datetime.datetime.strptime(query.before_date, "%Y/%m/%d").date()
        ids = []
        day = start
        while day < end_exclusive:
            for index in range(len(self.emails_by_day.get(day, []))):
                ids.append(f"{day.isoformat()}#{index}")
            day += datetime.timedelta(days=1)
        return ids[:max_results]

    def fetch(self, message_ids, batch_size: int = 20, log=None) -> dict[str, EmailMessage]:
        out = {}
        for message_id in message_ids:
            day_iso, _, index = message_id.partition("#")
            out[message_id] = self.emails_by_day[datetime.date.fromisoformat(day_iso)][int(index)]
        return out

    def close(self) -> None:
        self.closed = True


def _install_provider(monkeypatch, provider, provider_type: str) -> None:
    import pipeline

    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: provider_type)


def _read_ledger() -> dict:
    import paths

    if not paths.SCRAPE_STATUS_PATH.exists():
        return {}
    return json.loads(paths.SCRAPE_STATUS_PATH.read_text(encoding="utf-8"))


def _read_cache() -> dict:
    import paths

    if not paths.RELEASE_CACHE_PATH.exists():
        return {}
    return json.loads(paths.RELEASE_CACHE_PATH.read_text(encoding="utf-8"))


def _populate(after: str, before: str, *, refresh: bool = False) -> None:
    import pipeline

    pipeline.populate_release_cache(after, before, 100, 20, log=_silent, refresh=refresh)


# ---------------------------------------------------------------------------
# LOG-2 — trailing settling window (N=3): ledger-skipped, not release-skipped
# ---------------------------------------------------------------------------
def test_settling_window_days_are_queried_and_persisted_but_never_ledgered(
    freeze_today, monkeypatch
):
    freeze_today(JUL_2)
    plan = {
        day: [release_message(f"a{day.day:02d}", day.isoformat())]
        for day in (june(1), june(16), june(29), june(30))
    }
    provider = FakeProvider(plan)
    _install_provider(monkeypatch, provider, "gmail")

    _populate("2025-06-01", "2025-06-30")

    # The WHOLE range was queried in one pass — window days included.
    assert provider.search_calls == [("2025/06/01", "2025/07/01")]

    # Window-day releases ARE persisted (the skip is ledger-only)...
    cache = _read_cache()
    assert [row["url"] for row in cache["2025-06-30"]] == [release_url("a30")]
    assert [row["url"] for row in cache["2025-06-29"]] == [release_url("a29")]

    # ...but with today = Jul 2 and N = 3, days >= Jun 30 never enter the
    # ledger: Jun 1-29 are recorded checked, Jun 30 / Jul 1 / Jul 2 are not.
    ledger = _read_ledger()
    assert set(ledger) == {june(day).isoformat() for day in range(1, 30)}
    assert ledger["2025-06-01"]["empty"] is False
    assert ledger["2025-06-02"]["empty"] is True  # checked, no mail of its own

    # /scrape-status semantics: window days report unchecked/pending.
    import session_store

    status = session_store.scrape_status_for_range(june(29), JUL_2)
    assert status == {
        "2025-06-29": True,
        "2025-06-30": False,
        "2025-07-01": False,
        "2025-07-02": False,
    }


def test_repopulate_requeries_only_the_settling_window_and_self_heals(freeze_today, monkeypatch):
    freeze_today(JUL_2)
    _install_provider(monkeypatch, FakeProvider({}), "gmail")
    _populate("2025-06-01", "2025-06-30")

    # A late-arriving email for a window day shows up on the next populate
    # with no manual action: only the window day is re-queried (with the ±1
    # re-check pad), everything already recorded stays untouched.
    late = FakeProvider({june(30): [release_message("late", "2025-06-30")]})
    _install_provider(monkeypatch, late, "gmail")
    _populate("2025-06-01", "2025-06-30")

    assert late.search_calls == [("2025/06/29", "2025/07/02")]
    import session_store

    urls = {row["url"] for row in session_store.get_full_release_cache()}
    assert release_url("late") in urls
    # Still no window day in the ledger — it keeps self-healing until it ages out.
    assert set(_read_ledger()) == {june(day).isoformat() for day in range(1, 30)}


def test_ledger_is_sole_authority_and_stale_window_records_are_ignored(
    freeze_today, seed, make_release
):
    import session_store

    freeze_today(JUL_2)
    # A day with cached rows but NO ledger record is still missing (LOG-2:
    # cached data is returned for display/merge, the ledger decides coverage).
    seed.releases({june(10): [make_release(date="2025-06-10")]})
    cached, missing = session_store.cached_releases_for_range(june(10), june(10))
    assert [row["date"] for row in cached] == ["2025-06-10"]
    assert missing == [june(10)]

    # A stale on-disk record inside the window is ignored on read...
    seed.scrape_status([june(30), june(15)], source="gmail")
    status = session_store.scrape_status_for_range(june(15), june(30))
    assert status["2025-06-15"] is True
    assert status["2025-06-30"] is False
    _, missing = session_store.cached_releases_for_range(june(30), june(30))
    assert missing == [june(30)]

    # ...and dropped on the next ledger write.
    session_store.mark_dates_scraped([june(20)])
    assert set(_read_ledger()) == {"2025-06-15", "2025-06-20"}


# ---------------------------------------------------------------------------
# LOG-3 / UX-12 / UXP-11 backend — refresh=1 re-checks a fully-checked range
# ---------------------------------------------------------------------------
def test_refresh_requeries_checked_range_no_dupes_state_untouched(freeze_today, seed, monkeypatch):
    import paths
    import session_store

    freeze_today(JUL_2)
    original = release_url("original")
    _install_provider(
        monkeypatch, FakeProvider({june(10): [release_message("original", "2025-06-10")]}), "gmail"
    )
    _populate("2025-06-10", "2025-06-12")
    assert set(_read_ledger()) == {"2025-06-10", "2025-06-11", "2025-06-12"}

    # Star/see/enrich the existing row, then snapshot those stores.
    seed.viewed([original])
    seed.starred([original])
    seed.embed_cache({original: {"status": "ok", "release_id": 7, "is_track": False}})
    viewed_before = paths.VIEWED_PATH.read_bytes()
    starred_before = paths.STARRED_PATH.read_bytes()
    embeds_before = paths.EMBED_CACHE_PATH.read_bytes()

    # Without refresh, a fully-checked range never queries the provider.
    untouched = FakeProvider({june(10): [release_message("original", "2025-06-10")]})
    _install_provider(monkeypatch, untouched, "gmail")
    _populate("2025-06-10", "2025-06-12")
    assert untouched.search_calls == []

    # refresh=1: the provider IS re-queried (with the ±1 pad); the original
    # email is still there, and a newly-arrived one joins it.
    refreshed = FakeProvider(
        {
            june(10): [release_message("original", "2025-06-10")],
            june(11): [release_message("late-arrival", "2025-06-11")],
        }
    )
    _install_provider(monkeypatch, refreshed, "gmail")
    _populate("2025-06-10", "2025-06-12", refresh=True)

    assert refreshed.search_calls == [("2025/06/09", "2025/06/14")]

    # The late email appears; the re-seen original produced ZERO duplicates.
    rows = [row for day_rows in _read_cache().values() for row in day_rows]
    urls = [row["url"] for row in rows]
    assert urls.count(original) == 1
    assert urls.count(release_url("late-arrival")) == 1

    # The range is recorded checked again (not left stale)...
    assert set(_read_ledger()) == {"2025-06-10", "2025-06-11", "2025-06-12"}
    _, missing = session_store.cached_releases_for_range(june(10), june(12), provider="gmail")
    assert missing == []

    # ...and stars/seen/embeds are byte-identical.
    assert paths.VIEWED_PATH.read_bytes() == viewed_before
    assert paths.STARRED_PATH.read_bytes() == starred_before
    assert paths.EMBED_CACHE_PATH.read_bytes() == embeds_before


def test_refreshed_range_that_is_still_empty_is_recorded_empty_again(freeze_today, monkeypatch):
    freeze_today(JUL_2)
    _install_provider(monkeypatch, FakeProvider({}), "gmail")
    _populate("2025-06-20", "2025-06-21")
    assert {day: rec["empty"] for day, rec in _read_ledger().items()} == {
        "2025-06-20": True,
        "2025-06-21": True,
    }

    # Re-check comes back empty again: the days are re-recorded empty — never
    # left in a permanently "missing" state (mark_dates_not_scraped cleared
    # them first, the empty persist wrote them back).
    still_empty = FakeProvider({})
    _install_provider(monkeypatch, still_empty, "gmail")
    _populate("2025-06-20", "2025-06-21", refresh=True)

    assert still_empty.search_calls == [("2025/06/19", "2025/06/23")]
    assert {day: rec["empty"] for day, rec in _read_ledger().items()} == {
        "2025-06-20": True,
        "2025-06-21": True,
    }


def test_populate_route_passes_refresh_flag_to_pipeline(isolated_data_dir, monkeypatch):
    import server

    server = importlib.reload(server)
    server.app.config["TESTING"] = True
    monkeypatch.setattr(server, "get_current_provider_type", lambda: "imap")
    monkeypatch.setattr(server, "_has_credentials_for_provider", lambda: True)
    seen_flags: list[bool] = []

    def fake_populate(after, before, max_results, batch_size=20, log=None, refresh=False):
        seen_flags.append(refresh)

    monkeypatch.setattr(server, "populate_release_cache", fake_populate)
    client = server.app.test_client()

    body = client.get("/populate-range-stream?start=2025-06-10&end=2025-06-12&refresh=1").get_data(
        as_text=True
    )
    assert "event: done" in body
    body = client.get("/populate-range-stream?start=2025-06-10&end=2025-06-12").get_data(
        as_text=True
    )
    assert "event: done" in body
    assert seen_flags == [True, False]


# ---------------------------------------------------------------------------
# LOG-12 — LOCAL-date bucketing through BOTH providers + the refresh query pad
# ---------------------------------------------------------------------------
BOUNDARY_DATE_HEADER = "Mon, 16 Jun 2025 23:30:00 -0800"


@pytest.fixture
def new_york_clock():
    """Pin the machine-local timezone so the bucketing assertion is exact."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if saved is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = saved
    time.tzset()


def test_boundary_email_buckets_to_the_same_local_date_via_both_providers(
    new_york_clock, monkeypatch
):
    """``23:30:00 -0800`` on Jun 16 is 03:30 EDT on Jun 17: both adapters must
    bucket it to the LOCAL Jun 17, not the sender's Jun 16."""
    import gmail_client
    import gmail_provider as gmail_provider_mod
    from gmail_provider import GmailProvider
    from imap_client import ImapConfig
    from imap_provider import ImapProvider

    html = release_html("boundary")
    subject = "New release from Page boundary"

    # IMAP path: raw RFC 822 bytes through the real adapter fetch.
    raw_eml = (
        b"From: Bandcamp <noreply@bandcamp.com>\r\n"
        + f"Subject: {subject}\r\n".encode("ascii")
        + f"Date: {BOUNDARY_DATE_HEADER}\r\n".encode("ascii")
        + b'Content-Type: text/html; charset="utf-8"\r\n\r\n'
        + html.encode("utf-8")
    )
    imap = ImapProvider(ImapConfig(host="fixture.invalid", username="u", password="p"))
    monkeypatch.setattr(imap._client, "uid_fetch_body", lambda uid: raw_eml)
    imap_message = imap._fetch_single("1")

    # Gmail path: the real transport hands the RAW header through; the real
    # adapter fetch buckets it via the same shared helper.
    payload = {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": BOUNDARY_DATE_HEADER},
            ],
            "body": {"data": base64.urlsafe_b64encode(html.encode("utf-8")).decode("ascii")},
        }
    }
    entry = gmail_client._message_entry(payload)
    assert entry["date"] == BOUNDARY_DATE_HEADER, "transport must not interpret the header"
    monkeypatch.setattr(
        gmail_provider_mod,
        "get_messages",
        lambda service, ids, format="full", batch_size=20, log=None: {"g1": entry},
    )
    gmail = GmailProvider()
    gmail._service = object()
    gmail_message = gmail.fetch(["g1"])["g1"]

    assert imap_message.date == gmail_message.date == "2025-06-17"


def test_refresh_pad_picks_up_boundary_email_without_marking_pad_days(freeze_today, monkeypatch):
    freeze_today(JUL_2)
    _install_provider(monkeypatch, FakeProvider({}), "gmail")
    _populate("2025-06-10", "2025-06-12")

    # Two emails sit on Jun 13 in the provider account's timezone — outside
    # the range, visible only to the padded query. One buckets locally to
    # Jun 12 (inside the range), one to Jun 13 (outside).
    boundary = FakeProvider(
        {
            june(13): [
                release_message("boundary", "2025-06-12"),
                release_message("outside", "2025-06-13"),
            ]
        }
    )
    _install_provider(monkeypatch, boundary, "gmail")
    _populate("2025-06-10", "2025-06-12", refresh=True)

    # Query bounds padded ±1 day; marking is NOT: the pad days never enter
    # the ledger even though the "outside" row was fetched and cached.
    assert boundary.search_calls == [("2025/06/09", "2025/06/14")]
    cache = _read_cache()
    assert [row["url"] for row in cache["2025-06-12"]] == [release_url("boundary")]
    assert [row["url"] for row in cache["2025-06-13"]] == [release_url("outside")]
    assert set(_read_ledger()) == {"2025-06-10", "2025-06-11", "2025-06-12"}


def test_first_time_scans_keep_exact_unpadded_bounds(freeze_today, monkeypatch):
    freeze_today(JUL_2)
    provider = FakeProvider({})
    _install_provider(monkeypatch, provider, "gmail")
    _populate("2025-06-10", "2025-06-12")
    # No refresh, range fully outside the settling window: end-inclusive
    # bounds exactly as before (after start, before end+1).
    assert provider.search_calls == [("2025/06/10", "2025/06/13")]


# ---------------------------------------------------------------------------
# LOG-22 / PY-16 behavior half — provider switches offer re-check
# ---------------------------------------------------------------------------
def test_provider_switch_offers_recheck_instead_of_serving_old_coverage(freeze_today, monkeypatch):
    import session_store

    freeze_today(JUL_2)
    _install_provider(
        monkeypatch,
        FakeProvider({june(10): [release_message("gmail-era", "2025-06-10")]}),
        "gmail",
    )
    _populate("2025-06-10", "2025-06-12")
    assert all(rec["source"] == "gmail" for rec in _read_ledger().values())

    # Coverage honesty: for the active provider "imap" every Gmail-era day
    # reads unchecked (offered for re-check)...
    status = session_store.scrape_status_for_range(june(10), june(12), provider="imap")
    assert not any(status.values())
    _, missing = session_store.cached_releases_for_range(june(10), june(12), provider="imap")
    assert missing == [june(10), june(11), june(12)]
    # ...while for "gmail" (and for provider-less readers) it stays checked.
    assert all(session_store.scrape_status_for_range(june(10), june(12), provider="gmail").values())
    assert all(session_store.scrape_status_for_range(june(10), june(12)).values())

    # Switching the active provider to IMAP re-queries June instead of
    # trusting the Gmail-era ledger; both providers' rows coexist (merge is
    # additive by URL) and the days are re-attributed to IMAP.
    imap = FakeProvider({june(11): [release_message("imap-only", "2025-06-11")]})
    _install_provider(monkeypatch, imap, "imap")
    _populate("2025-06-10", "2025-06-12")

    assert imap.search_calls == [("2025/06/10", "2025/06/13")]
    urls = {row["url"] for row in session_store.get_full_release_cache()}
    assert {release_url("gmail-era"), release_url("imap-only")} <= urls
    ledger = _read_ledger()
    assert set(ledger) == {"2025-06-10", "2025-06-11", "2025-06-12"}
    assert all(rec["source"] == "imap" for rec in ledger.values())


def test_scrape_status_route_reports_other_providers_days_for_recheck(
    isolated_data_dir, seed, freeze_today, monkeypatch
):
    import server

    server = importlib.reload(server)
    server.app.config["TESTING"] = True
    freeze_today(JUL_2)
    seed.scrape_status([june(10), june(11)], source="gmail")

    monkeypatch.setattr(server, "get_current_provider_type", lambda: "imap")
    resp = server.app.test_client().get(
        "/scrape-status", query_string={"start": "2025-06-10", "end": "2025-06-11"}
    )
    assert resp.get_json() == {
        "scraped": [],
        "not_scraped": ["2025-06-10", "2025-06-11"],
    }

    monkeypatch.setattr(server, "get_current_provider_type", lambda: "gmail")
    resp = server.app.test_client().get(
        "/scrape-status", query_string={"start": "2025-06-10", "end": "2025-06-11"}
    )
    assert resp.get_json() == {
        "scraped": ["2025-06-10", "2025-06-11"],
        "not_scraped": [],
    }


# ---------------------------------------------------------------------------
# Acceptance grep — all "today" logic flows through util.today()
# ---------------------------------------------------------------------------
def test_no_direct_date_today_calls_outside_util():
    offenders = []
    for py in REPO_ROOT.glob("*.py"):
        count = py.read_text(encoding="utf-8").count("date.today()")
        if py.name == "util.py":
            assert count == 1, "util.today() is the single date.today() choke point"
            continue
        if count:
            offenders.append(py.name)
    assert offenders == [], f"direct date.today() calls found in: {offenders}"
