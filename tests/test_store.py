"""WP-03b · CQ-42 store round-trips at CURRENT behavior.

Two layers:

* ``json_store`` — the single locked/atomic IO floor: read/write/update/delete,
  the corruption-quarantine rule (present-but-garbage → ``*.corrupt-*`` + default;
  *missing* → quiet default), and unique-temp atomic writes leaving no debris.
* ``session_store`` — the date-keyed release/scrape/empty stores on top of it:
  ``persist_release_metadata`` + ``cached_releases_for_range`` +
  ``scrape_status_for_range`` + the ``mark_*`` helpers, all honoring
  ``exclude_today`` and per-day URL keep. Also the WP-04 persist-before-mark
  contract *at the session_store level*: a day is only ever marked scraped once
  its releases are already in the cache.

Import discipline (see conftest): reference store modules as ``import x`` so the
autouse data-dir reload rebinds their path-bound functions to the temp dir.
"""

from __future__ import annotations

import datetime

import pytest


# ===========================================================================
# json_store — the atomic/locked IO floor
# ===========================================================================
def _store_path(data_dir, name="probe.json"):
    return data_dir / name


def test_read_missing_returns_default_and_creates_nothing(data_dir):
    import json_store

    path = _store_path(data_dir)
    got = json_store.read_json(path, {"seed": 1})
    assert got == {"seed": 1}
    assert not path.exists(), "reading a missing store must not create a file"


def test_read_missing_returns_independent_default_copy(data_dir):
    import json_store

    default = {"nested": [1, 2]}
    got = json_store.read_json(_store_path(data_dir), default)
    got["nested"].append(3)
    # The caller mutating the result must not poison the shared default.
    assert default == {"nested": [1, 2]}


def test_write_then_read_roundtrip(data_dir):
    import json_store

    path = _store_path(data_dir)
    payload = {"a": 1, "b": ["x", "y"]}
    json_store.write_json(path, payload, indent=2)
    assert json_store.read_json(path, None) == payload


def test_update_json_load_mutate_save(data_dir):
    import json_store

    path = _store_path(data_dir)
    json_store.write_json(path, {"count": 1})

    def bump(data):
        data["count"] += 1
        return data

    result = json_store.update_json(path, bump, {})
    assert result == {"count": 2}
    assert json_store.read_json(path, None) == {"count": 2}


def test_update_json_returning_none_persists_inplace_mutation(data_dir):
    import json_store

    path = _store_path(data_dir)
    json_store.write_json(path, {"items": []})

    def mutate(data):
        data["items"].append("added")
        # returning None persists the in-place mutation

    json_store.update_json(path, mutate, {})
    assert json_store.read_json(path, None) == {"items": ["added"]}


def test_delete_json_reports_removal(data_dir):
    import json_store

    path = _store_path(data_dir)
    json_store.write_json(path, {"x": 1})
    assert json_store.delete_json(path) is True
    assert not path.exists()
    # Deleting a missing store is a quiet False, never an error.
    assert json_store.delete_json(path) is False


def test_corrupt_store_is_quarantined_and_defaulted(data_dir):
    import json_store

    path = _store_path(data_dir)
    path.write_text("{not valid json", encoding="utf-8")

    got = json_store.read_json(path, {"default": True})
    assert got == {"default": True}
    # The garbage file is moved aside, not silently overwritten.
    assert not path.exists()
    quarantined = list(data_dir.glob("probe.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid json"


def test_missing_store_is_never_quarantined(data_dir):
    import json_store

    path = _store_path(data_dir)
    json_store.read_json(path, [])
    assert not list(data_dir.glob("probe.json.corrupt-*"))


def test_write_leaves_no_temp_debris(data_dir):
    import json_store

    path = _store_path(data_dir)
    for i in range(5):
        json_store.write_json(path, {"i": i})
    leftovers = [p.name for p in data_dir.iterdir() if p.name != "probe.json"]
    assert leftovers == [], f"atomic write left debris: {leftovers}"


def test_quarantine_never_overwrites_prior_corrupt_file(data_dir):
    import json_store

    path = _store_path(data_dir)
    ts = "20250101T000000"
    path.write_text("garbage-1", encoding="utf-8")
    q1 = json_store.quarantine_corrupt(path, ts)
    path.write_text("garbage-2", encoding="utf-8")
    q2 = json_store.quarantine_corrupt(path, ts)
    assert q1 != q2, "second quarantine at the same timestamp must not clobber the first"
    assert q1.read_text(encoding="utf-8") == "garbage-1"
    assert q2.read_text(encoding="utf-8") == "garbage-2"


# ===========================================================================
# session_store — date-keyed release / scrape / empty round-trips
# ===========================================================================
JUN = lambda d: datetime.date(2025, 6, d)  # noqa: E731 - terse local date helper


def test_persist_and_read_release_roundtrip(frozen_today, make_release):
    import session_store

    rel = make_release(release_url="https://a.bandcamp.com/album/x", date="2025-06-16")
    session_store.persist_release_metadata([rel])

    cached, missing = session_store.cached_releases_for_range(JUN(16), JUN(16))
    assert [r["url"] for r in cached] == ["https://a.bandcamp.com/album/x"]
    assert missing == []


def test_persist_marks_only_days_with_releases_scraped(frozen_today, make_release):
    import session_store

    rel = make_release(date="2025-06-16")
    session_store.persist_release_metadata([rel])

    status = session_store.scrape_status_for_range(JUN(15), JUN(17))
    assert status["2025-06-16"] is True, "a persisted day must be marked scraped"
    assert status["2025-06-15"] is False
    assert status["2025-06-17"] is False


def test_persist_before_mark_contract_no_scraped_without_cache(frozen_today, make_release):
    import session_store

    # WP-04 persist-before-mark at the session_store level: after persist, any
    # day flagged scraped is already backed by its releases in the cache — the
    # write happens first, the mark second, in one call.
    rel = make_release(release_url="https://a.bandcamp.com/album/x", date="2025-06-16")
    session_store.persist_release_metadata([rel])

    status = session_store.scrape_status_for_range(JUN(16), JUN(16))
    cached, _ = session_store.cached_releases_for_range(JUN(16), JUN(16))
    scraped_days = {day for day, ok in status.items() if ok}
    for day in scraped_days:
        day_hits = [r for r in cached if r["date"] == day]
        assert day_hits, f"{day} marked scraped but has no cached releases"


def test_persist_keeps_todays_rows_but_never_marks_today(freeze_today, make_release):
    # Updated by WP-15 (LOG-2): the exclude-today skip is LEDGER-only now.
    # Today's fetched releases are persisted (data is never dropped); the day
    # simply is never recorded checked, so it stays re-queryable.
    import session_store

    today = freeze_today(datetime.date(2025, 6, 20))
    rel = make_release(date=today.isoformat())
    session_store.persist_release_metadata([rel])

    cached, _ = session_store.cached_releases_for_range(today, today)
    assert [r["date"] for r in cached] == [today.isoformat()]
    status = session_store.scrape_status_for_range(today, today)
    assert status[today.isoformat()] is False


def test_persist_can_include_today_when_flag_off(freeze_today, make_release):
    import session_store

    today = freeze_today(datetime.date(2025, 6, 20))
    rel = make_release(date=today.isoformat())
    session_store.persist_release_metadata([rel], exclude_today=False)

    cached, _ = session_store.cached_releases_for_range(today, today)
    assert [r["date"] for r in cached] == [today.isoformat()]


def test_persist_dedupes_same_url_within_a_day_keeps_existing(frozen_today, make_release):
    import session_store

    # Current behavior: per-day dedupe goes through dedupe_by_url ([*existing,
    # new]), which keeps the FIRST occurrence — the already-cached row wins and
    # a re-persist of the same URL does not overwrite it.
    url = "https://a.bandcamp.com/album/x"
    session_store.persist_release_metadata(
        [make_release(release_url=url, date="2025-06-16", release_title="Original")]
    )
    session_store.persist_release_metadata(
        [make_release(release_url=url, date="2025-06-16", release_title="Newer")]
    )
    cached, _ = session_store.cached_releases_for_range(JUN(16), JUN(16))
    assert len(cached) == 1, "same URL on the same day must collapse to one row"
    assert cached[0]["title"] == "Original"


def test_cached_releases_reports_unscraped_days_as_missing(frozen_today, make_release):
    import session_store

    session_store.persist_release_metadata([make_release(date="2025-06-16")])
    cached, missing = session_store.cached_releases_for_range(JUN(16), JUN(18))
    assert [r["date"] for r in cached] == ["2025-06-16"]
    assert missing == [JUN(17), JUN(18)]


def test_scrape_status_never_marks_today(freeze_today):
    import session_store

    today = freeze_today(datetime.date(2025, 6, 20))
    # Even if the store somehow lists today, the reader forces it False.
    session_store.mark_dates_scraped([today], exclude_today=False)
    status = session_store.scrape_status_for_range(today - datetime.timedelta(days=1), today)
    assert status[today.isoformat()] is False, "today is never reported scraped"


def test_mark_date_range_scraped_roundtrip(frozen_today):
    import session_store

    session_store.mark_date_range_scraped(JUN(10), JUN(12))
    status = session_store.scrape_status_for_range(JUN(9), JUN(13))
    assert status == {
        "2025-06-09": False,
        "2025-06-10": True,
        "2025-06-11": True,
        "2025-06-12": True,
        "2025-06-13": False,
    }


def test_mark_dates_scraped_excludes_today_and_settling_window(freeze_today):
    # Updated by WP-15 (LOG-2): "never mark today" generalized to the trailing
    # settling window — yesterday is inside it now, so the first markable day
    # is today - SETTLING_WINDOW_DAYS.
    import session_store

    today = freeze_today(datetime.date(2025, 6, 20))
    settled = today - datetime.timedelta(days=session_store.SETTLING_WINDOW_DAYS)
    yesterday = today - datetime.timedelta(days=1)
    session_store.mark_dates_scraped([settled, yesterday, today])  # exclude_today default True
    status = session_store.scrape_status_for_range(settled, today)
    assert status[settled.isoformat()] is True
    assert status[yesterday.isoformat()] is False, "settling-window days are never recorded"
    assert status[today.isoformat()] is False


def test_persist_empty_range_marks_scraped_and_not_missing(frozen_today, make_release):
    import session_store

    session_store.persist_empty_date_range(JUN(1), JUN(3))
    # Explicitly-empty days count as scraped, so they are not "missing".
    _cached, missing = session_store.cached_releases_for_range(JUN(1), JUN(3))
    assert missing == []
    status = session_store.scrape_status_for_range(JUN(1), JUN(3))
    assert all(status.values())


def test_persist_clears_empty_marker_when_day_gains_data(frozen_today, make_release):
    import session_store

    session_store.persist_empty_date_range(JUN(16), JUN(16))
    # A late release arrives for a previously-empty day.
    session_store.persist_release_metadata([make_release(date="2025-06-16")])
    cached, missing = session_store.cached_releases_for_range(JUN(16), JUN(16))
    assert [r["date"] for r in cached] == ["2025-06-16"]
    assert missing == []


def test_get_full_release_cache_flattens_and_dedupes(frozen_today, seed, make_release):
    import session_store

    url = "https://a.bandcamp.com/album/dup"
    seed.releases(
        {
            "2025-06-16": [make_release(release_url=url, date="2025-06-16")],
            "2025-06-17": [
                make_release(release_url=url, date="2025-06-17"),
                make_release(release_url="https://b/album/y", date="2025-06-17"),
            ],
        }
    )
    flat = session_store.get_full_release_cache()
    urls = [r["url"] for r in flat]
    assert urls.count(url) == 1, "flattened cache must dedupe by URL"
    assert "https://b/album/y" in urls


@pytest.mark.parametrize("bad", ["{garbage", "not json at all", "[1, 2"])
def test_release_cache_corruption_degrades_to_empty(frozen_today, data_dir, bad):
    import paths
    import session_store

    paths.RELEASE_CACHE_PATH.write_text(bad, encoding="utf-8")
    flat = session_store.get_full_release_cache()
    assert flat == [], "a corrupt release cache degrades to empty, not a crash"
    assert list(data_dir.glob("release_cache.json.corrupt-*"))
