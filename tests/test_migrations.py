"""WP-14 · Schema v2: migrations + model completion.

Covers:

- LOG-21 — the schema version marker (``meta.json``) + the one-shot ordered
  ``migrate()``: a legacy (unversioned) data dir is upgraded exactly once with
  ``.pre-v2`` backups of every store it changes; a second start performs NO
  migration; a fresh install writes schema 2 directly (no migration, no
  backups); a crash before the marker re-runs the idempotent transforms while
  the original backups stay untouched.
- LOG-9 — canonical URL normalization at parse time and at all four store
  lookups, plus the one-shot key rewrite with UNION collision merge (star
  preserved if either spelling was starred; one row survives).
- LOG-11 — ``no_results_dates.json`` folded into the single per-day ledger and
  deleted; empty days still count as checked; grep-proof that nothing reads
  the retired path constant.
- LOG-22/PY-16 schema half — every ledger day records its producing provider,
  set at the pipeline level.
- LOG-8 residual / LOG-19 finish — null-URL junk rows dropped, stale
  ``img_url`` keys stripped by the same rewrite.
- LOG-20/PERF-5 — ``/releases`` carries no description bodies
  (``has_description`` instead), embed_url is derived, every row carries
  ``source``.

Everything runs against the autouse-isolated temp data dir — never the real
application data dir.

Import discipline (see conftest): modules are referenced as ``import x``
inside tests so the autouse data-dir reload is honored.
"""

from __future__ import annotations

import datetime
import importlib
import json

import pytest

from email_provider import EmailMessage, SearchQuery

MESSY_URL = "HTTP://Artist.Bandcamp.com/album/X/?x=1#f"
CANONICAL_URL = "https://artist.bandcamp.com/album/X"
OTHER_URL = "https://other.bandcamp.com/album/y"


def _silent(*_args, **_kwargs) -> None:
    """No-op log sink."""


def june(day: int) -> datetime.date:
    return datetime.date(2025, 6, day)


def _write(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_row(url, date="2025-06-16", **overrides):
    """A release row exactly as the pre-v2 pipeline persisted it."""
    row = {
        "img_url": None,
        "date": date,
        "artist": "Aria Vale",
        "title": "Neon Fields",
        "page_name": "Midnight Tapes",
        "url": url,
        "release_id": None,
        "is_track": False,
    }
    row.update(overrides)
    return row


def _seed_legacy_stores(data_dir):
    """A realistic pre-v2 data dir: URL-case collisions, a null-URL junk row,
    img_url keys, viewed+starred overlap, a two-file day ledger."""
    import paths

    _write(
        paths.RELEASE_CACHE_PATH,
        {
            "2025-06-16": [
                _legacy_row(MESSY_URL, title=None),  # messy spelling, no title
                _legacy_row(CANONICAL_URL),  # canonical spelling, full row
                _legacy_row(None),  # LOG-8 residual junk
            ],
            "2025-06-17": [_legacy_row(OTHER_URL, date="2025-06-17")],
        },
    )
    # Overlap: the same release is starred under one spelling and viewed under
    # both — the rewrite must union them onto ONE canonical key.
    _write(paths.VIEWED_PATH, [MESSY_URL, CANONICAL_URL])
    _write(paths.STARRED_PATH, [MESSY_URL])
    _write(
        paths.EMBED_CACHE_PATH,
        {
            MESSY_URL: {
                "release_id": 111,
                "is_track": False,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=111/",
                "description": "",
            },
            CANONICAL_URL: {
                "release_id": 111,
                "is_track": False,
                "embed_url": "https://bandcamp.com/EmbeddedPlayer/album=111/",
                "description": "Great record.",
            },
        },
    )
    _write(paths.SCRAPE_STATUS_PATH, ["2025-06-16", "2025-06-17", "2025-06-18"])
    _write(data_dir / "no_results_dates.json", ["2025-06-18"])


@pytest.fixture
def migrations_mod(isolated_data_dir):
    import migrations

    return migrations


@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


# ---------------------------------------------------------------------------
# LOG-21 — fresh install / marker / second start
# ---------------------------------------------------------------------------
def test_fresh_install_writes_schema_2_directly_no_backups(migrations_mod, data_dir):
    import paths

    applied = migrations_mod.migrate()

    assert applied == []  # no migration ran
    assert _read(paths.SCHEMA_META_PATH) == {"schema": 2}
    assert migrations_mod.current_schema() == 2
    assert not list(data_dir.glob("*.pre-v2")), "a fresh install must not write backups"
    # No store files were conjured up either — only the marker.
    assert sorted(p.name for p in data_dir.iterdir()) == ["meta.json"]


def test_second_start_performs_no_migration(migrations_mod, data_dir, frozen_today):
    import paths

    _seed_legacy_stores(data_dir)
    first = migrations_mod.migrate()
    assert first, "the first start over legacy stores must migrate"

    snapshot = {
        p.name: p.read_bytes() for p in data_dir.iterdir() if p.is_file()
    }
    second = migrations_mod.migrate()
    assert second == [], "the second start must perform NO migration"
    after = {p.name: p.read_bytes() for p in data_dir.iterdir() if p.is_file()}
    assert after == snapshot, "a no-op start must not touch any store file"
    assert _read(paths.SCHEMA_META_PATH) == {"schema": 2}


def test_backups_present_after_real_migration(migrations_mod, data_dir, frozen_today):
    _seed_legacy_stores(data_dir)
    original = {
        name: (data_dir / name).read_bytes()
        for name in (
            "release_cache.json",
            "viewed_state.json",
            "starred_state.json",
            "embed_cache.json",
            "scrape_status.json",
            "no_results_dates.json",
        )
    }

    migrations_mod.migrate()

    for name, bytes_before in original.items():
        backup = data_dir / f"{name}.pre-v2"
        assert backup.exists(), f"missing .pre-v2 backup for {name}"
        assert backup.read_bytes() == bytes_before, f"backup of {name} must be pre-migration bytes"


def test_interrupted_migration_reruns_and_preserves_original_backups(
    migrations_mod, data_dir, frozen_today
):
    import paths

    _seed_legacy_stores(data_dir)
    original_cache = paths.RELEASE_CACHE_PATH.read_bytes()

    migrations_mod.migrate()
    # Simulate a crash that happened BEFORE the marker write: transforms ran,
    # marker missing. The next start must re-run (idempotently) and must NOT
    # clobber the true pre-migration backups.
    paths.SCHEMA_META_PATH.unlink()

    applied = migrations_mod.migrate()
    assert applied, "an unmarked data dir with stores present must migrate again"
    assert (data_dir / "release_cache.json.pre-v2").read_bytes() == original_cache
    assert migrations_mod.current_schema() == 2


# ---------------------------------------------------------------------------
# LOG-9 — round-trip: collision merge, union of star/seen/embed state
# ---------------------------------------------------------------------------
def test_migration_round_trip_collision_merge_star_preserved(
    migrations_mod, data_dir, frozen_today
):
    import paths

    _seed_legacy_stores(data_dir)
    migrations_mod.migrate()

    # ONE release-cache row for the collided URL, keyed canonically, with the
    # field-union of both spellings (title came from the canonical row).
    cache = _read(paths.RELEASE_CACHE_PATH)
    day_rows = cache["2025-06-16"]
    assert [row["url"] for row in day_rows] == [CANONICAL_URL]
    assert day_rows[0]["title"] == "Neon Fields"

    # Star survives although it was stored under the messy spelling (UNION).
    assert _read(paths.STARRED_PATH) == [CANONICAL_URL]
    # Viewed under both spellings collapses to one canonical entry.
    assert _read(paths.VIEWED_PATH) == [CANONICAL_URL]

    # Embed state: one canonical key, union-merged record (the non-empty
    # description wins), LOG-20 shape with NO stored embed_url.
    embed = _read(paths.EMBED_CACHE_PATH)
    assert set(embed) == {CANONICAL_URL}
    record = embed[CANONICAL_URL]
    assert record["status"] == "ok"
    assert record["release_id"] == 111
    assert record["description"] == "Great record."
    assert "embed_url" not in record


def test_null_url_rows_dropped_and_img_url_keys_stripped(
    migrations_mod, data_dir, frozen_today
):
    import paths

    _seed_legacy_stores(data_dir)
    migrations_mod.migrate()

    rows = [row for rows in _read(paths.RELEASE_CACHE_PATH).values() for row in rows]
    assert all(row.get("url") for row in rows), "null-URL junk rows must be gone"
    assert all("img_url" not in row for row in rows), "stale img_url keys must be stripped"
    assert all(row.get("source") for row in rows), "legacy rows must be stamped with a source"


def test_canonical_release_url_normalization_and_idempotence():
    import util

    assert util.canonical_release_url(MESSY_URL) == CANONICAL_URL
    assert util.canonical_release_url(CANONICAL_URL) == CANONICAL_URL
    # Path case is preserved; only the authority is lowercased.
    assert util.canonical_release_url("https://A.bandcamp.com/album/MiXeD") == (
        "https://a.bandcamp.com/album/MiXeD"
    )
    assert util.canonical_release_url(None) is None
    assert util.canonical_release_url("") is None

    samples = [
        MESSY_URL,
        CANONICAL_URL,
        "http://artist.bandcamp.com/track/y/",
        "https://music.staticbloom.net/album/paper-suns?from=email#top",
        "artist.bandcamp.com/album/x",
        "HTTPS://Artist.Bandcamp.COM:443/album/X/",
    ]
    for url in samples:
        once = util.canonical_release_url(url)
        assert once is not None
        assert util.canonical_release_url(once) == once, f"not idempotent for {url!r}"


# ---------------------------------------------------------------------------
# LOG-9 at the store-lookup layer — both spellings share ONE row + state
# ---------------------------------------------------------------------------
def test_url_spellings_share_one_row_and_state_through_routes(
    migrations_mod, data_dir, frozen_today, server_mod
):
    _seed_legacy_stores(data_dir)
    migrations_mod.migrate()
    client = server_mod.app.test_client()

    releases = client.get("/releases").get_json()["releases"]
    matching = [rel for rel in releases if "album/X" in (rel["url"] or "")]
    assert len(matching) == 1, "both spellings must share ONE row"
    assert matching[0]["url"] == CANONICAL_URL

    # The starred row answers to EITHER spelling (lookups canonicalize).
    assert client.get("/starred-state").get_json()["starred"] == [CANONICAL_URL]
    client.post("/starred-state", json={"url": MESSY_URL, "starred": False})
    assert client.get("/starred-state").get_json()["starred"] == []
    client.post("/viewed-state", json={"url": MESSY_URL, "read": True})
    assert client.get("/viewed-state").get_json()["viewed"] == [CANONICAL_URL]


# ---------------------------------------------------------------------------
# LOG-11 — one per-day ledger; empty days still count as checked
# ---------------------------------------------------------------------------
def test_no_results_dates_folded_into_ledger_and_file_deleted(
    migrations_mod, data_dir, frozen_today
):
    import paths
    import session_store

    _seed_legacy_stores(data_dir)
    migrations_mod.migrate()

    assert not (data_dir / "no_results_dates.json").exists(), "the second store must be deleted"
    ledger = _read(paths.SCRAPE_STATUS_PATH)
    assert set(ledger) == {"2025-06-16", "2025-06-17", "2025-06-18"}
    assert ledger["2025-06-16"]["empty"] is False  # had results
    assert ledger["2025-06-17"]["empty"] is False
    assert ledger["2025-06-18"]["empty"] is True  # checked-and-empty
    for record in ledger.values():
        assert record["source"], "every migrated ledger day must carry a source"

    # Behavior parity: the empty day still counts as checked — not missing.
    _cached, missing = session_store.cached_releases_for_range(june(16), june(18))
    assert missing == []
    status = session_store.scrape_status_for_range(june(16), june(18))
    assert status == {"2025-06-16": True, "2025-06-17": True, "2025-06-18": True}


def test_no_empty_dates_path_reader_left_anywhere():
    """Acceptance grep: the retired path constant has no readers."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    token = "EMPTY_DATES" + "_PATH"  # built dynamically so this file never matches
    offenders = []
    for py in list(repo_root.glob("*.py")) + list((repo_root / "tests").glob("*.py")):
        if token in py.read_text(encoding="utf-8"):
            offenders.append(py.name)
    assert offenders == [], f"EMPTY_DATES path constant still referenced in: {offenders}"


# ---------------------------------------------------------------------------
# LOG-22 / PY-16 schema half — the pipeline stamps provider sources
# ---------------------------------------------------------------------------
class FakeProvider:
    """Minimal EmailProvider double driven by a {date: [EmailMessage]} plan."""

    def __init__(self, emails_by_day):
        self.emails_by_day = {day: list(messages) for day, messages in emails_by_day.items()}

    def authenticate(self) -> None:
        pass

    def search(self, query: SearchQuery, max_results: int = 100, log=None) -> list[str]:
        start = datetime.datetime.strptime(query.after_date, "%Y/%m/%d").date()
        end_exclusive = datetime.datetime.strptime(query.before_date, "%Y/%m/%d").date()
        ids = []
        day = start
        while day < end_exclusive:
            for index in range(len(self.emails_by_day.get(day, []))):
                ids.append(f"{day.isoformat()}#{index}")
            day += datetime.timedelta(days=1)
        return ids[:max_results]

    def fetch(self, message_ids, batch_size: int = 20, log=None) -> dict[str, EmailMessage]:
        out = {}
        for message_id in message_ids:
            day_iso, _, index = message_id.partition("#")
            out[message_id] = self.emails_by_day[datetime.date.fromisoformat(day_iso)][int(index)]
        return out

    def close(self) -> None:
        pass


def _release_message(slug: str, date: str) -> EmailMessage:
    html = (
        f"<html><body><p>Greetings friend, <b>Page {slug}</b> just released "
        f"<i>Album {slug}</i> by Artist {slug}, check it out here: "
        f'<a href="https://{slug}.bandcamp.com/album/{slug}">listen</a></p></body></html>'
    )
    return EmailMessage(html=html, date=date, subject=f"New release from Page {slug}")


def _install_provider(monkeypatch, provider, provider_type: str) -> None:
    import pipeline

    monkeypatch.setattr(pipeline, "create_provider", lambda: provider)
    monkeypatch.setattr(pipeline, "get_current_provider_type", lambda: provider_type)


@pytest.mark.parametrize("provider_type", ["gmail", "imap"])
def test_every_ledger_day_records_its_producing_provider(
    frozen_today, monkeypatch, provider_type
):
    import paths
    import pipeline

    # Jun 16 yields a release; Jun 17 is queried in the same range but has no
    # mail of its own → checked-and-empty. Both must carry the provider.
    provider = FakeProvider({june(16): [_release_message("artist16", "2025-06-16")]})
    _install_provider(monkeypatch, provider, provider_type)

    pipeline.populate_release_cache("2025-06-16", "2025-06-17", 100, 20, log=_silent)

    ledger = _read(paths.SCRAPE_STATUS_PATH)
    assert set(ledger) == {"2025-06-16", "2025-06-17"}
    assert ledger["2025-06-16"] == {"empty": False, "source": provider_type}
    assert ledger["2025-06-17"] == {"empty": True, "source": provider_type}

    # The release rows carry the same pipeline-level source (LOG-21).
    rows = [row for rows in _read(paths.RELEASE_CACHE_PATH).values() for row in rows]
    assert rows and all(row["source"] == provider_type for row in rows)


def test_empty_range_ledger_days_record_provider_too(frozen_today, monkeypatch):
    import paths
    import pipeline

    _install_provider(monkeypatch, FakeProvider({}), "gmail")
    pipeline.populate_release_cache("2025-06-20", "2025-06-21", 100, 20, log=_silent)

    ledger = _read(paths.SCRAPE_STATUS_PATH)
    assert ledger == {
        "2025-06-20": {"empty": True, "source": "gmail"},
        "2025-06-21": {"empty": True, "source": "gmail"},
    }


# ---------------------------------------------------------------------------
# LOG-20 / PERF-5 — /releases payload: no description bodies, source on rows
# ---------------------------------------------------------------------------
def test_releases_payload_has_no_description_bodies_and_rows_carry_source(
    migrations_mod, data_dir, frozen_today, server_mod
):
    _seed_legacy_stores(data_dir)
    migrations_mod.migrate()

    releases = server_mod.app.test_client().get("/releases").get_json()["releases"]
    assert releases, "fixture rows must survive the migration"
    for rel in releases:
        assert "description" not in rel, "description bodies must leave the /releases payload"
        assert "source" in rel, "every row must carry source"
    enriched = next(rel for rel in releases if rel["url"] == CANONICAL_URL)
    assert enriched["has_description"] is True
    assert "album=111" in enriched["embed_url"]  # derived, not stored
    assert enriched["release_id"] == 111
