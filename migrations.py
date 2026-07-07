"""One-shot, versioned store migrations (WP-14 · LOG-21).

The JSON stores carry a schema version in the ``meta.json`` sidecar
(``{"schema": 2}``, paths.SCHEMA_META_PATH). :func:`migrate` runs once at
server startup:

- **Fresh install** (no marker, no stores): the marker is written directly at
  the current schema — no migration, no backups.
- **Legacy install** (no marker, stores present): every store the migration
  changes is first backed up to ``<name>.pre-v2``, then rewritten to the v2
  shape, and the marker is written LAST — so a crash mid-migration re-runs the
  (idempotent) transforms on the next start while the original bytes stay
  preserved in the backups. All writes go through json_store (atomic, locked).
- **Already at schema 2**: nothing happens.

The v1 → v2 transforms, in order:

1. ``release_cache.json`` — canonicalize every row's URL (LOG-9), drop
   null-URL junk rows (LOG-8 residual), strip stale ``img_url`` keys
   (LOG-19), stamp rows missing ``source`` with the currently configured
   provider (LOG-21 — best-effort attribution for pre-provider-aware data),
   and merge same-day URL-case collisions.
2. ``viewed_state.json`` / ``starred_state.json`` — canonicalize entries;
   collisions union away (a set), so a star under any URL spelling survives.
3. ``embed_cache.json`` — canonicalize keys with a field-union collision
   merge (an ``ok`` record beats an error record; fresher wins between
   equals; missing fields are filled from the loser), upgrade legacy flat
   entries to the LOG-20 ``status`` shape, and drop stored ``embed_url``
   keys (derived state is never stored).
4. ``scrape_status.json`` — fold ``no_results_dates.json`` into the single
   per-day ledger ``{date: {empty, source}}`` (LOG-11) and delete the second
   file; each day's ``empty`` flag is derived from whether the (cleaned)
   release cache holds rows for it, and ``source`` is stamped with the
   currently configured provider.
"""

from __future__ import annotations

import logging

import json_store
import paths
from provider_factory import get_current_provider_type
from util import canonical_release_url

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# The retired second per-day store (LOG-11). paths.py no longer names it; the
# migration is the only code that may still touch the legacy file.
LEGACY_EMPTY_DATES_FILENAME = "no_results_dates.json"

BACKUP_SUFFIX = f".pre-v{SCHEMA_VERSION}"


def _legacy_empty_dates_path():
    return paths.DATA_DIR / LEGACY_EMPTY_DATES_FILENAME


def _versioned_store_paths() -> list:
    """Every store file the schema marker versions (legacy file included)."""
    return [
        paths.RELEASE_CACHE_PATH,
        paths.SCRAPE_STATUS_PATH,
        paths.EMBED_CACHE_PATH,
        paths.VIEWED_PATH,
        paths.STARRED_PATH,
        _legacy_empty_dates_path(),
    ]


def current_schema() -> int:
    """The recorded store schema version; 0 means unversioned (legacy)."""
    meta = json_store.read_json(paths.SCHEMA_META_PATH, {})
    if isinstance(meta, dict) and isinstance(meta.get("schema"), int):
        return meta["schema"]
    return 0


def _write_marker() -> None:
    json_store.write_json(paths.SCHEMA_META_PATH, {"schema": SCHEMA_VERSION}, indent=2)


def _backup(path) -> None:
    """Preserve a store's pre-migration bytes as ``<name>.pre-v2``.

    Never overwrites an existing backup: if a previous (crashed) migration
    attempt already saved one, that copy is the true pre-migration state.
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists() or not path.exists():
        return
    backup.write_bytes(path.read_bytes())


def _fill_missing_fields(winner: dict, loser: dict) -> None:
    """Field-union half of a collision merge: the winner keeps every value it
    has; empty slots (missing/None/"") are filled from the losing record."""
    for key, value in loser.items():
        if winner.get(key) in (None, "") and value not in (None, ""):
            winner[key] = value


# ---------------------------------------------------------------------------
# Step 1: release cache
# ---------------------------------------------------------------------------
def _clean_release_row(row: dict, source: str) -> dict | None:
    """One release row → v2 shape, or None for null-URL junk (LOG-8)."""
    url = canonical_release_url(row.get("url"))
    if not url:
        return None
    cleaned = {key: value for key, value in row.items() if key != "img_url"}
    cleaned["url"] = url
    if not cleaned.get("source"):
        cleaned["source"] = source
    return cleaned


def _migrate_release_cache(source: str) -> dict:
    """Rewrite the release cache in place; returns the cleaned cache."""
    raw = json_store.read_json(paths.RELEASE_CACHE_PATH, {})
    cache = raw if isinstance(raw, dict) else {}
    cleaned_cache: dict = {}
    for day, rows in cache.items():
        if not isinstance(rows, list):
            continue
        cleaned_rows: list[dict] = []
        seen: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            cleaned = _clean_release_row(row, source)
            if cleaned is None:
                continue
            # Same-day URL-case collision: field-union merge — the first row
            # wins per field (matching dedupe_by_url's keep-first), the later
            # spelling only fills fields the winner lacks.
            index = seen.get(cleaned["url"])
            if index is None:
                seen[cleaned["url"]] = len(cleaned_rows)
                cleaned_rows.append(cleaned)
            else:
                _fill_missing_fields(cleaned_rows[index], cleaned)
        if cleaned_rows:
            cleaned_cache[day] = cleaned_rows
    if cleaned_cache != cache:
        _backup(paths.RELEASE_CACHE_PATH)
        json_store.write_json(paths.RELEASE_CACHE_PATH, cleaned_cache, indent=2)
    return cleaned_cache


# ---------------------------------------------------------------------------
# Step 2: viewed / starred URL sets
# ---------------------------------------------------------------------------
def _migrate_url_set(path) -> None:
    raw = json_store.read_json(path, [])
    items = raw if isinstance(raw, list) else []
    canonical = sorted({url for url in (canonical_release_url(item) for item in items) if url})
    if canonical != items:
        _backup(path)
        json_store.write_json(path, canonical)


# ---------------------------------------------------------------------------
# Step 3: embed cache
# ---------------------------------------------------------------------------
def _upgrade_embed_record(entry) -> dict | None:
    """One embed entry → v2 record (status shape, no stored embed_url)."""
    if not isinstance(entry, dict):
        return None
    record = dict(entry)
    record.pop("embed_url", None)
    if record.get("status") in ("ok", "error"):
        return record
    if record.get("release_id") is not None or entry.get("embed_url"):
        record["status"] = "ok"
        record.setdefault("fetched_at", None)
        return record
    return None


def _record_freshness(record: dict) -> tuple[int, float]:
    """Collision rank: ok beats error, then the fresher fetched_at wins."""
    status_rank = 1 if record.get("status") == "ok" else 0
    fetched_at = record.get("fetched_at")
    age_rank = float(fetched_at) if isinstance(fetched_at, (int, float)) else float("-inf")
    return (status_rank, age_rank)


def _migrate_embed_cache() -> None:
    raw = json_store.read_json(paths.EMBED_CACHE_PATH, {})
    cache = raw if isinstance(raw, dict) else {}
    merged: dict[str, dict] = {}
    for url, entry in cache.items():
        canonical = canonical_release_url(url)
        record = _upgrade_embed_record(entry)
        if not canonical or record is None:
            continue
        existing = merged.get(canonical)
        if existing is None:
            merged[canonical] = record
            continue
        # Union merge (LOG-9): the stronger record wins, then fills any
        # fields it lacks from the other spelling's record.
        winner, loser = (
            (record, existing)
            if _record_freshness(record) > _record_freshness(existing)
            else (existing, record)
        )
        if winner.get("status") == loser.get("status"):
            _fill_missing_fields(winner, loser)
        merged[canonical] = winner
    if merged != cache:
        _backup(paths.EMBED_CACHE_PATH)
        json_store.write_json(paths.EMBED_CACHE_PATH, merged, indent=2)


# ---------------------------------------------------------------------------
# Step 4: the per-day ledger (fold no_results_dates.json in — LOG-11)
# ---------------------------------------------------------------------------
def _date_strings(raw) -> set[str]:
    return {item for item in raw if isinstance(item, str)} if isinstance(raw, list) else set()


def _migrate_ledger(source: str, cleaned_cache: dict) -> None:
    legacy_empty_path = _legacy_empty_dates_path()
    raw_status = json_store.read_json(paths.SCRAPE_STATUS_PATH, [])
    empty_marked = _date_strings(json_store.read_json(legacy_empty_path, []))
    if isinstance(raw_status, dict):
        # Already the v2 dict shape (interrupted earlier run): keep it, just
        # union in any legacy empty days still on disk.
        ledger = {
            day: record if isinstance(record, dict) else {"empty": False, "source": source}
            for day, record in raw_status.items()
        }
        for day in empty_marked - set(ledger):
            ledger[day] = {"empty": True, "source": source}
        if ledger != raw_status:
            _backup(paths.SCRAPE_STATUS_PATH)
            json_store.write_json(
                paths.SCRAPE_STATUS_PATH, {day: ledger[day] for day in sorted(ledger)}, indent=2
            )
    else:
        checked = _date_strings(raw_status)
        days_with_rows = {day for day, rows in cleaned_cache.items() if rows}
        ledger = {
            day: {
                # "empty" is the derivable fact the old model encoded across
                # two files: checked, with no cached rows for the day.
                "empty": day not in days_with_rows,
                "source": source,
            }
            for day in sorted(checked | empty_marked)
        }
        if ledger or paths.SCRAPE_STATUS_PATH.exists():
            _backup(paths.SCRAPE_STATUS_PATH)
            json_store.write_json(paths.SCRAPE_STATUS_PATH, ledger, indent=2)
    if legacy_empty_path.exists():
        _backup(legacy_empty_path)
        json_store.delete_json(legacy_empty_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def migrate() -> list[str]:
    """Bring the JSON stores up to the current schema. Runs once per upgrade.

    Returns the list of migration steps applied (empty when nothing ran:
    already versioned, or a fresh install that only needed the marker).
    """
    if current_schema() >= SCHEMA_VERSION:
        return []
    if not any(path.exists() for path in _versioned_store_paths()):
        # Fresh install: stores will be created at schema 2 — marker only.
        _write_marker()
        return []

    applied: list[str] = []
    source = get_current_provider_type()

    cleaned_cache = _migrate_release_cache(source)
    applied.append("release_cache")
    _migrate_url_set(paths.VIEWED_PATH)
    applied.append("viewed_state")
    _migrate_url_set(paths.STARRED_PATH)
    applied.append("starred_state")
    _migrate_embed_cache()
    applied.append("embed_cache")
    _migrate_ledger(source, cleaned_cache)
    applied.append("scrape_status")

    # The marker is written LAST: a crash anywhere above re-runs the
    # idempotent transforms next start instead of stranding half-migrated
    # stores behind a "done" marker.
    _write_marker()
    logger.info("Migrated stores to schema %s (%s)", SCHEMA_VERSION, ", ".join(applied))
    return applied
