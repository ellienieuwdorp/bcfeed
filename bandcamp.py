"""Bandcamp page scraping and the embed-metadata cache (ARC-3/CQ-20/LOG-5).

This module is the single home for the fetch → parse → cache-write path behind
``/embed-meta``. ``get_embed_meta`` is cache-first: a fresh record (positive or
negative) is returned without touching the network, and every fetch outcome is
recorded so failures are not refetched before their retry TTL (PERF-2).

Embed record shape (LOG-20 contract, written from day one):

- ok:    ``{status: "ok", release_id, is_track, embed_url, description,
  fetched_at}`` — ``description`` is ``""`` when the page was fetched but had
  none (distinguishes fetched-but-none from never-fetched).
- error: ``{status: "error", code, fetched_at}`` with
  ``code ∈ {"http_<status>", "no_meta", "network"}``; retried only after
  ``EMBED_ERROR_RETRY_TTL_SECONDS``.

Legacy flat entries ``{release_id, is_track, embed_url, description}`` are
lazily upgraded to ``status: "ok"`` on read (LOG-5 migration); WP-14 performs
the one-shot rewrite and drops the stored ``embed_url``.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import time

import requests
from bs4 import BeautifulSoup

import json_store
import paths

logger = logging.getLogger(__name__)

# How long a failed fetch is remembered before one retry is allowed.
EMBED_ERROR_RETRY_TTL_SECONDS = 7 * 24 * 60 * 60

FETCH_USER_AGENT = "bcfeed/1.0"
FETCH_TIMEOUT_SECONDS = 10


def extract_bc_meta(html_text: str) -> dict | None:
    soup = BeautifulSoup(html_text, "html.parser")
    meta = soup.find("meta", attrs={"name": "bc-page-properties"})
    if not meta or "content" not in meta.attrs:
        return None
    raw = meta["content"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Some pages carry a Python-literal dict instead of JSON. A content
        # value that is neither must yield None, never an exception (CQ-20/PY-10).
        try:
            data = ast.literal_eval(raw)
        except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
            return None
    return data if isinstance(data, dict) else None


def extract_bandcamp_description(html_text: str) -> str | None:
    if not html_text:
        return None
    try:
        soup = BeautifulSoup(html_text, "html.parser")

        def _collect(el):
            if not el:
                return ""
            text = el.get_text("\n")
            text = text.replace("\r\n", "\n")
            lines = [ln.strip() for ln in text.split("\n")]
            text = "\n".join(lines)
            return re.sub(r"\n{3,}", "\n\n", text).strip("\n")

        about = soup.find(id="tralbum-about") or soup.find("div", class_="tralbum-about")
        credits = soup.find(class_="tralbum-credits") or soup.find(id="tralbum-credits")

        parts = []
        about_text = _collect(about)
        credits_text = _collect(credits)
        if about_text:
            parts.append(about_text)
        if credits_text:
            parts.append(f"{credits_text}")
        if parts:
            return "\n\n".join(parts)

        meta = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        if meta and meta.get("content"):
            return meta["content"].strip()
    except Exception:
        return None
    return None


def build_embed_url(item_id: int | None, is_track: bool) -> str | None:
    if not item_id:
        return None
    kind = "track" if is_track else "album"
    base = "https://bandcamp.com/EmbeddedPlayer"
    return f"{base}/{kind}={item_id}/size=large/bgcol=ffffff/linkcol=0687f5/tracklist=true/artwork=small/transparent=true/"


def fetch_release_page(url: str) -> str:
    """Download a Bandcamp release page and return its HTML.

    Kept as the one module-level fetch seam so WP-12 can swap in the polite,
    validated fetcher without touching ``get_embed_meta``.
    """
    resp = requests.get(
        url, headers={"User-Agent": FETCH_USER_AGENT}, timeout=FETCH_TIMEOUT_SECONDS
    )
    resp.raise_for_status()
    return resp.text


# ---------------------------------------------------------------------------
# Embed cache (all IO through json_store; server.py never touches this file)
# ---------------------------------------------------------------------------
def load_embed_cache() -> dict:
    """The raw embed cache store (dict of url → record)."""
    # paths.EMBED_CACHE_PATH is read live (not bound at import) so the test
    # harness's per-test data-dir isolation applies without module reloads.
    data = json_store.read_json(paths.EMBED_CACHE_PATH, {})
    return data if isinstance(data, dict) else {}


def _upgrade_record(entry) -> dict | None:
    """Normalize one cache entry to the LOG-20 record shape.

    Entries already carrying ``status`` pass through; legacy flat entries
    (pre-LOG-5) are lazily upgraded to ``status: "ok"`` with an unknown
    ``fetched_at``. Anything unrecognizable reads as not-cached.
    """
    if not isinstance(entry, dict):
        return None
    if entry.get("status") in ("ok", "error"):
        return entry
    if entry.get("embed_url") or entry.get("release_id"):
        upgraded = dict(entry)
        upgraded["status"] = "ok"
        upgraded.setdefault("fetched_at", None)
        return upgraded
    return None


def embed_cache_snapshot() -> dict[str, dict]:
    """All embed records keyed by URL, legacy entries lazily upgraded."""
    snapshot = {}
    for url, entry in load_embed_cache().items():
        record = _upgrade_record(entry)
        if record is not None:
            snapshot[url] = record
    return snapshot


def _store_embed_record(url: str, record: dict) -> None:
    def mutator(cache):
        if not isinstance(cache, dict):
            cache = {}
        cache[url] = record
        return cache

    json_store.update_json(paths.EMBED_CACHE_PATH, mutator, {}, indent=2)


def _cached_record(url: str, now: float) -> dict | None:
    """Return the cached record for ``url`` if it is still authoritative.

    Positive records never expire (cache aggressively, never re-fetch);
    negative records expire after ``EMBED_ERROR_RETRY_TTL_SECONDS`` — or
    immediately when their age is unknowable — allowing one retry.
    """
    record = _upgrade_record(load_embed_cache().get(url))
    if record is None:
        return None
    if record["status"] == "error":
        fetched_at = record.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return None
        if now - fetched_at >= EMBED_ERROR_RETRY_TTL_SECONDS:
            return None
    return record


def get_embed_meta(url: str, *, now: float | None = None) -> dict:
    """Cache-first embed metadata for one release URL.

    Returns the embed record (see module docstring). Consults the cache first;
    on a miss (or an expired negative record) fetches the page once, parses it,
    records the outcome — success or failure — and returns the new record.
    """
    now = time.time() if now is None else now
    record = _cached_record(url, now)
    if record is not None:
        return record

    try:
        html_text = fetch_release_page(url)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        code = f"http_{status_code}" if status_code else "network"
        logger.warning("Fetching Bandcamp page failed (%s): %s", code, url)
        record = {"status": "error", "code": code, "fetched_at": int(now)}
    else:
        data = extract_bc_meta(html_text)
        if not data:
            record = {"status": "error", "code": "no_meta", "fetched_at": int(now)}
        else:
            item_id = data.get("item_id")
            is_track = data.get("item_type") in ("track", "t")
            record = {
                "status": "ok",
                "release_id": item_id,
                "is_track": is_track,
                "embed_url": build_embed_url(item_id, is_track),
                "description": extract_bandcamp_description(html_text) or "",
                "fetched_at": int(now),
            }

    _store_embed_record(url, record)
    return record
