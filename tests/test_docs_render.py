"""WP-03b · CQ-43 markdown doc-render goldens (re-scoped at e363bf4).

The renderer is ``markdown-it-py`` now, so these goldens do NOT re-test the
library — they guard *our* configuration wrapped around it:

* the ``DOC_LINK_MAP`` internal-link rewrite (SETUP.md / GMAIL_SETUP.md /
  IMAP_SETUP.md / README.md → the in-app route) with ``target=_blank`` and
  ``rel=noopener`` injected on every rendered anchor;
* the ``html:true`` posture (raw HTML in our own trusted docs passes through);
* a regression-lock that a markdown ``javascript:`` link is NOT turned into a
  live anchor (keeps the upstream-fixed SEC-8 fixed).

Small crafted snippets carry the behavior locks (deterministic, minimal); one
golden per real served doc locks the finalized (WP-09) doc structure. Regenerate
the goldens deliberately after an intended renderer or doc change with::

    BCFEED_REGEN_GOLDENS=1 pytest tests/test_docs_render.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

GOLDENS_DIR = Path(__file__).resolve().parent / "goldens"
REPO_ROOT = Path(__file__).resolve().parent.parent
_REGEN = os.environ.get("BCFEED_REGEN_GOLDENS") == "1"


def _render(markdown_text: str) -> str:
    import server

    return server.DOC_MARKDOWN_RENDERER.render(markdown_text)


def _check_golden(name: str, rendered: str) -> str:
    """Compare ``rendered`` to the stored golden (or (re)write it under REGEN)."""
    golden = GOLDENS_DIR / name
    if _REGEN:
        GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
        golden.write_text(rendered, encoding="utf-8")
    assert golden.exists(), f"missing golden {name}; regenerate with BCFEED_REGEN_GOLDENS=1"
    assert rendered == golden.read_text(encoding="utf-8"), (
        f"rendered output drifted from golden {name}; if intended, regenerate"
    )
    return rendered


# ---------------------------------------------------------------------------
# Crafted-snippet behavior locks
# ---------------------------------------------------------------------------
INTERNAL_LINKS_MD = (
    "[Setup](SETUP.md) · [Gmail](GMAIL_SETUP.md) · [IMAP](IMAP_SETUP.md) · [Readme](README.md)\n"
)


def test_internal_link_rewrite_golden():
    out = _check_golden("snippet_internal_links.html", _render(INTERNAL_LINKS_MD))
    # Every internal doc filename becomes its in-app route.
    assert 'href="setup"' in out
    assert 'href="setup-gmail"' in out
    assert 'href="setup-imap"' in out
    assert 'href="readme"' in out
    # None of the raw .md targets survive as hrefs.
    for md in ("SETUP.md", "GMAIL_SETUP.md", "IMAP_SETUP.md", "README.md"):
        assert f'href="{md}"' not in out
    # target/rel injected on each.
    assert out.count('target="_blank"') == 4
    assert out.count('rel="noopener"') == 4


def test_external_link_keeps_href_but_gets_rel_golden():
    md = "[Privacy](privacy.md) and [brew](https://brew.sh)\n"
    out = _check_golden("snippet_external_links.html", _render(md))
    # Non-mapped targets are left intact (privacy.md is not an in-app route)...
    assert 'href="privacy.md"' in out
    assert 'href="https://brew.sh"' in out
    # ...but still get the safe target/rel treatment.
    assert out.count('target="_blank"') == 2
    assert out.count('rel="noopener"') == 2


def test_raw_html_passes_through_golden():
    md = 'Before <div class="note">raw <b>html</b> block</div> after\n'
    out = _check_golden("snippet_raw_html.html", _render(md))
    # html:true posture: our own doc HTML is not escaped.
    assert '<div class="note">' in out
    assert "<b>html</b>" in out
    assert "&lt;div" not in out


def test_javascript_href_is_not_a_live_link_golden():
    md = "[click me](javascript:alert(1))\n"
    out = _check_golden("snippet_js_href.html", _render(md))
    # SEC-8 regression lock: the markdown link is refused, rendered as literal
    # text — never a live anchor with a javascript: href.
    assert "<a " not in out
    assert "javascript:alert(1)" in out  # present only as escaped literal text
    assert 'href="javascript:' not in out


# ---------------------------------------------------------------------------
# Real served docs — one golden each (structure lock)
# ---------------------------------------------------------------------------
DOC_FILES = {
    "readme": "README.md",
    "setup": "SETUP.md",
    "gmail_setup": "GMAIL_SETUP.md",
    "imap_setup": "IMAP_SETUP.md",
}


@pytest.mark.parametrize("stem,filename", sorted(DOC_FILES.items()))
def test_real_doc_render_golden(stem, filename):
    source = (REPO_ROOT / filename).read_text(encoding="utf-8")
    out = _check_golden(f"doc_{stem}.html", _render(source))
    # Structural invariants that must hold for every rendered doc, independent
    # of the exact prose:
    import re

    anchors = re.findall(r"<a\b[^>]*>", out)
    assert anchors, f"{filename} rendered no links at all"
    for a in anchors:
        assert 'target="_blank"' in a, f"anchor without target=_blank in {filename}: {a}"
        assert 'rel="noopener"' in a, f"anchor without rel=noopener in {filename}: {a}"
    assert 'href="javascript:' not in out


def test_readme_internal_links_are_rewritten_to_routes():
    source = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    out = _render(source)
    # README links to all three sibling docs; each must be an in-app route.
    assert 'href="setup"' in out
    assert 'href="setup-gmail"' in out
    assert 'href="setup-imap"' in out
    assert 'href="SETUP.md"' not in out
