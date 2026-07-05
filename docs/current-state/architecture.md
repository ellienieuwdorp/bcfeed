# Architecture — current state

Factual description of how bcfeed is built as of `main` @ e363bf4 (the IMAP-provider merge, 2026-07). No recommendations here; those live in `docs/improvements/`. Line numbers were verified against this revision. Finding IDs (ARCH-n, PERF-n, JS-n, PY-n, SEC-n) refer to the verified audit; where a finding's status changed at e363bf4 it is noted inline. The original audit baseline was 598a9dd; screenshots in `docs/current-state/screenshots/` predate the settings-UI redesign.

## System overview

```
Email provider (Gmail OAuth API  or  generic IMAP)
        │  populate (user-triggered, SSE progress)
        ▼
pipeline.py ──► provider_factory ──► GmailProvider / ImapProvider  (EmailProvider interface)
        │                              search + batched fetch → normalized EmailMessage
        ▼
bandcamp_email_parser.py (subject-gated parse of the notification email)
        ▼
JSON stores in the OS data dir (release_cache, scrape_status, no_results_dates, provider_config, …)
   + secrets in the system keychain (Gmail client config, Gmail token, IMAP password)
        │
        ▼
server.py (Flask, threaded werkzeug) ──► dashboard.html/js/css (vanilla JS, single IIFE)
        ▲
        │  /embed-meta (lazy, per-release, on demand)
bandcamp.com (release page fetch → embed URL + description)
```

Single-process, single-user, local-first. There is no database, no background scheduler, no build step, and no test suite (ARCH-10). Persistent state is JSON files written whole-file per mutation, plus secrets held in the OS keychain (macOS Keychain via `keyring`). The Gmail token pickle was replaced by keychain-backed JSON at e363bf4 (SEC-5 resolved; see Data stores).

## Provider abstraction (new at e363bf4)

The single biggest structural change since 598a9dd: the populate path no longer talks to Gmail directly. It goes through a provider interface so the same pipeline can source Bandcamp notification emails from either the Gmail API or an arbitrary IMAP mailbox.

- **`email_provider.py`** defines the contract: an `EmailProvider` ABC (`authenticate` / `search` / `fetch` / `close`, plus context-manager support) and three shared types — the `EmailMessage` dataclass (`html`, `date` as `YYYY-MM-DD`, `subject`), the `SearchQuery` dataclass (`sender`, `subject_contains`, `after_date`, `before_date`), and the `AuthenticationError` / `ProviderError` exceptions used across the layer.
- **`provider_factory.py`** is the composition root: `create_provider()` reads `provider_config.json` (or an explicit config dict), lazily imports and returns a `GmailProvider` or an `ImapProvider`, and `load_provider_config` / `save_provider_config` own the config file. Saving strips the IMAP password out of the JSON and stores it in the keychain (`_store_imap_password_and_strip_from_config`); loading migrates any legacy plaintext password out of the file into the keychain (`_migrate_legacy_imap_password`). `get_current_provider_type()` returns `"gmail"` or `"imap"`.
- **`GmailProvider`** (`gmail_provider.py`) adapts the existing Gmail API code to the interface: it builds a Gmail query string from `SearchQuery`, calls `search_messages` / `get_messages` in `gmail_client`, and normalizes results to `EmailMessage`. Auth failures are re-raised as `AuthenticationError`.
- **`ImapProvider`** (`imap_provider.py`) implements the interface over raw IMAP: it translates `SearchQuery` into IMAP `UID SEARCH` criteria (`FROM`/`SUBJECT` quoted for strict servers, `SINCE`/`BEFORE` in `DD-Mon-YYYY`), reverses results to newest-first, fetches each message via `BODY[]`, and walks the MIME tree to extract the `text/html` part and decode RFC-2047 subjects.

## Module map

| Module | LOC | Responsibility |
|---|---|---|
| `bcfeed.py` | 55 | CLI entrypoint. Parses `--port` / `--no-browser`, calls `start_server_thread`, opens a browser tab, busy-waits on `thread.is_alive()` until Ctrl+C, then `server.shutdown()` (bcfeed.py:32-51). |
| `server.py` | 822 | Flask app and **all** HTTP endpoints; also a markdown-it-py doc renderer (server.py:70-84), the SSE populate stream, a second JSON-persistence implementation (`_load_set`/`_save_set`/`_load_embed_cache`/`_save_embed_cache`, server.py:94-166), IMAP config coercion/validation and folder discovery helpers (server.py:300-395), and server bootstrap (`start_server`, `find_free_port`, `start_server_thread`). Multiple non-HTTP concerns live in the route file (ARCH-8). |
| `pipeline.py` | 183 | Orchestrates a populate run through the provider interface: computes missing date ranges from cache, `create_provider().authenticate()`, per range `provider.search` + `provider.fetch` → `construct_release_list` (via `bandcamp_email_parser`), marks each range scraped, persists once at the end (pipeline.py:90-183). Defines `MaxResultsExceeded`. |
| `email_provider.py` | 128 | Provider contract: `EmailProvider` ABC + `EmailMessage`/`SearchQuery` dataclasses + `AuthenticationError`/`ProviderError`. Pure interface, no I/O. |
| `provider_factory.py` | 175 | `create_provider`, provider-config load/save, keychain password strip/migrate, `get_current_provider_type`. |
| `gmail_provider.py` | 163 | `GmailProvider`: adapts `gmail_client` to `EmailProvider`; builds Gmail query syntax; maps raw messages to `EmailMessage`. |
| `gmail_client.py` | 313 | (Renamed from `gmail.py`.) Google OAuth flow now backed by the keychain (`_load_stored_token`/`_persist_token`, gmail_client.py:102-125) with one-time migration of legacy `token.pickle` and `credentials.json` into the keychain; Gmail search with pagination (244-264), batched download (267-313, still relies on private `batch._responses`; no 429 retry), HTML part extraction (181-213). **Scope is still `https://mail.google.com/`** (full read/send/delete, gmail_client.py:217; SEC-3 present). |
| `imap_provider.py` | 275 | `ImapProvider`: `SearchQuery`→IMAP criteria, per-message `BODY[]` fetch, MIME walk for `text/html`, RFC-2047 subject decode, date parse. |
| `imap_client.py` | 210 | Low-level IMAP: SSL/plain connect + login + `select_folder` (readonly), `list_folders` with a regex `LIST`-response parser + `ImapFolder` metadata, `uid_search`, `uid_fetch_body` (handles both tuple and raw-bytes server responses), `close`. |
| `bandcamp_email_parser.py` | 107 | Pure parse of a Bandcamp notification email → `(img_url, release_url, is_track, artist, title, page_name)`. **Subject-gated**: returns all-`None` unless the subject starts with "new release from" (bandcamp_email_parser.py:36) and a Bandcamp `/album/` or `/track/` URL is present (extracted from `gmail.py`'s old scraping heuristics; PY-5 tightened). |
| `bandcamp.py` | 66 | Unchanged. Pure parsing of a fetched Bandcamp page: `bc-page-properties` meta (11-20, JSON with `ast.literal_eval` fallback), about/credits description, embed-URL builder. Network fetch happens in server.py, not here. |
| `credential_store.py` | 134 | Secure secret storage via `keyring` under service `"bcfeed"`: `imap-password`, `gmail-client-config`, `gmail-token`. `ensure_available`/`is_available` probe the backend; typed `CredentialStoreError` / `CredentialStoreUnavailableError`. |
| `session_store.py` | 273 | Unchanged. JSON-file persistence for release cache / scrape status / no-results dates; date-range math (`collapse_date_ranges`, `cached_releases_for_range`). |
| `util.py` | 95 | Unchanged. `parse_date`, `construct_release` dict factory (includes a `release_id` field), `dedupe_by_url`, `dedupe_by_date`. Pure leaf. |
| `paths.py` | 36 | Data-dir resolution wrapped in `get_data_dir()` (macOS `~/Library/Application Support/bcfeed`, else `~/.bcfeed`), still created **at import time** via the module-level `DATA_DIR = get_data_dir()` (paths.py:19); all path constants. Static/doc assets resolve next to the source file via `Path(__file__).resolve().with_name(...)` (paths.py:30-36). New: `IMAP_SETUP_PATH`. |
| `templates/docs.html` | 118 | Jinja shell for rendered markdown docs; injects `{{ body|safe }}`. |
| `dashboard.html` | 306 | Static page: two-pane layout, table skeleton, and a redesigned slide-in **Settings panel** (Appearance / Email Configuration / Data & Storage sections, with Gmail and IMAP provider sub-panels), plus server-down / max-results / missing-token modals. Version still hardcoded "bcfeed v1.0" (dashboard.html:73). |
| `dashboard.js` | 2,113 | The entire frontend: one async IIFE, grown ~400 lines for provider settings, IMAP folder discovery, and the redesigned panel. |
| `dashboard.css` | 1,058 | All styling; grew ~220 lines for the settings redesign and IMAP form controls. |

Import graph (acyclic, no cycles):

```
bcfeed.py → server.py
server.py → bandcamp, credential_store, util, paths, session_store, pipeline,
            gmail_client, provider_factory, email_provider, imap_client
pipeline.py → bandcamp_email_parser, provider_factory, email_provider, util, session_store
provider_factory.py → credential_store, email_provider, paths  (+ lazy gmail_provider, imap_provider)
gmail_provider.py → email_provider, gmail_client
gmail_client.py → credential_store, paths  (+ googleapiclient, google_auth_oauthlib)
imap_provider.py → email_provider, imap_client  (+ stdlib email)
imap_client.py → email_provider  (+ imaplib, ssl)
bandcamp_email_parser.py → bs4, furl
credential_store.py → keyring
session_store.py → paths, util
bandcamp.py → bs4          util.py / paths.py → leaves
```

Two parallel atomic-JSON-write implementations still exist (server.py:104-143 vs session_store.py; ARCH-8); `provider_factory` adds a third small one for `provider_config.json` (provider_factory.py:58-66).

Startup: importing `paths` creates the data dir → `bcfeed.main` → `start_server_thread` → `find_free_port` (server.py:192-199, still TOCTOU: probe socket closed before bind) → `start_server` → `make_server("0.0.0.0", port, app, threaded=True, request_handler=QuietHealthHandler)` (server.py:186, **LAN-exposed; SEC-1 present**) on a daemon thread → browser opened to `/dashboard` (bcfeed.py:22) → main thread polls `is_alive()` every 0.5 s (bcfeed.py:45-46). A second `0.0.0.0` bind lives in the `__main__` block's `app.run` (server.py:822).

## Request and data flows

### Populate (SSE pipeline)

The only write path from an email provider into the release cache. User clicks "Populate release list" → frontend opens `EventSource` on `GET /populate-range-stream?start&end` (dashboard.js:1446).

1. **Pre-stream validation** (server.py:625-663): date parsing, then **provider-aware** credential checks — for Gmail, `gmail_credentials_configured()` + `gmail_token_available()`; for IMAP, `_has_credentials_for_provider()` (host/username/password/folder all present). `POPULATE_LOCK` acquired non-blocking. Failures are emitted as SSE `event: error` prose. `max_results` is `int()`-coerced (default `GMAIL_MAX_RESULTS_HARD` = 2000) with no clamp; a non-numeric value still 500s (server.py:631).
2. **Worker thread** (server.py:665-704): daemon thread runs `populate_release_cache(start, end, max_results, batch_size=20, log=q.put)`; the request generator drains a `SimpleQueue` and emits each log line as an SSE `data:` line (newlines flattened to spaces, server.py:700).
3. **Pipeline** (pipeline.py:90-183): `cached_releases_for_range` computes missing ranges → `create_provider().authenticate()` → per range: build `SearchQuery` → `provider.search` (Gmail: full pagination then `[:max_results]`; IMAP: `UID SEARCH` then reverse + `[:max_results]`) → `provider.fetch` batched (20/batch) → `construct_release_list` parses via `bandcamp_email_parser` → **`mark_date_range_scraped` immediately** (pipeline.py:162) → after all ranges, **persist once at the very end** (pipeline.py:183), `provider.close()` in `finally`. This mark-before-persist ordering is unchanged and remains the highest-severity data-loss window (ARCH-2/PY-1): any exception between the mark and the final persist permanently marks days scraped with no cached releases.
   - **`MaxResultsExceeded` is now effectively unreachable**: `provider.search` already truncates to `max_results`, so the guard `len(message_ids) > max_results` (pipeline.py:141) can never fire. The exception, its SSE handler (server.py:683-684), and the max-results modal remain wired but dead in the provider path.
4. **Termination** (server.py:681-690): the worker now catches `GmailAuthError`, `MaxResultsExceeded`, `AuthenticationError`/`ProviderError`, **and** generic `Exception` — all but the max-results case emit an `ERROR:` line. This narrows ARCH-3: an unexpected exception now produces a visible `ERROR:` line before `event: done`, rather than an ambiguous silent success.
5. **Client side** (dashboard.js): log lines appended verbatim to the status log; UI meaning is still derived by **substring sniffing** English prose ("Maximum results", lines starting `ERROR:` — ARCH-4). `event: done` → full `window.location.reload()`.

Lock lifecycle quirk unchanged: `POPULATE_LOCK` is released in the SSE **generator's** `finally` (server.py:704), so a client disconnect frees the lock while the worker thread keeps running — two populate workers can then rewrite the same JSON files concurrently (ARCH-1, JS-10).

### Embed enrichment (lazy Bandcamp scraping)

Unchanged from 598a9dd. Populate never touches bandcamp.com. Enrichment is on-demand, per release, always initiated by the frontend, via `GET /embed-meta?url=...` → server fetches the client-supplied URL with `requests.get` (server.py:487-491) — **no scheme/host allowlist (SSRF, SEC-2 present)** — parses `bc-page-properties` + description (bandcamp.py), writes the result into `embed_cache.json` (server.py:507), returns `{release_id, is_track, embed_url, description}`. The endpoint still **writes but never reads** the embed cache (PERF-2/PY-10); the cache is consulted only by the `/releases` overlay (server.py:236-250). No in-flight dedupe on the client (JS-6).

### Read path

Unchanged. Page load: `config.json` → `/releases` (full flattened cache + full embed-cache overlay per request, no ETag, server.py:230-253) → `/viewed-state` + `/starred-state` → render → `/scrape-status` (async). Payload/timing figures from the audit still hold (PERF-5).

## Data stores

All JSON files in `DATA_DIR` (paths.py); secrets in the OS keychain.

| Store | Backing | Written by | Read by | Lifecycle notes |
|---|---|---|---|---|
| `release_cache.json` | JSON file | `persist_release_metadata` — full load, per-day `dedupe_by_url` merge, atomic rewrite | `get_full_release_cache` (per `/releases`), `cached_releases_for_range` | Corrupted file silently treated as empty (PY-12). Deleted by `/reset-caches` `clear_cache`. |
| `embed_cache.json` | JSON file | `_save_embed_metadata` per `/embed-meta` call — load, merge one key, rewrite whole file (server.py:139-166) | `/releases` overlay only (server.py:236-250) | Unbounded growth; any URL can be inserted. **Now also cleared by `clear_cache`** (server.py:556), alongside release/empty/scrape files. |
| `viewed_state.json`, `starred_state.json` | JSON array of release URLs | load-modify-save per POST (server.py:216-226, 264-274, via `_save_set`) | GET endpoints; frontend Sets | `/reset-caches` deletes **both** when either `clear_viewed` or `clear_starred` is set (server.py:559-563; PY-13 present). |
| `scrape_status.json` | JSON array of ISO dates | `mark_dates_scraped` / `mark_date_range_scraped`; today always dropped on save | `scrape_status_for_range`, `cached_releases_for_range` | The "never re-fetch" ledger; mark-before-persist ordering (ARCH-2) can poison it. |
| `no_results_dates.json` | JSON array of ISO dates | `persist_empty_date_range` | treated as scraped in `cached_releases_for_range` | A date is removed if a later persist stores releases for it. |
| `provider_config.json` | JSON file (**new**) | `save_provider_config` (atomic `.tmp`→replace, provider_factory.py:58-66) | `load_provider_config`, `create_provider`, `get_current_provider_type`, several server routes | `{"provider": "gmail"\|"imap", "imap_config": {host, port, username, folder, use_ssl}}`. The IMAP **password is never stored here** — it is stripped to the keychain on save and migrated out of any legacy plaintext on load. Defaults to `{"provider": "gmail"}` when absent/corrupt. |
| Keychain: `bcfeed / gmail-client-config` | OS keychain (**new**) | `/load-credentials` and one-time migration of `credentials.json` (gmail_client.py:163-177) | `_load_client_config` for the OAuth flow | Replaces on-disk `credentials.json` as the source of truth. |
| Keychain: `bcfeed / gmail-token` | OS keychain (**new**) | `_persist_token` after auth/refresh (gmail_client.py:116-125) | `_load_stored_token` | **Replaces `token.pickle`** as unencrypted JSON in the keychain (SEC-5 resolved for the token-at-rest concern; the full-mailbox scope it holds is still SEC-3). |
| Keychain: `bcfeed / imap-password` | OS keychain (**new**) | `save_imap_password` on config save/migration | `get_imap_password`, `has_imap_password` | The IMAP secret. |
| `token.pickle` (legacy) | JSON→pickle file | no longer written | `_load_legacy_token` reads a pre-existing pickle **once**, re-persists it to the keychain, then unlinks it (gmail_client.py:128-144) | Migration path only. Note: `pickle.load` on a local file remains in the code as a residual deserialization path. |
| `credentials.json` (legacy) | JSON file | `/load-credentials` no longer writes it | `_find_credentials_file` (data dir → `_MEIPASS` → CWD) → migrated into the keychain then unlinked | Legacy/bundled-secret fallback; the `_MEIPASS` branch is still a PyInstaller remnant (ARCH-9). |

### Release dict

Built by `construct_release` (util.py). `url` is the primary key everywhere. `img_url` is declared but never assigned by the email parser — dead end-to-end. `release_id` is a declared field but is set only via the embed-cache overlay at read time, not at parse time. There is still **no `source` field** — the release model does not record whether a row came from Gmail or IMAP (ARCH-5; the provider abstraction was added upstream of the release dict, not into it). `artist`/`title`/`page_name` come from copy-dependent regex/DOM heuristics against Bandcamp's email template (`bandcamp_email_parser.py:59-106`) and degrade to `None` on template changes. The null-URL junk-row path (PY-5) is tightened at e363bf4: the parser now requires both a "new release from" subject and a Bandcamp release URL, and `construct_release_list` skips any email with no HTML body or no release URL (pipeline.py:48-64).

## Concurrency model

Substantially unchanged.

- **Server**: werkzeug `make_server(..., threaded=True)` on a single daemon thread; main thread is a 0.5 s sleep loop.
- **The only lock** is `POPULATE_LOCK` (server.py:59), released on SSE generator teardown, not worker completion (see above).
- **Write atomicity**: JSON stores use write-tmp-then-`Path.replace`; concurrent writers to the same store still share one tmp path, papered over by a non-atomic fallback (server.py:108-111). `provider_config.json` uses the same pattern.
- **No locking on any store** (ARCH-1/PY-8/PERF-6): every mutation is unsynchronized load-modify-save; the frontend generates racing writers (fire-and-forget viewed/starred POSTs, concurrent `/embed-meta` writes). Keychain writes go through `keyring` and are not part of this hazard.
- **IMAP connections** are opened per request/run and closed in `finally`/`close()`; there is no connection pooling or reuse.

## Frontend architecture

- **One async IIFE**, no framework, no modules, no build step (dashboard.js). Grew to 2,113 lines at e363bf4, almost entirely for the settings redesign: a provider `<select>` that swaps Gmail vs IMAP sub-panels, the IMAP connect/discover/save flow (`/imap/discover`, `/provider-config`), and folder selection.
- **State / rendering model** unchanged from the audit: one mutable `state` object, module-scoped `releases`/`releaseMap`, **full re-render** of table/filters/calendar with `innerHTML` string interpolation of unescaped scraped data (JS-1/SEC-7), full-array calendar scans per toggle (PERF-1). Server sync is still fire-and-forget; refresh-after-populate is `window.location.reload()`.
- **Config-driven first run**: `/config.json` now returns `has_token`, `has_credentials` (provider-aware, `_has_credentials_for_provider`), `default_theme` ("light"), `clear_status_on_load` (false), and `show_dev_settings` (false). `has_credentials` falsy funnels the user into the redesigned Settings panel.
- **Settings panel** (dashboard.html:116-266): three sections — Appearance (dark-mode + cached-badge toggles), Email Configuration (provider select → Gmail panel with load/clear credentials + revoke link, or IMAP panel with host/user/port/security/password, "Connect & load folders", a folder `<select>` with a manual-entry fallback, and "Save IMAP Configuration"), and Data & Storage (clear cache & reset). The IMAP folder list is populated from `/imap/discover`, which ranks and recommends a folder server-side.
- **localStorage keys** unchanged (`bc_dashboard_theme`, `bc_show_cached_badges`, `bc_calendar_state_v1`).

## API surface

All JSON endpoints pass through `_corsify` → `Access-Control-Allow-Origin: *` (server.py:87-91; **SEC-4 present**). No auth, no Origin/Host validation, bound to `0.0.0.0` (SEC-1). Twenty route handlers (up from 17 at 598a9dd; new: `/setup-imap`, `/imap/discover`, `/provider-config`).

| Route | Methods | Lines | Purpose / notes |
|---|---|---|---|
| `/health` | GET, OPTIONS | 169-181 | `{"ok": true}`; log-suppressed via `QuietHealthHandler`. Polled every 5 s. |
| `/viewed-state` | GET, POST, OPTIONS | 208-227 | Set membership for `viewed_state.json`; POST `{url, read}`. Unlocked load-modify-save. |
| `/releases` | GET, OPTIONS | 230-253 | Full flattened cache + embed overlay. Exceptions → 500 with raw text. |
| `/starred-state` | GET, POST, OPTIONS | 256-275 | Mirror of `/viewed-state` for stars. |
| `/config.json` | GET | 398-411 | Runtime config; `embed_proxy_url` reflects the client's Host header; `has_token`/`has_credentials` drive the first-run funnel. |
| `/dashboard`, `/dashboard.css`, `/dashboard.js` | GET | 414-432 | `send_file` of the static assets; missing file → **500** leaking the absolute path. |
| `/setup`, `/setup-gmail`, `/setup-imap`, `/readme` | GET | 455-476 | Markdown docs rendered by **markdown-it-py** (`gfm-like`, HTML enabled) into `templates/docs.html`; a `link_open` rule rewrites relative doc links via `DOC_LINK_MAP` and adds `target=_blank rel=noopener` (server.py:70-84). `/setup-imap` is new. |
| `/embed-meta?url=` | GET, OPTIONS | 479-512 | Server-side fetch of the client-supplied URL, **zero validation (SEC-2)**; writes embed cache but never reads it; fetch failure → 502, missing meta → 404. |
| `/scrape-status?start&end` | GET, OPTIONS | 515-531 | Scraped vs not-scraped ISO dates; defaults to last 60 days; today always not-scraped. |
| `/reset-caches` | POST, OPTIONS | 534-565 | Flags `clear_cache`/`clear_viewed`/`clear_starred`. `clear_cache` now also deletes `embed_cache.json`; either of the latter two deletes **both** state files (PY-13). Always 200. |
| `/clear-credentials` | POST, OPTIONS | 568-584 | Now clears the Gmail **keychain** token + client config and unlinks any legacy `token.pickle`/`credentials.json` (`clear_gmail_credentials`), not just a pickle file. |
| `/load-credentials` | POST multipart | 587-622 | Saves the uploaded client secret to the **keychain** (`save_gmail_client_config_json`), then **synchronously runs the interactive OAuth browser flow inside the request thread** with no timeout (server.py:609 → gmail_client.py:216-236; SEC-6, UX-2 still present). |
| `/populate-range-stream?start&end&max_results` | GET (SSE) | 625-710 | The provider-aware populate pipeline described above. Non-numeric `max_results` → unhandled 500; not clamped to `GMAIL_MAX_RESULTS_HARD`. |
| `/imap/discover` | POST, OPTIONS | 717-752 | **New.** Opens an IMAP connection from the posted (or saved) config, lists mailboxes, ranks them (`_imap_folder_rank`: `\All`/inbox/archive preferred, junk/trash/sent demoted, non-selectable last), probes the top candidates for Bandcamp mail, and returns `{folders, recommended_folder}`. Errors → 400/500 with the exception text. |
| `/provider-config` | GET, POST, OPTIONS | 755-817 | **New.** GET returns the sanitized config (password replaced by a `has_password` boolean) plus `has_gmail_credentials`. POST updates `provider` and/or `imap_config`; an IMAP save is validated by actually connecting and selecting the chosen folder before persisting. Errors → 400/500. |

All OPTIONS preflights return 204. Error responses across the API still return raw exception strings and absolute filesystem paths.

## Packaging and distribution

Split-brained (ARCH-9), with two changes at e363bf4:

- **Markdown**: the hand-rolled renderer is gone. Docs now render through **markdown-it-py** (`markdown-it-py` + `linkify-it-py` added to `requirements.txt`), configured `gfm-like` with HTML passthrough. (Fixes the markdown finding from the original audit.)
- **New dependency**: `keyring` (for the credential store). `requirements.txt` no longer duplicates `requests`; it now lists `google-api-python-client`, `google-auth-oauthlib`, `requests`, `bs4`, `flask`, `furl`, `keyring`, `markdown-it-py`, `linkify-it-py` (still unpinned; still uses the `bs4` shim).
- **Versioning / Python-version / Homebrew-formula drift** and the **`sys._MEIPASS` PyInstaller remnant** (now in gmail_client.py:52-54) are unchanged from the audit. Secure-storage now additionally requires a working system keychain backend at runtime (present by default on macOS).
- **No tests, no CI** (ARCH-10). The pure, immediately testable seams have grown: `util`, `bandcamp`, `bandcamp_email_parser`, `collapse_date_ranges`, plus the provider adapters and `imap_client`'s `LIST`-response parser.

## Measured performance characteristics

The audit's 5,000-release benchmarks (PERF-1 through PERF-7) were not re-measured at e363bf4 and still describe the trajectory; none of the changed code alters the hot paths (calendar unseen-scan, `/embed-meta` rewrite, `/releases` payload, full-re-render). IMAP fetch is per-message rather than batched (imap_provider.py:156-169), so IMAP populate is slower than Gmail's batched download for the same message count — consistent with README.md:50's own note that IMAP "can take a bit longer."
