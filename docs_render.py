"""In-app markdown docs rendering (CQ-31): renderer config + link map.

Flask-free so the renderer configuration is testable on its own; server.py
keeps only the thin routes that wrap the rendered body in templates/docs.html.
"""

from __future__ import annotations

import html
from pathlib import Path

from markdown_it import MarkdownIt

# Repo-relative doc filenames → the in-app route that serves them.
DOC_LINK_MAP = {
    "SETUP.md": "setup",
    "IMAP_SETUP.md": "setup-imap",
    "GMAIL_SETUP.md": "setup-gmail",
    "README.md": "readme",
}


def _build_doc_markdown_renderer() -> MarkdownIt:
    md = MarkdownIt("gfm-like", {"html": True})

    def render_link_open(self, tokens, idx, options, env):
        href = tokens[idx].attrGet("href") or ""
        tokens[idx].attrSet("href", DOC_LINK_MAP.get(href, href))
        tokens[idx].attrSet("target", "_blank")
        tokens[idx].attrSet("rel", "noopener")
        return self.renderToken(tokens, idx, options, env)

    md.add_render_rule("link_open", render_link_open)
    return md


DOC_MARKDOWN_RENDERER = _build_doc_markdown_renderer()


def render_doc_body(path: Path) -> str | None:
    """Render a markdown doc file to HTML; None when the file is missing.

    Falls back to escaped preformatted text if rendering itself fails, so a
    doc always displays something.
    """
    if not path.exists():
        return None
    markdown_text = path.read_text(encoding="utf-8")
    try:
        return DOC_MARKDOWN_RENDERER.render(markdown_text)
    except Exception:
        return f"<pre>{html.escape(markdown_text)}</pre>"
