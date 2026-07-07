"""WP-16 · Provider robustness: Gmail + IMAP (Lane B).

Covers, with explicit CQ-71 provider-parity notes per fix:

- LOG-17/CQ-19/PY-9 — Gmail batch download uses the public callback API
  (responses paired to message ids via ``request_id``, never
  ``batch._responses``); 429s retry with bounded exponential backoff (fake
  clock) and surface a visible "retrying" progress event; a per-message 404
  is a counted skip. **IMAP twin:** a per-message fetch error (or an empty
  FETCH response) is a counted skip, never a lost batch.
- LOG-16/PERF-7 — Gmail pagination stops early once max_results is exceeded
  and passes ``maxResults`` through to the list call. **IMAP twin:** the UID
  set is capped before any bodies are fetched.
- LOG-14 — two-stage structural classifier: subject gate (quoted-OR locale
  phrase list; replies/receipts/digests/recommendations rejected) + genuine
  release link. Exercised through BOTH provider extraction paths with a
  synthetic fixture matrix (all fixtures are redacted synthetics — no real
  mail).
- LOG-15 — candidate link selection resists decoy footer/banner links;
  the repeated real release link wins over a first-in-document decoy.
  Provider parity: the parser is shared, exercised through both paths.
- LOG-23/PY-17 — a zero-result first-time IMAP scan writes NO empty-day
  ledger records without folder corroboration, and emits a
  "0 Bandcamp messages" diagnostic. **Gmail-N/A (documented asymmetry):
  Gmail search is account-global — no folder selection exists to mis-scope
  it — so GmailProvider deliberately has no ``corroborate_empty_result``
  hook and its zero-result ranges stay trusted (test below locks that in).**

All tests run against mocked Gmail services / IMAP connections and a fake
clock — no network, no real sleeping.
"""

from __future__ import annotations

import base64
import datetime
from pathlib import Path

import pytest

import pipeline
from email_provider import EmailMessage
from pipeline import MaxResultsExceeded, ProgressEmitter

REPO_ROOT = Path(__file__).resolve().parent.parent

SENDER = "noreply@bandcamp.com"


def _silent(*_args, **_kwargs) -> None:
    """No-op log sink for runs whose output we don't inspect."""


def june(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


# ---------------------------------------------------------------------------
# Fake Gmail service (list + batch with callbacks), fake HttpError, fake clock
# ---------------------------------------------------------------------------
class FakeHttpError(Exception):
    """Shape-compatible stand-in for googleapiclient.errors.HttpError."""

    def __init__(self, status: int, message: str = ""):
        super().__init__(message or f"HTTP {status}")
        self.status_code = status

        class _Resp:
            pass

        self.resp = _Resp()
        self.resp.status = status


def gmail_full_message(html: str, subject: str, date: str = "Mon, 16 Jun 2025 14:30:00 +0000"):
    """A Gmail-API-style 'full' message dict (body CTE already decoded)."""
    data = base64.urlsafe_b64encode(html.encode("utf-8")).decode("ascii")
    return {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date},
            ],
            "body": {"data": data},
        }
    }


def release_html(slug: str) -> str:
    return (
        f"<html><body><p>Greetings friend, <b>Page {slug}</b> just released "
        f"<i>Album {slug}</i> by Artist {slug}, check it out here: "
        f'<a href="https://{slug}.bandcamp.com/album/{slug}">listen</a></p></body></html>'
    )


class _FakeListRequest:
    def __init__(self, service, maxResults, pageToken):
        self.service = service
        self.maxResults = maxResults
        self.pageToken = pageToken

    def execute(self):
        self.service.list_calls.append({"maxResults": self.maxResults, "pageToken": self.pageToken})
        assert self.maxResults is not None, "maxResults must be passed through (LOG-16)"
        start = int(self.pageToken or 0)
        page = self.service.search_ids[start : start + self.maxResults]
        result = {"messages": [{"id": msg_id} for msg_id in page]}
        if start + len(page) < len(self.service.search_ids):
            result["nextPageToken"] = str(start + len(page))
        return result


class _FakeBatch:
    def __init__(self, service):
        self.service = service
        self._requests = []

    def add(self, request, callback=None, request_id=None):
        assert callback is not None, "batch.add must use the callback API (CQ-19)"
        assert request_id is not None, "responses must be paired via request_id (CQ-19)"
        self._requests.append((request, callback, request_id))

    def execute(self):
        self.service.batches_executed += 1
        for request, callback, request_id in self._requests:
            _kind, msg_id, _fmt = request
            plan = self.service.error_plan.get(msg_id)
            exc = plan.pop(0) if plan else None
            if exc is not None:
                callback(request_id, None, exc)
            else:
                callback(request_id, self.service.message_store[msg_id], None)


class FakeGmailService:
    """The slice of googleapiclient that gmail_client touches.

    ``search_ids`` backs users().messages().list (with pagination);
    ``messages`` maps id -> full message dict; ``error_plan`` maps id -> a
    queue of exceptions to deliver (one per batch attempt) before succeeding.
    """

    def __init__(self, messages=None, search_ids=None, error_plan=None):
        self.message_store = dict(messages or {})
        self.search_ids = list(search_ids if search_ids is not None else self.message_store)
        self.error_plan = {k: list(v) for k, v in (error_plan or {}).items()}
        self.list_calls: list[dict] = []
        self.batches_executed = 0

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, userId, q, maxResults=None, pageToken=None):
        return _FakeListRequest(self, maxResults, pageToken)

    def get(self, userId, id, format):
        return ("get", id, format)

    def new_batch_http_request(self):
        return _FakeBatch(self)


@pytest.fixture
def fake_clock(monkeypatch):
    """Replace the Gmail backoff sleep with a recorder — tests never sleep."""
    import gmail_client

    sleeps: list[float] = []
    monkeypatch.setattr(gmail_client, "_backoff_sleep", sleeps.append)
    return sleeps


def _install_gmail_provider(monkeypatch, service):
    from gmail_provider import GmailProvider

    provider = GmailProvider()
    provider._service = service
    provider.authenticate = lambda: None  # OAuth is out of scope here
    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: "gmail")
    return provider


# ---------------------------------------------------------------------------
# Fake IMAP client
# ---------------------------------------------------------------------------
def release_eml(slug: str, date_header: str = "Mon, 16 Jun 2025 14:30:00 +0000") -> bytes:
    return (
        b"From: Bandcamp <noreply@bandcamp.com>\r\n"
        b"To: listener@example.com\r\n"
        + f"Subject: New release from Page {slug}\r\n".encode("ascii")
        + f"Date: {date_header}\r\n".encode("ascii")
        + b"MIME-Version: 1.0\r\n"
        + b'Content-Type: text/html; charset="utf-8"\r\n'
        + b"\r\n"
        + release_html(slug).encode("utf-8")
    )


class FakeImapClient:
    """The slice of ImapClient that ImapProvider touches (no sockets).

    ``range_uids`` answers the pipeline's dated range search (criteria
    containing SINCE); ``sender_uids`` answers the folder-wide FROM-only
    corroboration search (LOG-23). ``bodies`` maps uid -> raw RFC822 bytes
    (or None for an empty FETCH); ``fetch_errors`` maps uid -> exception.
    """

    def __init__(self, range_uids=None, sender_uids=None, bodies=None, fetch_errors=None):
        self.range_uids = list(range_uids or [])
        self.sender_uids = list(sender_uids or [])
        self.bodies = dict(bodies or {})
        self.fetch_errors = dict(fetch_errors or {})
        self.search_calls: list[list[str]] = []
        self.fetch_calls: list[str] = []

    def authenticate(self, **_kwargs):
        pass

    def uid_search(self, criteria):
        self.search_calls.append(list(criteria))
        if "SINCE" in criteria or "BEFORE" in criteria:
            return list(self.range_uids)
        return list(self.sender_uids)

    def uid_fetch_body(self, msg_id):
        self.fetch_calls.append(msg_id)
        if msg_id in self.fetch_errors:
            raise self.fetch_errors[msg_id]
        return self.bodies.get(msg_id)

    def close(self):
        pass


def _make_imap_provider(fake_client, folder="Bandcamp"):
    from imap_client import ImapConfig
    from imap_provider import ImapProvider

    config = ImapConfig(host="fixture.invalid", username="u", password="p", folder=folder)
    provider = ImapProvider(config)
    provider._client = fake_client
    return provider


def _install_imap_provider(monkeypatch, fake_client, folder="Bandcamp"):
    provider = _make_imap_provider(fake_client, folder=folder)
    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: "imap")
    return provider


def _emitter():
    events: list[dict] = []
    return ProgressEmitter(send_event=events.append), events


def _read_status(start: datetime.date, end: datetime.date) -> dict:
    import session_store

    return session_store.scrape_status_for_range(start, end)


# ===========================================================================
# LOG-17 / CQ-19 / PY-9 — batch callback API, backoff, per-message skips
# ===========================================================================
def test_gmail_batch_callback_pairing_without_private_responses():
    """Parity: Gmail-specific transport (IMAP has no batch; its twin below)."""
    import gmail_client

    ids = [f"msg-{n}" for n in range(5)]
    service = FakeGmailService(
        messages={
            msg_id: gmail_full_message(release_html(msg_id), f"New release from Page {msg_id}")
            for msg_id in ids
        }
    )

    emails = gmail_client.get_messages(service, ids, format="full", batch_size=2, log=None)

    # Responses are keyed by (and paired to) their real message ids.
    assert list(emails) == ids
    for msg_id in ids:
        assert emails[msg_id]["subject"] == f"New release from Page {msg_id}"
        assert msg_id in emails[msg_id]["html"]
        assert emails[msg_id]["date"] == "2025-06-16"
    assert service.batches_executed == 3  # ceil(5/2)

    # Acceptance grep: no private-attribute access anywhere in the client.
    assert "_responses" not in (REPO_ROOT / "gmail_client.py").read_text(encoding="utf-8")


def test_gmail_429_retries_with_backoff_and_visible_retrying_event(
    frozen_today, monkeypatch, fake_clock
):
    """A single-batch 429 retries (fake clock) and the run completes, with a
    'retrying' progress event observed through the typed emitter."""
    ids = ["m1", "m2", "m3"]
    service = FakeGmailService(
        messages={
            msg_id: gmail_full_message(release_html(msg_id), f"New release from Page {msg_id}")
            for msg_id in ids
        },
        error_plan={"m2": [FakeHttpError(429, "Rate limit exceeded")]},
    )
    _install_gmail_provider(monkeypatch, service)
    emitter, events = _emitter()

    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=emitter)

    assert fake_clock == [1.0]  # bounded exponential backoff, first step
    retrying = [e for e in events if "retrying" in e["text"].lower()]
    assert retrying, "the user must see a visible retrying progress event"
    assert retrying[0]["level"] == "warn"
    assert emitter.new_releases == 3
    assert _read_status(june(16), june(16))["2025-06-16"]


def test_gmail_429_exhaustion_raises_honest_error_after_bounded_backoff(fake_clock):
    import gmail_client

    service = FakeGmailService(
        messages={"m1": gmail_full_message(release_html("m1"), "New release from Page m1")},
        error_plan={"m1": [FakeHttpError(429)] * 10},
    )

    with pytest.raises(Exception) as excinfo:
        gmail_client.get_messages(service, ["m1"], format="full", batch_size=20, log=None)

    assert fake_clock == [1.0, 2.0, 4.0]  # bounded: 3 retries, exponential
    assert "--batch" not in str(excinfo.value)  # the bogus flag is gone (PY-9)
    assert "rate-limiting" in str(excinfo.value)


def test_gmail_per_message_404_is_a_counted_skip_not_a_crash(frozen_today, monkeypatch, fake_clock):
    ids = [f"m{n}" for n in range(5)]
    service = FakeGmailService(
        messages={
            msg_id: gmail_full_message(release_html(msg_id), f"New release from Page {msg_id}")
            for msg_id in ids
            if msg_id != "m3"
        },
        search_ids=ids,
        error_plan={"m3": [FakeHttpError(404, "Not Found")]},
    )
    _install_gmail_provider(monkeypatch, service)
    emitter, events = _emitter()

    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=emitter)

    assert emitter.new_releases == 4  # 4 parsed, 1 counted skip, run continued
    assert fake_clock == []  # 404 is permanent: never retried
    assert any("404" in e["text"] and e["level"] == "warn" for e in events)
    assert any("Skipped 1 message(s) that could not be downloaded" in e["text"] for e in events)
    assert _read_status(june(16), june(16))["2025-06-16"]


def test_imap_per_message_fetch_error_is_a_counted_skip_not_a_lost_batch(frozen_today, monkeypatch):
    """IMAP twin of the Gmail 404/batch handling (LOG-17/CQ-71): one raising
    fetch and one empty FETCH response cost two counted skips; the other
    messages survive and the run completes."""
    fake = FakeImapClient(
        range_uids=["1", "2", "3", "4"],
        bodies={"1": release_eml("one"), "4": release_eml("four"), "3": None},
        fetch_errors={"2": OSError("connection reset by peer")},
    )
    _install_imap_provider(monkeypatch, fake)
    emitter, events = _emitter()

    pipeline.populate_release_cache("2025-06-16", "2025-06-16", 100, 20, log=emitter)

    assert emitter.new_releases == 2
    warn_lines = [e["text"] for e in events if e["level"] == "warn"]
    assert any("skipped message 2" in line for line in warn_lines)
    assert any("skipped message 3" in line for line in warn_lines)
    assert any("Skipped 2 message(s) that could not be downloaded" in e["text"] for e in events)
    assert _read_status(june(16), june(16))["2025-06-16"]


# ===========================================================================
# LOG-16 / PERF-7 — early pagination stop (Gmail) · UID cap (IMAP twin)
# ===========================================================================
def test_gmail_over_cap_search_stops_early_and_raises_max_results(frozen_today, monkeypatch):
    cap = 600
    service = FakeGmailService(messages={}, search_ids=[f"m{n}" for n in range(1200)])
    _install_gmail_provider(monkeypatch, service)

    with pytest.raises(MaxResultsExceeded) as excinfo:
        pipeline.populate_release_cache("2025-06-01", "2025-06-16", cap, 20, log=_silent)

    # Early stop: ≤ ceil((cap+1)/500) list calls, each passing maxResults.
    assert len(service.list_calls) <= -(-(cap + 1) // 500) == 2
    assert all(call["maxResults"] == 500 for call in service.list_calls)
    # Nothing was downloaded for an over-cap search.
    assert service.batches_executed == 0
    # The provider returned just enough ids (cap+1) to prove the overflow —
    # not the whole 1200-message result set.
    assert excinfo.value.found == cap + 1
    # And no day was marked checked.
    status = _read_status(june(1), june(16))
    assert not any(status.values())


def test_gmail_under_cap_search_uses_single_capped_list_call(monkeypatch):
    service = FakeGmailService(messages={}, search_ids=[f"m{n}" for n in range(30)])
    provider = _install_gmail_provider(monkeypatch, service)

    from email_provider import SearchQuery

    ids = provider.search(
        SearchQuery(
            sender=SENDER,
            subject_contains="New release from",
            after_date="2025/06/01",
            before_date="2025/06/17",
        ),
        max_results=100,
    )

    assert len(ids) == 30
    assert service.list_calls == [{"maxResults": 101, "pageToken": None}]


def test_imap_uid_set_is_capped_before_any_body_fetch(frozen_today, monkeypatch):
    """IMAP twin of the early stop: an over-cap UID SEARCH raises before a
    single body is fetched."""
    fake = FakeImapClient(range_uids=[str(n) for n in range(50)])
    provider = _install_imap_provider(monkeypatch, fake)

    from email_provider import SearchQuery

    query = SearchQuery(
        sender=SENDER,
        subject_contains="New release from",
        after_date="2025/06/01",
        before_date="2025/06/17",
    )
    assert len(provider.search(query, max_results=10)) == 11  # cap+1, no more

    with pytest.raises(MaxResultsExceeded):
        pipeline.populate_release_cache("2025-06-01", "2025-06-16", 10, 20, log=_silent)

    assert fake.fetch_calls == []  # zero bodies downloaded past the cap
    status = _read_status(june(1), june(16))
    assert not any(status.values())


# ===========================================================================
# LOG-14 — two-stage structural classifier (fixture matrix, both providers)
# ===========================================================================
# Accepted: current-format release notifications — album with "by artist"
# phrasing, track without it, custom-domain artist, a German locale subject,
# and the decoy-footer release (accepted AND resolves the real link, LOG-15).
ACCEPTED_FIXTURES = {
    "album_release": "https://midnighttapes.bandcamp.com/album/neon-fields",
    "track_release": "https://echoparade.bandcamp.com/track/glass-corridor",
    "custom_domain_album": "https://music.staticbloom.net/album/paper-suns",
    "locale_release_de": "https://nachtfarben.bandcamp.com/album/stille-signale",
    "decoy_footer_release": "https://midnighttapes.bandcamp.com/album/neon-fields",
}
# Rejected: same sender (or a reply quoting a notification), all carrying
# /album/ or /track/ links in the body — subject structure must reject them.
REJECTED_FIXTURES = ["decoy_receipt", "decoy_reply", "decoy_digest", "decoy_recommendation"]


def _fixture_message(emails, name: str, provider_path: str, date: str) -> EmailMessage:
    html = emails.html_via_gmail(name) if provider_path == "gmail" else emails.html_via_imap(name)
    return EmailMessage(html=html or "", date=date, subject=emails.subject(name))


@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
@pytest.mark.parametrize("name", sorted(ACCEPTED_FIXTURES))
def test_classifier_accepts_release_notifications(emails, provider_path, name):
    message = _fixture_message(emails, name, provider_path, "2025-06-21")
    releases = pipeline.construct_release_list({"1": message}, log=_silent)
    assert len(releases) == 1
    assert releases[0]["url"] == ACCEPTED_FIXTURES[name]


@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
@pytest.mark.parametrize("name", REJECTED_FIXTURES)
def test_classifier_rejects_non_release_mail_as_counted_skip(emails, provider_path, name):
    message = _fixture_message(emails, name, provider_path, "2025-06-21")
    lines: list[str] = []
    releases = pipeline.construct_release_list({"1": message}, log=lines.append)
    assert releases == []
    assert any("Skipped 1 message(s)" in line for line in lines)


def test_classifier_matrix_is_identical_through_both_providers(emails):
    """CQ-71: the classification outcome per fixture must not depend on which
    provider extracted the HTML."""
    for name in [*ACCEPTED_FIXTURES, *REJECTED_FIXTURES]:
        gmail_result = pipeline.construct_release_list(
            {"1": _fixture_message(emails, name, "gmail", "2025-06-21")}, log=_silent
        )
        imap_result = pipeline.construct_release_list(
            {"1": _fixture_message(emails, name, "imap", "2025-06-21")}, log=_silent
        )
        assert gmail_result == imap_result, f"{name}: provider paths disagree"


def test_album_fixture_still_parses_the_by_artist_variant(emails):
    """The 'by artist' phrasing keeps extracting artist + title (LOG-14 matrix)."""
    from bandcamp_email_parser import parse_release_email

    _img, url, is_track, artist, title, page = parse_release_email(
        emails.html_via_imap("album_release"), emails.subject("album_release")
    )
    assert url == ACCEPTED_FIXTURES["album_release"]
    assert (artist, title, page) == ("Aria Vale", "Neon Fields", "Midnight Tapes")
    assert is_track is False


def test_locale_subject_defeating_the_phrase_list_degrades_to_counted_skip(emails):
    """A subject-copy change the phrase list doesn't know yet costs a visible
    counted skip — never a junk row (LOG-14 acceptance)."""
    message = EmailMessage(
        html=emails.html_via_imap("album_release"),
        date="2025-06-16",
        subject="Bandcamp waves hello in an unknown language",
    )
    lines: list[str] = []
    assert pipeline.construct_release_list({"1": message}, log=lines.append) == []
    assert any("Skipped 1 message(s)" in line for line in lines)


# ===========================================================================
# LOG-15 — decoy-resistant release-link selection (shared parser, both paths)
# ===========================================================================
@pytest.mark.parametrize("provider_path", ["gmail", "imap"])
def test_decoy_footer_fixture_resolves_the_real_release_link(emails, provider_path):
    from bandcamp_email_parser import parse_release_email

    html = (
        emails.html_via_gmail("decoy_footer_release")
        if provider_path == "gmail"
        else emails.html_via_imap("decoy_footer_release")
    )
    # Regression proof: the decoy /album/ link comes FIRST in document order.
    assert html.index("daily.bandcamp.com/album/") < html.index("midnighttapes.bandcamp.com")

    _img, url, *_rest = parse_release_email(html, emails.subject("decoy_footer_release"))
    assert url == "https://midnighttapes.bandcamp.com/album/neon-fields"


def test_single_candidate_custom_domain_selection_unchanged(emails):
    """LOG-15 must not regress the custom-domain path heuristic."""
    from bandcamp_email_parser import parse_release_email

    _img, url, *_rest = parse_release_email(
        emails.html_via_imap("custom_domain_album"), emails.subject("custom_domain_album")
    )
    assert url == "https://music.staticbloom.net/album/paper-suns"


# ===========================================================================
# LOG-23 / PY-17 — IMAP empty-day gate + diagnostics (Gmail-N/A, documented)
# ===========================================================================
def _spy_persist_empty(monkeypatch):
    calls: list[tuple] = []
    real = pipeline.persist_empty_date_range

    def recording(start, end, **kwargs):
        calls.append((start, end))
        return real(start, end, **kwargs)

    monkeypatch.setattr(pipeline, "persist_empty_date_range", recording)
    return calls


def test_wrong_imap_folder_writes_no_empty_day_records_and_is_visible(frozen_today, monkeypatch):
    """A deliberately wrong folder (0 sender messages in it) must not poison
    the ledger — and the user sees a '0 Bandcamp messages' diagnostic."""
    import paths

    fake = FakeImapClient(range_uids=[], sender_uids=[])
    _install_imap_provider(monkeypatch, fake, folder="Newsletters")
    persist_calls = _spy_persist_empty(monkeypatch)
    emitter, events = _emitter()

    pipeline.populate_release_cache("2025-06-10", "2025-06-12", 100, 20, log=emitter)

    assert persist_calls == []  # zero empty-day ledger writes
    assert emitter.days_scraped == 0
    status = _read_status(june(10), june(12))
    assert not any(status.values())
    # LOG-11: the ledger is the only per-day store — nothing may resurrect the
    # retired no_results_dates.json file.
    assert not (paths.DATA_DIR / "no_results_dates.json").exists()

    diagnostics = [e for e in events if "0 Bandcamp messages" in e["text"]]
    assert diagnostics, "the mis-selected folder must be visible, not silent"
    assert diagnostics[0]["level"] == "warn"
    assert 'folder "Newsletters"' in diagnostics[0]["text"]

    # The folder-wide corroboration search was sender-only (FROM, undated).
    assert ["FROM", f'"{SENDER}"'] in fake.search_calls


def test_corroborated_empty_imap_range_is_recorded_with_diagnostic(frozen_today, monkeypatch):
    """When the folder demonstrably receives Bandcamp mail, an empty range IS
    trusted: ledger written, 'matched 0 of N' diagnostic emitted."""
    fake = FakeImapClient(range_uids=[], sender_uids=["7", "9"])
    _install_imap_provider(monkeypatch, fake, folder="INBOX")
    persist_calls = _spy_persist_empty(monkeypatch)
    emitter, events = _emitter()

    pipeline.populate_release_cache("2025-06-10", "2025-06-11", 100, 20, log=emitter)

    assert persist_calls == [(june(10), june(11))]
    assert emitter.days_scraped == 2
    status = _read_status(june(10), june(11))
    assert all(status.values())
    assert any("matched 0 of 2 messages" in e["text"] for e in events)


def test_corroboration_failure_fails_safe_and_leaves_days_unrecorded(frozen_today, monkeypatch):
    """If the corroboration search itself errors, the ledger must not be
    written either (fail safe, never poison)."""

    class FlakyImapClient(FakeImapClient):
        def uid_search(self, criteria):
            self.search_calls.append(list(criteria))
            if "SINCE" in criteria or "BEFORE" in criteria:
                return []
            raise OSError("connection dropped")

    fake = FlakyImapClient()
    _install_imap_provider(monkeypatch, fake, folder="INBOX")
    persist_calls = _spy_persist_empty(monkeypatch)

    pipeline.populate_release_cache("2025-06-10", "2025-06-10", 100, 20, log=_silent)

    assert persist_calls == []
    assert not any(_read_status(june(10), june(10)).values())


def test_gmail_zero_result_stays_trusted_no_folder_corroboration_needed(frozen_today, monkeypatch):
    """LOG-23 is IMAP-specific — documented Gmail-N/A. Gmail search is
    account-global (no folder to mis-select), so GmailProvider has no
    corroborate_empty_result hook and its empty ranges are recorded as
    before."""
    from gmail_provider import GmailProvider

    assert not hasattr(GmailProvider, "corroborate_empty_result")

    service = FakeGmailService(messages={}, search_ids=[])
    _install_gmail_provider(monkeypatch, service)
    persist_calls = _spy_persist_empty(monkeypatch)
    emitter, _events = _emitter()

    pipeline.populate_release_cache("2025-06-10", "2025-06-11", 100, 20, log=emitter)

    assert persist_calls == [(june(10), june(11))]
    assert emitter.days_scraped == 2
    assert all(_read_status(june(10), june(11)).values())
