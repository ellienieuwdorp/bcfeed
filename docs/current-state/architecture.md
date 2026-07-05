# Architecture — current state

Factual description of how bcfeed is built as of `main` @ 598a9dd (2026-07-05). No recommendations here; those live in `docs/improvements/`. Line numbers were verified against this revision. Finding IDs (ARCH-n, PERF-n, JS-n, PY-n, SEC-n) refer to the verified audit in the audit results; screenshots live in `docs/current-state/screenshots/`.

## System overview

```
Gmail (OAuth, "New release from" emails)
        │  populate (user-triggered, SSE progress)
        ▼
pipeline.py ──► gmail.py (search + batch download + email parsing)
        │
        ▼
JSON stores in the OS data dir (release_cache, scrape_status, no_results_dates, …)
        │
        ▼
server.py (Flask, threaded werkzeug) ──► dashboard.html/js/css (vanilla JS, single IIFE)
        ▲
        │  /embed-meta (lazy, per-release, on demand)
bandcamp.com (release page fetch → embed URL + description)
```

Single-process, single-user, local-first. There is no database, no background scheduler, no build step, and no test suite (ARCH-10). All persistent state is JSON files plus one pickle, written whole-file per mutation.

## Module map

| Module | LOC | Responsibility |
|---|---|---|
| `bcfeed.py` | 55 | CLI entrypoint. Parses `--port` / `--no-browser`, starts the server thread, opens a browser tab, busy-waits until Ctrl+C (bcfeed.py:26-51). |
| `server.py` | 626 | Flask app and **all** HTTP endpoints; also contains a hand-rolled markdown renderer (server.py:61-145), the SSE populate stream, a second JSON-persistence implementation (`_load_set`/`_save_set`/`_load_embed_cache`/`_save_embed_cache`, server.py:155-228), embed-cache writes, and server bootstrap (`find_free_port`, `start_server_thread`). Four non-HTTP concerns live in the route file (ARCH-8). |
| `pipeline.py` | 123 | Orchestrates a populate run: computes missing date ranges from cache, queries Gmail per range, parses emails into release dicts, persists once at the end (pipeline.py:59-123). Defines `MaxResultsExceeded`. |
| `gmail.py` | 295 | Google OAuth flow + `token.pickle` persistence (gmail.py:88-125), Gmail search with pagination (128-148), batched message download (151-197), HTML part extraction (53-85), per-email release-info scraping heuristics (203-295). |
| `bandcamp.py` | 66 | Pure parsing of a fetched Bandcamp page: `bc-page-properties` meta (11-20), about/credits description (23-58), embed URL builder (61-66). Network fetch happens in server.py:419-423, not here. |
| `session_store.py` | 273 | JSON-file persistence for release cache / scrape status / no-results dates; date-range math (`collapse_date_ranges`, `cached_releases_for_range`). |
| `util.py` | 95 | `parse_date`, `construct_release` dict factory, `dedupe_by_url`, `dedupe_by_date`. Pure leaf. |
| `paths.py` | 35 | Data-dir resolution (macOS `~/Library/Application Support/bcfeed`, else `~/.bcfeed`), created **at import time** (paths.py:16); all path constants. Static/doc assets resolve next to the source file (paths.py:30-35). |
| `templates/docs.html` | 76 | Jinja shell for rendered markdown docs; injects `{{ body|safe }}` (docs.html:72). Dark-only styling. |
| `dashboard.html` | 186 | Static page: two-pane layout, table skeleton, five modals, heavy inline styles. |
| `dashboard.js` | 1,725 | The entire frontend: one async IIFE (see "Frontend architecture"). |
| `dashboard.css` | 839 | All styling; three visible "generations" of design (see ui-visual audit). |

Import graph (acyclic, no cycles):

```
bcfeed.py → server.py
server.py → bandcamp, util, paths, session_store, pipeline, gmail
pipeline.py → gmail, util, session_store
gmail.py → paths (+ googleapiclient, google_auth_oauthlib, bs4, furl)
session_store.py → paths, util
bandcamp.py → bs4 only          util.py / paths.py → leaves
```

File I/O concentrates in `server.py`, `session_store.py`, `gmail.py` — meaning two parallel atomic-JSON-write implementations exist (server.py:155-228 vs session_store.py:30-83; ARCH-8).

Startup: importing `paths` creates the data dir → `bcfeed.main` → `find_free_port` (server.py:253-260, TOCTOU: probe socket closed before bind) → `make_server("0.0.0.0", port, app, threaded=True)` (server.py:247, LAN-exposed; SEC-1) on a daemon thread → browser opened to `/dashboard` (bcfeed.py:22) → main thread polls `is_alive()` every 0.5 s (bcfeed.py:45-46).

## Request and data flows

### Populate (SSE pipeline)

The only write path from Gmail into the release cache. User clicks "Populate release list" → frontend opens `EventSource` on `GET /populate-range-stream?start&end` (dashboard.js:1443).

1. **Pre-stream validation** (server.py:553-583): date parsing, credentials-file and token existence, `POPULATE_LOCK` acquired non-blocking. Failures are emitted as SSE `event: error` prose. `max_results` is `int()`-coerced with no validation and no clamp to `GMAIL_MAX_RESULTS_HARD` (server.py:559, 46).
2. **Worker thread** (server.py:591-609): daemon thread runs `populate_release_cache(start, end, max_results, batch_size=20, log=queue.put)`; the request generator drains a `SimpleQueue` and emits each log line as an SSE `data:` line (newlines flattened to spaces, server.py:616).
3. **Pipeline** (pipeline.py:59-123): `cached_releases_for_range` computes missing ranges → per range: Gmail `search_messages` (full pagination of *all* result pages before the cap is checked — PERF-7) → `get_messages` batched download (20/batch, relies on private `batch._responses`, gmail.py:162-165; no 429 retry) → **`mark_date_range_scraped` immediately** (pipeline.py:114) → after all ranges, parse emails (`construct_release_list`) and **persist once at the very end** (pipeline.py:123). This mark-before-persist ordering is the highest-severity data-loss window (ARCH-2/PY-1): any exception between those points permanently marks days as scraped with no cached releases, and the never-re-fetch principle makes them unrecoverable without a full cache reset.
4. **Termination**: the worker catches only `GmailAuthError` and `MaxResultsExceeded` (server.py:601-604); any other exception ends the stream with `event: done` — failure indistinguishable from success for exceptions with no preceding `ERROR:` log line (ARCH-3, corrected: search/download failures do log `ERROR:` lines that the client intercepts; parse-stage and persistence failures do not).
5. **Client side** (dashboard.js:1433-1486): log lines appended verbatim to the status log; UI meaning is derived by **substring sniffing** English prose ("Maximum results", lines starting `ERROR:` — ARCH-4). `event: done` → `window.location.reload()` (dashboard.js:1481-1484) — the refresh mechanism is a full page reload, not a refetch.

Lock lifecycle quirk: `POPULATE_LOCK` is released in the SSE **generator's** `finally` (server.py:619-620), so a client disconnect frees the lock while the worker thread keeps running — two populate workers can then rewrite the same JSON files concurrently (ARCH-1, JS-10).

### Embed enrichment (lazy Bandcamp scraping)

Populate never touches bandcamp.com. Enrichment is on-demand, per release, always initiated by the frontend:

- **Triggers**: row hover/focus after a 200 ms debounce (dashboard.js:914-928), row click/expand (838), starring (250 — star-triggers-preload), every render of a starred row (786-788), and the sequential "Preload" loop (1490-1540).
- **Request**: `GET /embed-meta?url=...` → server fetches the client-supplied URL with `requests.get` (server.py:419-423) — no scheme/host allowlist (SSRF, SEC-2) — parses `bc-page-properties` + description (bandcamp.py), writes the result into `embed_cache.json` (server.py:439), returns `{release_id, is_track, embed_url, description}`.
- **Cache asymmetry**: `/embed-meta` **writes** the embed cache but never **reads** it — every call is a live Bandcamp fetch plus a whole-file rewrite of the cache (server.py:411-444; PERF-2, PY-10). The cache is only consulted by the `/releases` overlay (server.py:296-311), so its benefit is limited to page loads.
- **Client caching**: `ensureEmbed` mutates the in-memory release object (dashboard.js:432-444) and short-circuits only when *both* `embed_url` and `description` are set (427-429). Releases whose fetch fails (404/deleted pages, missing `bc-page-properties`) never satisfy the guard and are re-fetched forever (PERF-2, corrected scope). No in-flight dedupe: hover + click + star can issue concurrent identical fetches (JS-6).

### Read path

Page load: `config.json` → `/releases` (full flattened cache + full embed-cache overlay per request, no ETag/caching, server.py:291-314) → `/viewed-state` + `/starred-state` → render → `/scrape-status` (async, not awaited). Measured at 5k fully preloaded releases the `/releases` payload is 7.61 MB vs 1.20 MB un-enriched, ~39 ms server-side (PERF-5); descriptions make up the bulk and are also fetchable per-release.

## Data stores

All in `DATA_DIR` (paths.py:22-28): macOS `~/Library/Application Support/bcfeed`, otherwise `~/.bcfeed`.

| File | Schema | Written by | Read by | Lifecycle notes |
|---|---|---|---|---|
| `release_cache.json` | `{"YYYY-MM-DD": [release, ...]}` | `persist_release_metadata` only — full load, per-day `dedupe_by_url` merge, full atomic rewrite (session_store.py:185-212) | `get_full_release_cache` (flatten+dedupe per `/releases` call, session_store.py:116-126), `cached_releases_for_range` | Corrupted file silently treated as empty and overwritten on next persist (session_store.py:39-50; PY-12). Deleted by `/reset-caches` `clear_cache`. |
| `embed_cache.json` | `{release_url: {release_id, is_track, embed_url, description}}` | `_save_embed_metadata` per `/embed-meta` call — load, merge one key, rewrite whole file with `indent=2` (server.py:208-228) | `/releases` overlay only (server.py:296-311) | Unbounded growth; any URL (not just Bandcamp) can be inserted via `/embed-meta`. 38 ms/rewrite at 5k entries / 7 MB (PERF-3/6). |
| `viewed_state.json`, `starred_state.json` | sorted JSON array of release URLs | load-modify-save per POST (server.py:282-287, 330-335, via `_save_set` 165-173) | GET endpoints; frontend Sets | `/reset-caches` deletes **both** when either `clear_viewed` or `clear_starred` is set (server.py:491-495; PY-13). |
| `scrape_status.json` | sorted array of ISO dates already Gmail-scraped | `mark_dates_scraped` / `mark_date_range_scraped`; today always dropped on save (`drop_today=True`, session_store.py:70-83) | `scrape_status_for_range`, `cached_releases_for_range` | This is the "never re-fetch" ledger; the mark-before-persist ordering (ARCH-2) can poison it. `mark_dates_not_scraped` (session_store.py:159-165) is dead code. |
| `no_results_dates.json` | sorted array of ISO dates whose Gmail query returned zero messages | `persist_empty_date_range` (session_store.py:257-273, also marks range scraped) | treated as scraped in `cached_releases_for_range` (224) | A date is removed if a later persist stores releases for it (206-208). |
| `token.pickle` | pickled Google credentials | `gmail_authenticate` (gmail.py:118-120, **non-atomic** write) | same | The only non-atomic, non-JSON store (SEC-5). Deleted by `/clear-credentials`, `/load-credentials`, and on 401/refresh failure (gmail.py:25-32). |
| `credentials.json` | OAuth client secret | `/load-credentials` upload (atomic, server.py:538-540) | `_find_credentials_file`: data dir → PyInstaller `_MEIPASS` → CWD (gmail.py:34-49) | The `_MEIPASS` branch is a PyInstaller remnant (spec files deleted; ARCH-9). |

### Release dict

Built by `construct_release` (util.py:33-52). `url` is the primary key everywhere (dedupe, embed cache key, viewed/starred sets, `/embed-meta` target). `img_url` is declared but never assigned — dead end-to-end. `release_id`, `embed_url`, `description` exist only via the embed-cache overlay at read time (server.py:296-311), never at parse time. There is **no source field** — the model assumes Gmail/Bandcamp only (relevant to the IMAP item on the TODO; ARCH-5). Fields `artist`/`title`/`page_name` come from copy-dependent regex/DOM heuristics against Bandcamp's email template (gmail.py:249-292) and degrade to `None` on template changes. Emails with no release URL still produce cached null-URL junk rows because the all-None guard is defeated by the separately parsed date (pipeline.py:38; PY-5).

## Concurrency model

- **Server**: werkzeug `make_server(..., threaded=True)` — one thread per request — on a single daemon thread (server.py:247-249); main thread is a 0.5 s sleep loop (bcfeed.py:45-46).
- **The only lock in the system** is `POPULATE_LOCK` (server.py:45), scoping one populate at a time — released on SSE generator teardown, not worker completion (see above).
- **Write atomicity**: all JSON stores use write-tmp-then-`Path.replace` (atomic on POSIX). But concurrent writers to the same store share **the same tmp path** (`path.with_suffix(".tmp")`), so interleaved replaces can raise `FileNotFoundError`, papered over by a non-atomic fallback (server.py:169-173). `token.pickle` is written directly.
- **No locking on any store** (ARCH-1/PY-8/PERF-6): every mutation is unsynchronized load-modify-save. The frontend itself generates the racing writers: "Mark as seen" fires one fire-and-forget POST per visible row (dashboard.js:1122-1142), and renderTable's starred-row `ensureEmbed` loop plus hover prefetch fire concurrent `/embed-meta` calls whose whole-file rewrites drop each other's entries. Last-writer-wins update loss is realistic today, not theoretical.
- **SSE**: `stream_with_context`, no heartbeat; log lines sanitized only by replacing `\n` with a space (server.py:616). Fine for localhost (no proxy in path).

## Frontend architecture

- **One async IIFE**, no framework, no modules, no build step: dashboard.js:6-1724. Sole deliberate global is `window.BC_CONFIG_PROMISE` (js:1-4), a `config.json` fetch started before the IIFE. Endpoint URLs are derived **twice** — once from the hardcoded default `http://localhost:5050/embed-meta` (js:9-21), again after config load (js:48-55); `apiHost` is computed only from the first pass, so the server-down modal's localhost guard is dead code (JS-2).
- **State**: one mutable `state` object literal (js:257-272): `sortKey`, `direction`, `showLabels:Set`, `showOnlyLabels:Set`, `viewed:Set`, `starred:Set`, `showOnlyStarred`, `hideViewed`, `hideViewedSnapshot:Set`, `expandedKey`, `dateFilterFrom/To`, `filterByDate`, `showCachedBadges`. Plus module-scoped `releases` array and `releaseMap` keyed by `releaseKey` (url, or a `page_name|artist|title|date` composite, js:78-80), `scrapeStatus` Sets, a single-entry `calendars` map (leftover generality, js:1158-1160), and latch flags `serverDownShown`/`maxNoticeShown`.
- **Rendering model: full re-render.** `renderTable` (js:721-934) wipes `tbody.innerHTML` and rebuilds every row with ~8 fresh listeners each; `renderFilters` (js:457-531) and `renderCalendar` (js:1209-1324, fixed 42 cells) do the same for their regions. No diffing. Rows are built by **innerHTML string interpolation of unescaped scraped data** (js:756-769; JS-1/SEC-7). `setViewed` triggers a full calendar re-render per toggle (js:216), which scans the whole releases array per cell (js:1254-1259) — the multiplier behind PERF-1 (measured 9.8 ms/scan at 5k releases; ~3-5 s freeze for a bulk mark-seen of 300-500 rows).
- **Server sync**: viewed/starred POSTs are fire-and-forget with no queueing or ordering guarantee (js:183-206). `ensureEmbed` caches by mutating fetched release objects in memory; wiped on reload. Refresh-after-populate is `window.location.reload()`.
- **localStorage keys** (the only client persistence):
  - `bc_dashboard_theme` (js:274; written on **every** `applyTheme` call, so first load locks in a value as if user-chosen)
  - `bc_show_cached_badges` (js:275)
  - `bc_calendar_state_v1` — `{from, to}` selected range (js:991, 1593-1616); the sole survivor of the reload-on-populate.
- **Shared-state ownership hazards** (documented because they shape any refactor): the Populate button and status log each have two independent writers (`updateSelectionStatusLog` js:575-579 vs the SSE flow js:1429-1460; JS-3), `fetchScrapeStatus` blanks the "N releases shown" label after every load (js:1587 → 606-608; JS-4, visible in screenshot `10b-populated-full-light.png`), and `renderFilters` resets label selections on date navigation (js:473-478; JS-5).
- **Init order**: config → theme → modal/button wiring → calendar init (empty) → restore saved range → health poll (500 ms then 5 s) → `initData` (releases, viewed, starred → default range spans the entire dataset if nothing saved, js:1542-1555 — PERF-4 → render → async scrape-status). Missing token (`config.has_token` falsy) shows the credentials modal and funnels into Settings.

## API surface

All JSON endpoints pass through `_corsify` → `Access-Control-Allow-Origin: *` (server.py:148-152; SEC-4). No auth, no Origin/Host validation, bound to `0.0.0.0` (SEC-1).

| Route | Methods | Lines | Purpose / notes |
|---|---|---|---|
| `/health` | GET | 231-235 | `{"ok": true}`; log-suppressed. Polled by the frontend every 5 s. |
| `/releases` | GET | 291-314 | Full flattened cache + embed overlay. Exceptions → 500 with raw exception text. |
| `/viewed-state` | GET, POST | 269-288 | Set membership for `viewed_state.json`; POST `{url, read}`. Unlocked load-modify-save. |
| `/starred-state` | GET, POST | 317-336 | Mirror of `/viewed-state` for stars. |
| `/config.json` | GET | 339-350 | Runtime config; `embed_proxy_url` reflects the client's Host header; `has_token` drives the first-run modal. |
| `/dashboard`, `/dashboard.css`, `/dashboard.js` | GET | 353-371 | `send_file` of the static assets; missing file → **500** (not 404) leaking the absolute path. |
| `/setup`, `/setup-gmail`, `/readme` | GET | 393-408 | Markdown docs rendered by the hand-rolled renderer (server.py:81-145) into `templates/docs.html`. |
| `/embed-meta?url=` | GET | 411-444 | Server-side fetch of the client-supplied URL, zero validation (SEC-2); writes embed cache but never reads it; fetch failure → 502 with exception text; uncaught `ast.literal_eval` path (bandcamp.py:20; PY-10). |
| `/scrape-status?start&end` | GET | 447-463 | Scraped vs not-scraped ISO dates; defaults to last 60 days; today always not-scraped. |
| `/reset-caches` | POST | 466-497 | Flags `clear_cache`/`clear_viewed`/`clear_starred`; either of the latter two deletes **both** state files (PY-13). Always 200. |
| `/clear-credentials` | POST | 500-518 | Deletes `token.pickle` only (name notwithstanding). |
| `/load-credentials` | POST multipart | 521-550 | Saves uploaded `credentials.json` (unvalidated), deletes token, then **synchronously runs the interactive OAuth browser flow inside the request thread** with no timeout (server.py:545 → gmail.py:115-116; SEC-6, UX-2). |
| `/populate-range-stream?start&end&max_results` | GET (SSE) | 553-626 | The populate pipeline described above. Non-numeric `max_results` → unhandled 500; value not clamped to `GMAIL_MAX_RESULTS_HARD`. |

All OPTIONS preflights return 204. Error responses across the API return raw exception strings and absolute filesystem paths (server.py:313, 356-377, 426, 518, 550).

## Packaging and distribution

Distributed via a Homebrew tap; developed as a plain-source repo. Current state is split-brained (ARCH-9, corrected details):

- **Versioning**: the formula in `keinobjekt/homebrew-bcfeed` sources `github.com/keinobjekt/bcfeed` at tag `v1.0-beta2`, consistent with the UI's hardcoded "bcfeed v1.0" (dashboard.html:73) — but the working repo's remote is `ellienieuwdorp/bcfeed` with **no tags at all**; releases are tagged only on the `keinobjekt` mirror, and the UI version string is hardcoded rather than derived from anything.
- **Python version — three answers**: formula `depends_on python@3.11`; `.python-version` says `3.10.19`; SETUP.md:41 says "3.10 or newer".
- **Dependencies — two answers**: `requirements.txt` lists `requests` twice (lines 3 and 7), uses the `bs4` shim instead of `beautifulsoup4`, and pins nothing; the formula pins everything (vendoring both `beautifulsoup4 4.14.3` and the `bs4 0.0.2` shim). Dev and distributed dependency sets can drift arbitrarily.
- **PyInstaller remnants**: gmail.py:42-44 still checks `sys._MEIPASS` for a bundled `credentials.json` even though all spec files were deleted — dead code that also encodes shipping an OAuth client secret inside a binary. Meanwhile paths.py:30-35 resolves the six static/doc assets via `Path(__file__).with_name(...)` with no explicit bundle handling (likely workable under PyInstaller since bundled `__file__` resolves inside `_MEIPASS`, but untested; the TODO's sole open item is "package in executable with installer").
- **Runtime layout**: source and static assets live together in the repo root; user data lives in the OS data dir (paths.py:4-17), created at import time. `credentials.json`/`token.pickle` are correctly outside the repo and gitignored.
- **No tests, no CI**: no test files, no `.github/` directory (ARCH-10). The structural blockers are paths.py's import-time data-dir creation with hardcoded constants (no injection point) and six inline `datetime.date.today()` call sites. The pure, immediately testable seams are util.py, bandcamp.py, the markdown renderer, `collapse_date_ranges`, and `scrape_info_from_email`.

## Measured performance characteristics

From the audit's benchmarks at a simulated 5,000-release library (perf dimension; all figures verified):

| Operation | Cost |
|---|---|
| One calendar unseen-scan (42 cells × all releases) | 9.8 ms; multiplied per row by bulk mark-seen → ~3 s (300 rows) / ~5 s (500 rows) main-thread freeze (PERF-1) |
| One `/embed-meta` call, server side | Bandcamp GET (0.3-0.8 s typical) + ~204 ms double BeautifulSoup parse + 38 ms whole-file rewrite of a 7 MB embed cache (PERF-3) |
| Sequential preload of 200 releases | 2-4 minutes, ~1.4 GB of redundant cache rewrites (PERF-3) |
| `/releases` payload | 1.20 MB un-enriched → 7.61 MB fully preloaded; ~39 ms server-side per request (PERF-5) |
| Single viewed/starred toggle | ~3 ms file I/O at 5k viewed URLs — fine in isolation (PERF-6) |
| Over-limit first populate | ~50 sequential Gmail `list` calls (~15-25 s) whose results are then discarded by the `max_results` check (PERF-7) |
| `release_cache.json` full rewrite | once per populate run, ~1.5 MB at 5k releases — explicitly a non-issue |

At the current few-hundred-release scale everything above is comfortably fast on localhost; the figures describe the trajectory at the realistic heavy-user ceiling.
