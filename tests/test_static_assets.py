"""WP-18 · ARC-4 — the /web/js static-module route.

After the frontend was split from the single ``dashboard.js`` IIFE into native
ES modules under ``web/js/``, the server grew one minimal, path-traversal-safe
route to serve them. These tests pin its contract: real modules serve 200 with
the JavaScript mimetype, and anything that tries to escape ``web/js`` (or is not
a ``.js`` file) gets the same generic 404 as any other missing app file — with
no CORS header and no leaked filesystem detail.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def server_mod(isolated_data_dir):
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


def test_main_module_serves_200_javascript(server_mod):
    resp = server_mod.app.test_client().get("/web/js/main.js")
    assert resp.status_code == 200
    assert resp.mimetype == "application/javascript"
    assert len(resp.data) > 0
    # It really is the bootstrap module, not some other file.
    assert b"initConfig" in resp.data


def test_every_shipped_module_is_served(server_mod):
    client = server_mod.app.test_client()
    for path in sorted((REPO_ROOT / "web" / "js").glob("*.js")):
        resp = client.get(f"/web/js/{path.name}")
        assert resp.status_code == 200, path.name
        assert resp.mimetype == "application/javascript", path.name


def test_no_cors_header_on_module_response(server_mod):
    resp = server_mod.app.test_client().get("/web/js/main.js")
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_missing_module_is_generic_404(server_mod):
    resp = server_mod.app.test_client().get("/web/js/does-not-exist.js")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    assert str(REPO_ROOT) not in body


@pytest.mark.parametrize(
    "url",
    [
        "/web/js/../server.py",
        "/web/js/%2e%2e/server.py",
        "/web/js/%2e%2e/%2e%2e/paths.py",
        "/web/js/../../server.py",
    ],
)
def test_path_traversal_is_rejected(server_mod, url):
    resp = server_mod.app.test_client().get(url)
    assert resp.status_code == 404, url
    body = resp.get_data(as_text=True)
    # The server source must never be reflected back.
    assert "frontend_module" not in body
    assert "import" not in body


def test_non_js_extension_under_web_js_is_rejected(server_mod, isolated_data_dir):
    # A real file next to the modules that is not a .js is still not served.
    (REPO_ROOT / "web" / "js" / "secret.txt").write_text("nope", encoding="utf-8")
    try:
        resp = server_mod.app.test_client().get("/web/js/secret.txt")
        assert resp.status_code == 404
    finally:
        (REPO_ROOT / "web" / "js" / "secret.txt").unlink(missing_ok=True)


def test_dashboard_css_still_served(server_mod):
    resp = server_mod.app.test_client().get("/dashboard.css")
    assert resp.status_code == 200
    assert resp.mimetype == "text/css"
