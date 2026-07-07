"""
Session and release metadata persistence utilities.

Stores provider-fetched release metadata (not Bandcamp-enriched) keyed by
release date so we can reuse it across runs and avoid re-downloading
messages for dates we've already processed.

Schema v2 (WP-14 · LOG-11/LOG-21/LOG-22): "which days are done" is ONE
per-day ledger, ``scrape_status.json``::

    { "YYYY-MM-DD": {"empty": bool, "source": "gmail" | "imap" | null} }

A day present in the ledger has been checked with the email provider;
``empty`` distinguishes checked-and-no-results from checked-with-results, and
``source`` records which provider produced the record (set by the pipeline —
the WP-15 provider-switch semantics consume it). The former
``no_results_dates.json`` second store is folded in and deleted by the one-shot
migration (migrations.py). Legacy v1 ledgers (a plain list of ISO dates) are
still readable pre-migration.

Today is never recorded as checked (it isn't final yet) — the ``exclude_today``
invariant is enforced on every ledger write and read. WP-15 (LOG-2)
generalizes it to a trailing **settling window**: the most recent
``SETTLING_WINDOW_DAYS`` calendar days (today included) are never recorded as
checked. Their releases ARE persisted — the skip is ledger-only — so the days
stay re-queryable and a late-arriving email self-heals on the next run. The
ledger is the sole authority for "checked": a day with cached rows but no
ledger record is still missing (re-queried).

Provider-switch honesty (WP-15 · LOG-22/PY-16, decision (a)): each record's
``source`` is consulted on read — when the caller passes the active provider
and a day's record was produced by the *other* provider, the day reads as
not-checked (offered for re-check) rather than silently serving the old
provider's coverage as the new one's.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable

import json_store
from paths import RELEASE_CACHE_PATH, SCRAPE_STATUS_PATH
from util import canonical_release_url, dedupe_by_url
from util import today as _today

CacheType = dict[str, list[dict]]
LedgerType = dict[datetime.date, dict]

CACHE_PATH = RELEASE_CACHE_PATH

# LOG-2 (WP-15): the trailing settling window. The last N calendar days —
# today and the N-1 days before it — are never final: notification emails for
# them may still be arriving (and timezone skew blurs the boundary by up to a
# day, LOG-12), so the ledger never records them as checked. Constant by
# design; no UI knob.
SETTLING_WINDOW_DAYS = 3


def _load_cache() -> CacheType:
    data = json_store.read_json(CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _to_date(val) -> datetime.date | None:
    """Normalize string or date into a date object (YYYY-MM-DD; legacy YYYY/MM/DD)."""
    if val is None:
        return None
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# The per-day ledger (schema v2)
# ---------------------------------------------------------------------------
def _ledger_from_raw(raw) -> LedgerType:
    """Parse the on-disk ledger. Tolerates the legacy v1 list-of-dates shape."""
    ledger: LedgerType = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            day = _to_date(key)
            if not day:
                continue
            record = value if isinstance(value, dict) else {}
            ledger[day] = {
                "empty": bool(record.get("empty", False)),
                "source": record.get("source"),
            }
    elif isinstance(raw, list):
        # Legacy v1 (pre-migration read tolerance): presence means checked.
        for item in raw:
            day = _to_date(item)
            if day:
                ledger[day] = {"empty": False, "source": None}
    return ledger


def first_settling_date() -> datetime.date:
    """The first day of the trailing settling window (LOG-2).

    Days on/after this date (the last ``SETTLING_WINDOW_DAYS`` calendar days,
    today included) are never recorded as checked: they are dropped on every
    ledger write AND ignored on read, so a re-populate re-queries them and
    late-arriving emails self-heal without manual action.
    """
    return _today() - datetime.timedelta(days=SETTLING_WINDOW_DAYS - 1)


def _drop_settling_days(ledger: LedgerType) -> LedgerType:
    """Remove settling-window (and future) days from a ledger in place."""
    cutoff = first_settling_date()
    for day in [day for day in ledger if day >= cutoff]:
        del ledger[day]
    return ledger


def _checked_by(record: dict | None, provider: str | None) -> bool:
    """Does this ledger record count as checked for the active provider?

    LOG-22/PY-16 (decision (a), single-provider-at-a-time): a day checked by
    the *other* provider is offered for re-check — it reads as not-checked for
    the current provider instead of silently serving the old provider's
    coverage. ``provider=None`` means "any provider" (callers with no provider
    context). A record whose ``source`` is None (legacy v1 rows, or cached
    re-persists) has unknown provenance and is trusted for any provider —
    treating it as mismatched would force a surprise re-check of the entire
    history.
    """
    if record is None:
        return False
    if provider is None:
        return True
    source = record.get("source")
    return source is None or source == provider


def _load_ledger() -> LedgerType:
    # Settling-window days are ignored on read even if an older file still
    # lists them (lazy migration: they are dropped on the next write).
    return _drop_settling_days(_ledger_from_raw(json_store.read_json(SCRAPE_STATUS_PATH, {})))


def _serialize_ledger(ledger: LedgerType) -> dict:
    return {
        day.isoformat(): {"empty": ledger[day]["empty"], "source": ledger[day]["source"]}
        for day in sorted(ledger)
    }


def _update_ledger(mutate) -> None:
    """Atomically load-mutate-save the ledger through json_store.

    Settling-window days (today and the ``SETTLING_WINDOW_DAYS - 1`` days
    before it) are always dropped on save: the exclude-today invariant,
    generalized by LOG-2, means the ledger can never claim a still-settling
    day is checked — its emails may still be arriving.
    """

    def mutator(raw):
        ledger = mutate(_ledger_from_raw(raw))
        return _serialize_ledger(_drop_settling_days(ledger))

    json_store.update_json(SCRAPE_STATUS_PATH, mutator, {}, indent=2)


def _record_checked_days(days: Iterable[datetime.date], *, empty: bool, source: str | None) -> None:
    """Merge day records into the ledger.

    Merge rule for a day already present: a checked-with-results record wins
    over checked-and-empty (``empty`` only stays True when both agree — data
    is never demoted to "empty" by a later zero-result mark for the same day),
    and a concrete ``source`` wins over an unknown one.
    """
    days = set(days)
    if not days:
        return

    def mutate(ledger: LedgerType) -> LedgerType:
        for day in days:
            existing = ledger.get(day)
            if existing:
                ledger[day] = {
                    "empty": existing["empty"] and empty,
                    "source": source or existing["source"],
                }
            else:
                ledger[day] = {"empty": empty, "source": source}
        return ledger

    _update_ledger(mutate)


def get_full_release_cache() -> list[dict]:
    """
    Return all cached release metadata, flattened across all dates.
    """
    cache = _load_cache()
    all_items: list[dict] = []
    for day in sorted(cache.keys()):
        day_entries = cache.get(day) or []
        if isinstance(day_entries, list):
            all_items.extend(day_entries)
    return dedupe_by_url(all_items)


def _range_days(
    start: datetime.date, end: datetime.date, *, exclude_today: bool
) -> set[datetime.date]:
    """The inclusive date range as a set, optionally without today."""
    today = _today()
    days: set[datetime.date] = set()
    cursor = start
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        if not (exclude_today and cursor == today):
            days.add(cursor)
        cursor += one_day
    return days


def mark_dates_scraped(
    dates: Iterable[datetime.date],
    *,
    exclude_today: bool = True,
    empty: bool = False,
    source: str | None = None,
) -> None:
    """
    Mark specific dates as having been checked with the email provider.
    """
    today = _today()
    to_add = {
        day
        for day in dates
        if isinstance(day, datetime.date) and not (exclude_today and day == today)
    }
    _record_checked_days(to_add, empty=empty, source=source)


def mark_date_range_scraped(
    start: datetime.date,
    end: datetime.date,
    *,
    exclude_today: bool = True,
    empty: bool = False,
    source: str | None = None,
) -> None:
    """Mark a contiguous date range as checked."""
    if start > end:
        return
    to_add = _range_days(start, end, exclude_today=exclude_today)
    _record_checked_days(to_add, empty=empty, source=source)


def mark_dates_not_scraped(dates: Iterable[datetime.date]) -> None:
    """Explicitly mark dates as not-scraped (removes their ledger records).

    # Resurrected in WP-15 for LOG-3: the pipeline's ``refresh=1`` re-check
    # clears the selected days here before re-querying, so a refreshed range
    # that comes back empty is recorded empty-again instead of left stale.
    """
    to_drop = {day for day in dates if isinstance(day, datetime.date)}
    if not to_drop:
        return

    def mutate(ledger: LedgerType) -> LedgerType:
        for day in to_drop:
            ledger.pop(day, None)
        return ledger

    _update_ledger(mutate)


def scrape_status_for_range(
    start: datetime.date, end: datetime.date, *, provider: str | None = None
) -> dict[str, bool]:
    """
    Return a mapping of ISO date -> checked flag for the inclusive range.
    Settling-window days (today included) are always False — still pending
    (LOG-2) — and, when ``provider`` is given, days checked by a different
    provider read False too (offered for re-check, LOG-22).
    """
    status = {}
    checked = _load_ledger()
    cursor = start
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        status[cursor.isoformat()] = _checked_by(checked.get(cursor), provider)
        cursor += one_day
    return status


def persist_release_metadata(
    releases: Iterable[dict],
    *,
    exclude_today: bool = True,
    source: str | None = None,
    mark_within: tuple[datetime.date, datetime.date] | None = None,
) -> None:
    """
    Save release metadata into the cache, keyed by release date.

    Every row is persisted regardless of its date — the exclude-today /
    settling-window skip is LEDGER-only (LOG-2): dropping a fetched release
    would lose data, while an unmarked day merely stays re-queryable (merge
    is by URL, so re-querying is harmless). ``exclude_today`` gates only the
    ledger mark below (the settling-window drop applies on every write
    regardless).

    ``mark_within`` (LOG-12 query pad): when given, only persisted days inside
    that inclusive range may be recorded checked. A padded refresh query can
    over-fetch rows bucketed just outside the queried range; those rows are
    still cached, but their days were only partially covered and must not be
    claimed as checked.

    URLs are canonicalized on the way in (LOG-9 store-write half), so the
    release cache is only ever keyed by canonical URLs. ``source`` names the
    provider whose run produced the data (recorded on the day's ledger entry);
    pass ``None`` when merely re-persisting cached rows so an existing
    provider attribution is never overwritten.
    """
    canonicalized: list[dict] = []
    for release in releases:
        url = release.get("url")
        canonical = canonical_release_url(url) if url else None
        if canonical and canonical != url:
            release = {**release, "url": canonical}
        canonicalized.append(release)
    releases = canonicalized
    scraped_days: set[datetime.date] = set()

    def mutate_cache(cache):
        if not isinstance(cache, dict):
            cache = {}
        for release in releases:
            day = _to_date(release.get("date"))
            if not day:
                continue
            key = day.isoformat()
            existing = cache.get(key, [])
            # avoid duplicates for the same day by URL
            cache[key] = dedupe_by_url([*existing, release])
            scraped_days.add(day)
        return cache

    json_store.update_json(CACHE_PATH, mutate_cache, {}, indent=2)
    # Invariant (I2): the cache write above precedes the ledger mark below —
    # a day is only ever recorded checked once its data is durable.
    if mark_within is not None:
        lo, hi = mark_within
        scraped_days = {day for day in scraped_days if lo <= day <= hi}
    if scraped_days:
        mark_dates_scraped(scraped_days, exclude_today=exclude_today, empty=False, source=source)


def cached_releases_for_range(
    start: datetime.date, end: datetime.date, *, provider: str | None = None
) -> tuple[list[dict], list[datetime.date]]:
    """
    Return (cached_releases, missing_dates) for the inclusive date range.

    The LEDGER is the sole authority for "checked" (LOG-2): missing_dates are
    exactly the days without a (current-provider-valid, LOG-22) ledger record
    — cached rows for such days are still returned for display/merge, but the
    day is re-queried. Settling-window days are never in the ledger, so they
    are always missing; checked-and-empty days (LOG-11) are never missing.
    """
    cache = _load_cache()
    checked = _load_ledger()
    cursor = start
    cached: list[dict] = []
    missing: list[datetime.date] = []
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        releases_for_day = cache.get(cursor.isoformat())
        if releases_for_day:
            cached.extend(releases_for_day)
        if not _checked_by(checked.get(cursor), provider):
            missing.append(cursor)
        cursor += one_day
    return dedupe_by_url(cached), missing


def collapse_date_ranges(dates: list[datetime.date]) -> list[tuple[datetime.date, datetime.date]]:
    """Collapse a list of dates into contiguous inclusive ranges."""
    if not dates:
        return []
    dates = sorted(set(dates))
    ranges: list[tuple[datetime.date, datetime.date]] = []
    start = prev = dates[0]
    for day in dates[1:]:
        if day == prev + datetime.timedelta(days=1):
            prev = day
            continue
        ranges.append((start, prev))
        start = prev = day
    ranges.append((start, prev))
    return ranges


def persist_empty_date_range(
    start: datetime.date,
    end: datetime.date,
    *,
    exclude_today: bool = True,
    source: str | None = None,
) -> None:
    """
    Record a contiguous date range that returned no provider results so we
    avoid querying it again. One ledger write: the days are checked with
    ``empty: true`` (LOG-11 — no second store). Optionally excludes today.
    """
    if start > end:
        return
    to_add = _range_days(start, end, exclude_today=exclude_today)
    _record_checked_days(to_add, empty=True, source=source)
