# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the double-clickable macOS ``bcfeed.app`` (WP-29 · ARC-6).

Onedir windowed bundle whose entry point is the existing ``bcfeed.main`` (start
the local Flask server, open the dashboard in the browser). The Homebrew tap
stays the primary channel; this ``.app`` is a GitHub release asset. Nothing here
changes runtime code — every bundled asset is resolved at runtime by
``paths.resource_path()`` (WP-28), which prefers ``sys._MEIPASS`` when frozen, so
the assets simply have to land at the bundle root under the same relative names.

Build with ``packaging/build_app.sh`` (clean + pyinstaller + ad-hoc codesign).

IMPORTANT (privacy model, ARC-6 / R5): no OAuth client secret is ever bundled.
``credentials.json`` / ``client_secret`` are user-owned and live only in the data
dir / system keychain, never inside a distributed binary.
"""

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# The spec executes with the repo root off sys.path; add it so `import paths`
# (the single source of truth for the version) resolves. SPECPATH is injected by
# PyInstaller and is the directory containing this spec (the repo root).
sys.path.insert(0, SPECPATH)  # noqa: F821 — SPECPATH is a PyInstaller spec global

import paths  # single source of truth for the version (paths.__version__)  # noqa: E402

# --- Bundled read-only assets --------------------------------------------
# Enumerated from paths.py: every path built via resource_path() that the
# server actually serves. (paths.DASHBOARD_JS_PATH is a dead leftover constant
# — dashboard.js was split into web/js/*.js by WP-18 and no longer exists.)
#   dashboard.html / dashboard.css  -> /dashboard, /dashboard.css
#   web/ (the ES modules)           -> /web/js/*.js
#   templates/ (docs.html)          -> render_template() for the doc routes
#   *.md docs                       -> /readme, /setup, /setup-gmail, /setup-imap
datas = [
    ("dashboard.html", "."),
    ("dashboard.css", "."),
    ("web", "web"),
    ("templates", "templates"),
    ("README.md", "."),
    ("SETUP.md", "."),
    ("GMAIL_SETUP.md", "."),
    ("IMAP_SETUP.md", "."),
]

# google-api-python-client v2 defaults to *static* discovery: build("gmail","v1")
# reads discovery_cache/documents/gmail.v1.json instead of fetching it over HTTP.
# Bundle ONLY that one document — collecting the whole cache pulls in ~99 MB of
# every-other-Google-API schema (and those schemas embed "client_secret" as an
# OAuth field name, which needlessly trips the release-checklist secrets grep).
datas += collect_data_files(
    "googleapiclient", includes=["discovery_cache/documents/gmail.v1.json"]
)

# --- Hidden imports (the R5 tarpit) --------------------------------------
# PyInstaller's static analysis misses two dynamic-discovery stacks:
#   1. keyring picks its backend at runtime by scanning keyring.backends.* — the
#      macOS Keychain backend (keyring.backends.macOS) is never statically
#      referenced, so without this the bundle has NO keychain backend and all
#      credential storage silently breaks.
#   2. googleapiclient / google-auth resolve submodules dynamically.
hiddenimports = [
    "keyring.backends.macOS",
    "keyring.backends.macOS.api",
]
hiddenimports += collect_submodules("keyring.backends")
hiddenimports += collect_submodules("googleapiclient")
hiddenimports += collect_submodules("google_auth_oauthlib")
hiddenimports += collect_submodules("google.auth")

a = Analysis(
    ["bcfeed.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Prune the discovery cache. pyinstaller-hooks-contrib's hook-googleapiclient.model
# unconditionally collects the ENTIRE discovery cache (~99 MB, 586 Google-API
# schema documents) even though bcfeed only ever calls build("gmail","v1"). Those
# schemas also embed "client_secret" as an OAuth field name, tripping the
# release-checklist secrets grep. Keep only gmail.v1.json; drop the rest.
_DISCOVERY_DOCS = "discovery_cache/documents/"
a.datas = [
    entry
    for entry in a.datas
    if _DISCOVERY_DOCS not in entry[0].replace("\\", "/")
    or entry[0].replace("\\", "/").endswith("/gmail.v1.json")
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bcfeed",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed .app — no orphaned terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # ad-hoc signing is applied by build_app.sh
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="bcfeed",
)

app = BUNDLE(
    coll,
    name="bcfeed.app",
    icon=None,
    bundle_identifier="me.nieuwdorp.bcfeed",
    version=paths.__version__,
    info_plist={
        "CFBundleName": "bcfeed",
        "CFBundleDisplayName": "bcfeed",
        "CFBundleShortVersionString": paths.__version__,
        "CFBundleVersion": paths.__version__,
        # Background local-server app: no Dock icon / menu bar needed. Keeps the
        # windowed bundle from stealing focus as a foreground GUI app.
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
    },
)
