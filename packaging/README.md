# Packaging — macOS `.app` bundle

The double-clickable `bcfeed.app` is built from `bcfeed.spec` (repo root) with
PyInstaller. It is a **secondary** distribution channel: the Homebrew tap
(`brew install keinobjekt/bcfeed/bcfeed`) stays the primary, first-class path and
nothing here blocks a CLI release. The `.app` is attached as a GitHub release
asset (WP-29 · ARC-6). See `docs/release-checklist.md` §4–5 for where this slots
into the release runbook.

## Build

```bash
.venv/bin/pip install pyinstaller     # build-only tool; NOT a runtime dependency
packaging/build_app.sh                # clean -> pyinstaller -> ad-hoc codesign
open dist/bcfeed.app                   # local smoke test
```

`build_app.sh` cleans `build/` and `dist/`, runs `pyinstaller bcfeed.spec`
(onedir — **not** onefile, per ARC-6), then ad-hoc-signs the bundle
(`codesign --force --deep -s -`) and prints the result path. `dist/` and
`build/` are gitignored.

PyInstaller is deliberately **not** in `pyproject.toml`: it is a build tool, not
a runtime dependency, and the shipped app already vendors its own Python. Install
it into the project venv so the app's dependencies are importable during the
analysis pass.

### What the spec bundles

Every asset is resolved at runtime by `paths.resource_path()`, which prefers
`sys._MEIPASS` when frozen (WP-28). The spec's `datas` therefore just place each
asset at the bundle root under its normal relative name:

- `dashboard.html`, `dashboard.css`
- `web/` (the ES modules served at `/web/js/*.js`)
- `templates/` (`docs.html`, used by `render_template` for the doc routes)
- `README.md`, `SETUP.md`, `GMAIL_SETUP.md`, `IMAP_SETUP.md`

### Hidden imports (the R5 tarpit)

Two dynamic-discovery stacks are invisible to PyInstaller's static analysis and
are forced in via `hiddenimports`:

- **`keyring.backends.macOS`** — keyring selects its backend at runtime by
  scanning `keyring.backends.*`. Without this the bundle ships **no** Keychain
  backend and *all credential storage silently breaks*. This is the e363bf4
  addition to the R5 watchlist.
- **`googleapiclient` / `google-auth`** submodules — resolved dynamically by the
  Google client stack.

## No secrets ride along

The bundle contains **no** `credentials.json` / `client_secret`. OAuth client
secrets are user-owned and live only in the data dir / system Keychain, never
inside a distributed binary (the deleted `gmail_client` `_MEIPASS` branch was the
one violation of this — see ARC-6). Verify:

```bash
find dist/bcfeed.app -iname '*credential*' -o -iname '*client_secret*'   # expect: no output
grep -rl "client_secret" dist/bcfeed.app 2>/dev/null                     # expect: no output
```

## Data directory

The app writes **only** to the OS data dir, never inside the bundle
(`~/Library/Application Support/bcfeed` on macOS; overridable with
`BCFEED_DATA_DIR`). This is what makes onedir-in-a-`.dmg`/zip safe under app
translocation: the bundle location does not affect where state lives.

## Gatekeeper / distribution (ad-hoc signing)

The bundle is **ad-hoc signed** (`-s -`), which Apple treats as unsigned. On a
machine that downloaded it (i.e. the file carries `com.apple.quarantine`), the
first launch is blocked as "unidentified developer". Users must:

> **Right-click the app → Open → Open** in the dialog (once). Thereafter it
> launches normally.

Call this out in the GitHub release notes. Developer ID + notarization ($99/yr)
is deferred until the audience outgrows people comfortable with right-click-open.

### The only honest Gatekeeper test is maintainer-gated

Gatekeeper and app translocation behave differently for a freshly built local
file versus a real download. The authoritative test — copy to a **fresh macOS
user account** / clean VM's `~/Downloads`, double-click (blocked), right-click →
Open (launches), exercise every doc route, quit and confirm no orphan process —
is **maintainer-run** and documented in `docs/release-checklist.md` §5. It is not
part of the automated build.
