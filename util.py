import datetime
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit


def today() -> datetime.date:
    """Return the current local date.

    Single choke point for "now" so tests can freeze it and every caller
    agrees on the same day boundary (the pervasive exclude-today logic
    depends on this being consistent within a run).
    """
    return datetime.date.today()


def parse_date(val, *, allow_none: bool = False) -> datetime.date | None:
    """Parse an ISO (YYYY-MM-DD) or RFC 2822 date string into a date."""
    if val is None:
        if allow_none:
            return None
        raise ValueError("Missing date")
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.datetime.strptime(val, fmt).date()
            except ValueError:
                continue
        try:
            parsed = parsedate_to_datetime(val)
            if parsed is not None:
                return parsed.date()
        except (TypeError, ValueError, IndexError):
            pass
    if allow_none:
        return None
    raise ValueError("Incorrect date format, should be YYYY-MM-DD or RFC 2822 date")


def canonical_release_url(url) -> str | None:
    """The one canonical form of a release URL (LOG-9): identity everywhere.

    One canonical URL = one release across every URL-keyed store (release
    cache, embed cache, viewed, starred). Normalization: force ``https``,
    lowercase the host (dropping default ports), strip query/fragment and any
    trailing slash. Path case is PRESERVED — Bandcamp slugs are, in principle,
    case-sensitive; only the authority is case-insensitive.

    Idempotent: ``canonical_release_url(canonical_release_url(u)) ==
    canonical_release_url(u)``. Returns ``None`` for empty/non-string input.
    """
    if not isinstance(url, str):
        return None
    raw = url.strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.netloc:
        # Scheme-less input ("artist.bandcamp.com/album/x"): re-split with a
        # scheme so the host/path boundary is found.
        parts = urlsplit(f"https://{raw.lstrip('/')}")
        if not parts.netloc:
            return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    netloc = host
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port not in (80, 443):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def construct_release(
    is_track=None,
    release_url=None,
    date=None,
    artist_name=None,
    release_title=None,
    page_name=None,
    release_id=None,
    source=None,
):
    """Build the canonical release dict (schema v2).

    The URL is canonicalized here — parse time is the first of the LOG-9
    normalization points, so no non-canonical URL ever enters a store.
    ``source`` names the email provider that produced the row
    ("gmail" | "imap"); the pipeline sets it (LOG-21/LOG-22 schema half).
    The always-null ``img_url`` key is gone (LOG-19): artwork is enrichment
    state (``art_url`` on the embed record), never a parse-time field.
    """
    release = {}
    release["date"] = date
    release["artist"] = artist_name
    release["title"] = release_title
    release["page_name"] = page_name
    release["url"] = canonical_release_url(release_url)
    release["release_id"] = release_id
    release["is_track"] = is_track
    release["source"] = source
    return release


def dedupe_by_url(items: Iterable[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for item in items:
        url = item.get("url")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        deduped.append(item)
    return deduped


def dedupe_by_date(items: Iterable[dict], *, keep: str = "last") -> list[dict]:
    """Deduplicate by URL, keeping the first/last entry based on release date.

    Why ``keep="last"`` is the pipeline's arbitration rule (LOG-10 contract):
    the same URL can arrive on multiple dates (an announcement email followed
    by the release email, or a re-send), and the *most recent* notification
    date is the one closest to actual availability — so the row lands on the
    later date. The ``>=`` comparison makes later-processed equal-date entries
    win deterministically, which means the result converges regardless of the
    order cached and freshly-parsed lists are combined.

    Items with a missing or unparseable date are tolerated, never fatal
    (CQ-14/LOG-10): a date-less item never wins a conflict against a dated
    entry for the same URL, so one bad cached record degrades to one
    never-winning record instead of aborting every populate of its range.

    Field-precedence note (LOG-10, documented here with the dedupe contract):
    a row's parse-time ``is_track`` is the URL-path heuristic and is only the
    pre-enrichment fallback — Bandcamp's own ``item_type`` (via the embed
    record) is authoritative and overlays it at read time in ``/releases``.
    Enrichment never writes back into release rows (one owner per field).
    """
    if keep not in {"first", "last"}:
        raise ValueError("keep must be 'first' or 'last'")

    kept: dict[str, tuple[datetime.date | None, dict]] = {}
    without_url: list[dict] = []

    for item in items:
        url = item.get("url")
        if not url:
            without_url.append(item)
            continue
        date = parse_date(item.get("date"), allow_none=True)
        if url not in kept:
            kept[url] = (date, item)
            continue
        existing_date, _ = kept[url]
        if date is None:
            # Date-less items never win a keep conflict.
            replace = False
        elif existing_date is None:
            replace = True
        elif keep == "last":
            replace = date >= existing_date
        else:
            replace = date <= existing_date
        if replace:
            kept[url] = (date, item)

    deduped = [item for _, item in kept.values()]
    deduped.extend(without_url)
    return deduped
