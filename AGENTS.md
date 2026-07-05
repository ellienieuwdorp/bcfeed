# AGENTS.md — bcfeed

Guidance for AI coding agents working in this repository. Human-facing docs live in `README.md`, `SETUP.md`, `GMAIL_SETUP.md`, `IMAP_SETUP.md`, and `./docs/`.

## What this project is

**bcfeed** is a local-first macOS tool that turns Bandcamp "New release from…" email notifications into a browsable release dashboard. A Flask server (`server.py`) runs on `localhost:5050` and serves a static single-page app (`dashboard.html` + `dashboard.js` + `dashboard.css`). The user selects a date range, the app searches their mailbox for Bandcamp notification emails, parses release metadata out of the email HTML, and renders a sortable/filterable table with on-demand Bandcamp player embeds. Email is fetched through a pluggable provider layer: either the **Gmail API** (OAuth) or a generic **IMAP** account. The active provider is chosen in the settings panel and persisted to `provider_config.json`.

Core principles (respect these in every change):
- **Local-first, privacy-first**: no telemetry, no remote services beyond the chosen email provider (Gmail API or IMAP) and Bandcamp scraping. All state is local files; secrets live in the system keychain.
- **Single user, personal tool**: no accounts, no multi-user concerns.
- **Cache aggressively, never re-fetch**: each scraped day is remembered permanently; today is never marked scraped (it isn't final yet).
- **Practical over polished**: no framework, no build step. Keep it simple; the codebase has repeatedly gotten *smaller* on purpose.
- **Be polite to upstream**: batch Gmail requests, cap results (`GMAIL_MAX_RESULTS_HARD = 2000`), lazy Bandcamp scraping with timeouts.

## Layout

| Path | Role |
|---|---|
| `bcfeed.py` | CLI entrypoint: starts server thread, opens browser, waits |
| `server.py` | Flask app: all HTTP endpoints, SSE populate stream, IMAP folder discovery, docs rendering (markdown-it-py) |
| `pipeline.py` | Cache-aware populate orchestration (email provider → releases); provider-agnostic |
| `email_provider.py` | Abstract `EmailProvider` interface + `EmailMessage`/`SearchQuery` dataclasses + provider exceptions |
| `provider_factory.py` | Reads/writes `provider_config.json`, migrates legacy plaintext IMAP passwords to keychain, constructs the active provider |
| `gmail_client.py` | Gmail OAuth flow, search/batch-fetch, email HTML decode (formerly `gmail.py`) |
| `gmail_provider.py` | `EmailProvider` adapter wrapping `gmail_client` |
| `imap_client.py` | Low-level IMAP: connect/auth, folder listing, search, fetch (`ImapClient`, `ImapConfig`, `ImapFolder`) |
| `imap_provider.py` | `EmailProvider` adapter wrapping `imap_client` |
| `bandcamp_email_parser.py` | Parses release metadata out of a Bandcamp notification email's HTML |
| `credential_store.py` | Keychain-backed secret storage via `keyring` (IMAP password, Gmail client config + token) |
| `bandcamp.py` | Bandcamp page scraping (`bc-page-properties` meta, description, embed URL) |
| `session_store.py` | JSON persistence keyed by date (release cache, scrape status, empty dates) |
| `util.py` | Date parsing, release dict construction, dedupe helpers |
| `paths.py` | Every file path; data dir resolution |
| `dashboard.html/js/css` | The entire frontend (vanilla JS, one async IIFE, no framework) |
| `templates/docs.html` | Jinja template wrapping in-app rendered markdown docs |
| `docs/` | Audit, current-state and improvement-plan documentation |

## Running for development

```bash
# one-time setup (Python ≥3.10; Homebrew python works)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# run (opens browser; use --no-browser to skip)
.venv/bin/python bcfeed.py --no-browser
# dashboard: http://localhost:5050/dashboard
```

- The server picks the next free port if 5050 is taken (`find_free_port`).
- Frontend files are served from disk per request — edit `dashboard.{html,js,css}` and just reload the browser. Backend changes need a server restart.
- Gmail features require user-supplied OAuth credentials (see `GMAIL_SETUP.md`); IMAP requires host/username/password/folder (see `IMAP_SETUP.md`). Without a configured provider the app runs but populate fails; everything driven by cached JSON stores still works.
- Secrets are stored in the system keychain via `keyring`, so a usable keychain backend must be available for credential save/load. `credential_store.is_available()` / `ensure_available()` probe it.

## Data stores (all JSON, in `~/Library/Application Support/bcfeed/`)

| File | Shape | Meaning |
|---|---|---|
| `release_cache.json` | `{ "YYYY-MM-DD": [release, …] }` | Release metadata parsed from provider-fetched emails, per email date |
| `embed_cache.json` | `{ url: {release_id, is_track, embed_url, description} }` | Bandcamp-scraped enrichment |
| `viewed_state.json` / `starred_state.json` | `[url, …]` | Seen/starred sets |
| `scrape_status.json` | `["YYYY-MM-DD", …]` | Days already fetched from the email provider |
| `no_results_dates.json` | `["YYYY-MM-DD", …]` | Days known to have zero results |
| `provider_config.json` | `{ "provider": "gmail"|"imap", "imap_config": {host, port, username, folder, use_ssl} }` | Active email provider + non-secret IMAP settings (password is NOT stored here — it lives in the keychain) |

**Secrets are no longer stored on disk.** They live in the system keychain under service `bcfeed` (see `credential_store.py`): `imap-password`, `gmail-client-config` (the former `credentials.json`), and `gmail-token` (the former `token.pickle`). `paths.py` still defines `CREDENTIALS_PATH` (`credentials.json`) and `TOKEN_PATH` (`token.pickle`) purely as **legacy migration/import sources**: on first use `gmail_client` reads any old `credentials.json`/`token.pickle`, copies it into the keychain, then deletes the on-disk file. Do not write secrets to these paths in new code.

Release dict fields: `img_url` (always null currently), `date`, `artist`, `title`, `page_name`, `url`, `release_id`, `is_track`. Enrichment fields (`embed_url`, `description`) are merged in from `embed_cache.json` by `/releases` at read time. Identity key is `url`.

**Mock data for UI work**: write these JSON files directly (server reads them per request — no restart needed). Real Bandcamp URLs passed through `GET /embed-meta?url=…` populate `embed_cache.json` with working player embeds.

## API surface (all CORS `*`, no auth)

- `GET /dashboard`, `/dashboard.css`, `/dashboard.js` — static app
- `GET /config.json` — `{title, embed_proxy_url, has_token, has_credentials, default_theme, clear_status_on_load, show_dev_settings}` (`has_credentials` reflects the active provider: Gmail creds present, or complete IMAP config)
- `GET /releases` — entire flattened release cache + embed enrichment
- `GET/POST /viewed-state`, `/starred-state` — `{url, read|starred}` toggles
- `GET /scrape-status?start&end` — per-day scraped flags
- `GET /populate-range-stream?start&end` — **SSE**: plain-text log lines, `event: error`, `event: done`; pulls from whichever provider is active
- `GET /embed-meta?url=` — scrapes a Bandcamp page, caches + returns embed metadata
- `POST /reset-caches`, `/clear-credentials`, `/load-credentials` (multipart file, Gmail client config → keychain)
- `GET/POST /provider-config` — read (password-redacted, exposes `has_password`/`has_gmail_credentials`) or save the active provider + IMAP settings; POST validates by opening an IMAP connection before persisting
- `POST /imap/discover` — authenticate against IMAP and return `{folders, recommended_folder}` for the folder picker
- `GET /readme`, `/setup`, `/setup-gmail`, `/setup-imap` — markdown docs rendered via **markdown-it-py** (`gfm-like`, in-app link rewriting) in `server.py`
- `GET /health`

## Conventions & gotchas

- **No tests, no CI, no linter config exist.** If you add behavior worth protecting, prefer small pytest files colocated in a new `tests/`; don't introduce heavyweight tooling without being asked.
- Python: stdlib-flavored, `from __future__ import annotations`, snake_case, docstrings on modules/functions. Keep dependencies to the existing nine in `requirements.txt`: `google-api-python-client`, `google-auth-oauthlib`, `requests`, `bs4`, `flask`, `furl`, `keyring` (keychain secrets), `markdown-it-py` + `linkify-it-py` (docs rendering). Discuss before adding more.
- Frontend: everything lives inside the single IIFE in `dashboard.js`; state is a mutable `state` object + module-scoped `releases` array; rendering is full re-render (`renderTable()`, `renderFilters()`, `renderCalendar("range")`). Match that style for small fixes; discuss before introducing modules/build steps.
- The UI persists to localStorage: `bc_dashboard_theme`, `bc_calendar_state_v1`, cached-badge key. The server owns viewed/starred state.
- "Today is never scraped": `exclude_today` logic is pervasive (`session_store.py`) and the calendar blocks selecting today. Don't break this invariant.
- `.gitignore` blocks `*.json` — deliberate, so stray OAuth credentials can't be committed. `requirements.txt` etc. are tracked exceptions already; adding a new tracked `.json` file requires a `!` rule.
- Known mismatch: docs instruct the `gmail.readonly` scope but `gmail_client.py:217` still requests full `https://mail.google.com/` — flagged in `docs/current-state/known-issues.md` (SEC-3); a fix must invalidate existing tokens carefully.
- Bind address is `0.0.0.0` (`server.py:186` `start_server`, and the `__main__` `app.run` at `server.py:822`) and CORS is `*` — known hardening item (SEC-1), see `docs/current-state/known-issues.md`.
- The hand-rolled markdown renderer flagged in the original audit has been **replaced** by `markdown-it-py` (`server.py` `_build_doc_markdown_renderer`); that finding no longer applies.
- The dashboard was only ever tested on Chrome/macOS.

## Verifying UI changes

Use Playwright (Chromium) against the running server; seed mock data as above, pre-set `localStorage.bc_calendar_state_v1` to select a data-bearing range, and screenshot. `docs/audit/process.md` documents the harness used for the 2026-07 audit.
