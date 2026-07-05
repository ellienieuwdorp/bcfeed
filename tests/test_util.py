"""WP-03b · CQ-41 pure-function coverage for util (+ session_store.collapse_date_ranges).

These lock the *current* (post-Phase-0) behavior of the small, side-effect-free
helpers the whole pipeline leans on: date parsing, the canonical release-dict
shape, and the two dedupe strategies (URL identity and date-based keep-last/
keep-first, including the WP-04 rule that a date-less record never wins a
conflict). Range collapsing rounds it out.

Import discipline (see conftest): reference modules as ``import util`` so the
autouse data-dir reload rebinds cleanly; never bind names at module top.
"""

from __future__ import annotations

import datetime

import pytest


# ---------------------------------------------------------------------------
# parse_date
# ---------------------------------------------------------------------------
def test_parse_date_iso():
    import util

    assert util.parse_date("2025-06-16") == datetime.date(2025, 6, 16)


def test_parse_date_slash_legacy():
    import util

    assert util.parse_date("2025/06/16") == datetime.date(2025, 6, 16)


def test_parse_date_rfc2822():
    import util

    assert util.parse_date("Mon, 16 Jun 2025 23:30:00 +0000") == datetime.date(2025, 6, 16)


def test_parse_date_passthrough_date_and_datetime():
    import util

    d = datetime.date(2025, 6, 16)
    dt = datetime.datetime(2025, 6, 16, 23, 30, 0)
    assert util.parse_date(d) == d
    assert util.parse_date(dt) == d


def test_parse_date_none_raises_by_default():
    import util

    with pytest.raises(ValueError):
        util.parse_date(None)


def test_parse_date_none_allowed_returns_none():
    import util

    assert util.parse_date(None, allow_none=True) is None


def test_parse_date_garbage_raises_by_default():
    import util

    with pytest.raises(ValueError):
        util.parse_date("not-a-date")


def test_parse_date_garbage_allowed_returns_none():
    import util

    assert util.parse_date("not-a-date", allow_none=True) is None
    assert util.parse_date("2025-13-99", allow_none=True) is None


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------
def test_today_returns_a_date():
    import util

    result = util.today()
    assert isinstance(result, datetime.date)
    # The single "now" choke point tracks the system clock (unfrozen here).
    assert result == datetime.date.today()


def test_today_is_frozen_by_fixture(frozen_today):
    import util

    assert util.today() == frozen_today == datetime.date(2025, 7, 1)


# ---------------------------------------------------------------------------
# construct_release — canonical dict shape
# ---------------------------------------------------------------------------
def test_construct_release_shape_and_field_mapping():
    import util

    rel = util.construct_release(
        is_track=True,
        release_url="https://a.bandcamp.com/track/x",
        date="2025-06-16",
        artist_name="Artist",
        release_title="Title",
        page_name="Page",
        release_id=42,
    )
    assert rel == {
        "img_url": None,
        "date": "2025-06-16",
        "artist": "Artist",
        "title": "Title",
        "page_name": "Page",
        "url": "https://a.bandcamp.com/track/x",
        "release_id": 42,
        "is_track": True,
    }
    # img_url is a stable null key (CQ-32): present, always None.
    assert "img_url" in rel
    assert rel["img_url"] is None


def test_construct_release_defaults_are_none():
    import util

    rel = util.construct_release()
    assert set(rel) == {
        "img_url",
        "date",
        "artist",
        "title",
        "page_name",
        "url",
        "release_id",
        "is_track",
    }
    assert all(v is None for v in rel.values())


# ---------------------------------------------------------------------------
# dedupe_by_url
# ---------------------------------------------------------------------------
def test_dedupe_by_url_keeps_first_and_order():
    import util

    items = [
        {"url": "u1", "title": "first"},
        {"url": "u2", "title": "second"},
        {"url": "u1", "title": "dupe-dropped"},
    ]
    out = util.dedupe_by_url(items)
    assert [i["title"] for i in out] == ["first", "second"]


def test_dedupe_by_url_keeps_all_urlless():
    import util

    items = [{"title": "a"}, {"url": "", "title": "b"}, {"url": None, "title": "c"}]
    out = util.dedupe_by_url(items)
    assert [i["title"] for i in out] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# dedupe_by_date — keep-last / keep-first / date-less never wins
# ---------------------------------------------------------------------------
def test_dedupe_by_date_keep_last_takes_later_date():
    import util

    items = [
        {"url": "u", "date": "2025-06-10", "tag": "early"},
        {"url": "u", "date": "2025-06-20", "tag": "late"},
    ]
    out = util.dedupe_by_date(items, keep="last")
    assert len(out) == 1
    assert out[0]["tag"] == "late"


def test_dedupe_by_date_keep_first_takes_earlier_date():
    import util

    items = [
        {"url": "u", "date": "2025-06-20", "tag": "late"},
        {"url": "u", "date": "2025-06-10", "tag": "early"},
    ]
    out = util.dedupe_by_date(items, keep="first")
    assert len(out) == 1
    assert out[0]["tag"] == "early"


@pytest.mark.parametrize("order", ["dated_first", "dateless_first"])
def test_dedupe_by_date_dateless_never_wins(order):
    import util

    dated = {"url": "u", "date": "2025-06-10", "tag": "dated"}
    dateless = {"url": "u", "date": None, "tag": "dateless"}
    items = [dated, dateless] if order == "dated_first" else [dateless, dated]
    out = util.dedupe_by_date(items, keep="last")
    assert len(out) == 1
    assert out[0]["tag"] == "dated", "a date-less record must never win keep-last"


def test_dedupe_by_date_both_dateless_keeps_first_seen():
    import util

    items = [
        {"url": "u", "date": None, "tag": "first"},
        {"url": "u", "date": "garbage", "tag": "second"},
    ]
    out = util.dedupe_by_date(items, keep="last")
    assert len(out) == 1
    assert out[0]["tag"] == "first"


def test_dedupe_by_date_garbage_date_is_tolerated_not_fatal():
    import util

    # A garbage-dated record must never abort dedupe (CQ-14/LOG-10); it simply
    # never wins against a well-dated one.
    items = [
        {"url": "u", "date": "garbage", "tag": "bad"},
        {"url": "u", "date": "2025-06-10", "tag": "good"},
    ]
    out = util.dedupe_by_date(items, keep="last")
    assert len(out) == 1
    assert out[0]["tag"] == "good"


def test_dedupe_by_date_keeps_urlless_records():
    import util

    items = [
        {"url": "u", "date": "2025-06-10", "tag": "dated"},
        {"date": "2025-06-11", "tag": "no-url-a"},
        {"url": "", "date": "2025-06-12", "tag": "no-url-b"},
    ]
    out = util.dedupe_by_date(items, keep="last")
    tags = {i["tag"] for i in out}
    assert tags == {"dated", "no-url-a", "no-url-b"}


def test_dedupe_by_date_equal_dates_keep_last_takes_later_iteration():
    import util

    items = [
        {"url": "u", "date": "2025-06-10", "tag": "one"},
        {"url": "u", "date": "2025-06-10", "tag": "two"},
    ]
    out = util.dedupe_by_date(items, keep="last")
    assert out[0]["tag"] == "two"


def test_dedupe_by_date_invalid_keep_raises():
    import util

    with pytest.raises(ValueError):
        util.dedupe_by_date([], keep="middle")


# ---------------------------------------------------------------------------
# collapse_date_ranges (session_store) — contiguous run folding
# ---------------------------------------------------------------------------
def _d(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


def test_collapse_date_ranges_empty():
    import session_store

    assert session_store.collapse_date_ranges([]) == []


def test_collapse_date_ranges_single_day():
    import session_store

    assert session_store.collapse_date_ranges([_d(5)]) == [(_d(5), _d(5))]


def test_collapse_date_ranges_contiguous_run():
    import session_store

    assert session_store.collapse_date_ranges([_d(1), _d(2), _d(3)]) == [(_d(1), _d(3))]


def test_collapse_date_ranges_splits_on_gap():
    import session_store

    got = session_store.collapse_date_ranges([_d(1), _d(2), _d(5), _d(6), _d(9)])
    assert got == [(_d(1), _d(2)), (_d(5), _d(6)), (_d(9), _d(9))]


def test_collapse_date_ranges_sorts_and_dedupes_input():
    import session_store

    got = session_store.collapse_date_ranges([_d(3), _d(1), _d(2), _d(2), _d(3)])
    assert got == [(_d(1), _d(3))]
