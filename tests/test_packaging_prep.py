"""WP-28 · Packaging prep hygiene (ARCH-9 / CQ-64).

Locks in the three packaging invariants:

1. **One version string.** ``paths.__version__`` is the single source of truth;
   the literal appears in exactly one place across the code-carrying files, is
   surfaced via ``/config.json``, and is rendered in Settings→About from config
   (never hardcoded in HTML/JS).
2. **``resource_path()`` resolves bundled assets** in the dev tree and honours a
   PyInstaller ``sys._MEIPASS`` bundle root (the seam WP-29 builds on).
3. **The dead ``_MEIPASS`` credentials branch is gone** from gmail_client.py —
   bundling an OAuth client secret contradicts the user-owned-credentials model.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import paths

REPO_ROOT = Path(paths.__file__).resolve().parent
EXPECTED_VERSION = "1.1.0"


@pytest.fixture
def server_mod(isolated_data_dir):
    """Import server bound to this test's isolated data dir."""
    import server

    importlib.reload(server)
    server.app.config["TESTING"] = True
    return server


# ---------------------------------------------------------------------------
# One version string
# ---------------------------------------------------------------------------
def test_version_constant_defined_in_paths():
    assert paths.__version__ == EXPECTED_VERSION
    # A plain string literal — pyproject reads it via AST attr resolution.
    assert isinstance(paths.__version__, str)


def test_version_literal_appears_exactly_once_in_source():
    """The version number is hardcoded in ONE code-carrying file: paths.py.

    pyproject reads it dynamically (attr), requirements has no version, and the
    HTML/JS render it from /config.json — so the literal must not leak anywhere
    else. Docs/tags (WP-30) are intentionally out of this scope.
    """
    code_files = [
        REPO_ROOT / "paths.py",
        REPO_ROOT / "server.py",
        REPO_ROOT / "bcfeed.py",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "requirements.txt",
        REPO_ROOT / "dashboard.html",
        *sorted((REPO_ROOT / "web" / "js").glob("*.js")),
    ]
    carriers = [
        f.relative_to(REPO_ROOT).as_posix()
        for f in code_files
        if f.exists() and EXPECTED_VERSION in f.read_text(encoding="utf-8")
    ]
    assert carriers == ["paths.py"], f"version literal leaked into: {carriers}"


def test_config_json_carries_version(server_mod):
    resp = server_mod.app.test_client().get("/config.json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["version"] == paths.__version__


def test_about_renders_from_config_not_hardcoded():
    """Settings→About must read the version from config, not embed a number."""
    html = (REPO_ROOT / "dashboard.html").read_text(encoding="utf-8")
    # The About element exists as a config-render target...
    assert 'id="about-version"' in html
    # ...and carries no baked-in version number (e.g. the old "bcfeed v1.0").
    assert "bcfeed v" not in html

    settings_js = (REPO_ROOT / "web" / "js" / "settings.js").read_text(encoding="utf-8")
    # settings.js sources the version from the runtime config object.
    assert "config.raw.version" in settings_js
    # No hardcoded semver-ish literal in the settings controller.
    assert EXPECTED_VERSION not in settings_js


# ---------------------------------------------------------------------------
# resource_path()
# ---------------------------------------------------------------------------
def test_resource_path_resolves_real_dev_assets():
    """Every bundled asset resource_path names exists in the dev tree."""
    for name in (
        "dashboard.html",
        "dashboard.css",
        "README.md",
        "SETUP.md",
        "IMAP_SETUP.md",
        "GMAIL_SETUP.md",
    ):
        resolved = paths.resource_path(name)
        assert resolved.exists(), f"resource_path({name!r}) → {resolved} missing"


def test_asset_constants_route_through_resource_path():
    """The paths.py asset constants equal resource_path() of their basenames."""
    assert paths.DASHBOARD_PATH == paths.resource_path("dashboard.html")
    assert paths.DASHBOARD_CSS_PATH == paths.resource_path("dashboard.css")
    assert paths.README_PATH == paths.resource_path("README.md")
    assert paths.SETUP_PATH == paths.resource_path("SETUP.md")
    assert paths.IMAP_SETUP_PATH == paths.resource_path("IMAP_SETUP.md")
    assert paths.GMAIL_SETUP_PATH == paths.resource_path("GMAIL_SETUP.md")


def test_resource_path_honours_meipass_bundle_root(monkeypatch, tmp_path):
    """Under a PyInstaller bundle, assets resolve from sys._MEIPASS."""
    import sys

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = paths.resource_path("dashboard.html")
    assert resolved == tmp_path / "dashboard.html"


# ---------------------------------------------------------------------------
# Dead _MEIPASS credentials branch removed
# ---------------------------------------------------------------------------
def test_gmail_client_has_no_meipass_credentials_branch():
    """The client-secret-from-bundle branch is gone from gmail_client.py."""
    source = (REPO_ROOT / "gmail_client.py").read_text(encoding="utf-8")
    assert "_MEIPASS" not in source
    # And `sys` is no longer imported there (its only use was that branch).
    assert "import sys" not in source


def test_only_resource_path_references_meipass():
    """The lone legitimate _MEIPASS reference lives in paths.resource_path()."""
    paths_source = (REPO_ROOT / "paths.py").read_text(encoding="utf-8")
    assert paths_source.count("_MEIPASS") >= 1
    server_source = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert "_MEIPASS" not in server_source
