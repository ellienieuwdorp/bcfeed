# Release checklist — v1.1.0

Maintainer runbook for cutting a **bcfeed** release. Every step here is
**maintainer-gated**: nothing in this repo tags, pushes, or publishes on its own.
Run the steps in order, top to bottom, on a clean checkout of `main`.

The version lives in exactly one place — `paths.__version__` (currently
`1.1.0`) — and is surfaced from there to `/config.json`, Settings → About, and
the built package (`pyproject.toml` reads it as its dynamic version). Bump that
constant first if you are cutting a different version; every command below reads
`$VERSION` from it.

```bash
cd /path/to/bcfeed
VERSION="$(python3 -c 'import paths; print(paths.__version__)')"
echo "Releasing v${VERSION}"        # expect: Releasing v1.1.0
```

---

## 1. Pre-flight (must be green before tagging)

```bash
# clean tree on main, up to date with origin
git switch main && git pull --ff-only
git status --porcelain            # expect: no output

# unit suite + wording guard + doc-render goldens
.venv/bin/pytest -q --ignore=tests/e2e

# end-to-end (Playwright) — needs the e2e venv / BCFEED_APP_PYTHON
.venv/bin/pytest -q tests/e2e

# lint + format
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npx prettier --check dashboard.html dashboard.css web/js/

# a clean install runs the app
python3 -m venv /tmp/bcfeed-relcheck
/tmp/bcfeed-relcheck/bin/pip install .
/tmp/bcfeed-relcheck/bin/bcfeed --no-browser --port 5099 &   # Ctrl-C after it serves
```

Confirm the version is consistent everywhere:

```bash
python3 -c 'import paths; print(paths.__version__)'          # 1.1.0
curl -s http://localhost:5050/config.json | python3 -m json.tool | grep -i version
# Settings → About in the running app shows the same string.
grep -rn "1\.1\.0" README.md SETUP.md GMAIL_SETUP.md IMAP_SETUP.md privacy.md || true
# (docs mention no other version number; only paths.py is the source of truth)
```

---

## 2. Tag and push

```bash
git tag -a "v${VERSION}" -m "bcfeed v${VERSION}"
git push origin "v${VERSION}"
```

Then create the GitHub release from the tag (attach the `.app` from step 4 once
built):

```bash
gh release create "v${VERSION}" \
  --title "bcfeed v${VERSION}" \
  --notes-file docs/release-notes-v${VERSION}.md   # or --generate-notes
```

**Release-note must-haves for v1.1.0** (the one user-visible behavior change):

> bcfeed now requests **read-only** access to Gmail instead of full access. If
> you connected Gmail with an older version, you will be asked to **reconnect
> once** (Settings → Email connection → Connect Gmail…). This is a one-time step.

---

## 3. Homebrew tap formula regeneration

The tap is `keinobjekt/homebrew-bcfeed` (`brew tap keinobjekt/bcfeed`). Regenerate
the formula against the pushed tag.

```bash
# in the tap repo checkout
brew update
brew bump-formula-pr --url="https://github.com/keinobjekt/bcfeed/archive/refs/tags/v${VERSION}.tar.gz" \
  keinobjekt/bcfeed/bcfeed
# (or edit Formula/bcfeed.rb by hand: bump `url`, refresh `sha256`)
```

The formula's Python resource stanzas must match the **pinned** runtime
dependencies in `pyproject.toml`. As of v1.1.0 that list is:

| Resource | Pin | New at v1.1.0? |
|---|---|---|
| google-api-python-client | 2.198.0 | no |
| google-auth-oauthlib | 1.4.0 | no |
| requests | 2.34.2 | no |
| beautifulsoup4 | 4.15.0 | no (was the `bs4` shim before) |
| flask | 3.1.3 | no |
| furl | 2.1.4 | no |
| **keyring** | **25.7.0** | **yes — must be added** |
| **markdown-it-py** | **4.2.0** | **yes — must be added** |
| **linkify-it-py** | **2.1.0** | **yes — must be added** |

`keyring`, `markdown-it-py`, and `linkify-it-py` were added by the e363bf4 merge
and are **absent from any pre-1.1.0 formula** — the formula install will fail at
runtime without them (keychain storage and the in-app doc renderer). Include
their own transitive deps (`markdown-it-py` pulls `mdurl`; `linkify-it-py` pulls
`uc-micro-py`; `keyring` pulls `jaraco.*` helpers) — `brew bump-formula-pr`
resolves these, or use `brew update-python-resources Formula/bcfeed.rb`.

Depends-on: `python@3.11` (matches `requires-python = ">=3.11"`).

Verify the regenerated formula from the tap before opening the PR:

```bash
brew install --build-from-source keinobjekt/bcfeed/bcfeed
brew test keinobjekt/bcfeed/bcfeed
bcfeed --no-browser --port 5098      # confirm it serves, then quit
brew audit --strict --online keinobjekt/bcfeed/bcfeed
```

---

## 4. macOS .app bundle (see WP-29)

The double-clickable `.app` is built by WP-29's PyInstaller spec (`bcfeed.spec`)
— it is **not** part of this repo yet and does not block the Homebrew CLI
release. When WP-29 has landed:

```bash
.venv/bin/pip install pyinstaller
pyinstaller bcfeed.spec               # onedir .app per ARC-6
```

WP-29 owns the spec details; the two things it must get right (per the plan and
risk R5):

- `datas` route every bundled asset (`dashboard.html`, `dashboard.css`,
  `web/js/`, the `*.md` docs) through `paths.resource_path()`.
- hidden-imports cover `googleapiclient` **and** `keyring`'s dynamic backend
  discovery (the macOS `keyring.backends.macOS` backend) — a missing keyring
  backend breaks all credential storage in the bundle.

Confirm no secrets ride along — check for an actual bundled credential *file*,
not the substring (the bundled setup docs/UI — `GMAIL_SETUP.md`,
`dashboard.html`, `web/js/modals.js` — legitimately mention `client_secret_….json`
as instructional text, so a plain `grep -rl "client_secret"` reports those three
as expected false positives, never a real secret):

```bash
find dist/bcfeed.app \( -name credentials.json -o -name 'client_secret*.json' \)   # expect: no output
```

---

## 5. Clean-account Gatekeeper test (the only honest .app test)

Gatekeeper and app translocation behave differently for a file that was just
built locally. Test on a **fresh macOS user account** (or a clean VM) that never
saw the build tree:

1. Copy `dist/bcfeed.app` to that account's `~/Downloads` (so it carries the
   `com.apple.quarantine` attribute a real download has).
2. Double-click → expect Gatekeeper to block it ("unidentified developer").
3. Right-click → **Open** → **Open** in the dialog. It should now launch, open
   the dashboard in the browser, and render every doc route
   (`/readme`, `/setup`, `/setup-gmail`, `/setup-imap`).
4. Quit → confirm no orphan `bcfeed`/Python process remains (`pgrep -fl bcfeed`).
5. Confirm the data dir is untouched by the bundle location (it lives under the
   user's app-data path, not inside the `.app`).

Document the right-click-open step in the GitHub release notes — ad-hoc signing
means users hit the same Gatekeeper prompt.

---

## 6. Post-release

- [ ] GitHub release published, `.app` attached (once WP-29 ships).
- [ ] Tap PR merged; `brew install keinobjekt/bcfeed/bcfeed` installs v${VERSION}.
- [ ] Release notes call out the one-time Gmail read-only reconnect.
- [ ] `docs/TODO.md` DONE history updated if you track releases there.
