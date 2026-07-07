import datetime
import json

import util
from bandcamp_email_parser import parse_release_email
from email_provider import AuthenticationError, SearchQuery
from provider_factory import create_provider, get_current_provider_type
from session_store import (
    cached_releases_for_range,
    collapse_date_ranges,
    mark_date_range_scraped,
    persist_empty_date_range,
    persist_release_metadata,
)
from util import construct_release, dedupe_by_date, dedupe_by_url, parse_date


class MaxResultsExceeded(Exception):
    def __init__(self, max_results: int, found: int):
        super().__init__(
            f"Exceeded maximum number of results per search (max={max_results}, num results={found})"
        )
        self.max_results = max_results
        self.found = found


class ParseError(Exception):
    """Wholesale failure turning downloaded messages into releases.

    Per-message parse problems are counted skips inside
    ``construct_release_list``; this wraps the crash case where the parse stage
    itself blew up, so the SSE worker can report error code ``parse`` (WP-10).
    """


class ProgressEmitter:
    """Typed progress reporting for populate runs (WP-10 · ARC-1/ARCH-4).

    The SSE route hands the pipeline an instance wired to its event queue via
    ``send_event`` (called with one payload dict per progress/log event);
    plain log callables (tests, CLI ``print``) are adapted via :meth:`ensure`
    and receive only the human-readable ``text`` line, so every pre-WP-10
    ``log=...`` call site keeps working unchanged.

    Event payload shape (protocol v1 — consumed by dashboard.js, and by the
    WP-22 progress UI later):

    ``{v: 1, phase, current, total, message, level, text}``

    - ``phase``: one of :data:`PHASES` or ``None`` for generic lines.
    - ``current``/``total``: nullable ints; both present means the client may
      render a determinate progress bar, absent means indeterminate.
    - ``level``: ``info`` | ``warn`` | ``error``.
    - ``message``/``text``: the same human-readable line. ``text`` is the
      log-box fallback the client always appends; ``message`` is the copy
      channel (the UX plan owns wording, the protocol just carries it).

    Terminal outcomes are NOT emitted here: the pipeline raises, and the SSE
    worker maps the exception to a terminal ``event: error`` code — or emits
    ``event: done`` with this emitter's accumulated ``new_releases`` /
    ``days_scraped`` summary.

    The instance is itself callable (``emitter("line")``) so it can be passed
    anywhere a bare ``log`` callable is expected (providers, tests).
    """

    PHASES = ("query", "download", "parse", "persist", "cache")
    LEVELS = ("info", "warn", "error")

    def __init__(self, send_event=None, log=None):
        self._send_event = send_event
        self._log = log
        self.new_releases = 0
        self.days_scraped = 0

    @classmethod
    def ensure(cls, log) -> "ProgressEmitter":
        """Return ``log`` itself if it is already an emitter, else wrap it."""
        if isinstance(log, cls):
            return log
        return cls(log=log if callable(log) else None)

    def emit(
        self,
        text,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        level: str = "info",
    ) -> None:
        text = str(text)
        if self._send_event is not None:
            self._send_event(
                {
                    "v": 1,
                    "phase": phase,
                    "current": current,
                    "total": total,
                    "message": text,
                    "level": level,
                    "text": text,
                }
            )
        elif self._log is not None:
            self._log(text)

    def __call__(self, text, **kwargs) -> None:
        """Bare-log compatibility: ``emitter("line")`` == ``emit("line")``."""
        self.emit(text, **kwargs)


def encode_event_payload(payload: dict) -> str:
    """Serialize one SSE payload to a single-line JSON string.

    Lives here (not in server.py) because server.py is grep-guarded against
    ``json.*`` usage (WP-05 store-hardening proof) — its JSON IO must flow
    through dedicated modules.
    """
    return json.dumps(payload)


def _provider_fetch_log(emit: ProgressEmitter):
    """Adapter forwarding a provider's ``fetch(log=...)`` lines as download events.

    The provider's own per-batch ``Downloading messages i to j`` line is
    dropped: WP-10 drives batching from the pipeline and emits a typed
    download event per batch (with run-level ``current``/``total``), so the
    provider's copy would be a duplicate with chunk-relative numbers.
    Everything else (e.g. IMAP per-message fetch warnings) passes through.
    Interim seam until WP-16 hands providers the emitter directly.
    """

    def forward(text):
        text = str(text)
        if text.startswith("Downloading messages"):
            return
        lowered = text.lower()
        # Rate-limit retry lines (WP-16/LOG-17) are visible as warnings so the
        # user can see the run is backing off, not stuck.
        level = "warn" if lowered.startswith("warning") or "retrying" in lowered else "info"
        emit(text, phase="download", level=level)

    return forward


def _countable_days(start: datetime.date, end: datetime.date) -> int:
    """Days in [start, end] the scrape ledger can record (today is never marked)."""
    count = 0
    day = start
    today = util.today()
    while day <= end:
        if day != today:
            count += 1
        day += datetime.timedelta(days=1)
    return count


def construct_release_list(emails: dict, *, log=print, source: str | None = None) -> list[dict]:
    """Parse email messages into release lists.

    ``source`` stamps each constructed release with the provider that produced
    it ("gmail" | "imap") — set here at the pipeline level, never inside the
    provider adapters (LOG-21/LOG-22 schema half).
    """
    emit = ProgressEmitter.ensure(log)
    emit("Parsing messages...", phase="parse", current=0, total=len(emails))
    releases_unsifted = []
    skipped = 0
    for _msg_id, email in emails.items():
        # Handle both EmailMessage objects and legacy dict format
        if hasattr(email, "html"):
            # EmailMessage from provider
            html_text = email.html
            raw_date = email.date if email.date else None
            subject = email.subject
        else:
            # Legacy dict format. Providers only ever hand us EmailMessage
            # objects or these legacy dicts, so there is no third shape to
            # fall back to (the old str-email branch was unreachable, CQ-01).
            html_text = email.get("html")
            raw_date = email.get("date")
            subject = email.get("subject", "")

        # Normalize the date up front. Providers hand us YYYY-MM-DD on the
        # happy path, but a missing/garbled Date header surfaces as "" (IMAP)
        # or the raw header text (Gmail / legacy dicts). A bad date must cost
        # one message, never the whole run (CQ-14/PY-7).
        parsed_date = parse_date(raw_date, allow_none=True)
        date = parsed_date.strftime("%Y-%m-%d") if parsed_date else None

        if not html_text:
            skipped += 1
            continue

        try:
            _placeholder, release_url, is_track, artist_name, release_title, page_name = (
                parse_release_email(html_text, subject)
            )
        except Exception as exc:
            skipped += 1
            emit(f"Warning: failed to parse one message: {exc}", phase="parse", level="warn")
            continue

        # Only keep emails we could match to a Bandcamp release URL.
        if not release_url:
            skipped += 1
            continue

        # A release without a parseable date cannot be bucketed: drop it here
        # with a counted skip rather than letting a date-less row crash
        # dedupe_by_date or linger unbucketable in the cache (LOG-24/PY-18).
        if date is None:
            skipped += 1
            emit(
                "Warning: skipped one message with a missing or unparseable date.",
                phase="parse",
                level="warn",
            )
            continue

        # release_url is guaranteed non-None here (checked above), so the old
        # "not all fields are None" guard was always true — dead (CQ-01).
        releases_unsifted.append(
            construct_release(
                date=date,
                release_url=release_url,
                is_track=is_track,
                artist_name=artist_name,
                release_title=release_title,
                page_name=page_name,
                source=source,
            )
        )

    # Sift releases with identical urls
    emit("Checking for releases with identical URLS...", phase="parse")
    if skipped:
        emit(f"Skipped {skipped} message(s) due to parse errors.", phase="parse", level="warn")
    releases = dedupe_by_url(releases_unsifted)

    return releases


def populate_release_cache(
    after_date: str, before_date: str, max_results: int, batch_size: int, log=print
) -> None:
    """
    Use cached email-scraped release metadata for previously seen dates.
    Only hit email provider for dates in the requested range that have no cache entry.

    Progress is reported through a :class:`ProgressEmitter` (pass one as
    ``log``, or any plain callable for text-only output). The emitter also
    accumulates the run summary (``new_releases``/``days_scraped``) that the
    SSE route's terminal ``event: done`` reports.
    """
    emit = ProgressEmitter.ensure(log)
    start_date = parse_date(after_date)
    end_date = parse_date(before_date)
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date")

    cached_releases, missing_dates = cached_releases_for_range(start_date, end_date)
    missing_ranges: list[tuple[datetime.date, datetime.date]] = list(
        collapse_date_ranges(missing_dates)
    )
    releases = list(cached_releases)

    # The active provider tags everything this run records: each release row
    # and each ledger day carries source = "gmail" | "imap" (LOG-21/LOG-22
    # schema half — the switch-behavior half is WP-15's). Set here at the
    # pipeline level, never in the provider adapters.
    provider_type = get_current_provider_type()
    provider_name = "IMAP" if provider_type == "imap" else "Gmail"

    if missing_ranges:
        emit(
            f"The following date ranges will be downloaded from {provider_name}:",
            phase="query",
            current=0,
            total=len(missing_ranges),
        )
        for start_missing, end_missing in missing_ranges:
            emit(f"  {start_missing} to {end_missing}", phase="query")
    else:
        emit(
            f"This date range has already been scraped; no {provider_name} download needed.",
            phase="cache",
        )
        # Still need to dedupe and persist cached releases. No source is
        # passed: this run downloaded nothing, so re-persisting cached rows
        # must not overwrite the days' original provider attribution.
        deduped = dedupe_by_date(releases, keep="last")
        persist_release_metadata(deduped, exclude_today=True)
        emit("")
        emit(f"Loaded {len(deduped)} unique releases from cache.", phase="cache")
        return

    total_ranges = len(missing_ranges)
    provider = None
    try:
        provider = create_provider()
        provider.authenticate()

        for range_index, (start_missing, end_missing) in enumerate(missing_ranges, start=1):
            query_after = start_missing.strftime("%Y-%m-%d")
            query_before = (end_missing + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            emit("")
            emit(
                f"Querying {provider_name} for {query_after} to {query_before}...",
                phase="query",
                current=range_index,
                total=total_ranges,
            )
            try:
                # Build search query based on provider type
                search_query = SearchQuery(
                    sender="noreply@bandcamp.com",
                    subject_contains="New release from",
                    after_date=query_after.replace("-", "/"),  # Provider expects YYYY/MM/DD
                    before_date=query_before.replace("-", "/"),
                )
                message_ids = provider.search(
                    search_query,
                    max_results=max_results,
                    log=lambda text: emit(text, phase="query"),
                )
                # Enforce max_results limit explicitly so callers can surface the condition.
                if max_results and len(message_ids) > max_results:
                    raise MaxResultsExceeded(max_results, len(message_ids))
            except MaxResultsExceeded:
                raise
            except Exception as exc:
                emit(f"ERROR: {exc}", phase="query", level="error")
                raise
            if not message_ids:
                emit(
                    f"No messages found for {query_after} to {query_before}",
                    phase="query",
                    current=range_index,
                    total=total_ranges,
                )
                # LOG-23/PY-17 gate: a zero-result search may only write
                # empty-day ledger records when the provider corroborates it.
                # IMAP search is folder-scoped — a mis-selected folder returns
                # nothing and would otherwise poison the ledger with
                # checked-and-empty days that are never re-queried. Gmail has
                # no such hook (its search is account-global, no wrong-folder
                # failure mode), so its empty results stay trusted as before.
                trusted = True
                corroborate = getattr(provider, "corroborate_empty_result", None)
                if corroborate is not None:
                    trusted, diagnostic = corroborate(search_query)
                    if diagnostic:
                        emit(
                            diagnostic,
                            phase="query",
                            level="info" if trusted else "warn",
                        )
                if trusted:
                    persist_empty_date_range(
                        start_missing, end_missing, exclude_today=True, source=provider_type
                    )
                    emit.days_scraped += _countable_days(start_missing, end_missing)
                continue
            total_messages = len(message_ids)
            emit(
                f"Found {total_messages} messages for {query_after} to {query_before}",
                phase="download",
                current=0,
                total=total_messages,
            )
            # WP-10: the pipeline drives download batching so it can emit
            # determinate progress (current/total) per batch — the provider
            # fetches exactly the same chunks it previously chunked itself.
            step = batch_size if batch_size and batch_size > 0 else total_messages
            emails: dict = {}
            try:
                for batch_start in range(0, total_messages, step):
                    batch = message_ids[batch_start : batch_start + step]
                    emit(
                        f"Downloading messages {batch_start} to "
                        f"{min(batch_start + len(batch), total_messages)}",
                        phase="download",
                        current=batch_start,
                        total=total_messages,
                    )
                    emails.update(
                        provider.fetch(batch, batch_size=step, log=_provider_fetch_log(emit))
                    )
                emit(
                    f"Downloaded {len(emails)} messages",
                    phase="download",
                    current=total_messages,
                    total=total_messages,
                )
            except Exception as exc:
                emit(f"ERROR: {exc}", phase="download", level="error")
                raise
            try:
                new_releases = construct_release_list(emails, log=emit, source=provider_type)
            except Exception as exc:
                raise ParseError(f"Couldn't read the downloaded messages: {exc}") from exc
            emit(
                f"Parsed {len(new_releases)} releases from {provider_name} "
                f"for {query_after} to {query_before}.",
                phase="parse",
                current=len(emails),
                total=len(emails),
            )
            releases.extend(new_releases)
            emit.new_releases += len(new_releases)
            # Invariant (LOG-1/CQ-10): persist this range's releases BEFORE
            # marking the range scraped. The scrape ledger must never claim a
            # day whose data is not durable — under the never-re-fetch
            # principle, mark-before-persist turns any later failure into
            # permanent silent data loss. Keep-last arbitration runs against
            # the cache plus earlier ranges so the winning row per URL matches
            # the previous end-of-run persist; only this range's winners are
            # written (no re-persisting of earlier data).
            emit(
                f"Saving releases for {query_after} to {query_before}...",
                phase="persist",
                current=range_index,
                total=total_ranges,
            )
            new_urls = {release["url"] for release in new_releases}
            deduped_so_far = dedupe_by_date(releases, keep="last")
            persist_release_metadata(
                [release for release in deduped_so_far if release.get("url") in new_urls],
                exclude_today=True,
                source=provider_type,
            )
            # Mark the entire queried span as checked so we do not re-fetch it.
            # This must stay the LAST step of each range. Days in the span that
            # gained no releases are checked-and-empty; the ledger merge keeps
            # empty=False for the days persist_release_metadata just recorded.
            mark_date_range_scraped(
                start_missing, end_missing, exclude_today=True, empty=True, source=provider_type
            )
            emit.days_scraped += _countable_days(start_missing, end_missing)
    except AuthenticationError as exc:
        emit(f"ERROR: Authentication failed: {exc}", level="error")
        raise
    except Exception as exc:
        emit(f"ERROR: {exc}", level="error")
        raise
    finally:
        if provider is not None:
            try:
                provider.close()
            except Exception:
                pass

    # Deduplicate on URL after combining cached + new. Each range already
    # persisted its own releases above (before its scraped mark), so no tail
    # persist is needed — a second write here would double-persist.
    deduped = dedupe_by_date(releases, keep="last")

    emit("")
    emit(f"Loaded {len(deduped)} unique releases including cache.", phase="persist")
