#!/usr/bin/env bash
#
# Reproducible build of the double-clickable macOS bcfeed.app (WP-29 · ARC-6).
#
# Produces an ad-hoc-signed PyInstaller onedir bundle at dist/bcfeed.app. The
# Homebrew tap remains the primary distribution channel; this .app is a GitHub
# release asset. Run from the repo root:
#
#     packaging/build_app.sh
#
# PyInstaller is a BUILD tool, not a runtime dependency: it is intentionally NOT
# in pyproject.toml. Install it into the project venv (its deps must be
# importable for the analysis pass):
#
#     .venv/bin/pip install pyinstaller
#
# Ad-hoc signing (`-s -`) is unsigned as far as Apple is concerned: users will
# hit Gatekeeper and must right-click -> Open the first time (see packaging/README.md).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYINSTALLER="${PYINSTALLER:-.venv/bin/pyinstaller}"
CODESIGN="${CODESIGN:-codesign}"
APP="dist/bcfeed.app"

if [ ! -x "$PYINSTALLER" ]; then
    echo "error: $PYINSTALLER not found. Install it into the project venv:" >&2
    echo "    .venv/bin/pip install pyinstaller" >&2
    exit 1
fi

echo ">> Cleaning previous build/ and dist/ ..."
rm -rf build dist

echo ">> Running PyInstaller (onedir .app) ..."
"$PYINSTALLER" --noconfirm --clean bcfeed.spec

if [ ! -d "$APP" ]; then
    echo "error: expected $APP was not produced by PyInstaller." >&2
    exit 1
fi

echo ">> Ad-hoc code-signing the bundle ..."
# --force replaces any nested signatures PyInstaller left; --deep signs every
# embedded dylib/framework; -s - is the ad-hoc identity (no Developer ID).
"$CODESIGN" --force --deep -s - "$APP"

echo ">> Verifying signature ..."
"$CODESIGN" -dv "$APP" 2>&1 | sed 's/^/   /'

echo
echo ">> Build complete: $REPO_ROOT/$APP"
echo "   Launch with:  open '$REPO_ROOT/$APP'"
echo "   First launch is Gatekeeper-blocked (ad-hoc signed) — right-click -> Open."
echo "   See packaging/README.md for the Gatekeeper / distribution notes."
