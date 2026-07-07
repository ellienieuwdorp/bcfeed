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
invariant is enforced on every ledger write and read.
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


def _load_ledger() -> LedgerType:
    return _ledger_from_raw(json_store.read_json(SCRAPE_STATUS_PATH, {}))


def _serialize_ledger(ledger: LedgerType) -> dict:
    return {
        day.isoformat(): {"empty": ledger[day]["empty"], "source": ledger[day]["source"]}
        for day in sorted(ledger)
    }


def _update_ledger(mutate) -> None:
    """Atomically load-mutate-save the ledger through json_store.

    Today is always dropped on save: the exclude-today invariant means the
    ledger can never claim today is checked (its emails are still arriving).
    """

    def mutator(raw):
        ledger = mutate(_ledger_from_raw(raw))
        ledger.pop(_today(), None)
        return _serialize_ledger(ledger)

    json_store.update_json(SCRAPE_STATUS_PATH, mutator, {}, indent=2)


def _record_checked_days(
    days: Iterable[datetime.date], *, empty: bool, source: str | None
) -> None:
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

    # retained for WP-15/LOG-3: currently uncalled, but the re-check/reset flow
    # resurrects it — do NOT delete as dead code (CQ-01 deviation).
    """
    to_drop = {day for day in dates if isinstance(day, datetime.date)}
    if not to_drop:
        return

    def mutate(ledger: LedgerType) -> LedgerType:
        for day in to_drop:
            ledger.pop(day, None)
        return ledger

    _update_ledger(mutate)


def scrape_status_for_range(start: datetime.date, end: datetime.date) -> dict[str, bool]:
    """
    Return a mapping of ISO date -> checked flag for the inclusive range.
    Today's date is always False (not checked).
    """
    status = {}
    checked = _load_ledger()
    today = _today()
    cursor = start
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        status[cursor.isoformat()] = cursor in checked and cursor != today
        cursor += one_day
    return status


def persist_release_metadata(
    releases: Iterable[dict], *, exclude_today: bool = True, source: str | None = None
) -> None:
    """
    Save release metadata into the cache, keyed by release date.
    Skips today's date when exclude_today is True.

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
    today = _today()
    scraped_days: set[datetime.date] = set()

    def mutate_cache(cache):
        if not isinstance(cache, dict):
            cache = {}
        for release in releases:
            day = _to_date(release.get("date"))
            if not day:
                continue
            if exclude_today and day == today:
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
    if scraped_days:
        mark_dates_scraped(scraped_days, exclude_today=exclude_today, empty=False, source=source)


def cached_releases_for_range(
    start: datetime.date, end: datetime.date
) -> tuple[list[dict], list[datetime.date]]:
    """
    Return (cached_releases, missing_dates) for the inclusive date range.
    missing_dates are days that have not been checked yet — a day is covered
    either by cached releases or by a ledger record (which includes
    checked-and-empty days, LOG-11).
    """
    cache = _load_cache()
    checked = _load_ledger()
    cursor = start
    cached: list[dict] = []
    missing: list[datetime.date] = []
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        iso = cursor.isoformat()
        releases_for_day = cache.get(iso)
        if releases_for_day:
            cached.extend(releases_for_day)
        elif cursor not in checked:
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
