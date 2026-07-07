"""Self-verification of the WP-03a test harness.

Proves the conftest fixtures actually work: data-dir isolation, the store
seeders + a real session_store round-trip, the frozen "today", and each email
fixture being exercisable through BOTH provider extraction paths.
"""

from __future__ import annotations

import datetime


def test_data_dir_is_isolated(data_dir):
    import paths

    # The path modules resolve to the temp dir, never the real app data dir.
    assert paths.DATA_DIR == data_dir
    assert "Application Support" not in str(paths.RELEASE_CACHE_PATH)
    assert str(data_dir) in str(paths.RELEASE_CACHE_PATH)


def test_seed_release_and_read_back(seed, make_release):
    # Seed a release directly into the JSON store...
    release = make_release(
        release_url="https://midnighttapes.bandcamp.com/album/neon-fields",
        date="2025-06-16",
        artist_name="Aria Vale",
        release_title="Neon Fields",
        page_name="Midnight Tapes",
    )
    seed.releases({"2025-06-16": [release]})

    # ...and read it back through the real persistence module.
    import session_store

    cache = session_store.get_full_release_cache()
    assert len(cache) == 1
    assert cache[0]["url"] == "https://midnighttapes.bandcamp.com/album/neon-fields"
    assert cache[0]["artist"] == "Aria Vale"
    assert cache[0]["title"] == "Neon Fields"


def test_frozen_today(frozen_today):
    import session_store
    import util

    assert util.today() == datetime.date(2025, 7, 1)
    assert frozen_today == datetime.date(2025, 7, 1)
    # session_store's captured alias is frozen too, so exclude-today logic agrees.
    assert session_store._today() == datetime.date(2025, 7, 1)


def test_frozen_today_drives_exclude_today(frozen_today, seed):
    # The frozen clock drives the exclude-today logic. Updated by WP-15
    # (LOG-2): the skip is LEDGER-only now — today's row IS persisted (data
    # is never dropped), but the frozen "today" is never recorded as checked.
    import session_store

    session_store.persist_release_metadata(
        [
            {
                "url": "https://a.bandcamp.com/album/x",
                "date": "2025-07-01",
                "artist": "A",
                "title": "X",
                "page_name": "A",
                "is_track": False,
                "img_url": None,
                "release_id": None,
            }
        ],
        exclude_today=True,
    )
    assert [r["date"] for r in session_store.get_full_release_cache()] == ["2025-07-01"]
    status = session_store.scrape_status_for_range(frozen_today, frozen_today)
    assert status["2025-07-01"] is False, "the frozen today is never recorded checked"


def test_email_fixtures_exercise_both_provider_paths(emails):
    # The HTML release fixtures must extract identically through Gmail and IMAP.
    for name in ("album_release", "track_release", "custom_domain_album", "linkless"):
        gmail_html = emails.html_via_gmail(name)
        imap_html = emails.html_via_imap(name)
        assert gmail_html, f"{name}: Gmail path yielded no HTML"
        assert imap_html, f"{name}: IMAP path yielded no HTML"
        assert gmail_html == imap_html, f"{name}: provider paths disagree"
        assert "<a href=" in imap_html


def test_plaintext_only_has_no_html_part(emails):
    # No text/html part -> both extractors report empty; the parser guard (the
    # regression-locked CQ-12 fix) treats this as a skip, not a crash.
    assert emails.html_via_imap("plaintext_only") == ""
    assert emails.html_via_gmail("plaintext_only") is None


def test_album_fixture_parses_field_by_field(emails):
    from bandcamp_email_parser import parse_release_email

    html = emails.html_via_imap("album_release")
    subject = emails.subject("album_release")
    img_url, url, is_track, artist, title, page = parse_release_email(html, subject)

    assert url == "https://midnighttapes.bandcamp.com/album/neon-fields"
    assert is_track is False
    assert title == "Neon Fields"
    assert artist == "Aria Vale"
    assert page == "Midnight Tapes"


def test_track_fixture_is_track_without_artist(emails):
    from bandcamp_email_parser import parse_release_email

    html = emails.html_via_gmail("track_release")
    subject = emails.subject("track_release")
    _img, url, is_track, artist, title, _page = parse_release_email(html, subject)

    assert url == "https://echoparade.bandcamp.com/track/glass-corridor"
    assert is_track is True
    assert title == "Glass Corridor"
    assert artist is None  # "by <artist>" phrasing absent


def test_linkless_fixture_yields_no_release_url(emails):
    from bandcamp_email_parser import parse_release_email

    html = emails.html_via_imap("linkless")
    subject = emails.subject("linkless")
    result = parse_release_email(html, subject)
    assert result == (None, None, None, None, None, None)


def test_page_fixture_meta_and_description(pages):
    from bandcamp import extract_bandcamp_description, extract_bc_meta

    meta = extract_bc_meta(pages.html("release_page"))
    assert meta is not None
    assert meta["item_type"] == "album"
    assert meta["item_id"] == 100200300

    desc = extract_bandcamp_description(pages.html("release_page"))
    assert desc and "synthetic album about text" in desc


def test_page_fixture_without_properties(pages):
    from bandcamp import extract_bandcamp_description, extract_bc_meta

    # No bc-page-properties -> None; description still extractable.
    assert extract_bc_meta(pages.html("release_page_no_properties")) is None
    desc = extract_bandcamp_description(pages.html("release_page_no_properties"))
    assert desc and "Synthetic about text" in desc
