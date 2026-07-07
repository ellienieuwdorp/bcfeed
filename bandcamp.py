"""Bandcamp page scraping and the embed-metadata cache (ARC-3/CQ-20/LOG-5).

This module is the single home for the fetch → parse → cache-write path behind
``/embed-meta``. ``get_embed_meta`` is cache-first: a fresh record (positive or
negative) is returned without touching the network, and every fetch outcome is
recorded so failures are not refetched before their retry TTL (PERF-2).

Embed record shape (LOG-20 contract — the record is the single owner of
enrichment state):

- ok:    ``{status: "ok", release_id, is_track, description, fetched_at}`` —
  ``description`` is ``""`` when the page was fetched but had none
  (distinguishes fetched-but-none from never-fetched). When the page carries
  ``og:image`` the record also has ``art_url`` (LOG-19 capture half); the key
  is absent when the page had none (positive records never refetch, so
  absence is stable). ``embed_url`` is NOT stored — it is derived from
  ``release_id`` + ``is_track`` via ``build_embed_url`` at response time
  (WP-14 · LOG-20: stored derived state could go stale).
- error: ``{status: "error", code, fetched_at}`` with
  ``code ∈ {"http_<status>", "no_meta", "network"}``; retried only after
  ``EMBED_ERROR_RETRY_TTL_SECONDS``.

Legacy flat entries ``{release_id, is_track, embed_url, description}`` are
lazily upgraded to ``status: "ok"`` on read (LOG-5 migration); the WP-14
one-shot rewrite (migrations.py) drops stored ``embed_url`` keys and
canonicalizes the URL keys.

WP-12 hardening — all Bandcamp HTTP goes through ``fetch_release_page``, the
single choke point (LOG-7): token-bucket rate limit (~1 req/s sustained,
injectable clock/sleep via ``_rate_limiter``), Retry-After/backoff on 429/503,
10 s timeout, response-size cap, and the ARC-5f/SEC-2 SSRF allowlist
(``validate_fetch_url``): https-only, ``*.bandcamp.com`` or a release-cache
URL, resolve-and-block private/loopback/link-local IPs, manual single-hop
redirect handling with re-validation. A blocked URL raises
``FetchBlockedError`` and is never fetched and never cached.
"""

from __future__ import annotations

import ast
import ipaddress
import json
import logging
import random
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

import json_store
import paths

logger = logging.getLogger(__name__)

# How long a failed fetch is remembered before one retry is allowed.
EMBED_ERROR_RETRY_TTL_SECONDS = 7 * 24 * 60 * 60

FETCH_USER_AGENT = "bcfeed/1.0"
FETCH_TIMEOUT_SECONDS = 10
# Response-size cap (ARC-5f step 5): real release pages are well under this;
# anything bigger is aborted mid-download, never buffered whole.
FETCH_MAX_BYTES = 2 * 1024 * 1024
# Bounded retries on 429/503 (LOG-7): honor Retry-After, else backoff+jitter.
FETCH_MAX_RETRIES = 2
# Redirect budget (ARC-5f step 4): custom domains commonly redirect to
# *.bandcamp.com — one re-validated hop covers that, nothing more.
FETCH_MAX_REDIRECTS = 1

# DNS seam: tests swap this out; production resolves via the socket module.
_getaddrinfo = socket.getaddrinfo


class FetchBlockedError(Exception):
    """The URL failed the SSRF allowlist — never fetched, never cached."""


class OversizedResponseError(Exception):
    """The response body exceeded ``FETCH_MAX_BYTES``; download aborted."""


class _TokenBucket:
    """Token bucket enforcing ~``rate`` requests/s sustained across callers.

    ``clock``/``sleep`` are injectable so tests drive it with a fake clock;
    production uses ``time.monotonic``/``time.sleep``. A small burst
    ``capacity`` keeps interactive one-off fetches instant while sustained
    traffic (preload workers, WP-17) is paced to ``rate``.
    """

    def __init__(
        self,
        rate: float = 1.0,
        capacity: float = 3.0,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self._rate = rate
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block (via the injected sleep) until a request token is available."""
        while True:
            with self._lock:
                now = self._clock()
                elapsed = max(0.0, now - self._updated)
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)

    def sleep(self, seconds: float) -> None:
        """Sleep through the injected clock (used for Retry-After/backoff)."""
        if seconds > 0:
            self._sleep(seconds)


# The one rate limiter every Bandcamp fetch shares (LOG-7). Tests replace this
# attribute with a fake-clock bucket — that swap is the clock injection point.
_rate_limiter = _TokenBucket()


# ---------------------------------------------------------------------------
# SSRF allowlist (ARC-5f / SEC-2)
# ---------------------------------------------------------------------------
def _known_release_url(url: str) -> bool:
    """True if ``url`` is exactly a release the email pipeline produced."""
    import session_store

    return any(rel.get("url") == url for rel in session_store.get_full_release_cache())


def _resolves_to_blocked_ip(host: str) -> bool:
    """Resolve ``host`` and block private/loopback/link-local/reserved ranges.

    An unresolvable host is not blocked here: the fetch itself will fail with
    the same network error a plain outage produces, so refusing early would
    only add a distinguishable oracle without stopping anything the fetch
    would not stop itself.
    """
    try:
        infos = _getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if not ip.is_global:
            return True
    return False


def validate_fetch_url(url: str) -> bool:
    """ARC-5f validation chain for one candidate fetch URL.

    https only; ``*.bandcamp.com`` (any subdomain, or the apex) is allowed
    directly; any other host only if the exact URL is already a release-cache
    key (custom-domain releases the user's own mailbox produced). Finally the
    host must not resolve to a non-global IP — even bandcamp-looking names.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host != "bandcamp.com" and not host.endswith(".bandcamp.com"):
        if not _known_release_url(url):
            return False
    return not _resolves_to_blocked_ip(host)


def extract_bc_meta(page: str | BeautifulSoup) -> dict | None:
    soup = _parse_page(page)
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


def _parse_page(page: str | BeautifulSoup) -> BeautifulSoup:
    """Parse raw HTML, or pass an already-parsed soup straight through.

    The extractors accept either so ``get_embed_meta`` can build ONE soup per
    fetched page and share it (PERF-3 parse-once) while direct string callers
    keep working.
    """
    if isinstance(page, str):
        return BeautifulSoup(page, "html.parser")
    return page


def extract_bandcamp_description(page: str | BeautifulSoup | None) -> str | None:
    if page is None or (isinstance(page, str) and not page):
        return None
    try:
        soup = _parse_page(page)

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


def extract_art_url(page: str | BeautifulSoup) -> str | None:
    """The release artwork URL from ``og:image``, if present (LOG-19)."""
    soup = _parse_page(page)
    meta = soup.find("meta", attrs={"property": "og:image"})
    if meta and meta.get("content"):
        return meta["content"].strip() or None
    return None


def build_embed_url(item_id: int | None, is_track: bool) -> str | None:
    if not item_id:
        return None
    kind = "track" if is_track else "album"
    base = "https://bandcamp.com/EmbeddedPlayer"
    return f"{base}/{kind}={item_id}/size=large/bgcol=ffffff/linkcol=0687f5/tracklist=true/artwork=small/transparent=true/"


def _close_response(resp) -> None:
    close = getattr(resp, "close", None)
    if callable(close):
        close()


def _retry_delay(headers, attempt: int) -> float:
    """Seconds to wait before retry ``attempt`` (1-based): Retry-After wins."""
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except (TypeError, ValueError):
            pass
    # Exponential backoff with jitter (LOG-7): 1s, 2s, ... plus up to 500 ms.
    return float(2 ** (attempt - 1)) + random.uniform(0, 0.5)


def _read_body_capped(resp) -> str:
    """Read a response body, aborting as soon as it exceeds FETCH_MAX_BYTES."""
    headers = getattr(resp, "headers", None) or {}
    declared = headers.get("Content-Length") or headers.get("content-length")
    if declared and str(declared).isdigit() and int(declared) > FETCH_MAX_BYTES:
        _close_response(resp)
        raise OversizedResponseError("declared body size over cap")
    iter_content = getattr(resp, "iter_content", None)
    if not callable(iter_content):
        text = resp.text
        if len(text) > FETCH_MAX_BYTES:
            raise OversizedResponseError("body over cap")
        return text
    chunks = []
    total = 0
    for chunk in iter_content(chunk_size=65536):
        total += len(chunk)
        if total > FETCH_MAX_BYTES:
            _close_response(resp)
            raise OversizedResponseError("body over cap")
        chunks.append(chunk)
    body = b"".join(chunks)
    if isinstance(body, str):  # decode_unicode fakes; requests yields bytes
        return body
    encoding = getattr(resp, "encoding", None) or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def fetch_release_page(url: str) -> str:
    """Download a Bandcamp release page and return its HTML.

    The ONE choke point for all Bandcamp HTTP (LOG-7): validates the URL
    against the SSRF allowlist (raising ``FetchBlockedError`` — never fetched),
    paces requests through the shared token bucket, honors Retry-After /
    backs off on 429/503 (bounded retries), follows at most one re-validated
    redirect hop, and aborts oversized bodies.
    """
    if not validate_fetch_url(url):
        raise FetchBlockedError(url)
    current = url
    redirects = 0
    attempt = 0
    while True:
        _rate_limiter.acquire()
        resp = requests.get(
            current,
            headers={"User-Agent": FETCH_USER_AGENT},
            timeout=FETCH_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
        status = getattr(resp, "status_code", None) or 0
        headers = getattr(resp, "headers", None) or {}
        if status in (429, 503) and attempt < FETCH_MAX_RETRIES:
            _close_response(resp)
            attempt += 1
            _rate_limiter.sleep(_retry_delay(headers, attempt))
            continue
        if status in (301, 302, 303, 307, 308):
            _close_response(resp)
            location = headers.get("Location") or headers.get("location")
            redirects += 1
            if redirects > FETCH_MAX_REDIRECTS or not location:
                raise FetchBlockedError(current)
            target = urljoin(current, location)
            if not validate_fetch_url(target):
                raise FetchBlockedError(target)
            current = target
            continue
        resp.raise_for_status()
        return _read_body_capped(resp)


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
    except FetchBlockedError:
        # Disallowed URL (ARC-5f): never fetched and never cached — the embed
        # cache holds only URLs that passed validation. The route maps this to
        # a uniform generic 400.
        raise
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        code = f"http_{status_code}" if status_code else "network"
        logger.warning("Fetching Bandcamp page failed (%s): %s", code, url)
        record = {"status": "error", "code": code, "fetched_at": int(now)}
    else:
        # ONE soup per page, shared by every extractor (PERF-3 parse-once).
        soup = BeautifulSoup(html_text, "html.parser")
        data = extract_bc_meta(soup)
        if not data:
            # bc-page-properties is required before an ok record is cached
            # (ARC-5f step 5): a non-release page never enriches the cache.
            record = {"status": "error", "code": "no_meta", "fetched_at": int(now)}
        else:
            item_id = data.get("item_id")
            is_track = data.get("item_type") in ("track", "t")
            # embed_url is deliberately NOT stored (LOG-20): consumers derive
            # it from release_id + is_track via build_embed_url.
            record = {
                "status": "ok",
                "release_id": item_id,
                "is_track": is_track,
                "description": extract_bandcamp_description(soup) or "",
                "fetched_at": int(now),
            }
            art_url = extract_art_url(soup)
            if art_url:
                record["art_url"] = art_url

    _store_embed_record(url, record)
    return record
