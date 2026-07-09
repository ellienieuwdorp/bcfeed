"""WP-26 · Wording enforcement grep-guard (UXP-1 / docs/copy.md).

A pragmatic guard that fails if a *banned* implementation term reappears in any
**user-visible** string: JavaScript string literals rendered to the DOM,
``dashboard.html`` text/attributes, and the user-facing message strings the
Python backend sends to the browser (SSE ``message`` fields, ``event: error``
messages, and JSON ``error``/``message`` bodies).

Banned (per docs/copy.md): populate, preload, cache(d), scrape(d), parse, query,
token, credentials, provider, configuration, embed, proxy.

Exemptions (documented, deliberate):

* **Code identifiers.** Any hit where the banned word is glued to a separator
  (``- _ / . #``) is a kebab/snake/dotted/path identifier — CSS class, element
  id, ``data-*`` attribute, JSON key, module path, endpoint, filename — and is
  exempt (e.g. ``populate-range``, ``cached-badge``, ``provider_config``,
  ``/embed-meta``, ``token.pickle``). camelCase identifiers (``isPopulating``,
  ``embedProxyUrl``) never match a ``\\b`` word boundary, so they are exempt for
  free.
* **``IMAP``** is allowed — the user's own mail service uses that word.
* **"email/mail provider(s)", "most providers", "your mail provider"** — the
  real-world sense of "the email service you use". ``provider`` as an internal
  abstraction / settings label stays banned.
* **Developer channels** — JS ``console.*`` args and Python ``app.logger`` /
  ``#`` comments / docstrings are not shown to users. The JS scanner reads only
  string literals (skipping comments and ``console.*``); the Python scanner reads
  only the named user-facing sinks.

To prove the guard is strict, ``test_guard_flags_a_banned_button_label`` feeds it
a ``"Populating…"`` label and asserts it is caught.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_JS_DIR = REPO_ROOT / "web" / "js"
DASHBOARD_HTML = REPO_ROOT / "dashboard.html"
PY_MODULES = [
    "pipeline.py",
    "gmail_client.py",
    "gmail_provider.py",
    "imap_client.py",
    "imap_provider.py",
    "provider_factory.py",
    "credential_store.py",
    "server.py",
]

# The banned stems, as whole words (case-insensitive). "IMAP" is deliberately
# absent (allowed); "config" is absent (only "configuration" is banned).
BANNED = re.compile(
    r"(?i)\b("
    r"populat(?:e|es|ed|ing)|"
    r"preload(?:s|ed|ing)?|"
    r"cach(?:e|es|ed)|"
    r"scrap(?:e|es|ed|ing)|"
    r"pars(?:e|es|ed|ing)|"
    r"quer(?:y|ies|ied|ying)|"
    r"tokens?|"
    r"credentials?|"
    r"providers?|"
    r"configurations?|"
    r"embed(?:s|ded|ding)?|"
    r"prox(?:y|ies)"
    r")\b"
)

_SEPARATORS = set("-_/.#")

# Real-world "email service" sense of provider — allowed. Checked against a small
# window around the hit.
_PROVIDER_OK = re.compile(
    r"(?i)(?:email|mail)\s+providers?"
    r"|most\s+providers?"
    r"|your\s+(?:email\s+|mail\s+)?provider"
    r"|providers?\s+\(including"
    r"|provider'?s\b"
    r"|provider\s+does\s+not"
)


def _is_exempt(text: str, match: re.Match) -> bool:
    """True if a banned hit is a code identifier or an allowed provider phrase."""
    start, end = match.start(), match.end()
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before in _SEPARATORS or after in _SEPARATORS:
        return True  # kebab/snake/dotted/path identifier
    if match.group(1).lower().startswith("provider"):
        window = text[max(0, start - 20) : min(len(text), end + 20)]
        if _PROVIDER_OK.search(window):
            return True
    return False


def _violations_in_text(text: str, where: str) -> list[str]:
    out = []
    for m in BANNED.finditer(text):
        if _is_exempt(text, m):
            continue
        snippet = text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ")
        out.append(f"{where}: …{snippet.strip()}… (banned: {m.group(1)!r})")
    return out


# --- JavaScript: read only string literals, skip comments and console.* -------
# Comments are stripped first (block, then line comments — but not the `//` in a
# URL, guarded by the `:` lookbehind) so comment prose is never scanned. String
# literals (", ', `) are then matched non-overlapping left-to-right; a regex
# literal such as /'/g cannot swallow code because we match on bodies, not by
# hand-tracking quotes. Literals whose call site is `console.*` are skipped
# (developer channel).
_BACKTICK = re.compile(r"`(?:[^`\\]|\\.)*`", re.S)
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")


def _scan_literals(text: str, pattern: re.Pattern, name: str, out: list[str]) -> None:
    for m in pattern.finditer(text):
        if "console." in text[max(0, m.start() - 40) : m.start()]:
            continue  # developer channel
        out.extend(_violations_in_text(m.group(0)[1:-1], name))


def _js_violations(path: Path) -> list[str]:
    src = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
    out: list[str] = []
    # Template literals may span lines; scan and then remove them so their inner
    # quotes can't desync the per-line quoted-string scan below.
    _scan_literals(src, _BACKTICK, path.name, out)
    src = _BACKTICK.sub(" ", src)
    # Per-line so a lone quote in a regex literal (e.g. /"/g) can corrupt at most
    # its own line, never swallow code on later lines.
    for line in src.splitlines():
        line = re.sub(r"(?<![:/])//.*$", "", line)  # line comment, but not URL //
        _scan_literals(line, _QUOTED, path.name, out)
    return out


# --- Python: read only the user-facing message sinks --------------------------
_STR = r"[rfb]*(['\"])(.*?)\2"
_PY_SINKS = [
    re.compile(r"emit\(\s*" + _STR, re.S),  # SSE progress/log message (1st arg)
    re.compile(r"error_stream\(\s*['\"].*?['\"]\s*,\s*" + _STR, re.S),  # error msg (2nd arg)
    re.compile(r'"(?:error|message)"\s*:\s*' + _STR, re.S),  # JSON error/message body
    re.compile(r"\blog\(\s*" + _STR, re.S),  # streamed log line
]


def _py_violations(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    out = []
    for pat in _PY_SINKS:
        for m in pat.finditer(src):
            msg = m.group(m.lastindex)  # the captured string body
            out.extend(_violations_in_text(msg, path.name))
    return out


# --- HTML: text + attributes, comments stripped -------------------------------
def _html_violations(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    return _violations_in_text(src, path.name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_no_banned_terms_in_js():
    violations = []
    for path in sorted(WEB_JS_DIR.glob("*.js")):
        violations.extend(_js_violations(path))
    assert not violations, "banned vocabulary in user-visible JS:\n" + "\n".join(violations)


def test_no_banned_terms_in_dashboard_html():
    violations = _html_violations(DASHBOARD_HTML)
    assert not violations, "banned vocabulary in dashboard.html:\n" + "\n".join(violations)


def test_no_banned_terms_in_python_user_facing_messages():
    violations = []
    for name in PY_MODULES:
        violations.extend(_py_violations(REPO_ROOT / name))
    assert not violations, "banned vocabulary in Python user-facing messages:\n" + "\n".join(
        violations
    )


def test_guard_flags_a_banned_button_label():
    """The guard must reject a reintroduced banned label (strictness proof)."""
    # A plain single-word label like the old "Populating…" is caught…
    assert _violations_in_text("Populating…", "sample")
    assert _violations_in_text("Populate release list", "sample")
    assert _violations_in_text("All embeds cached for this range", "sample")
    # …while identifiers and the allowed provider sense are not.
    assert not _violations_in_text("populate-range", "sample")
    assert not _violations_in_text("cached-badge", "sample")
    assert not _violations_in_text("Works with most email providers", "sample")
    assert not _violations_in_text("Mail server (IMAP)", "sample")
