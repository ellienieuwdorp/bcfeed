# bcfeed — Architecture Improvement Plan

Status: proposal (no code changes yet). Date: 2026-07-05.
Sources: `docs/audit` process + verified findings in the audit report (IDs `ARCH-n`, `SEC-n`, `PY-n`, `JS-n`, `PERF-n` referenced below), backend/frontend deep-read maps.

Guiding constraint: bcfeed is a single-user, local-first utility. Every item below must reduce total code or total risk; nothing introduces servers, frameworks, background daemons, or operational machinery. Where a choice exists between "correct and boring" and "capable and clever", pick boring.

**Sizing key:** S = ≤ half a day, M = 1–2 days, L = 3+ days.

**Recommended order:** ARC-7a (test seam) → ARC-2a (storage correctness floor) → ARC-1 (typed SSE) → ARC-5 (security batch) → ARC-3 (backend layering) → ARC-7b/c (tests + CI) → ARC-4 (frontend split) → ARC-2b (SQLite) → ARC-6 (packaging). Rationale: the test seam is 3 lines and makes everything after it verifiable; the correctness floor and security batch are small and remove live data-loss/exposure; the two big refactors (ARC-4, ARC-2b) land only once tests exist to catch regressions.

---

## ARC-1 — Structured populate-progress protocol (typed JSON SSE events)

**Size: M** | Depends on: nothing | Enables: real progress UI, honest failure reporting, future IMAP source

### Problem
The populate stream emits unstructured prose lines (`server.py:616-617`); the client parses meaning out of English — `"Maximum results"` substring triggers a modal, `"ERROR:"` prefix routes to the error handler (`dashboard.js:1465-1478`). Any wording change silently breaks UI behavior (ARCH-4). Worse, uncaught worker exceptions end the stream with `event: done`, so failure renders as success (ARCH-3, PY-2). The backend already computes everything a determinate progress bar needs: missing ranges up front (`pipeline.py:70`), message counts (`pipeline.py:104`), batch index (batch loop in `gmail.py`, batch_size 20 at `server.py:597`).

### Design
Keep SSE, keep the single stream. Every `data:` payload becomes one JSON object:

```json
{"v": 1, "phase": "search", "current": 2, "total": 5,
 "message": "Checking mail for 3–9 June…", "level": "info",
 "text": "Checking mail for 3–9 June…"}
```

- `phase`: `"search" | "download" | "parse" | "save"` (fixed enum; a future IMAP source adds phases, not vocabulary).
- `current`/`total`: nullable ints. `total` known per phase (ranges for search, message count for download). Absent → indeterminate spinner, not a guess.
- `message`: plain-language human-readable line (the UX plan owns the copy; the protocol just carries it).
- `level`: `"info" | "warn" | "error"`.
- `text`: byte-identical mirror of `message`, kept for one release so the existing log box works mid-migration; delete after the frontend switch.

Terminal events become named SSE events with JSON payloads:

- `event: done` → `{"new_releases": N, "days_scraped": M}` — lets the UI say "N new releases" and refetch `/releases` instead of `window.location.reload()`.
- `event: error` → `{"code": "auth" | "max_results" | "gmail" | "parse" | "internal", "message": "..."}` — emitted for **every** worker exception: wrap the worker body in `except Exception as exc: q.put(("error", code, str(exc)))`. This closes the failure-looks-like-success path permanently.

Implementation shape: change the `log=queue.put` callback into a tiny emitter object (`emit.log(msg)`, `emit.progress(phase, cur, tot)`, `emit.error(code, msg)`) passed into `populate_release_cache`; the SSE generator `json.dumps`es whatever it drains. ~50 lines server-side, ~40 client-side (audit estimate).

### Rationale (first principles)
A protocol between two halves of the same app should carry structure, not prose, because prose couples UI behavior to copywriting. The only reason to keep prose is human debugging — the `message` field preserves that. SSE itself is the right transport here (one-way, localhost, no proxy) and should not be replaced.

### Risk / compat
- Low. Both halves ship together; there is no external consumer of this stream. The `text` mirror is belt-and-braces, not a real compat need.
- The `max_results` modal and error alert flows must be re-pointed at `code` fields in the same change, or they go dead — grep for `"Maximum results"` and `"ERROR:"` in `dashboard.js` as the completeness check.
- Do **not** fix the lock-release-on-disconnect bug (`server.py:619-620`) as part of this item's scope creep — but note it must move to the worker's `finally` when this file is touched (ARCH-1).

### Acceptance criteria
1. No substring matching on SSE payloads anywhere in `dashboard.js`.
2. Killing the worker mid-run (inject a raised exception in `construct_release_list`) produces a visible `event: error` in the client and the log survives — never `event: done`.
3. During a 3-range populate, the client receives `progress` events sufficient to render a determinate bar (`current`/`total` present for search and download phases).
4. `event: done` carries `new_releases`; the client refetches `/releases` and does not call `window.location.reload()`.
5. `POPULATE_LOCK` is released by the worker thread, not the SSE generator; disconnecting the EventSource mid-run and re-requesting `/populate-range-stream` returns "already running".

---

## ARC-2 — Storage: JSON correctness floor now, SQLite as the end state

### Evaluation

**Stay on JSON (fixed)?** Virtues: files are human-inspectable (a real local-first value), zero migration, `clear cache` = delete files. Fixed cost: needs one shared lock, one store module (persistence is currently implemented twice — `server.py:155-228` vs `session_store.py:30-83`, which is how the same tmp-path bug got written twice), unique tmp names, and per-range persistence ordering. Remaining ceiling even after fixes: every mutation rewrites a whole file (PERF-6), every `/releases` re-reads and re-flattens everything, multi-file updates (release cache + empty dates + scrape status, `session_store.py:209-212`) stay non-transactional, and corruption recovery stays manual.

**SQLite (stdlib, one file, WAL)?** Deletes ~150 lines of bespoke persistence, makes the three-store populate commit a single transaction (structurally fixing the ARCH-2 scraped-before-persisted class), gives indexed range queries, and fixes the whole lost-update family (ARCH-1, PERF-1, PERF-6) at the storage layer. Costs: one-time migration, and loss of open-in-editor debuggability (mitigated: `sqlite3 bcfeed.db .dump`, plus an optional `--export-json` flag).

**Recommendation: both, in sequence.** The JSON fixes are small and needed *this week* because they stop live data loss; SQLite is the right end state but should land after tests exist. This is not doing the work twice — the JSON floor's main deliverable (one store module with narrow function signatures) is exactly the seam the SQLite swap plugs into.

### ARC-2a — JSON correctness floor

**Size: S** | Depends on: ARC-7a (to test it) | Fixes: ARCH-1, ARCH-2, PY-8, PY-12 (partially)

- One `threading.Lock` in `session_store` wrapping every load-modify-save; all persistence helpers in `server.py` (`_load_set`, `_save_set`, `_load_embed_cache`, `_save_embed_cache`) deleted and re-pointed at `session_store`.
- Unique tmp names (`tempfile.NamedTemporaryFile(dir=store_dir, delete=False)` + `os.replace`) — kills the shared-`.tmp`-path collision and its non-atomic fallback (`server.py:169-173`).
- Reorder the pipeline: `persist_release_metadata(range_releases)` **then** `mark_date_range_scraped(...)`, per range, inside the loop (~5-line reorder of `pipeline.py:110-123`). A crash can now only lose the in-flight range, and that range stays marked un-scraped so retry re-fetches it.
- Corrupted store files: rename to `<name>.corrupt-<ts>.json` and surface a log/`event: error` instead of silently treating as empty and overwriting (`session_store.py:39-42`).

**Acceptance criteria:** (1) a pytest that fires 50 concurrent viewed-state POSTs at a threaded test client loses zero marks; (2) a pytest that raises inside `construct_release_list` mid-run leaves the failed range absent from `scrape_status.json` and prior ranges' releases present in `release_cache.json`; (3) a hand-corrupted `release_cache.json` is preserved under a `.corrupt-*` name, never overwritten.

### ARC-2b — SQLite migration

**Size: M** | Depends on: ARC-2a, ARC-7b (tests) | Fixes: PERF-1/6 storage half, ARCH-7

Schema sketch (one file, `bcfeed.db`, `PRAGMA journal_mode=WAL`):

```sql
CREATE TABLE releases   (url TEXT PRIMARY KEY, day TEXT NOT NULL, artist TEXT, title TEXT,
                         page_name TEXT, is_track INTEGER, source TEXT DEFAULT 'gmail');
CREATE INDEX releases_day ON releases(day);
CREATE TABLE day_status (day TEXT PRIMARY KEY, status TEXT CHECK(status IN ('scraped','empty')));
CREATE TABLE flags      (url TEXT NOT NULL, kind TEXT CHECK(kind IN ('viewed','starred')),
                         PRIMARY KEY (url, kind));
CREATE TABLE embed_meta (url TEXT PRIMARY KEY, release_id TEXT, is_track INTEGER,
                         embed_url TEXT, description TEXT, fetched_at TEXT, fetch_failed INTEGER DEFAULT 0);
```

Notes: `source` column added now so IMAP later needs no migration (ARCH-5's advice); `fetch_failed`/`fetched_at` on `embed_meta` give the negative-cache PERF-2 needs; `flags` unifies viewed/starred and makes the `/reset-caches` both-flags bug (PY-13) structurally impossible to reintroduce.

Migration: on startup, if `bcfeed.db` is absent and JSON stores exist → import all five stores inside one transaction → rename JSON files to `*.imported.bak` (never delete). Keep the existing `session_store` function signatures (`get_full_release_cache`, `persist_release_metadata`, `cached_releases_for_range`, viewed/starred get/set) so `server.py`/`pipeline.py` barely change. Populate persists one range = one transaction across `releases` + `day_status`.

**Risk / compat:** migration bugs could lose stars/viewed — mitigated by the `.bak` rename and a round-trip test (JSON fixtures → import → export → compare). `Clear cache` UX changes from "delete files" to targeted `DELETE FROM` statements — simpler and finally makes the flag semantics exact. If the maintainer vetoes SQLite on inspectability grounds, ARC-2a alone is an acceptable permanent state; document the accepted ceiling (whole-file rewrites, non-transactional multi-store updates are gone either way only under 2b).

**Acceptance criteria:** (1) migration round-trip test passes on fixture stores including a starred+viewed overlap; (2) all five JSON stores gone from the write path (grep: no `json.dump` outside migration/export); (3) mark-100-rows-seen issues one transaction (with ARC-4's batch endpoint) and survives a concurrent populate without lost rows; (4) `PRAGMA integrity_check` clean after a kill -9 during populate.

---

## ARC-3 — Backend layering: get non-HTTP concerns out of server.py

**Size: M** | Depends on: ARC-2a (store consolidation overlaps) | Fixes: ARCH-8, PY-6 (testability of), PERF-2 (cache-first embed), SEC-6 (blocking OAuth, structural half)

### Problem
`server.py` (626 lines) contains four non-HTTP concerns: a 65-line hand-rolled markdown renderer (`server.py:61-145`), inline Bandcamp fetching in `/embed-meta` (`server.py:419-423`) that never reads its own cache, a duplicate persistence layer (`server.py:155-228`), and a synchronous interactive OAuth flow inside a request handler (`server.py:545` → `flow.run_local_server`, no timeout). This coupling directly blocks the cheapest wins: a cache-first `/embed-meta`, golden-testable markdown, one store.

### Design — target module boundaries

| Module | Owns | Must not |
|---|---|---|
| `server.py` (~300 lines) | routes, request parsing, JSON serialization, SSE plumbing | touch files directly, call `requests`, render markdown |
| `docs.py` (new) | `render_markdown_html`, `format_setup_inline`, doc-path → HTML | know about Flask |
| `bandcamp.py` (grown) | `get_embed_meta(url)` = cache lookup → validated fetch → parse → cache write (single home for fetch+parse+cache; consumes the ARC-5f validator) | be called with unvalidated URLs |
| `session_store.py` / `storage.py` | **all** persistence (ARC-2) | — |
| `gmail.py` | auth + search + download + email parsing (unchanged scope) | be invoked synchronously from a request thread for interactive auth |

`/load-credentials` change: validate the upload is a plausible OAuth client-secret JSON (has `installed.client_id` etc.), save atomically, delete stale token, **return immediately**. Interactive auth runs on demand at next populate (which already handles `GmailAuthError`) or via an explicit "Connect Gmail" trigger — exact flow is the UX plan's call; the architectural requirement is only *no interactive OAuth inside a request handler*.

The hand-rolled markdown renderer stays hand-rolled (no-deps ethos) but gets golden tests (ARC-7b) — it demonstrably mangles `[label](https://…)` links today (PY-6), which a golden of `SETUP.md` catches on every future edit.

### Rationale
Layering in a 1,600-line codebase is not about purity; it is about giving each fragile heuristic (markdown, Bandcamp DOM, email copy) a home that a test can import without standing up Flask, and making the route file small enough that security review of the HTTP surface is a single-file read.

### Risk / compat
Low — mechanical moves with unchanged behavior, except the `/load-credentials` response shape (frontend updated in the same change; both ship together). The embed cache-first change alters observable behavior only by *removing* redundant network fetches.

### Acceptance criteria
1. `grep -n "requests\.\|BeautifulSoup\|json.dump\|open(" server.py` → no hits outside app bootstrap.
2. `docs.py` and `bandcamp.get_embed_meta` importable and testable without a Flask app or network (fetch injected/mockable).
3. Two `/embed-meta` calls for the same URL hit the network once (second is a cache read) — regression test.
4. `/load-credentials` returns in < 1 s regardless of OAuth state; no request thread ever blocks on human interaction.

---

## ARC-4 — Frontend decomposition: one IIFE → native ES modules, no build step

**Size: L** | Depends on: ARC-1 (protocol first, so the populate module is written once), ARC-7c (Playwright smoke as the safety net) | Fixes: ARCH-5, JS-3 (single ownership), PERF-1/ARCH-6 (render coalescing), JS-6 (in-flight dedupe home)

### Problem
1,725 lines in one IIFE with shared mutable state: the Populate button has two owners (`dashboard.js:575-579` vs `1460`), endpoints are derived twice with a stale `apiHost` left over (`9-21` vs `48-55`), cached-badge logic exists twice, and every feature edit lands in the same file. Full re-render is per-*toggle* today: `setViewed` rebuilds the calendar every call; mark-all-seen = N calendar rebuilds + N racing POSTs (PERF-1).

### Design

**Module split (native `<script type="module">`, same-origin, no bundler, no framework):**

```
web/js/
  config.js     endpoint derivation, once, after config.json load (kills stale apiHost, JS-2's dead guard)
  api.js        all fetch calls incl. batch viewed endpoint; esc/url-validation helpers live beside their render users
  state.js      the state object + mutate() entry point + scheduleRender()
  table.js      renderTable + row/detail templates + delegated listeners
  calendar.js   renderCalendar + selection model
  status.js     progress bar / log / header counts (single owner of populate-button state)
  modals.js     dialog helpers (focus trap/Escape once, reused)
  populate.js   SSE client for the ARC-1 protocol; preload loop
  main.js       init order only
```

Serving: add one Flask static route for `web/js/` (via `send_from_directory` with `safe_join`, or Flask's `static_folder`) — the current one-route-per-file pattern (`server.py:353-371`) does not scale to nine files. `paths.py` gains the directory constant (see ARC-6 resource-path note).

**Rendering strategy — incremental where it pays, wholesale where it's cheap:**
- Structural changes (sort, filter, data refetch): keep full rebuild, but build into a `DocumentFragment` and swap once.
- Row-state changes (seen/star toggle): mutate the existing row's classes in place; **no** tbody rebuild, **no** calendar rebuild per toggle.
- All renders go through `scheduleRender(regions)` which coalesces via `queueMicrotask`/`requestAnimationFrame`: N state mutations in one task → one render. Mark-all-seen becomes: mutate state N times → one batched POST (`{urls: [...], read: true}` — new endpoint, pairs with ARC-2) → one table render + one calendar render.
- Calendar: precompute a `day → unseen-count` map once per render instead of the per-cell O(releases) scan (`dashboard.js:1254-1272`).

**Event-listener hygiene:** one delegated `click`/`keydown` listener on `tbody` (replacing ~8 listeners × N rows per render), one document-level keydown for shortcuts, `AbortController`-scoped listeners for modals. Delegation also removes the listener-rebind cost that makes full re-render expensive today.

**Single-owner rule:** each DOM region has exactly one module that writes it. The populate button and status area belong to `status.js`; `calendar.js` requests updates through state mutation, never writes the log (kills JS-3).

This is a *mechanical* split — behavior-preserving, verified by the Playwright smoke before/after. It is the prerequisite for IMAP/multi-source (a second source becomes a new module + a `source` field, not edits across one shared file).

### Rationale
The monolith is at a feature-cost breaking point, not a performance one. Native ES modules give the decomposition without paying the local-first tax of a build step: the files served are the files edited, view-source stays honest, and there is no toolchain to rot.

### Risk / compat
- Medium: the split touches everything; sequence as move-code-then-refactor (first commit: extract modules verbatim; later commits: single-owner/coalescing changes) so bisection stays possible.
- `type="module"` implies strict mode + deferred execution — the IIFE's implicit ordering assumptions (config promise, init order) must become explicit imports; `main.js` owns init order.
- No old-browser concern: product targets current Chrome on macOS.

### Acceptance criteria
1. No IIFE; no module over ~450 lines; `dashboard.js` deleted.
2. Endpoint derivation happens exactly once, after config load.
3. Populate-button `disabled`/label written from exactly one module (grep-verifiable).
4. Mark-all-seen on 300 rows: 1 POST, ≤ 2 renders, main thread blocked < 100 ms (vs multi-second today, PERF-1 benchmark).
5. Playwright smoke (ARC-7c) green before and after the split with identical assertions.
6. Row seen/star toggle does not rebuild tbody (assert node identity across toggle in the smoke test).

---

## ARC-5 — Security hardening batch

All items are small; ship as one release ("localhost lockdown") because several depend on each other for their guarantees. Threat model honored: single user, localhost, low-sensitivity data — this is about not being *accidentally* exposed to the LAN and the web, not enterprise auth.

### ARC-5a — Bind 127.0.0.1 — **Size: S** (fixes SEC-1)
`make_server("127.0.0.1", ...)` at `server.py:247` and the same host in `find_free_port` (`server.py:256/259`). Nothing needs the LAN bind — `bcfeed.py:22` only ever opens localhost. **AC:** `curl http://<lan-ip>:<port>/health` from another device fails; localhost works. **Compat:** anyone deliberately using bcfeed over LAN loses that — acceptable and correct for this product; not configurable (a knob here is an invitation to re-expose).

### ARC-5b — Remove `ACAO:*`, validate Host — **Size: S** (fixes SEC-4 read-side)
Delete `_corsify`'s wildcard (`server.py:148-152`, SSE at 564/623) — the app is same-origin and needs no CORS at all. Add a before-request Host check: `Host ∈ {localhost:<port>, 127.0.0.1:<port>}` else 403 — blocks DNS rebinding. **AC:** cross-origin `fetch` from a test page cannot read any endpoint; request with `Host: evil.example` → 403. **Compat:** none for the shipped app (browser sets Host to what the user typed, always localhost).

### ARC-5c — Mutation guard: custom header, not a session token — **Size: S** (fixes SEC-4 write-side, SEC-6 reachability)
Evaluated a per-launch session token embedded in the served HTML: it works, but it complicates every fetch and the SSE URL, and after 5a+5b the only remaining vector is blind cross-site form/simple-request POSTs. Requiring a custom header (`X-BCFeed-Request: 1`) on every mutating route closes that: custom headers force a CORS preflight, which fails with no `ACAO`. **Verdict: header requirement yes; session token not warranted** at this threat model — revisit only if the app ever intentionally serves non-localhost. **AC:** `curl -X POST /reset-caches` without the header → 403; a cross-origin multipart POST to `/load-credentials` from a test page never reaches the handler.

### ARC-5d — OAuth scope → `gmail.readonly` + re-auth migration — **Size: S** (fixes SEC-3)
Change `gmail.py:89` to `https://www.googleapis.com/auth/gmail.readonly` (the app only calls `messages().list/get`; docs and privacy.md already promise read-only — this is a documented-behavior violation today). **Migration:** on token load, inspect `creds.scopes`; if it includes `mail.google.com`, delete the token and report `has_token: false` — the existing missing-token modal flow then walks the user through reconnecting. The UX plan owns the one-line explanation ("bcfeed now asks for read-only access; please reconnect"). **AC:** fresh consent screen shows read-only only; a legacy full-scope token is invalidated on first launch and the reconnect flow triggers; no code path requests any other scope (grep).

### ARC-5e — Token as JSON, not pickle; tight perms — **Size: S** (fixes SEC-5)
`Credentials.to_json()` → atomic write → `chmod 0600`; load via `from_authorized_user_file`. Same perms for `credentials.json` on upload. **Migration:** if `token.json` absent and `token.pickle` present → load pickle once, save JSON, unlink pickle (in practice most users re-auth anyway via 5d — the two items should ship together so there is exactly one re-auth event). **AC:** no `import pickle` in the codebase; `stat -f %Lp token.json` = 600; pickle file gone after first launch.

### ARC-5f — `/embed-meta` URL allowlist — **Size: M** (fixes SEC-2, supports PERF-2)
The strongest available control is that `/embed-meta` only ever *needs* to fetch pages for releases the user's own Gmail produced. Validation chain, in order:
1. Scheme must be `https`.
2. **Host ends with `.bandcamp.com`** → allowed directly; **any other host** (Bandcamp custom domains are legitimate) → allowed **only if the exact URL is already a key in the release cache** — i.e., it arrived via the email pipeline, not from an arbitrary caller.
3. Resolve the host; reject private/link-local/loopback ranges (SSRF floor even for cache-listed URLs, since email content is third-party-authored).
4. `requests.get(..., allow_redirects=False)`; on 3xx, follow at most one hop **re-running steps 1–3** on the target (custom domains commonly redirect to `*.bandcamp.com`).
5. Cap response size (e.g. 2 MB streamed) and require the `bc-page-properties` meta before caching or returning a description — page-level verification that this is actually a Bandcamp release page, so a non-Bandcamp page's metadata is never exfiltrated into the UI or cache.
6. Return generic error bodies (no refusal/timeout/status oracle, `server.py:426` today).

**AC:** requests for `http://…`, `https://192.168.1.1/…`, `https://example.com/album/x` (not in cache) all 400 with identical bodies; a custom-domain URL present in the release cache succeeds; embed cache contains only URLs that passed validation; response bodies over the cap abort. **Compat:** none — the frontend only ever requests release URLs.

**Batch risk:** the only user-visible cost of ARC-5 is the one-time Gmail reconnect (5d/5e). Everything else is invisible when it works — which is exactly the point.

---

## ARC-6 — Packaging: path to a double-clickable app

**Size: M (L with notarization)** | Depends on: ARC-9-style hygiene below; independent of other items | Fixes: ARCH-9, TODO's sole open item

### Prerequisite hygiene (do first, tiny, valuable even if packaging never ships)
- Single `VERSION` constant in `paths.py` (or `__init__`), surfaced in `/config.json` and the header — today the tap says v1.0-beta2, the HTML hardcodes "v1.0", the working repo has no tags.
- `pyproject.toml` with pinned deps replacing `requirements.txt` (which lists `requests` twice, uses the `bs4` shim, pins nothing while the formula pins everything). One Python version story (pick 3.11 to match the formula).
- `resource_path()` helper in `paths.py` used by *all* bundled assets (dashboard files, docs, templates) — today none are bundle-aware while `gmail.py:42-44` has a dead `_MEIPASS` branch for exactly the file that must **never** be bundled (an OAuth client secret inside a distributed binary contradicts the user-owned-credentials privacy model). Delete that branch.

### Options considered
- **PyInstaller `.app`** — smallest delta: a windowed onedir bundle whose entry point is the existing `bcfeed.main` (start server, open browser). Known costs: spec-file `datas` for the seven assets (solved by `resource_path()`), hidden-import fiddling for `googleapiclient`, Gatekeeper.
- **Briefcase (BeeWare)** — generates a well-formed `.app` with bundled Python and handles signing scaffolding, but imposes its project layout and template machinery on a codebase whose entire ethos is "no machinery", for zero user-visible benefit over PyInstaller for a Flask-in-browser app.
- **Menu-bar app (`rumps`)** — not a packaging alternative but a *shell*: a status-bar icon with "Open dashboard / Quit" is the natural macOS shape for a background local server (no orphaned terminal window, obvious way to quit). Small dependency, composes with either bundler.

### Recommendation
**PyInstaller onedir `.app`, with a `rumps` menu-bar shell as the entry point (ARC-6b, optional second step).** Rationale: least new machinery, remnants show it was already attempted once, and the failure mode of the previous attempt (asset paths) is fixed by the prerequisite hygiene, not by switching tools. Keep the Homebrew tap as the primary channel; distribute the `.app` as a GitHub release asset (later, optionally, a Homebrew cask). Signing: start with ad-hoc signing + documented right-click-open; Developer ID + notarization ($99/yr) only if the audience grows beyond people comfortable with that.

### Risk / compat
- Gatekeeper friction for unsigned apps is real; document it honestly rather than pretending it away.
- PyInstaller + google-api-python-client is a known hidden-imports tarpit; time-box it, and keep the CLI path first-class so packaging trouble never blocks users.
- App translocation: onedir in a `.dmg`/zip is fine as long as no writes ever target the bundle — all writes already go to the data dir; the deleted `_MEIPASS` credentials branch was the one violation of this.

### Acceptance criteria
1. `open dist/bcfeed.app` on a clean macOS user account → browser opens the dashboard; all doc routes render (the exact routes that would have 500'd before `resource_path()`).
2. Quitting via the menu-bar item (6b) or Activity Monitor leaves no orphan process; data dir untouched by the bundle location.
3. One version string: UI header == `/config.json` == git tag == formula.
4. `credentials.json` is not in the bundle and `_find_credentials_file` has no `_MEIPASS` branch (grep).
5. CLI (`bcfeed` via Homebrew) still works identically.

---

## ARC-7 — Test baseline: pytest + one Playwright smoke + CI

The heuristics that hold the product up (email-copy regexes `gmail.py:249-292`, Bandcamp DOM selectors `bandcamp.py:11-58`, hand-rolled markdown) are exactly the code that regresses silently, and there are zero tests and no CI today (ARCH-10, PY-4). This is the multiplier item: everything above becomes safe to do once it exists.

### ARC-7a — The 3-line seam — **Size: S**
`paths.get_data_dir()` reads `BCFEED_DATA_DIR` env var before defaulting; keep the import-time directory creation but make it use that result. Unlocks `tmp_path`-isolated tests for the whole persistence + pipeline layer. Also introduce a single `today()` helper (in `util.py`) replacing the six inline `datetime.date.today()` call sites, so the today-exclusion logic (subject of the most recent bugfix, 598a9dd) is testable via monkeypatching one name. **AC:** importing `session_store` in a test with the env var set touches only the tmp dir.

### ARC-7b — pytest suite (~25 cases) — **Size: M**
Fixtures: one saved real Bandcamp notification email (HTML part), one plain-text-only email (the PY-3 crasher), one saved release page, one page without `bc-page-properties`, golden HTML for `SETUP.md`.

| Area | Cases |
|---|---|
| `util` | `parse_date` (ISO / `YYYY/MM/DD` / RFC 2822 / None / garbage), `dedupe_by_url`, `dedupe_by_date` with a malformed cached date (PY-7 regression) |
| `session_store` | store round-trips, corruption → preserved-not-overwritten (ARC-2a AC), `collapse_date_ranges`, `cached_releases_for_range` today-exclusion (frozen clock) |
| `pipeline` | persist-before-mark ordering (ARC-2a AC), junk-row guard (PY-5), max-results path |
| `gmail` parsing | `scrape_info_from_email` on fixture emails incl. no-HTML-part (PY-3) and no-Date-header |
| `bandcamp` | `extract_bc_meta` valid/invalid content (PY-10 `literal_eval` path), description fallbacks, `get_embed_meta` cache-first (ARC-3 AC) |
| `docs` renderer | golden render of `SETUP.md`; `[label](https://…)` link regression (PY-6); `javascript:` href rejected |
| routes (Flask test client) | `/reset-caches` flag matrix (PY-13), viewed-state concurrency (ARC-2a AC), Host/header guards (ARC-5b/c ACs), `max_results` non-numeric input |

No mocks of Gmail/OAuth at this tier — the network edge stays untested by design; the parsing behind it is fully covered by fixtures.

### ARC-7c — Playwright smoke — **Size: S–M**
One spec: launch server with `BCFEED_DATA_DIR` pointing at seeded fixture stores → open `/dashboard` → assert rows render with fixture titles → click a row (detail opens, marked seen) → star a row → reload → seen/starred persisted → select a calendar day → row set filters. Chromium only, headless, ~30 lines. This is the regression net for ARC-4's mechanical split (its AC #5).

### ARC-7d — CI: GitHub Actions — **Size: S**
One workflow, `ubuntu-latest`, on push/PR: `pip install -e . && ruff check . && pytest`; second job (allowed ~2 min) installs Chromium and runs the smoke against the fixture data dir. No release automation, no matrix, no badges-as-goals — the entire value is "the fixture-covered heuristics cannot silently break again."

**Risk:** near zero; the only trap is over-testing (snapshotting volatile things). Keep goldens limited to the markdown renderer, where mangling is the known live bug.

**Acceptance criteria:** (1) `pytest` green locally in < 30 s with no network; (2) CI red on a deliberate revert of the PY-6 link fix or the pipeline reorder; (3) smoke runs against a fresh checkout with one command documented in the README dev section.

---

## Explicit non-goals

Recorded so future contributors don't "improve" past the product's shape:

- No web framework, no frontend build step, no TypeScript migration.
- No async rewrite (threads + one lock/SQLite are sufficient at single-user concurrency).
- No client-server auth beyond ARC-5 (no accounts, no HTTPS on loopback).
- No database server, no Docker, no telemetry/crash reporting.
- No plugin system for sources — IMAP, when it comes, is a module and a `source` column, both provisioned above.

## Cross-plan notes

- The plain-language renaming of populate/preload/cache/token and the progress-bar/toast UI that ARC-1's protocol enables are specified in the UX/UI plans; ARC-1 deliberately carries `message` strings opaquely so copy can change without protocol changes.
- The XSS/escaping fixes (JS-1/SEC-7) are frontend-quality work that should land *before or with* ARC-4's table module so the new `table.js` is born escaping-correct.
- High-severity correctness fixes bundled here (ARC-2a's reorder, ARC-1's error event) supersede piecemeal fixes of ARCH-1/2/3 — do them once, in this shape.
