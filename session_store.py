"""
Session and release metadata persistence utilities.

Stores Gmail-scraped release metadata (not Bandcamp-enriched) keyed by
release date so we can reuse it across runs and avoid re-downloading
messages for dates we've already processed. Also persists empty-date
ranges and scrape status for the same date buckets.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable, Iterable
from pathlib import Path

import json_store
from paths import EMPTY_DATES_PATH, RELEASE_CACHE_PATH, SCRAPE_STATUS_PATH
from util import dedupe_by_url
from util import today as _today

CacheType = dict[str, list[dict]]

CACHE_PATH = RELEASE_CACHE_PATH
EMPTY_PATH = EMPTY_DATES_PATH


def _load_cache() -> CacheType:
    data = json_store.read_json(CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _save_cache(cache: CacheType) -> None:
    json_store.write_json(CACHE_PATH, cache, indent=2)


def _dates_from_raw(raw) -> set[datetime.date]:
    dates: set[datetime.date] = set()
    for item in raw if isinstance(raw, list) else []:
        day = _to_date(item)
        if day:
            dates.add(day)
    return dates


def _load_date_set(path: Path) -> set[datetime.date]:
    return _dates_from_raw(json_store.read_json(path, []))


def _update_date_set(
    path: Path,
    mutate: Callable[[set[datetime.date]], set[datetime.date]],
    *,
    drop_today: bool = False,
) -> None:
    """Atomically load-mutate-save a date-set store through json_store."""

    def mutator(raw):
        dates = mutate(_dates_from_raw(raw))
        if drop_today:
            # Always treat today as not-scraped.
            dates.discard(_today())
        return sorted(day.isoformat() for day in dates)

    json_store.update_json(path, mutator, [], indent=2)


def _load_empty_dates() -> set[datetime.date]:
    return _load_date_set(EMPTY_PATH)


def _load_scrape_status() -> set[datetime.date]:
    return _load_date_set(SCRAPE_STATUS_PATH)


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


def mark_dates_scraped(dates: Iterable[datetime.date], *, exclude_today: bool = True) -> None:
    """
    Mark specific dates as having been scraped from Gmail.
    """
    today = _today()
    to_add = {
        day
        for day in dates
        if isinstance(day, datetime.date) and not (exclude_today and day == today)
    }
    _update_date_set(SCRAPE_STATUS_PATH, lambda scraped: scraped | to_add, drop_today=True)


def mark_date_range_scraped(
    start: datetime.date, end: datetime.date, *, exclude_today: bool = True
) -> None:
    """Mark a contiguous date range as scraped."""
    if start > end:
        return
    to_add = _range_days(start, end, exclude_today=exclude_today)
    _update_date_set(SCRAPE_STATUS_PATH, lambda scraped: scraped | to_add, drop_today=True)


def mark_dates_not_scraped(dates: Iterable[datetime.date]) -> None:
    """Explicitly mark dates as not-scraped (removes from scraped set)."""
    to_drop = {day for day in dates if isinstance(day, datetime.date)}
    _update_date_set(SCRAPE_STATUS_PATH, lambda scraped: scraped - to_drop, drop_today=True)


def scrape_status_for_range(start: datetime.date, end: datetime.date) -> dict[str, bool]:
    """
    Return a mapping of ISO date -> scraped flag for the inclusive range.
    Today's date is always False (not scraped).
    """
    status = {}
    scraped = _load_scrape_status()
    today = _today()
    cursor = start
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        is_scraped = cursor in scraped and cursor != today
        status[cursor.isoformat()] = is_scraped
        cursor += one_day
    return status


def persist_release_metadata(releases: Iterable[dict], *, exclude_today: bool = True) -> None:
    """
    Save release metadata into the cache, keyed by release date.
    Skips today's date when exclude_today is True.
    """
    releases = list(releases)
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
    # if we now have data for a day that was previously marked empty, clear that marker
    _update_date_set(EMPTY_PATH, lambda empty_dates: empty_dates - scraped_days)
    if scraped_days:
        mark_dates_scraped(scraped_days, exclude_today=exclude_today)


def cached_releases_for_range(
    start: datetime.date, end: datetime.date
) -> tuple[list[dict], list[datetime.date]]:
    """
    Return (cached_releases, missing_dates) for the inclusive date range.
    missing_dates are days that have not been scraped yet.
    """
    cache = _load_cache()
    empty_dates = _load_empty_dates()
    scraped_dates = _load_scrape_status()
    # Treat explicitly empty days as already scraped (so they are not missing).
    scraped_dates.update(empty_dates)
    cursor = start
    cached: list[dict] = []
    missing: list[datetime.date] = []
    one_day = datetime.timedelta(days=1)
    while cursor <= end:
        iso = cursor.isoformat()
        releases_for_day = cache.get(iso)
        if releases_for_day:
            cached.extend(releases_for_day)
        elif cursor not in scraped_dates:
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
    start: datetime.date, end: datetime.date, *, exclude_today: bool = True
) -> None:
    """
    Record a contiguous date range that returned no Gmail results so we avoid
    querying it again. Optionally excludes today's date.
    """
    if start > end:
        return
    to_add = _range_days(start, end, exclude_today=exclude_today)
    _update_date_set(EMPTY_PATH, lambda empty_dates: empty_dates | to_add)
    mark_date_range_scraped(start, end, exclude_today=exclude_today)
