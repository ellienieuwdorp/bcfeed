"""WP-10 · Typed SSE protocol for /populate-range-stream (ARC-1/ARCH-4).

Covers:

- Progress/log events are JSON payloads ``{v, phase, current, total, message,
  level, text}`` with a fixed phase/level vocabulary — never bare prose.
- A 3-range run emits query and download events with ``current``/``total``
  sufficient for a determinate progress bar, and terminates in ``event: done``
  with ``{new_releases, days_scraped}``.
- Failure can never render as success: an injected mid-run exception yields a
  terminal ``event: error`` with the right ``code`` and is NEVER followed by
  ``event: done`` (the pre-WP-10 defect).
- Exception → code mapping: MaxResultsExceeded → ``max_results``,
  AuthenticationError → ``auth``, ProviderError → ``gmail``, a parse-stage
  crash → ``parse``, anything else → ``internal``.
- WP-05 lock lifetime through the typed protocol: disconnect mid-run plus an
  immediate re-request is rejected with code ``busy`` ("already running").

Import discipline (see conftest): modules referenced as ``import x`` inside
tests/fixtures so the autouse data-dir reload is honored; server.py binds
store paths at import so the ``server_mod`` fixture reloads it per test.
"""

from __future__ import annotations

import datetime
import importlib
import json
import threading
import time

import pytest

from email_provider import EmailMessage, ProviderError, SearchQuery

PHASES = {"query", "download", "parse", "persist", "cache"}
LEVELS = {"info", "warn", "error"}
ERROR_CODES = {"auth", "max_results", "gmail", "parse", "internal", "busy"}
EVENT_KEYS = {"v", "phase", "current", "total", "message", "level", "text"}

POPULATE_URL = "/populate-range-stream?start=2025-06-10&end=2025-06-16"


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


@pytest.fixture
def past_gate(server_mod, monkeypatch):
    """Route around the credential/keychain checks so the worker runs."""
    monkeypatch.setattr(server_mod, "get_current_provider_type", lambda: "imap")
    monkeypatch.setattr(server_mod, "_has_credentials_for_provider", lambda: True)
    return server_mod


def parse_sse(body: str) -> list[tuple[str | None, str]]:
    """Parse an SSE body into [(event_name_or_None, data), ...] blocks."""
    events: list[tuple[str | None, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name: str | None = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        events.append((name, "\n".join(data_lines)))
    return events


def message_events(events) -> list[dict]:
    return [json.loads(data) for name, data in events if name is None]


def terminal_events(events) -> list[tuple[str, dict]]:
    return [(name, json.loads(data)) for name, data in events if name is not None]


def june(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


def release_html(slug: str) -> str:
    return (
        f"<html><body><p><b>Page {slug}</b> just released <i>Album {slug}</i> "
        f'by Artist {slug}: <a href="https://{slug}.bandcamp.com/album/{slug}">'
        f"listen</a></p></body></html>"
    )


def release_message(slug: str, date: str) -> EmailMessage:
    return EmailMessage(html=release_html(slug), date=date, subject=f"New release from Page {slug}")


class FakeProvider:
    """EmailProvider double driven by a {date: [EmailMessage]} plan.

    ``search`` returns one id per planned message in the queried window;
    ``fetch`` resolves any subset of those ids (the WP-10 pipeline batches the
    id list itself). ``fail_search_with`` raises that exception on the Nth
    search (1-based); ``overflow_search`` returns more ids than max_results.
    """

    def __init__(self, emails_by_day, *, fail_search_with=None, fail_on_call=1, overflow=0):
        self.emails_by_day = {day: list(msgs) for day, msgs in emails_by_day.items()}
        self.fail_search_with = fail_search_with
        self.fail_on_call = fail_on_call
        self.overflow = overflow
        self.search_calls = 0
        self.fetch_calls: list[list[str]] = []
        self.closed = False

    def authenticate(self) -> None:
        pass

    def search(self, query: SearchQuery, max_results: int = 100, log=None) -> list[str]:
        self.search_calls += 1
        if self.fail_search_with is not None and self.search_calls == self.fail_on_call:
            raise self.fail_search_with
        start = datetime.datetime.strptime(query.after_date, "%Y/%m/%d").date()
        end_exclusive = datetime.datetime.strptime(query.before_date, "%Y/%m/%d").date()
        ids = []
        day = start
        while day < end_exclusive:
            for index in range(len(self.emails_by_day.get(day, []))):
                ids.append(f"{day.isoformat()}#{index}")
            day += datetime.timedelta(days=1)
        if self.overflow:
            return ids + [f"overflow#{i}" for i in range(self.overflow)]
        return ids[:max_results]

    def fetch(self, message_ids, batch_size: int = 20, log=None) -> dict[str, EmailMessage]:
        self.fetch_calls.append(list(message_ids))
        out = {}
        for message_id in message_ids:
            day_iso, _, index = message_id.partition("#")
            out[message_id] = self.emails_by_day[datetime.date.fromisoformat(day_iso)][int(index)]
        return out

    def close(self) -> None:
        self.closed = True


def install_provider(monkeypatch, provider) -> None:
    import pipeline

    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: "imap")


def three_range_setup(seed):
    """Jun 12 + Jun 15 pre-scraped → Jun 10-16 collapses to 3 missing ranges
    (10-11), (13-14), (16) covering 5 days with one message each."""
    seed.scrape_status([june(12), june(15)])
    missing = [june(10), june(11), june(13), june(14), june(16)]
    return {day: [release_message(f"artist-{day.day:02d}", day.isoformat())] for day in missing}


def run_stream(server_mod, url: str = POPULATE_URL):
    resp = server_mod.app.test_client().get(url)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    return parse_sse(resp.get_data(as_text=True))


def wait_unlocked(server_mod, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not server_mod.POPULATE_LOCK.locked():
            return True
        time.sleep(0.01)
    return not server_mod.POPULATE_LOCK.locked()


# ---------------------------------------------------------------------------
# Happy path: typed progress events + terminal done
# ---------------------------------------------------------------------------
def test_three_range_run_emits_typed_events_and_done(past_gate, seed, monkeypatch):
    install_provider(monkeypatch, FakeProvider(three_range_setup(seed)))
    events = run_stream(past_gate)

    messages = message_events(events)
    assert messages, "run must emit progress/log events"
    for payload in messages:
        assert set(payload) == EVENT_KEYS
        assert payload["v"] == 1
        assert payload["phase"] is None or payload["phase"] in PHASES
        assert payload["level"] in LEVELS
        assert isinstance(payload["text"], str)
        assert payload["message"] == payload["text"]
        for key in ("current", "total"):
            assert payload[key] is None or isinstance(payload[key], int)

    # Determinate-bar material: query events step current 1..3 of total 3…
    query_steps = [
        (p["current"], p["total"])
        for p in messages
        if p["phase"] == "query" and p["current"] and p["total"]
    ]
    assert set(query_steps) >= {(1, 3), (2, 3), (3, 3)}
    # …and every download event carries current/total ints for its range.
    downloads = [p for p in messages if p["phase"] == "download"]
    assert downloads
    for p in downloads:
        assert isinstance(p["current"], int) and isinstance(p["total"], int)
        assert 0 <= p["current"] <= p["total"]
    # Each 1-message batch reports completion current == total.
    assert any(p["current"] == p["total"] > 0 for p in downloads)

    # Exactly one terminal event: done with the run summary.
    terminals = terminal_events(events)
    assert len(terminals) == 1
    name, payload = terminals[0]
    assert name == "done"
    assert payload == {"new_releases": 5, "days_scraped": 5}
    # …and it is the last event of the stream.
    assert events[-1][0] == "done"


def test_fully_cached_range_reports_zero_new(past_gate, seed, monkeypatch, make_release):
    seed.scrape_status([june(16)])
    seed.releases({june(16): [make_release()]})
    install_provider(monkeypatch, FakeProvider({}))
    events = run_stream(past_gate, "/populate-range-stream?start=2025-06-16&end=2025-06-16")
    terminals = terminal_events(events)
    assert terminals == [("done", {"new_releases": 0, "days_scraped": 0})]
    assert any(p["phase"] == "cache" for p in message_events(events))


# ---------------------------------------------------------------------------
# Failure can never render as success
# ---------------------------------------------------------------------------
def test_midrun_provider_failure_yields_gmail_code_and_never_done(past_gate, seed, monkeypatch):
    provider = FakeProvider(
        three_range_setup(seed),
        fail_search_with=ProviderError("injected provider failure"),
        fail_on_call=2,
    )
    install_provider(monkeypatch, provider)
    events = run_stream(past_gate)

    names = [name for name, _ in events if name is not None]
    assert names == ["error"], f"only terminal must be error, got {names}"
    _, payload = terminal_events(events)[0]
    assert payload["code"] == "gmail"
    assert "injected provider failure" in payload["message"]
    assert events[-1][0] == "error"
    # The log survives: range 1 completed and streamed before the failure.
    texts = [p["text"] for p in message_events(events)]
    assert any("2025-06-10" in t for t in texts)


def test_parse_stage_crash_yields_parse_code_and_never_done(past_gate, seed, monkeypatch):
    import pipeline

    def exploding_parse(emails, *, log=print):
        raise RuntimeError("injected parse crash")

    install_provider(monkeypatch, FakeProvider(three_range_setup(seed)))
    monkeypatch.setattr(pipeline, "construct_release_list", exploding_parse)
    events = run_stream(past_gate)

    assert [name for name, _ in events if name] == ["error"]
    _, payload = terminal_events(events)[0]
    assert payload["code"] == "parse"
    assert "injected parse crash" in payload["message"]


def test_max_results_exceeded_yields_max_results_code(past_gate, seed, monkeypatch):
    seed.scrape_status([june(12), june(15)])
    provider = FakeProvider(three_range_setup(seed), overflow=5000)
    install_provider(monkeypatch, provider)
    events = run_stream(past_gate)

    assert [name for name, _ in events if name] == ["error"]
    _, payload = terminal_events(events)[0]
    assert payload["code"] == "max_results"
    assert "Maximum results reached" in payload["message"]


def test_auth_failure_yields_auth_code(past_gate, monkeypatch):
    from email_provider import AuthenticationError

    def failing_populate(start, end, max_results, batch_size=20, log=None):
        raise AuthenticationError("bad credentials")

    monkeypatch.setattr(past_gate, "populate_release_cache", failing_populate)
    events = run_stream(past_gate)
    assert terminal_events(events)[0][0] == "error"
    assert terminal_events(events)[0][1]["code"] == "auth"


def test_unexpected_worker_crash_yields_internal_code(past_gate, monkeypatch):
    def exploding_populate(start, end, max_results, batch_size=20, log=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(past_gate, "populate_release_cache", exploding_populate)
    events = run_stream(past_gate)
    names = [name for name, _ in events if name]
    assert names == ["error"]
    _, payload = terminal_events(events)[0]
    assert payload["code"] == "internal"
    assert "boom" in payload["message"]


def test_preflight_rejections_are_typed_error_events(server_mod):
    events = run_stream(server_mod, "/populate-range-stream")
    assert terminal_events(events) == [
        ("error", {"code": "internal", "message": "Missing start/end"})
    ]

    events = run_stream(server_mod, "/populate-range-stream?start=2025-06-02&end=2025-06-01")
    assert terminal_events(events)[0][1]["code"] == "internal"


# ---------------------------------------------------------------------------
# WP-05 lock lifetime through the typed protocol
# ---------------------------------------------------------------------------
def test_disconnect_then_rerequest_reports_busy(past_gate, monkeypatch):
    started = threading.Event()
    unblock = threading.Event()

    def blocking_populate(start, end, max_results, batch_size=20, log=None):
        if log:
            log("worker running")
        started.set()
        assert unblock.wait(timeout=10), "test never unblocked the fake worker"

    monkeypatch.setattr(past_gate, "populate_release_cache", blocking_populate)

    # Connect, then disconnect without consuming the stream (client vanished).
    resp1 = past_gate.app.test_client().get(POPULATE_URL, buffered=False)
    assert started.wait(timeout=5), "populate worker never started"
    resp1.close()

    # Immediate re-request while the worker still owns POPULATE_LOCK.
    try:
        events = run_stream(past_gate)
        assert terminal_events(events) == [
            ("error", {"code": "busy", "message": "Another populate is already running"})
        ]
    finally:
        unblock.set()
    assert wait_unlocked(past_gate), "worker exit did not release POPULATE_LOCK"

    # And a fresh run afterwards terminates in done, not error.
    events = run_stream(past_gate)
    assert [name for name, _ in events if name] == ["done"]
