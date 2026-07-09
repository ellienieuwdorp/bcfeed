"""WP-04 · Pipeline correctness tests.

Covers:

- CQ-10/LOG-1/PY-1/ARCH-2: releases are persisted per range BEFORE that range
  is marked scraped. A mid-run failure keeps completed ranges (data + scraped
  mark) and leaves the failed/unreached ranges unmarked so the next run
  re-fetches exactly those.
- CQ-14/PY-7/LOG-10 (crash half): garbage/missing dates — in the cache or in
  legacy email dicts — no longer abort dedupe or the whole populate.
- PY-18/LOG-24: an IMAP message with a missing/garbled Date header yields one
  counted skip at construction — never a raise in dedupe_by_date, never a
  date:null row in the release cache.
- CQ-16/PY-11: the Gmail HTML path no longer applies a speculative
  quoted-printable decode; '=E2'/'id=3D' lookalike sequences round-trip
  byte-identical. The IMAP declared-CTE decode is unchanged (CQ-71).
- Regression locks for the upstream fixes: a plain-text-only email costs
  exactly one counted skip in a 50-email run; a linkless email produces zero
  cached rows. Fixtures run through BOTH provider extraction paths.
"""

from __future__ import annotations

import base64
import datetime
import email
import json

import pytest

from email_provider import EmailMessage, SearchQuery


def _silent(*_args, **_kwargs) -> None:
    """A no-op log sink for pipeline runs whose output we don't inspect."""


def june(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


def release_html(slug: str) -> str:
    """A minimal but real Bandcamp release-notification HTML body."""
    return (
        f"<html><body><p>Greetings friend, <b>Page {slug}</b> just released "
        f"<i>Album {slug}</i> by Artist {slug}, check it out here: "
        f'<a href="https://{slug}.bandcamp.com/album/{slug}">listen</a></p></body></html>'
    )


def release_message(slug: str, date: str) -> EmailMessage:
    return EmailMessage(
        html=release_html(slug),
        date=date,
        subject=f"New release from Page {slug}",
    )


def _read_cache() -> dict:
    """Read release_cache.json raw from the isolated data dir."""
    import paths

    if not paths.RELEASE_CACHE_PATH.exists():
        return {}
    return json.loads(paths.RELEASE_CACHE_PATH.read_text(encoding="utf-8"))


def _cache_rows(cache: dict) -> list[dict]:
    return [row for rows in cache.values() for row in rows]


class FakeProvider:
    """Minimal EmailProvider double driven by a {date: [EmailMessage]} plan.

    ``search`` returns one id per planned message inside the queried window
    (pipeline passes YYYY/MM/DD, before-date exclusive); ``fetch`` resolves
    those ids. ``fail_on_search_call`` injects a mid-run provider failure on
    the Nth range (1-based) to exercise the LOG-1 ordering invariant.
    """

    def __init__(self, emails_by_day, *, fail_on_search_call: int | None = None):
        self.emails_by_day = {day: list(messages) for day, messages in emails_by_day.items()}
        self.fail_on_search_call = fail_on_search_call
        self.search_calls: list[tuple[str, str]] = []
        self.closed = False

    def authenticate(self) -> None:
        pass

    def search(self, query: SearchQuery, max_results: int = 100, log=None) -> list[str]:
        self.search_calls.append((query.after_date, query.before_date))
        if (
            self.fail_on_search_call is not None
            and len(self.search_calls) == self.fail_on_search_call
        ):
            raise RuntimeError("injected provider failure")
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


def _install_provider(monkeypatch, provider, provider_type: str = "imap") -> None:
    import pipeline

    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: provider_type)


def _three_range_setup(seed):
    """Seed Jun 12 + Jun 15 scraped so Jun 10-16 collapses to 3 missing ranges:
    (10-11), (13-14), (16)."""
    seed.scrape_status([june(12), june(15)])
    return {
        day: [release_message(f"artist-{day.day:02d}", day.isoformat())]
        for day in (june(10), june(11), june(13), june(14), june(16))
    }


# ---------------------------------------------------------------------------
# CQ-10 / LOG-1 / PY-1 / ARCH-2 — persist per range BEFORE mark-scraped
# ---------------------------------------------------------------------------
def test_midrun_failure_keeps_completed_ranges_and_refetches_the_rest(
    frozen_today, seed, monkeypatch
):
    import pipeline
    import session_store

    emails_by_day = _three_range_setup(seed)
    failing = FakeProvider(emails_by_day, fail_on_search_call=2)
    _install_provider(monkeypatch, failing)

    with pytest.raises(RuntimeError, match="injected provider failure"):
        pipeline.populate_release_cache("2025-06-10", "2025-06-16", 100, 20, log=_silent)

    # Range 1 (Jun 10-11) is persisted...
    cache = _read_cache()
    assert set(cache) == {"2025-06-10", "2025-06-11"}
    assert cache["2025-06-10"][0]["url"] == "https://artist-10.bandcamp.com/album/artist-10"
    assert cache["2025-06-11"][0]["url"] == "https://artist-11.bandcamp.com/album/artist-11"
    # ...AND marked scraped; failed/unreached ranges stay unmarked.
    status = session_store.scrape_status_for_range(june(10), june(16))
    assert status["2025-06-10"] and status["2025-06-11"]
    assert not status["2025-06-13"]
    assert not status["2025-06-14"]
    assert not status["2025-06-16"]

    # The next run re-fetches ONLY the failed/unreached ranges (never-re-fetch
    # holds for range 1) and completes.
    retry = FakeProvider(emails_by_day)
    _install_provider(monkeypatch, retry)
    lines: list[str] = []
    pipeline.populate_release_cache("2025-06-10", "2025-06-16", 100, 20, log=lines.append)
    assert retry.search_calls == [("2025/06/13", "2025/06/15"), ("2025/06/16", "2025/06/17")]

    cache = _read_cache()
    assert set(cache) == {
        "2025-06-10",
        "2025-06-11",
        "2025-06-13",
        "2025-06-14",
        "2025-06-16",
    }
    status = session_store.scrape_status_for_range(june(10), june(16))
    for day in (june(10), june(11), june(13), june(14), june(16)):
        assert status[day.isoformat()], f"{day} should be marked scraped after the retry run"
    assert any("5 releases ready" in line for line in lines)


def test_persist_happens_before_mark_within_each_range(frozen_today, seed, monkeypatch):
    """CQ-10 acceptance: if persisting range 2 raises, range 1 is persisted AND
    marked, range 2 is neither marked nor partially persisted."""
    import pipeline
    import session_store

    emails_by_day = _three_range_setup(seed)
    _install_provider(monkeypatch, FakeProvider(emails_by_day))

    real_persist = pipeline.persist_release_metadata
    calls = {"count": 0}

    def failing_persist(releases, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated persist failure")
        return real_persist(releases, **kwargs)

    monkeypatch.setattr(pipeline, "persist_release_metadata", failing_persist)

    with pytest.raises(RuntimeError, match="simulated persist failure"):
        pipeline.populate_release_cache("2025-06-10", "2025-06-16", 100, 20, log=_silent)

    cache = _read_cache()
    assert set(cache) == {"2025-06-10", "2025-06-11"}
    status = session_store.scrape_status_for_range(june(10), june(16))
    assert status["2025-06-10"] and status["2025-06-11"]
    # Range 2's persist failed BEFORE its scraped mark was written.
    assert not status["2025-06-13"]
    assert not status["2025-06-14"]
    # Range 3 was never reached.
    assert not status["2025-06-16"]


# ---------------------------------------------------------------------------
# CQ-14 / PY-7 / LOG-10 — malformed dates never abort dedupe or populate
# ---------------------------------------------------------------------------
def test_dedupe_by_date_tolerates_garbage_and_null_dates():
    from util import dedupe_by_date

    items = [
        {"url": "https://a.bandcamp.com/album/x", "date": "garbage"},
        {"url": "https://a.bandcamp.com/album/y", "date": None},
        {"url": "https://a.bandcamp.com/album/z", "date": "2025-06-16"},
    ]
    out = dedupe_by_date(items, keep="last")
    assert {item["url"] for item in out} == {item["url"] for item in items}


def test_dateless_items_never_win_keep_last():
    from util import dedupe_by_date

    url = "https://a.bandcamp.com/album/x"
    dated = {"url": url, "date": "2025-06-20"}
    for loser in ({"url": url, "date": None}, {"url": url, "date": "not-a-date"}):
        for ordering in ([dated, loser], [loser, dated]):
            out = dedupe_by_date(ordering, keep="last")
            assert out == [dated], f"dated entry must win over {loser['date']!r}"


def test_keep_last_semantics_unchanged_for_dated_items():
    from util import dedupe_by_date

    url = "https://a.bandcamp.com/album/x"
    early = {"url": url, "date": "2025-06-10"}
    late = {"url": url, "date": "2025-06-20"}
    for ordering in ([early, late], [late, early]):
        assert dedupe_by_date(ordering, keep="last") == [late]


def test_cached_garbage_date_no_longer_aborts_populate(frozen_today, seed, make_release):
    import pipeline

    seed.releases(
        {
            "2025-06-16": [
                make_release(release_url="https://ok.bandcamp.com/album/ok", date="2025-06-16"),
                make_release(release_url="https://bad.bandcamp.com/album/bad", date="garbage"),
                make_release(release_url="https://null.bandcamp.com/album/null", date=None),
            ]
        }
    )
    seed.scrape_status([june(16)])

    lines: list[str] = []
    # Fully-cached range: the early dedupe previously raised ValueError here.
    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=lines.append)
    assert any("3 releases ready" in line for line in lines)


def test_cached_garbage_date_does_not_abort_range_persist(
    frozen_today, seed, make_release, monkeypatch
):
    """The per-range persist dedupes new against cached rows; a garbage cached
    date must not abort the loop path either."""
    import pipeline
    import session_store

    seed.releases(
        {
            "2025-06-16": [
                make_release(release_url="https://ok.bandcamp.com/album/ok", date="2025-06-16"),
                make_release(release_url="https://bad.bandcamp.com/album/bad", date="garbage"),
            ]
        }
    )
    seed.scrape_status([june(16)])
    _install_provider(
        monkeypatch, FakeProvider({june(17): [release_message("newday", "2025-06-17")]})
    )

    pipeline.populate_release_cache("2025-06-16", "2025-06-17", 100, 20, log=_silent)

    cache = _read_cache()
    assert cache["2025-06-17"][0]["url"] == "https://newday.bandcamp.com/album/newday"
    assert session_store.scrape_status_for_range(june(17), june(17))["2025-06-17"]


def test_legacy_dict_garbage_or_missing_date_is_a_counted_skip():
    """The legacy-dict branch (old pipeline.py:40 crash site) tolerates a
    present-but-garbage date and a missing date."""
    import pipeline

    emails_dict = {
        "1": {
            "html": release_html("good"),
            "date": "2025-06-16",
            "subject": "New release from Page good",
        },
        "2": {
            "html": release_html("bad"),
            "date": "garbage",
            "subject": "New release from Page bad",
        },
        "3": {"html": release_html("none"), "date": None, "subject": "New release from Page none"},
    }
    lines: list[str] = []
    releases = pipeline.construct_release_list(emails_dict, log=lines.append)
    assert [release["url"] for release in releases] == ["https://good.bandcamp.com/album/good"]
    assert releases[0]["date"] == "2025-06-16"
    assert any("Skipped 2 email(s)" in line for line in lines)
    assert sum("missing or unreadable date" in line for line in lines) == 2


# ---------------------------------------------------------------------------
# PY-18 / LOG-24 — IMAP missing/garbled Date header
# ---------------------------------------------------------------------------
def _release_eml(date_header: str | None) -> bytes:
    lines = [
        b"From: Bandcamp <noreply@bandcamp.com>",
        b"To: listener@example.com",
        b"Subject: New release from Midnight Tapes",
    ]
    if date_header is not None:
        lines.append(b"Date: " + date_header.encode("ascii"))
    lines += [
        b"MIME-Version: 1.0",
        b'Content-Type: text/html; charset="utf-8"',
        b"",
        b"<html><body><p>Greetings friend, <b>Midnight Tapes</b> just released "
        b"<i>Neon Fields</i> by Aria Vale, check it out here: "
        b'<a href="https://midnighttapes.bandcamp.com/album/neon-fields">listen</a></p></body></html>',
    ]
    return b"\r\n".join(lines)


def _imap_fetch_single(raw: bytes) -> EmailMessage | None:
    """Run raw message bytes through the real ImapProvider fetch path
    (network call stubbed out)."""
    from imap_client import ImapConfig
    from imap_provider import ImapProvider

    provider = ImapProvider(ImapConfig(host="fixture.invalid", username="u", password="p"))
    provider._client.uid_fetch_body = lambda _msg_id: raw
    return provider._fetch_single("1")


@pytest.mark.parametrize("date_header", [None, "not a real date at all"])
def test_imap_unparseable_date_is_one_counted_skip(date_header):
    import pipeline
    from util import dedupe_by_date

    message = _imap_fetch_single(_release_eml(date_header))
    assert message is not None
    assert message.date == ""  # the IMAP adapter normalizes bad headers to ""

    lines: list[str] = []
    releases = pipeline.construct_release_list({"1": message}, log=lines.append)
    assert releases == []
    assert any("missing or unreadable date" in line for line in lines)
    assert any("Skipped 1 email(s)" in line for line in lines)
    # Nothing date-less reaches dedupe; and dedupe would tolerate it anyway.
    assert dedupe_by_date(releases, keep="last") == []


def test_imap_unparseable_date_never_persists_a_null_date_row(frozen_today, monkeypatch):
    import pipeline
    import session_store

    valid = _imap_fetch_single(_release_eml("Mon, 16 Jun 2025 14:30:00 +0000"))
    assert valid is not None and valid.date == "2025-06-16"
    dateless = _imap_fetch_single(_release_eml(None))
    _install_provider(monkeypatch, FakeProvider({june(16): [valid, dateless]}))

    # The run completes (no raise in dedupe_by_date)...
    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=_silent)

    # ...the valid message is cached, and no date:null row exists anywhere.
    rows = _cache_rows(_read_cache())
    assert [row["url"] for row in rows] == ["https://midnighttapes.bandcamp.com/album/neon-fields"]
    assert all(row.get("date") for row in rows)
    assert session_store.scrape_status_for_range(june(16), june(16))["2025-06-16"]


# ---------------------------------------------------------------------------
# CQ-16 / PY-11 — Gmail quopri double-decode removed (IMAP decode untouched)
# ---------------------------------------------------------------------------
QP_LOOKALIKE_HTML = (
    '<html><body><p id=3D"track">Literal =E2 and =C3 sequences must survive</p>'
    '<a href="https://a.bandcamp.com/album/x?ref=3Demail">x</a></body></html>'
)


def _gmail_message_with_html(html: str) -> dict:
    """A Gmail API 'full' message: body data base64url-wrapped, CTE already
    decoded by the API (which is why no quopri pass may be applied)."""
    data = base64.urlsafe_b64encode(html.encode("utf-8")).decode("ascii")
    return {"payload": {"mimeType": "text/html", "headers": [], "body": {"data": data}}}


def test_gmail_html_with_qp_lookalikes_roundtrips_byte_identical():
    import gmail_client

    extracted = gmail_client.get_html_from_message(_gmail_message_with_html(QP_LOOKALIKE_HTML))
    assert extracted == QP_LOOKALIKE_HTML  # byte-identical: no speculative decode
    assert 'id=3D"track"' in extracted
    assert "=E2" in extracted


def test_imap_html_with_qp_lookalikes_survives_too():
    from imap_client import ImapConfig
    from imap_provider import ImapProvider

    raw = (
        b"From: Bandcamp <noreply@bandcamp.com>\r\n"
        b"Subject: New release from Page\r\n"
        b"Date: Mon, 16 Jun 2025 14:30:00 +0000\r\n"
        b"MIME-Version: 1.0\r\n"
        b'Content-Type: text/html; charset="utf-8"\r\n'
        b"\r\n" + QP_LOOKALIKE_HTML.encode("utf-8")
    )
    provider = ImapProvider(ImapConfig(host="fixture.invalid", username="u", password="p"))
    extracted = provider._extract_html(email.message_from_bytes(raw))
    assert 'id=3D"track"' in extracted
    assert "=E2" in extracted


QP_DECLARED_EML = (
    b"From: Bandcamp <noreply@bandcamp.com>\r\n"
    b"Subject: New release from Page\r\n"
    b"Date: Mon, 16 Jun 2025 14:30:00 +0000\r\n"
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: text/html; charset="utf-8"\r\n'
    b"Content-Transfer-Encoding: quoted-printable\r\n"
    b"\r\n"
    b"<html><body><p>Caf=C3=A9 =E2=80=94 d=C3=A9j=C3=A0 vu</p></body></html>\r\n"
)


def test_imap_quoted_printable_declared_part_still_decodes():
    """CQ-71 no-regress: the IMAP path decodes per the declared CTE."""
    from imap_client import ImapConfig
    from imap_provider import ImapProvider

    provider = ImapProvider(ImapConfig(host="fixture.invalid", username="u", password="p"))
    extracted = provider._extract_html(email.message_from_bytes(QP_DECLARED_EML))
    assert "Café — déjà vu" in extracted


def test_gmail_api_style_delivery_of_qp_mail_needs_no_quopri():
    """The Gmail API decodes the CTE before handing bytes over; simulating
    that delivery shows genuinely QP mail still decodes without the hack."""
    import gmail_client

    message = email.message_from_bytes(QP_DECLARED_EML)
    decoded = message.get_payload(decode=True)  # what the Gmail API delivers
    payload = {
        "payload": {
            "mimeType": "text/html",
            "headers": [],
            "body": {"data": base64.urlsafe_b64encode(decoded).decode("ascii")},
        }
    }
    extracted = gmail_client.get_html_from_message(payload)
    assert "Café — déjà vu" in extracted


# ---------------------------------------------------------------------------
# Regression locks for the upstream fixes (CQ-12/PY-3, CQ-13/PY-5)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
def test_plaintext_only_email_in_50_email_run_costs_one_skip(emails, provider_path):
    import pipeline

    if provider_path == "gmail":
        plain_html = emails.html_via_gmail("plaintext_only")
        assert plain_html is None
    else:
        plain_html = emails.html_via_imap("plaintext_only")
        assert plain_html == ""

    batch: dict[str, EmailMessage] = {
        f"id-{n}": release_message(f"artist{n:02d}", "2025-06-16") for n in range(49)
    }
    batch["plain"] = EmailMessage(
        html=plain_html, date="2025-06-20", subject=emails.subject("plaintext_only")
    )

    lines: list[str] = []
    releases = pipeline.construct_release_list(batch, log=lines.append)
    assert len(releases) == 49
    assert len({release["url"] for release in releases}) == 49
    assert any("Skipped 1 email(s)" in line for line in lines)


@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
def test_linkless_email_produces_zero_cached_rows(frozen_today, monkeypatch, emails, provider_path):
    import pipeline

    html = (
        emails.html_via_gmail("linkless")
        if provider_path == "gmail"
        else emails.html_via_imap("linkless")
    )
    message = EmailMessage(html=html, date="2025-06-19", subject=emails.subject("linkless"))
    _install_provider(monkeypatch, FakeProvider({june(19): [message]}), provider_type=provider_path)

    pipeline.populate_release_cache("2025-06-19", "2025-06-19", 100, 20, log=_silent)

    assert _cache_rows(_read_cache()) == []


@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
def test_album_fixture_populates_identically_through_both_providers(
    frozen_today, monkeypatch, emails, provider_path
):
    import pipeline

    html = (
        emails.html_via_gmail("album_release")
        if provider_path == "gmail"
        else emails.html_via_imap("album_release")
    )
    message = EmailMessage(html=html, date="2025-06-16", subject=emails.subject("album_release"))
    _install_provider(monkeypatch, FakeProvider({june(16): [message]}), provider_type=provider_path)

    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=_silent)

    rows = _cache_rows(_read_cache())
    assert len(rows) == 1
    assert rows[0]["url"] == "https://midnighttapes.bandcamp.com/album/neon-fields"
    assert rows[0]["date"] == "2025-06-16"
    assert rows[0]["artist"] == "Aria Vale"
    assert rows[0]["title"] == "Neon Fields"
