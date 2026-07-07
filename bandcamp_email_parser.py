from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup
from furl import furl

# ---------------------------------------------------------------------------
# Stage-1 subject classification (LOG-14): a quoted-OR list of known
# release-notification subject forms, not a single English startswith. The
# non-English entries are the locale variants exercised by the synthetic
# fixture matrix; extend this tuple as real localized samples are collected.
# Matching is a case-folded substring test so artist-first word orders
# (e.g. the Japanese form) match too.
RELEASE_SUBJECT_PHRASES = (
    "new release from",  # en
    "neue veröffentlichung von",  # de
    "nueva publicación de",  # es
    "nouvelle sortie de",  # fr
    "novo lançamento de",  # pt
    "の新作リリース",  # ja (artist-first)
)

# Reply/forward prefixes reject a subject outright, even when it quotes a
# release phrase ("Re: New release from …" is somebody's reply, not a
# notification).
_REPLY_PREFIXES = ("re:", "fwd:", "fw:", "aw:", "tr:", "sv:")

# Other noreply@bandcamp.com mail (receipts, digests, recommendations) also
# carries /album/ links, so the sender alone can never be the filter — these
# subject markers reject before the link stage runs.
_NON_RELEASE_SUBJECT_MARKERS = (
    "receipt",
    "your order",
    "order confirmation",
    "digest",
    "you might like",
    "recommended for you",
)


def _subject_is_release_notification(subject_text: str) -> bool:
    """Stage 1 of the two-stage match (LOG-14): classify the subject line.

    Accepts any known release-notification subject form (quoted-OR across
    locales); rejects replies/forwards and known non-release Bandcamp mail
    (receipts, digests, recommendations). Rejects surface as counted skips in
    the pipeline — never junk rows, never an aborted run.
    """
    lowered = subject_text.casefold()
    if any(lowered.startswith(prefix) for prefix in _REPLY_PREFIXES):
        return False
    if any(marker in lowered for marker in _NON_RELEASE_SUBJECT_MARKERS):
        return False
    return any(phrase.casefold() in lowered for phrase in RELEASE_SUBJECT_PHRASES)


def parse_release_email(email_html: str | bytes | None, subject: str | None = None):
    """
    Parse a Bandcamp release-notification email into lightweight release info.

    Returns a 6-tuple:
        (None, release_url, is_track, artist_name, release_title, page_name)

    The leading slot is a reserved placeholder kept for tuple-shape stability;
    artwork extraction is a later change (LOG-19). Nothing is parsed into it —
    the old always-None field plumbing was removed here (CQ-32).
    """
    release_url = None
    is_track = None
    artist_name = None
    release_title = None
    page_name = None

    s = email_html
    try:
        s = s.decode()  # type: ignore[union-attr]
    except Exception:
        s = "" if s is None else str(s)

    if not s or s.lower() == "none":
        return None, None, None, None, None, None

    subject_text = (subject or "").strip()
    # Stage 1 (LOG-14): classify the subject. If we can't read the subject,
    # treat it as non-release to avoid misclassifying other Bandcamp emails
    # (orders, merch, digests, etc.).
    if not subject_text or not _subject_is_release_notification(subject_text):
        return None, None, None, None, None, None

    soup = BeautifulSoup(s, "html.parser")

    def _find_bandcamp_release_url() -> str | None:
        """Stage 2 (LOG-14/LOG-15): pick the genuine release link.

        Collects every candidate release link (a path containing /album/ or
        /track/ — custom artist domains stay supported), then chooses by
        structure instead of first-anchor-wins: real notifications repeat the
        release link (artwork, title, button) while footer/marketing decoys
        ("discover more", digest items) appear once — most-frequent wins, an
        anchor wrapping the artwork <img> breaks ties, then document order.
        """
        candidates: list[str] = []
        wraps_image: set[str] = set()
        for a in soup.find_all("a", href=True):
            parsed = furl(a["href"])
            path = str(parsed.path).lower()
            if "/album/" not in path and "/track/" not in path:
                continue
            url = parsed.remove(args=True, fragment=True).url
            candidates.append(url)
            if a.find("img") is not None:
                wraps_image.add(url)
        if not candidates:
            return None

        counts = Counter(candidates)

        def _rank(url: str) -> tuple:
            return (counts[url], url in wraps_image, -candidates.index(url))

        return max(dict.fromkeys(candidates), key=_rank)

    release_url = _find_bandcamp_release_url()
    if release_url is None:
        return None, None, None, None, None, None

    # track (vs release) flag
    release_path = str(furl(release_url).path).lower()
    is_track = "/track/" in release_path

    # attempt to scrape artist/release/page from the email itself
    # formats:
    # "page_name just released release_title by artist_name, check it out here"
    # "artist_name just released release_title, check it out here"
    full_text = soup.get_text(" ", strip=True)
    # Remove the leading greeting which always starts with "Greetings <username>, "
    if full_text.lower().startswith("greetings "):
        # drop first sentence up to first comma
        if "," in full_text:
            full_text = full_text.split(",", 1)[1].strip()
    # Strip the trailing call-to-action
    full_text = re.split(r",\s*check it out here", full_text, flags=re.IGNORECASE)[0].strip()

    # Expecting one of:
    # 1) "<page_name> just released <release_title>"
    # 2) "<page_name> just released <release_title> by <artist_name>"
    # or with "just announced" instead of "just released"
    release_phrase = r"just\s+(?:released|announced)"
    release_match = re.search(release_phrase, full_text, flags=re.IGNORECASE)
    after = ""
    if release_match:
        before, after = re.split(release_phrase, full_text, maxsplit=1, flags=re.IGNORECASE)
        page_name = (page_name or before).strip() if before else page_name
        after = after.strip()

    italic_texts = []
    for tag in soup.find_all(["span", "i", "em"]):
        style = tag.get("style", "").lower()
        if tag.name in {"i", "em"} or "italic" in style:
            text = tag.get_text(" ", strip=True)
            if text:
                italic_texts.append(text)

    if italic_texts:
        if after:
            for text in italic_texts:
                if text in after:
                    release_title = text
                    break
        if not release_title:
            release_title = italic_texts[0]

    if after and release_title:
        m = re.search(re.escape(release_title) + r"\s+by\s+(.+)$", after, flags=re.IGNORECASE)
        if m:
            artist_name = artist_name or m.group(1).strip()

    return None, release_url, is_track, artist_name, release_title, page_name
