# Known issues

Canonical defect and debt register for bcfeed, distilled from the 2026-07-05 full audit (7 dimensions, every medium/high finding adversarially verified). This is the list the improvement plans reference — IDs are stable; do not renumber.

- **Status labels:** `confirmed` = evidence held exactly on verification; `partial` = held with corrections (the corrected reading is what appears below); `low, not verified` = low-severity findings that were not adversarially re-verified.
- **Evidence:** file:line references are to the repo at commit 598a9dd; screenshots live in `docs/current-state/screenshots/` (superset in the audit scratchpad `shots/`).
- **Cross-references:** the same root cause sometimes surfaced in multiple dimensions (e.g. unlocked JSON stores → PY-8 / ARCH-1 / PERF-6). Entries are kept separate as-found and cross-linked; a fix should close the whole cluster.
- **Do not break while fixing:** the fast no-framework table, star-triggers-preload (dashboard.js:250), the calendar-as-coverage-map, honest SSE progress content, hover-prefetch, in-app docs, keyboard triage shortcuts, and local-first storage are verified strengths, not accidents.

## Python backend

### PY-1 · high · confirmed — Dates are marked scraped before releases are persisted; a mid-populate failure permanently loses those days
`populate_release_cache` marks each range scraped inside the loop (pipeline.py:114) but persists releases only once at the end (pipeline.py:123). Any failure between the two (Gmail 429, MaxResultsExceeded on a later range, parse crash at pipeline.py:110, bad date at util.py:81) leaves days marked done with no data; `cached_releases_for_range` (session_store.py:234) then never re-fetches them. Same root as ARCH-2; compounds with PY-2/PY-3.
- Evidence: pipeline.py:114 vs 123; session_store.py:234; pipeline.py:95-110
- Fix: persist per-range inside the loop and make mark-scraped the last step of each range (the empty-range path at pipeline.py:102 already does this correctly).

### PY-2 · high · confirmed — Populate worker swallows unexpected exceptions and the SSE stream reports success
The worker catches only GmailAuthError and MaxResultsExceeded (server.py:601-604); anything else escapes to stderr, `finally: q.put(None)` ends the stream, and the generator emits `event: done` (server.py:618). Parse-stage and final dedupe/persist failures (pipeline.py:110, 117-123) log no ERROR line at all, so they fail completely silently. Same root as ARCH-3.
- Evidence: server.py:591-618; pipeline.py:110, 117-123
- Fix: `except Exception` in the worker that queues `ERROR: {exc}`, plus a distinct terminal `event: error` vs `event: done`.

### PY-3 · high · confirmed — Emails without an HTML part crash the whole populate run
`get_html_from_message` returns None for plain-text-only messages (gmail.py:83); `scrape_info_from_email` guards soup construction (gmail.py:222) but calls `_find_bandcamp_release_url()` unconditionally (gmail.py:235), dereferencing `soup.find_all` (gmail.py:226). Empirically reproduced: one plain-text email aborts the entire populate — silently, per PY-2.
- Evidence: gmail.py:222, 226, 235; reproduced with `scrape_info_from_email(None, 'New release from X')` → AttributeError
- Fix: early-return the None tuple when `email_text` is falsy; wrap per-email parsing in try/except-log-skip.

### PY-4 · high · confirmed — Zero tests on a heuristics-heavy codebase; module structure actively resists testing
No test files exist. The most fragile code (email-copy regexes gmail.py:249-292, Bandcamp DOM selectors bandcamp.py:11-58, hand-rolled markdown server.py:81-145) has zero fixtures. paths.py binds store paths at import time with a mkdir side effect (paths.py:16-35), and `date.today()` is called inline in six places, blocking cheap tests. Same root as ARCH-10.
- Evidence: no tests/ or .github/ in repo; paths.py:16-35; session_store.py:74,134,149,176,197,266
- Fix: BCFEED_DATA_DIR env override in `get_data_dir()`, ~20 pytest cases over the pure seams with saved email/page fixtures, one CI job (ruff + pytest).

### PY-5 · medium · confirmed — Emails yielding no release URL become null-URL junk rows that are cached and served
When no /album/ or /track/ link is found, the 6-None tuple passes the guard at pipeline.py:38 because `date` is set separately (pipeline.py:32). Null-URL rows survive dedupe (util.py:59-64), are persisted, served to the frontend, and can never be marked viewed/starred (URL is the primary key).
- Evidence: pipeline.py:32-38; util.py:59-64; reproduced with a linkless email → all-null release dict
- Fix: gate on `release_url is not None` instead of the all-None check.

### PY-6 · medium · confirmed — Markdown renderer mangles standard `[label](https://…)` links into nested broken anchors
The autolink regex (server.py:72-76) runs after the markdown-link rewrite (server.py:71) and re-matches the URL inside the just-generated href attribute. Latent only because no shipped .md file uses an external markdown link today; the first one written breaks the rendered /setup page.
- Evidence: server.py:71-76; reproduced nested `<a href="<a href="…` output; `grep '](http' *.md` is empty
- Fix: autolink before the link pass (or tokenize links out first); add a golden test over SETUP.md.

### PY-7 · medium · partial — One malformed date bricks every future populate of the affected range, silently
Two unguarded `parse_date` calls: pipeline.py:32 (message with missing/unparseable Date header — gmail.py:192 falls back to the raw header string) and util.py:81 via pipeline.py:117 (bad per-item date in release_cache.json). Both raise ValueError, are swallowed into a silent `done` (PY-2), and recur on every populate covering that range — other ranges are unaffected (correction from verification). In case 2, newly fetched ranges were already marked scraped before the crash (PY-1), so that Gmail data is marked done but never persisted.
- Evidence: pipeline.py:32; util.py:81 via pipeline.py:117; gmail.py:187-192
- Fix: `parse_date(..., allow_none=True)` + skip-with-log in both spots; treat date-less items like the without_url bucket.

### PY-8 · medium · confirmed — No locking on any JSON store, and the populate lock is released while the worker still runs
Every store is unsynchronized load-modify-save under a threaded server (server.py:247): viewed/starred (server.py:282-287, 330-335), embed cache (server.py:208-228). Concurrent writers share one tmp path (server.py:166), with a non-atomic fallback papering over the race (server.py:169-173). POPULATE_LOCK is released in the SSE generator's `finally` on client disconnect while the daemon worker keeps running (server.py:619-620), allowing two concurrent populate workers. Same cluster as ARCH-1 / PERF-6.
- Evidence: server.py:165-173, 208-228, 282-287, 330-335, 619-620; session_store.py:209-212
- Fix: one lock per store (or one global), unique tmp names, release POPULATE_LOCK in the worker's finally.

### PY-9 · medium · confirmed — get_messages depends on the private `batch._responses` attribute; its 429 message references a nonexistent `--batch` flag
gmail.py:162-165 reads a private google-api-python-client attribute and rides on dict insertion order; any library upgrade can break downloads. On 429 the user is told to use `--batch`, which does not exist (bcfeed.py defines only --port/--no-browser; batch size is hardcoded at server.py:597). No retry/backoff, so one rate limit aborts the populate (and strands ranges per PY-1).
- Evidence: gmail.py:158-174; bcfeed.py:28-29; server.py:597
- Fix: use `batch.add(request, callback=...)`; retry 429s with backoff; delete the --batch text.

### PY-10 · medium · confirmed — /embed-meta error handling is fragile: uncaught ast.literal_eval, no cache read, raw exception passthrough
`extract_bc_meta` falls back to `ast.literal_eval` (bandcamp.py:17-20) with no try/except at the call site (server.py:429) → raw Flask HTML 500 without CORS headers. The endpoint writes embed_cache.json but never reads it before fetching (server.py:411-444; see PERF-2). Fetch failures return the raw exception string (server.py:426).
- Evidence: bandcamp.py:17-20; server.py:411-444
- Fix: wrap literal_eval, consult `_load_embed_cache()` first, return generic error messages.

### PY-11 · medium · confirmed — Speculative quopri double-decode can corrupt legitimate email HTML
gmail.py:66-72 applies `quopri.decodestring` to already-CTE-decoded Gmail bodies under a bare except; any legitimate `=XX` sequence (query strings, encoded URLs) is silently transmuted, and the corruption propagates into cached release data.
- Evidence: gmail.py:64-75
- Fix: delete the quopri pass, or apply only when headers declare quoted-printable.

### PY-12 · medium · confirmed — Corrupted JSON stores are silently reset to empty and then overwritten
All loaders return empty on any exception (session_store.py:39-42, 66-67; server.py:161-162, 197-198), so a truncated release_cache.json is overwritten with only the new run's data — while scrape_status.json still marks the lost dates as scraped (PY-1), making them unrecoverable.
- Evidence: session_store.py:39-50, 66-67; server.py:161-162, 197-198; interaction with session_store.py:234
- Fix: on JSONDecodeError, rename to `*.corrupt-<timestamp>` and log loudly before starting fresh.

### PY-13 · medium · confirmed — /reset-caches clears both viewed and starred state when either flag is set
`if clear_viewed or clear_starred:` unlinks both paths (server.py:491-495). Latent because the frontend hardcodes all flags true (dashboard.js:1044-1053), but any future granular UI silently destroys data.
- Evidence: server.py:491-495; dashboard.js:1044-1053
- Fix: split into two independent `if` blocks.

### PY-14 · low, not verified — Dead and misleading code cluster
`mark_dates_not_scraped` never called (session_store.py:159-165); `img_url` dead end-to-end (gmail.py:204, util.py:37,44); dead decode block (gmail.py:211-215); unused `import os` (gmail.py:2); unreachable-and-broken str fallback (pipeline.py:28-34); `/clear-credentials` deletes the token, not credentials (server.py:500-514); GMAIL_MAX_RESULTS_HARD not enforced against client input (server.py:559, unhandled ValueError on non-numeric); assorted shadowed builtins and exact-type checks (gmail.py:141-159).
- Fix: delete dead branches/field/import; rename or fix /clear-credentials; clamp max_results.

### PY-15 · low, not verified — Two parallel atomic-JSON persistence implementations
server.py:155-205 duplicates session_store.py:30-83 with divergent details (non-atomic fallback only in server.py; different indents); plus alias constants (session_store.py:22-23). Any store fix must land twice or silently diverge — this is how the shared-tmp-path bug was written twice.
- Fix: one json_store helper module used from both; drop the aliases.

## Frontend JavaScript

### JS-1 · high · confirmed — Unescaped innerHTML interpolation of email/scraped data (HTML injection / XSS) in row and detail templates
renderTable interpolates `page_name`, `artist`, `title`, `url` into innerHTML with zero escaping (dashboard.js:756-769); the detail fallback and iframe src likewise (dashboard.js:840, 844). Data originates from parsed Gmail HTML and Bandcamp scrapes. Both a rendering bug (legit titles with `<` or `"`) and an XSS vector in an origin that can drive /load-credentials, /reset-caches, and the SSRF-capable /embed-meta. Same as SEC-7.
- Evidence: dashboard.js:756-769, 840, 844; pipeline.py:89; server.py:411-444
- Fix: build cells with createElement/textContent (renderFilters already does) or an esc() helper; whitelist http(s) for href/src.

### JS-2 · high · confirmed — Server-down modal: one transient health-check failure permanently blocks the UI; the localhost guard is dead code
One failed 4s health check shows an undismissable full-screen modal (dashboard.html:148-150) and `serverDownShown` latches so polling stops (dashboard.js:30, 133-134, 157) — no recovery even when the server returns. The intended local-host guard is inert: `apiHost` is a const computed from the hardcoded default before config load (dashboard.js:11-17; re-derivation at 48-55 never recomputes it), so the check at line 175 is always true and the comment at 177 describes unimplemented behavior. Same event as UX-15.
- Evidence: dashboard.js:11-17, 30, 132-138, 156-181; dashboard.html:148-150
- Fix: keep polling, auto-hide on recovery, require 2-3 consecutive failures, add a retry/reload affordance, derive apiHost after config.

### JS-3 · medium · confirmed — Interacting with the calendar mid-populate wipes the streaming log and re-enables the Populate button
Any calendar click during a stream runs `updateSelectionStatusLog`, which overwrites `populateLog.innerHTML` (dashboard.js:564) and resets the button's disabled/label state (dashboard.js:575-579). Clicking Populate again opens a second EventSource that the backend rejects, surfacing a spurious "failure" alert for a run that is actually progressing.
- Evidence: dashboard.js:559-579 vs 1418-1487; server.py:582-583
- Fix: an `isPopulating` flag; single owner for button state; selection-summary writes become no-op/append-only during a stream.

### JS-4 · medium · confirmed — 'N releases shown' label is erased moments after every page load
`fetchScrapeStatus` (fired un-awaited from initData, dashboard.js:1716) calls `updateHeaderRange()` with no count (dashboard.js:1587), and the null branch blanks the label (dashboard.js:606-608). Visible in 10b-populated-full-light.png (empty bottom status bar with 49 releases shown).
- Evidence: dashboard.js:592-609, 933, 1587, 1716; shots/10b-populated-full-light.png
- Fix: cache the last count in updateHeaderRange, or recompute internally.

### JS-5 · medium · confirmed — Label filter selections silently reset: unchecking the last 'show' box re-checks everything; date navigation wipes selections
`state.showLabels` is refilled with all labels when it empties (dashboard.js:476-478) and replaced wholesale when the visible label signature changes with the date range (dashboard.js:473-475, fed by 727). User filter state cannot survive normal navigation.
- Evidence: dashboard.js:457-479, 727
- Fix: store exclusions instead of inclusions; treat empty selection as explicit 'show none'.

### JS-6 · medium · confirmed — Starred releases lacking cached description/embed are re-fetched over the network on every table render; no in-flight dedupe
ensureEmbed short-circuits only when BOTH embed_url and description are set (dashboard.js:427-429), and renderTable calls it for every starred row (dashboard.js:786-788) — with /embed-meta never consulting its own cache (server.py:411-444), each such row triggers a live Bandcamp fetch on every sort/filter/toggle. No per-URL in-flight promise; schedulePreload arms duplicate timers (dashboard.js:914-928). See PERF-2 for the verified scope (fetch-failure/deleted pages are the forever-refetch population).
- Evidence: dashboard.js:426-455, 786-788, 914-928; server.py:411-444
- Fix: per-URL in-flight promise + negative caching; clearTimeout in schedulePreload; cache-first /embed-meta.

### JS-7 · medium · confirmed — Core interactions are keyboard/AT-inaccessible: mouse-only calendar, untrapped modals, click-only sort headers, silent status log
Calendar day cells are plain divs with click listeners only (dashboard.js:1235-1322); modals have no dialog role, focus trap, or Escape (dashboard.js:1012-1041, 373-402; dashboard.html:116-180); sort headers are bare `<th>`s (dashboard.js:950-963); the populate log has no aria-live (dashboard.html:102-110); expandable rows lack aria-expanded (dashboard.js:858-867).
- Evidence: dashboard.js:950-963, 1012-1041, 1235-1322; dashboard.html:90-95, 102-110, 116-180
- Fix: day cells as buttons in a role=grid with arrow keys; role=dialog + trap + Escape; th → button + aria-sort; aria-live=polite on the log.

### JS-8 · medium · confirmed — 'u' (mark unread) shortcut desyncs row visuals from state; contains an unreachable branch
The u handler (dashboard.js:878-891) clears the dot's read class but never restores the row's `unseen` class, diverging from the dot-click path (dashboard.js:906-909). The else branch creating a new .row-dot is unreachable. Escape/re-render row-close paths leave a stale `state.expandedKey` (dashboard.js:739-741, 853-856).
- Evidence: dashboard.js:878-891 vs 900-912, 739-741; dashboard.css:675-676
- Fix: one `setRowReadState(tr, release, isRead)` helper for both paths; clear expandedKey in closeOpenDetailRows.

### JS-9 · medium · confirmed — Load-credentials modal cannot be cancelled: X and backdrop still open the OS file picker
`hideLoadCredsModal` unconditionally calls `openLoadCredsFile()` (dashboard.js:380-385), and every dismissal routes through it (dashboard.js:386-392). Heavyweight consequence given the backend then blocks on the OAuth flow (UX-2). See also UX-16.
- Evidence: dashboard.js:367-392; dashboard.html:166-180
- Fix: only Continue opens the picker; X/backdrop just close.

### JS-10 · medium · partial — SSE populate lifecycle is brittle: transient stream errors alert 'failed' while the server keeps working; success is a full page reload
handleError is bound to the EventSource error event (dashboard.js:1445-1462, 1480), which also fires on transient disconnects; it closes the stream and alerts, while the server worker keeps running with POPULATE_LOCK released (server.py:585-620) — a second concurrent run is then possible. The done handler is `window.location.reload()` (dashboard.js:1481-1484), discarding sort/filters/scroll/log. (Correction: the button label IS restored correctly on error; "Populate" is only an empty-label fallback.) Same reload issue as UX-9.
- Evidence: dashboard.js:1433-1487; server.py:553-626
- Fix: distinguish terminal errors from blips; refetch /releases on done instead of reloading.

### JS-11 · medium · confirmed — Mark-as-seen over a visible range performs N full calendar rebuilds plus N unbatched POSTs
setViewed calls `renderCalendar('range')` per toggle (dashboard.js:216) with a per-cell scan of the whole releases array (dashboard.js:1254-1259); markVisibleRows loops it per visible row (dashboard.js:1122-1142) and fires one POST per row against an unlocked store. Quantified at scale in PERF-1; same cluster as ARCH-6.
- Evidence: dashboard.js:207-217, 1122-1142, 1254-1259; server.py:165-173
- Fix: precompute a date→unseen-count map per render; render once after the batch; bulk viewed-state endpoint.

### JS-12 · low, not verified — Leftover generality and dead code
Single-entry `calendars` map with a threaded type parameter (dashboard.js:1158-1160, 1209-1219, 1326-1334); `scrapeStatus.notScraped` written, never read, and a same-expression-twice `||` (dashboard.js:1582-1584, 418); input listeners on permanently hidden inputs that can never fire (dashboard.js:1680-1681; dashboard.html:58-61); performReset's hardcoded flags and dead local resets, with UI/server desync on the error path (dashboard.js:1043-1081); endpoints derived twice with stale `apiHost` (dashboard.js:9-21 vs 48-55); duplicated logic pairs (225-231 vs 767; 551-556 vs 614-628; 830-834); two settingsBtn click listeners (999, 1016); fetchScrapeStatus relies on an implicit server sort-order contract (1575-1578; session_store.py:121).
- Fix: inline the single calendar, delete dead code, derive endpoints once after config, extract shared helpers.

### JS-13 · low, not verified — default_theme config is dead: JS force-locks dark theme and hides the toggle for end users
With dev settings hidden and no saved theme, dark is forced and persisted as if user-chosen (dashboard.js:283-289); the theme checkbox is a hidden .dev-setting (dashboard.html:122-125; dashboard.js:68-73), so the shipped `default_theme:'light'` (server.py:345-348) does nothing and the light theme is unreachable. See also UX-18, ARCH-11.
- Fix: honor default_theme/prefers-color-scheme, expose the toggle, persist only on explicit user choice.

### JS-14 · low, not verified — All-or-nothing init and silent persistence: an auxiliary state-fetch failure hides the whole dashboard; state POST failures only console.warn
A failed viewed/starred fetch replaces the entire UI with the error bar even when /releases succeeded (dashboard.js:91-121, 1706-1720); persistViewedRemote/persistStarredRemote failures are silent, so toggles look successful but vanish on reload (dashboard.js:183-206).
- Fix: degrade gracefully on auxiliary fetch failure; surface persistence failures (revert or note in the log).

### JS-15 · low, not verified — Status log has two write protocols (textContent vs innerHTML); modal dismissal semantics differ between modals
innerHTML writes with `<br>` and inline color (dashboard.js:559-572) interleave with plain-text appends (dashboard.js:149-155, 1470-1475), flattening line breaks and leaking color; the max-results backdrop dismisses on any click including its own text (dashboard.js:400-402) unlike every other modal.
- Fix: single log append/replace API owning formatting via classes; reuse the shared backdrop-click pattern.

## Architecture

### ARCH-1 · high · confirmed — JSON store writes are load-modify-save with no locking; 'Mark as seen' realistically loses updates
Threaded server (server.py:247) + unsynchronized read-mutate-rewrite on every mutable store (server.py:282-287, 330-335, 208-228), while the frontend itself generates the concurrency (markVisibleRows fires one POST per visible row, dashboard.js:1122-1142). Shared tmp path can raise FileNotFoundError, papered over by a non-atomic fallback (server.py:165-173). POPULATE_LOCK releases on client disconnect while the worker keeps running (server.py:608-620). Cluster: PY-8, PERF-6, PERF-1.
- Evidence: server.py:247, 282-287, 165-173, 208-228, 608-620; dashboard.js:1122-1142, 207-217
- Fix: per-store locks, unique tmp names, worker-owned POPULATE_LOCK release, batch mark-seen endpoint.

### ARCH-2 · high · confirmed — Populate marks dates scraped before persisting releases: a mid-run crash silently and permanently loses those days
Same defect as PY-1, viewed at the state-machine level: mark-scraped at pipeline.py:114 vs persist at pipeline.py:123, with the never-re-fetch principle (session_store.py:229-236) converting transient failure into permanent silent loss. The three stores per persist are updated non-transactionally (session_store.py:209-212).
- Evidence: pipeline.py:110-123; session_store.py:209-236, 39-50; gmail.py:222-235
- Fix: persist per-range before marking scraped — a ~5-line reorder.

### ARCH-3 · high · partial — Uncaught populate-worker exceptions end the SSE stream with event:done; the silent-failure path is real but narrower than first claimed
Only GmailAuthError/MaxResultsExceeded are caught; the finally sentinel yields `event: done` and the client reloads (server.py:594-617; dashboard.js:1481-1483). Correction from verification: lines starting `ERROR:` ARE intercepted client-side (dashboard.js:1476-1478), so auth/search/download failures are surfaced; the failure-looks-like-success path applies to exceptions with no preceding ERROR line — parse-stage crashes (pipeline.py:110, no try/except in 22-56) and post-loop dedupe/persist failures — during which earlier ranges may already be marked scraped without persisted releases (ARCH-2). Same defect as PY-2.
- Evidence: server.py:591-618; pipeline.py:97-110; dashboard.js:1476-1484
- Fix: worker-level except-Exception → `event: error`; client refetches /releases instead of reloading.

### ARCH-4 · medium · confirmed — SSE carries only prose log strings, forcing the UI into English-substring sniffing
The client parses meaning out of sentences ('Maximum results' → modal, 'ERROR:' → handleError; dashboard.js:1465-1478); any wording change silently breaks UI behavior. The backend already computes everything a determinate progress bar needs (pipeline.py:70, 104; server.py:597).
- Evidence: server.py:560-569, 585-618; dashboard.js:1445-1484; pipeline.py:70-110
- Fix: typed JSON events ({log, progress, error, complete}) with a text field kept for migration; ~50 lines server, ~40 client.

### ARCH-5 · medium · confirmed — The 1725-line single IIFE is at a feature-cost (not performance) breaking point; do not build IMAP/multi-source on it as-is
Button state has multiple owners (dashboard.js:575-579 vs 1460), logic exists in duplicate (225-231 vs 767), endpoints are derived twice (9-21 vs 48-55), refresh is a page reload, and the data model has no source concept (util.py:33-52; releaseKey assumes Bandcamp URLs, dashboard.js:78-80).
- Evidence: dashboard.js:6-1724; util.py:33-52
- Fix: mechanical split into ~6 ES modules (no build step; app is same-origin-served, server.py:353-371); add a `source` field to construct_release now.

### ARCH-6 · medium · confirmed — Per-row work triggers whole-app re-renders: mark-all-seen is O(rows × releases) with N network calls and N calendar rebuilds
Same defect as JS-11/PERF-1 at the design level: renderCalendar per toggle (dashboard.js:216, 1254-1272), renderTable rebuilding all rows and listeners per render (dashboard.js:721-934), against the app's own stated volume assumptions (server.py:46).
- Evidence: dashboard.js:207-217, 1122-1142, 1254-1272, 721-934
- Fix: coalesce renders per microtask; batch the POST; precompute per-day unseen counts.

### ARCH-7 · medium · confirmed — SQLite (stdlib, one file) is warranted: simpler than the current six JSON stores plus two duplicate persistence layers
One bcfeed.db with WAL gives transactional multi-table updates (fixing ARCH-1/ARCH-2 at the storage layer), indexed range queries, and deletes ~150 lines of bespoke persistence — zero new dependencies. Not a substitute for the pipeline ordering fix. If JSON is kept for hand-debuggability, the correctness floor is: one lock, one store module, unique tmp files, per-range persistence.
- Evidence: paths.py:22-29; server.py:155-228; session_store.py:30-83, 116-126, 209-212
- Fix: storage.py with sqlite3, keeping existing store function signatures.

### ARCH-8 · medium · confirmed — server.py mixes a markdown engine, in-route scraping, a duplicate persistence layer, and a blocking OAuth flow
A 65-line markdown renderer (server.py:61-145), Bandcamp fetching inline in /embed-meta which never reads the cache it writes (server.py:411-444), duplicate store IO (server.py:155-228 vs session_store.py:30-83), and /load-credentials synchronously running interactive OAuth in the request thread with no timeout (server.py:545 → gmail.py:115-116). Blocks the cheapest wins (cache-first embed-meta, testable markdown).
- Evidence: server.py:61-145, 155-228, 411-444, 521-550; gmail.py:115-116
- Fix: extract docs.py; move fetch+cache into bandcamp.py with cache-first get_embed_meta; collapse JSON IO into one module; make /load-credentials save-and-return.

### ARCH-9 · medium · partial — Distribution is split-brained: version, Python version, and dependency pins each have multiple conflicting answers
Corrected picture: the Homebrew formula sources keinobjekt/bcfeed at tag v1.0-beta2 (consistent with the hardcoded 'bcfeed v1.0', dashboard.html:73), but the working repo's remote (ellienieuwdorp/bcfeed) has no tags and the version string is hand-maintained. Confirmed: three Python version stories (formula python@3.11, .python-version 3.10.19, SETUP.md:41 '3.10 or newer'); requirements.txt lists requests twice (lines 3, 7), uses the bs4 shim, pins nothing while the formula pins everything; gmail.py:42-44 retains a dead sys._MEIPASS credentials branch (encoding shipping an OAuth secret in a binary). paths.py's `Path(__file__).with_name` asset resolution (paths.py:30-35) would likely survive PyInstaller — fragility, not a guaranteed 500 (correction).
- Evidence: dashboard.html:73; requirements.txt:3,7; gmail.py:34-49; paths.py:30-35; SETUP.md:41; .python-version
- Fix: single VERSION constant surfaced via /config.json; fix requirements.txt; delete the _MEIPASS branch or add a shared resource_path(); tag releases in this repo.

### ARCH-10 · medium · confirmed — Zero tests, no CI, partial typing; import-time side effects block the cheapest tests
Same defect as PY-4 with the architecture remedy spelled out: BCFEED_DATA_DIR injection point in paths.py (3 lines) unlocks tmp-dir isolation for the whole persistence/pipeline layer; ~20 fixture-based tests plus one GitHub Actions job (ruff + pytest) is roughly a day of work.
- Evidence: no tests/ or .github/; paths.py:16-19; server.py:491-495; pipeline.py:38
- Fix: env-var data-dir override, fixtures for email/page parsing and markdown goldens, minimal CI.

### ARCH-11 · low, not verified — config.json is fake config: all flags are hardcoded server constants, and two contradict each other
GET /config.json returns literals (server.py:339-350); show_dev_settings requires a source edit; the shipped default_theme:'light' is unreachable because the frontend forces dark when dev settings are off (dashboard.js:285-290). `title` is never consumed; the one thing users might configure (port) is CLI-only.
- Fix: delete the dead/contradictory flags and JS branches; gate dev settings on an env var if ever needed.

## UI visual

### UI-1 · high · confirmed — Primary action button is unreadable in light mode (1.34:1; disabled ~1.06:1)
'Populate release list' is inline-styled white text on a 35%-alpha blue fill (dashboard.html:52); in light mode it computes to 1.34:1 (AA needs 4.5:1), and the disabled state (dashboard.css:86-92) makes the label literally invisible. Dark computes 8.70:1 — designed on dark, never checked on light.
- Evidence: dashboard.html:52; dashboard.css:86-92; shots 01-first-run-light.png, 06-populate-error-light.png vs 10-populated-dark.png
- Fix: solid accent fill via a .button-primary class; never white text on sub-50%-alpha fills.

### UI-2 · high · confirmed — Populated calendar-day numbers are illegible in light mode (1.26:1)
`.calendar-day.unseen-day .date-label` forces #fff on rgba(100,168,255,0.28) (dashboard.css:502-505) — ghost digits across the entire populated month in 10-populated-light.png.
- Evidence: dashboard.css:502-505; shots 10-populated-light.png vs 10-populated-dark.png
- Fix: theme-aware pill text routed through --accent; ≥4.5:1 in both themes.

### UI-3 · high · confirmed — Error feedback is visually broken: 1.29:1 error bar in light mode, and the captured error state shows nothing at all
Inline error-bar styling (dashboard.html:85) computes to 1.29:1 on light. The captured credential-less populate failure (06-populate-error-light.png, 06b-log-closeup.png) shows an empty Status box and no visible error anywhere. Interacts with UX-3.
- Evidence: dashboard.html:85; shots 06-populate-error-light.png, 06b-log-closeup.png
- Fix: semantic danger tokens per theme applied via a class; errors written to an always-visible surface.

### UI-4 · high · confirmed — Light theme is a half-ported skin of a dark-first design
Every sampled load-bearing pair passes dark and fails light (button 8.70 vs 1.34; day pills ~7 vs 1.26; log 6.70 vs 2.45; error bar 10.78 vs 1.29; CACHED 7.23 vs 3.65). Mechanical causes: white-alpha control fills that vanish on white (dashboard.css:74,124,406,477,492) and hardcoded dark-cyan/#64a8ff accent literals ignoring the light accent (dashboard.css:59,152,159,507-508,566,581-582).
- Evidence: dashboard.css:14-25, 74, 124, 406, 507-508; shots 02-empty-dashboard-light.png, 10-populated-light.png vs dark counterparts
- Fix: per-theme tokens (--control-bg, --accent-tint, --danger-*) replacing every hardcoded rgba literal — or declare dark-only for v1.

### UI-5 · medium · partial — Calendar 'populated' treatment is indistinguishable from 'selected'; the glow-ring/scraped styles are dead code
Selected days duplicate the in-range gradient exactly (dashboard.css:580-584 vs 506-508); in a populated+selected month every cell is a blue pill and most carry a red dot — a uniform wall of emphasis (10-populated-*.png). Correction: `.calendar-day.scraped` styles including the glow ring (dashboard.css:539-561) are dead — JS applies `unseen-day` (dashboard.js:1260), a misnamed class (the legend's 'Unseen' is the red dot).
- Evidence: dashboard.css:484-584; dashboard.js:1260; shots 10-populated-light.png vs 11-calendar-june-light.png
- Fix: one channel per state (fill = selected, dot = unseen, subdued mark = populated); delete dead/misnamed state classes.

### UI-6 · medium · confirmed — Populated table: chrome outshouts data — red dots on ~90% of rows, shouting CACHED badges, pseudo-zebra tints
The alarm-red unseen dot is the highest-chroma element on screen for the default state of new releases; unseen is double-encoded (dot + row tint, dashboard.css:675-677) yet destroyed on hover by a specificity accident (tr.data-row:hover, dashboard.css:681-683, later in order than .unseen). The 10px bold uppercase CACHED badge outranks album titles (13-starred-filter.png).
- Evidence: dashboard.css:103-115, 146-161, 675-683; shots 10-populated-light.png, 13-starred-filter.png
- Fix: single subtle unseen encoding; demote/relocate CACHED; `tr.data-row.unseen:hover` fix.

### UI-7 · medium · confirmed — Wireframe scaffolding shipped to production: dashed borders and hatch fills frame the whole sidebar
`.wireframe-panel` dashed border (dashboard.css:261-273), `.wireframe-body` diagonal hatch (dashboard.css:349-361), dashed `.detail-desc` (dashboard.css:721-733), plus a second hatch for the disabled overlay (dashboard.css:282-288). The single strongest 'generated/unfinished' signal on screen.
- Evidence: dashboard.css:261-273, 349-361, 721-733; shots 02-empty-dashboard-light.png, 11-calendar-june-light.png (scratchpad), 12-row-expanded-light.png
- Fix: standard 1px var(--border) + var(--surface) card treatment; delete the hatches; rename the classes.

### UI-8 · medium · confirmed — AI-smell inventory: neon body gradients, glow shadows, four icon systems, ALL-CAPS microcopy, version in H1, uniform hover-lift
Ambient pink/cyan radial gradients (dashboard.css:33-34, 41-45); glow shadows (dashboard.css:528, 553, 559 — note 539-561 are dead per UI-5, the dot glow at 528 is live); ⚙️ emoji + text '?' + unicode carats + inline SVG star in one viewport (dashboard.html:22, 31, 79-80; dashboard.js:759); hardcoded ALL-CAPS literals (dashboard.html:39, 51, 76); 'bcfeed v1.0' in the H1 (dashboard.html:73); translateY hover-lift on everything (dashboard.css:83, 130, 565).
- Evidence: as cited; shots 10-populated-dark.png, 02-empty-dashboard-light.png
- Fix: kill gradients/glows; one SVG icon system; sentence-case microcopy via one class; version to Settings/about.

### UI-9 · medium · confirmed — Status log: sole feedback channel styled as low-contrast fake links in a fixed 200px box
Log lines hardcode #64a8ff (dashboard.js:565, 570) — 2.45:1 on light, and blue proportional text reads as hyperlinks that aren't. The box is fixed 200px whether empty or full (dashboard.css:441-450), reading as a broken form field when empty and stealing ~25% of table height when populated.
- Evidence: dashboard.js:565, 570; dashboard.css:440-459; shots 06b-log-closeup.png, 18b-log-closeup.png (scratchpad), 02-empty-dashboard-light.png
- Fix: 12px monospace in --muted with semantic warn/error colors; auto-height to a max; collapsed when empty.

### UI-10 · medium · confirmed — Settings-modal generation is the right visual direction built the wrong way (inline styles), and its sibling modals diverge
The Settings panel is the calmest surface in the app (03-settings-light.png) but is almost entirely inline-styled HTML with spacer divs and a fourth ad-hoc red (dashboard.html:130-144), while the max-results/server-down modals abandon shared radius/backdrop/z conventions (dashboard.css:585-605, 806-826).
- Evidence: dashboard.html:116-147; dashboard.css:585-605, 744-783, 806-826; shots 03-settings-light.png vs 10-populated-light.png
- Fix: adopt the settings modal's restraint as the target language; extract .modal/.modal-actions/.button-danger classes; retrofit the rest.

### UI-11 · medium · confirmed — Two competing accent blues (three in light mode) and four unrelated reds split the color system
Token accent is #52d0ff/#1f7aff, but the most important actions use untokenized #64a8ff (dashboard.html:52; dashboard.css:244-245, 503, 526-528; dashboard.js:565). Four reds (#ff5f5f, #ff6b6b, #b83a3a, #ffc5c5) with no danger token.
- Evidence: as cited; shot 10-populated-light.png
- Fix: one accent token + derived tint scale; one semantic danger token; delete the #64a8ff family.

### UI-12 · medium · partial — Calendar days and sort headers are completely unreachable by keyboard; invisible-but-interactive read dots
Correction from verification: rows keep browser-default focus rings and full keyboard support, so the gap is narrower than 'zero focus styles' — but calendar day divs (dashboard.js:1240-1281) and sortable `<th>`s (dashboard.js:949-958) have no tabindex or key handling at all, and the read dot at opacity:0 keeps a live cursor:pointer click target (dashboard.css:113-115; dashboard.js:900-911).
- Evidence: dashboard.css:113-115, 484, 653-664; dashboard.js:949-958, 1240-1281
- Fix: :focus-visible token; calendar days as real buttons; visibility:hidden (or no pointer semantics) for the read dot.

### UI-13 · medium · confirmed — CACHED badge fails AA in light mode (3.65:1 at 10px) while broadcasting internal plumbing
10px 700-weight uppercase accent text on hardcoded cyan tint (dashboard.css:146-161); below the 4.5:1 requirement in light, clashes with the light accent, and surfaces cache-layer state next to album titles. Vocabulary aspect covered in UX-6.
- Evidence: dashboard.css:146-161; shots 10-populated-light.png, 13-starred-filter.png
- Fix: gate behind the dev 'show cached' setting; if kept, ≥11px non-bold theme-aware tint.

### UI-14 · medium · confirmed — At 1000px the Date column is clipped mid-header and mid-value
Fixed 360px sidebar + inline table min-widths (dashboard.html:90-95) exceed a half-screen window; the single 900px breakpoint (dashboard.css:827-838) doesn't fire at 1000px. DATE truncates to 'DAT' and values to '2026…' (19-narrow-1000px.png) with no scroll hint.
- Evidence: dashboard.html:90-95; dashboard.css:168-174, 827-838; shot 19-narrow-1000px.png
- Fix: intermediate ~1100px breakpoint narrowing the sidebar and relaxing Title min-width; protect the date column.

### UI-15 · medium · confirmed — Links have no resting affordance: 'Show setup instructions' looks like plain text
`a.link` is body-colored with a transparent underline until hover (dashboard.css:46-60); the first-run escape hatch to setup docs (dashboard.html:175) is visually identical to paragraph text (04-load-creds-modal-light.png). Same for table release links (dashboard.js:765-767).
- Evidence: dashboard.css:46-60; dashboard.html:175; shot 04-load-creds-modal-light.png
- Fix: resting accent color or persistent underline.

### UI-16 · low, not verified — Geometry and rhythm entropy: 6 radii, 3-vs-4px spacing systems, 3 inline-only button sizes, 11 font sizes
Radii from 6px to 999px with buttons never matching their panels; the calendar cluster runs a 3/5/7px rhythm against 4/8/12/16 elsewhere (dashboard.css:378-435); button sizing exists only as inline styles in three variants (dashboard.html:46, 52, 54-55, 132).
- Fix: 4px spacing scale, two radii (8px control / 12px surface), 4-step type scale as tokens.

### UI-17 · low, not verified — Legend and dead chrome: mismatched legend swatches, undefined --header-bg, dead CSS rules
Legend swatches are ad-hoc inline spans at different sizes than the calendar dots they explain (dashboard.html:40-44); `header` references a never-defined --header-bg (dashboard.css:64); dead rules (.pill, .inline-link, .calendar-row) and the duplicated selected/in-range gradient confirm CSS/DOM drift.
- Fix: render the legend with the calendar's own components; define or delete --header-bg; purge dead rules.

### UI-18 · low, not verified — Disabled and enabled buttons are nearly indistinguishable in the sidebar action stack
opacity:0.45 + grayscale disabled treatment (dashboard.css:86-92) lands next to ghost buttons that are already white-on-white in light mode; in 10-populated-light.png 'Preload release data' reads inert while 'Mark as seen' reads active with no pixel-level rationale.
- Fix: visible fill for enabled buttons in both themes; reserve reduced opacity for disabled.

## UX flow

### UX-1 · high · confirmed — First value is ~15 manual steps away, and the in-app funnel dead-ends at an unordered Settings button list
Install → first release row realistically takes 20-30 minutes through a Google Cloud console gauntlet, and the in-app funnel lands on a flat Settings list of four mostly-destructive buttons with no sequencing and the setup link buried one modal deeper (dashboard.html:154-180; 03-settings-light.png). A first-run user is one misclick from 'Clear cache' (UX-4).
- Evidence: shots 01-first-run-light.png, 03-settings-light.png, 04-load-creds-modal-light.png; dashboard.js:1026-1031; GMAIL_SETUP.md steps 1-7; SETUP.md:20-33
- Fix: first-run wizard (1. Google setup guide inline → 2. Load credentials → 3. one-click 'fetch last 30 days'); destructive actions into a separate danger zone.

### UX-2 · high · confirmed — Credential upload blocks on a browser OAuth flow the UI never mentions, with no timeout and no recovery
POST /load-credentials synchronously runs `run_local_server` (server.py:544-546; gmail.py:115-116): a Google consent tab opens unannounced (including the 'unverified app' interstitial), and if the user misses or closes it, the POST blocks forever and the Load button never re-enables. The most fragile onboarding step fails silently.
- Evidence: server.py:544-546; gmail.py:115-116; dashboard.html:166-180; dashboard.js:332-399; shot 04-load-creds-modal-light.png
- Fix: warn in the modal about the sign-in window and interstitial; run auth off-thread with a timeout and streamed state (waiting/completed/abandoned).

### UX-3 · high · confirmed — Populate failures surface only as a blocking alert() plus a log line that other code freely overwrites; observed end state is an empty Status box
handleError alerts and appends to a log that updateSelectionStatusLog rewrites wholesale (dashboard.js:1445-1461, 559-572). The captured credential-less populate ends at '0 releases shown', empty Status box, no persistent error anywhere (06-populate-error-light.png). The error text itself is developer vocabulary ('Gmail token missing') with no link to the Settings it references.
- Evidence: shots 06-populate-error-light.png, 06b-log-closeup.png (scratchpad); dashboard.js:1445-1461, 559-572; server.py:560-580
- Fix: persistent inline error banner with an 'Open Settings' action; never clobber an unacknowledged error.

### UX-4 · high · confirmed — 'Clear cache' silently deletes the user's stars and seen-state with no confirmation
performReset hardcodes clear_cache, clear_viewed, clear_starred to true and fires on a bare click (dashboard.js:1043-1082). One click irreversibly destroys the only state the user personally created. The TODO history shows this coupling was once a fixed bug, reintroduced deliberately.
- Evidence: dashboard.js:1043-1082; shot 03-settings-light.png; TODO.rtf DONE list
- Fix: confirmation dialog enumerating what is deleted; split 'clear downloaded data' from 'reset stars and seen history' (backend flags already separate — see PY-13).

### UX-5 · high · confirmed — Empty dashboard says 'No releases match the current filter' — factually wrong and CTA-free at the moment of highest drop-off
The single generic empty message (dashboard.html:100) serves both 'never fetched anything' and 'filters exclude everything'. Post-credentials, nothing cues the next step: empty Status box, blank panel header, and the one required action expressed only as a sidebar button (01/02 screenshots).
- Evidence: dashboard.html:100; shots 01-first-run-light.png, 02-empty-dashboard-light.png
- Fix: branch the empty state — never-fetched gets a directive CTA ('pick dates, then Populate') with an inline button; filtered-out keeps the current message plus 'clear filters'.

### UX-6 · medium · confirmed — Every surface speaks the implementation's language (populate, preload, cache, token, scraped days)
Full inventory in the audit: Populate/Preload buttons and tooltips, CACHED badges, 'not yet populated' status text, 'Populated' legend, 'Gmail token missing', raw pipeline log verbatim ('Parsing messages...', 'Checking for releases with identical URLS...'), the max-results modal, and Settings' Clear cache/credentials. The log also leaks the end-exclusive Gmail query (user selects to 06-30, reads 'to 2026-07-01' — pipeline.py:88-104), which reads as an off-by-one bug.
- Evidence: dashboard.html:52-53, 152, 160; dashboard.js:561-585, 634-648, 767; pipeline.py:88-120; shots 10-populated-light.png, 18b-log-closeup.png (scratchpad)
- Fix: one rename pass (Populate → 'Get releases'; Preload → 'Load players'; 'populated' → 'checked'; humanized log lines with user-facing date ranges).

### UX-7 · medium · confirmed — The Status log is the primary feedback organ, doing five jobs that all have proper UI primitives, from the wrong corner of the screen
It carries selection summary, the only workflow tutorial, live populate progress, preload progress, credential results, and errors (dashboard.js:559-572, 1463-1479, 1517-1534, 316-355) — rendered bottom-right, ~900px from the sidebar controls it describes, with no aria-live. Each job wants a real primitive: inline summary beside the calendar, progress bar, toasts, banners; keep the raw log as a collapsible 'Details' debug view.
- Evidence: shots 10-populated-light.png, 18b-log-closeup.png (scratchpad); dashboard.html:102-110
- Fix: rehouse each job into its primitive; keep the log behind the existing collapse toggle.

### UX-8 · medium · confirmed — Label filter uses two unlabeled checkbox columns with hidden modal behavior ('show' vs 'show only')
No column headers (15-label-filters.png); checking any 'show only' box silently disables/grays the entire other column (dashboard.js:481, 493), which reads as breakage; no all/none affordance; unchecking the last 'show' box silently re-checks everything (dashboard.js:476-478; see JS-5). The most confusing single control in the app.
- Evidence: dashboard.js:457-531; shot 15-label-filters.png
- Fix: one checkbox column + per-row 'only' link on hover, plus All/None.

### UX-9 · medium · confirmed — Populate ends in a full page reload with no outcome summary
`window.location.reload()` on done (dashboard.js:1481-1484) discards scroll, sort, filters, expanded row, and the log the user was reading, and never answers 'how many new releases?'. Same mechanism as JS-10.
- Evidence: dashboard.js:1481-1484; shots 18-populate-success-log.png vs 10-populated-light.png
- Fix: refetch /releases + scrape-status, re-render in place, toast 'Added N releases for <range>'.

### UX-10 · medium · confirmed — 'Mark as seen/unseen' sits under 'SELECTED DATE RANGE:' but actually operates on currently rendered (filtered) rows
markVisibleRows iterates the DOM (dashboard.js:1122-1142), so active label/starred/unseen filters silently change scope; the label carries no object and there is no confirmation or undo for flipping a month of unread state.
- Evidence: dashboard.html:54-55; dashboard.js:1122-1142; shot 10-populated-light.png
- Fix: name the scope ('Mark 17 shown as seen'), move out of the date-range group, add undo toast.

### UX-11 · medium · partial — Preload is an uncancellable multi-minute sequential loop whose only feedback is log lines
One awaited /embed-meta fetch per release (dashboard.js:1503-1534), a few seconds each per README.md:43 — minutes for a month, with no progress bar, ETA, or cancel. Correction from verification: interruption is low-cost, not a lost commitment — each embed persists server-side immediately (server.py:227-228) and a re-run resumes incrementally; the fix needed is visibility and control, not crash-safety.
- Evidence: dashboard.js:1490-1540; README.md:37-47
- Fix: progress bar with count + Cancel (AbortController + loop flag); optionally 2-3× concurrency with politeness delays.

### UX-12 · medium · confirmed — A populated range can never be re-checked; the only 'refresh' path is the data-destroying Clear cache
Once all days are marked scraped the button is permanently disabled ('Release list populated', dashboard.js:575-578); the append-only-per-day model is documented only in the README, never at the disabled button that raises the question. Interacts badly with PY-1 (interrupted populates leave gaps that can never be refilled).
- Evidence: dashboard.js:575-578, 1044-1046; README.md:30
- Fix: low-key 'Re-check this range' action clearing scrape-status for just those days; URL dedupe already exists.

### UX-13 · medium · confirmed — The calendar's three encodings (selected, populated, unseen) share one blue-and-dot vocabulary; data gaps are the least salient cells
Not-yet-populated days — the exact answer to 'where are my gaps?' — are the quietest cells on the grid, while the legend's 'Unseen' collides with the 'SHOW ONLY: Unseen' filter term and the internal class is misnamed `unseen-day` (dashboard.js:1260-1262). The calendar-as-coverage-map idea itself is the app's best IA and must be kept.
- Evidence: shots 10-populated-light.png, 15-label-filters.png; dashboard.js:1242-1272; dashboard.html:38-45
- Fix: distinct treatment per state with unchecked-inside-selection loudest; rename legend terms ('Checked', 'Has unlistened releases').

### UX-14 · medium · confirmed — Keyboard shortcuts exist (arrows, Enter/Space, s, u, Escape) but are documented nowhere
Implemented at dashboard.js:852-898; zero mentions in README/SETUP/GMAIL_SETUP or the UI. The fastest triage loop in the app is a secret.
- Evidence: dashboard.js:852-898; grep of docs returns nothing; dashboard.html:80
- Fix: one-line hint under the table or a shortcuts section behind the '?' button.

### UX-15 · medium · confirmed — Server-down modal is undismissable, permanently latches, and its advice cannot restore the page
Same event as JS-2, user-facing consequence: after restarting the server as instructed, the modal never clears (polling stopped, no reload button), bricking the tab until the user guesses to reload — against the product's known 'keep the Terminal open' Achilles heel (SETUP.md:17, 33, 49).
- Evidence: dashboard.html:148-150; dashboard.js:133-134, 156-182, 1699-1700
- Fix: keep polling, auto-recover, add an explicit Reload button.

### UX-16 · low, not verified — Load-credentials modal: forced file picker on cancel, a typo, and its most important link hidden
All three dismissal paths open the OS file picker (JS-9); body copy reads 'This must downloaded from Google Cloud.' (dashboard.html:173, visible in 04-load-creds-modal-light.png); 'Show setup instructions' renders as plain text (UI-15).
- Fix: true cancel; fix typo; style the setup link as a link/secondary button.

### UX-17 · low, not verified — The 'Today' button selects yesterday, and today's exclusion is never explained
Today is deliberately unselectable (commit 598a9dd; dashboard.js:1170-1176) but the button labeled 'Today' jumps to yesterday (dashboard.js:1371-1387) and the grayed-out today cell carries no explanation (01-first-run-light.png).
- Fix: rename to 'Latest'/'Yesterday'; tooltip on today's cell ("Today's emails are still arriving").

### UX-18 · low, not verified — End users get a forced dark theme and a Settings panel containing nothing but destructive actions
With dev settings hidden, dark is forced with no prefers-color-scheme support (dashboard.js:68-77, 285-303; see JS-13/ARCH-11), so the shipped Settings modal contains exactly four destructive/plumbing buttons and zero preferences (03-settings-light.png).
- Fix: ship the working theme toggle, default to prefers-color-scheme, group Settings into Preferences / Credentials / Danger zone.

## Security and privacy

### SEC-1 · high · confirmed — Server binds 0.0.0.0, exposing the entire unauthenticated API to the whole LAN
make_server("0.0.0.0", ...) (server.py:247) and find_free_port binding "" (server.py:256, 259), with no auth/Origin/Host gate on any route: any LAN peer can read release history, wipe caches, delete the Gmail token, upload rogue credentials, drive populate, and use /embed-meta as an SSRF proxy. bcfeed.py:22 only ever opens localhost, so nothing needs the LAN bind.
- Evidence: server.py:247, 256, 259; bcfeed.py:22
- Fix: bind 127.0.0.1 in both places — one line.

### SEC-2 · high · confirmed — /embed-meta is an unauthenticated SSRF proxy with no scheme/host allowlist
requests.get on the raw client-supplied URL with redirects followed (server.py:415-426); error bodies form a port open/closed/filtered oracle, and internal pages' meta descriptions are extracted and returned verbatim (bandcamp.py:51-55). Also poisons embed_cache.json with arbitrary keys (server.py:439). Bounded to network SSRF (requests has no file:// adapter).
- Evidence: server.py:415-426, 439; bandcamp.py:51-55
- Fix: allowlist https + *.bandcamp.com, resolve-and-block private/link-local IPs, disable redirects, cap response size.

### SEC-3 · high · confirmed — App requests FULL Gmail scope (read/send/delete) while docs and privacy page promise read-only
gmail.py:89 hardcodes `https://mail.google.com/` while SETUP.md:106 and GMAIL_SETUP.md:53 instruct registering gmail.readonly and privacy.md promises read-only. The app only calls messages().list/get (gmail.py:130, 136, 160). A documented-behavior violation that over-provisions an unencrypted token; one-string fix (existing tokens must be re-issued).
- Evidence: gmail.py:89, 130, 136, 160; SETUP.md:106; GMAIL_SETUP.md:53; privacy.md
- Fix: scope → `gmail.readonly`; document re-auth.

### SEC-4 · medium · confirmed — CORS ACAO:* plus no Host validation lets any website drive the local API and enables DNS-rebinding reads
`_corsify` sets Access-Control-Allow-Origin:* on all JSON routes and SSE (server.py:148-152, 564, 623); no route validates Host/Origin, and /config.json reflects request.host_url (server.py:341). CSRF state-mutation (reset-caches, clear-credentials, load-credentials as a CORS-simple multipart POST) plus rebinding reads of /releases and state. Verification nuance: ACAO:* enables the cross-origin reads; the CSRF writes fire regardless of CORS. Compounded by SEC-1.
- Evidence: server.py:148-152, 341, 564, 623
- Fix: drop ACAO:*, validate Host against localhost:port, require a custom header or CSRF token on mutating routes.

### SEC-5 · medium · confirmed — OAuth token stored as unencrypted pickle with full-mailbox scope and default file perms
pickle.load/dump for Google credentials (gmail.py:97-99, 119-120, non-atomic, default umask); pickle deserialization is an RCE anti-pattern (defense-in-depth here), and the concrete harm is an unencrypted full-scope token readable by other local accounts. Compounds SEC-3.
- Evidence: gmail.py:97-99, 119-120; server.py:539
- Fix: Credentials.to_json(), chmod 0600, atomic write.

### SEC-6 · medium · confirmed — /load-credentials accepts any file, overwrites credentials, and blocks the request thread on a human OAuth flow
No validation that the upload is a Google client-secret JSON; deletes the existing token; synchronously runs run_local_server with no timeout (server.py:521-550; gmail.py:116). Reachable cross-origin as a CORS-simple request (SEC-4): a malicious page can brick the user's auth and pop an unexpected consent window. Verification nuance: the server is threaded, so the block consumes a thread per request rather than the sole worker.
- Evidence: server.py:521-550; gmail.py:116
- Fix: validate the JSON shape before saving; run auth off-thread with a timeout; gate behind same-origin checks.

### SEC-7 · medium · confirmed — Release fields injected via innerHTML without escaping (stored injection from third-party email content)
Same defect as JS-1, security framing: title/artist/page derive from get_text()/regexes over third-party-authored Bandcamp email HTML (gmail.py:272-292), get_text() un-escapes entities, and innerHTML executes img/svg onerror payloads in the origin with full API access. Niche (requires following a malicious artist/label) but genuine. Note: the SSE populate-log path is safe — textContent at dashboard.js:1473.
- Evidence: dashboard.js:756-769, 840; gmail.py:272-292
- Fix: escape or DOM-build all release fields; validate release.url as http(s).

### SEC-8 · low, not verified — Hand-rolled markdown renderer allows javascript: link hrefs on doc pages
Text segments are html.escaped, but the link regex (server.py:71) and _rewrite_doc_link (server.py:54-58) pass hrefs through without scheme filtering into `{{ body|safe }}` (templates/docs.html:72). Low risk while docs are repo-shipped; cheap to close.
- Fix: reject non-http(s)/mailto schemes in _rewrite_doc_link and the autolink regex.

### SEC-9 · low, not verified — Error responses leak absolute filesystem paths and raw exception text to clients
/releases, static-asset 500s, doc serving, /embed-meta, /clear-credentials, /load-credentials all return f-strings with exception text or absolute paths (server.py:313, 356, 363, 370, 377, 426, 518, 550) — username/path disclosure to any LAN peer under SEC-1 and the oracle for SEC-2.
- Fix: generic client messages, details to the server log; 404 (not 500) for missing files.

## Performance

### PERF-1 · high · confirmed — 'Mark as seen' does a full calendar re-render per row plus N racing POSTs — multi-second freeze and lost viewed-state at scale
Benchmarked at 5,000 releases: one calendar scan = 9.8 ms; marking 300 visible rows ≈ 3 s of blocked main thread (500 rows ≈ 5 s) plus hundreds of DOM grid rebuilds, while up to 6 concurrent unlocked load-modify-write POSTs silently drop marks that reappear as unseen after reload. The one interaction combining the O(N) scan, full re-render, and the write race into user-visible failure. Cluster: JS-11, ARCH-6, ARCH-1.
- Evidence: dashboard.js:1122-1140, 207-217, 1253-1259; server.py:282-287, 247; calbench.js at 5k releases
- Fix: batch endpoint (urls list) or server-side lock; update state directly and render once at the end.

### PERF-2 · medium (downgraded from high on verification) · partial — Releases whose embed fetch fails are re-fetched from bandcamp.com forever; /embed-meta never reads its own cache
Correction from verification: live Bandcamp pages always yield a description (auto-generated 'released <date>' credits + og:description), so the original 'descriptionless pages refetch forever' population is actually fetch/extraction failures — deleted/404 pages or pages without bc-page-properties — which cache nothing and are refired on every starred-row render (dashboard.js:787), hover (917-927), and preload run (guard at 1506), permanently inflating failure counts. Independently confirmed: /embed-meta does a full page download + double BeautifulSoup parse (~204 ms measured) + whole-file 7 MB cache rewrite (38 ms measured) per call with no cache read (server.py:411-444), and the unlocked read-modify-write (server.py:207-228) can drop entries under the per-starred-row burst. Preload itself is sequential, not concurrent.
- Evidence: dashboard.js:426-455, 786-788, 1506; server.py:411-444, 207-228; bandcamp.py:23-58; benchmarks (204 ms double-parse, 38 ms/7 MB rewrite)
- Fix: negative-cache failures (description:"" / fetched flag), cache-first /embed-meta, per-URL in-flight dedupe.

### PERF-3 · medium · confirmed — Preload is strictly serial at ~1 s/release with a full cache rewrite per item — 200 releases takes 2-4 minutes and writes ~1.4 GB at 5k-entry cache size
Per item: bandcamp.com GET (0.3-0.8 s) + two full parses (~204 ms) + whole-file embed_cache.json rewrite (38 ms at 7 MB). No 429/Retry-After handling; per-item rewrite cost grows quadratically with library size.
- Evidence: dashboard.js:1490-1540; server.py:411-444, 201-228; bandcamp.py:11-58; perfbench.py
- Fix: parse once (one soup for meta+description), batch cache flushes, modest concurrency with 429 handling.

### PERF-4 · medium · confirmed — Default date filter spans the entire dataset: first paint builds every row with 8 listeners each, repeated on every interaction
setDefaultDateFilters uses min→max of all dates (dashboard.js:1542-1555); renderTable wipes and rebuilds all rows with innerHTML parse + 8 addEventListener calls each (dashboard.js:721-934) — on the order of 1-3 s at 5,000 rows, re-run on every filter/sort/toggle.
- Evidence: dashboard.js:721-934, 1542-1555, 1190-1191, 494-516
- Fix: default the range to the most recent month (or virtualize); event delegation on tbody.

### PERF-5 · medium · confirmed — /releases ships the entire enriched library on every page load — 7.6 MB at 5k preloaded releases, re-downloaded after every populate via full page reload
Full flatten + full embed-cache overlay including all descriptions (server.py:291-314; session_store.py:116-126), fetched with cache:no-store (dashboard.js:124), then re-downloaded wholesale by the post-populate reload (dashboard.js:1481-1484). Descriptions — the bulk of the payload — are only needed in the expanded row and are already lazily fetchable.
- Evidence: server.py:291-314; session_store.py:116-126; dashboard.js:122-131, 1481-1484; perfbench.py (7.61 MB vs 1.20 MB, 39 ms/request)
- Fix: omit descriptions from /releases; refetch instead of reload after populate; optional date-range param.

### PERF-6 · medium · confirmed — Every mutation rewrites an entire JSON store with no locking — O(library) I/O per toggle and lost updates under frontend-generated concurrency
Measured: viewed toggle 3 ms/307 KB (fine singly); embed-cache write 38 ms/7 MB (not fine at per-request frequency). The frontend routinely creates the racing writers (markVisibleRows bursts, un-deduped ensureEmbed calls). Storage-layer view of the ARCH-1/PY-8 cluster; release_cache's once-per-populate rewrite is explicitly fine (see not-issues).
- Evidence: server.py:155-228, 282-287; session_store.py:45-50, 185-212; perfbench.py
- Fix: one lock per store or SQLite (ARCH-7); batch embed writes; client-side in-flight dedupe.

### PERF-7 · medium · confirmed — search_messages downloads every Gmail result page before the max_results cap is checked
Pagination runs to exhaustion without passing maxResults (gmail.py:128-139); the cap is enforced afterward (pipeline.py:95-96), discarding everything. A first-run backlog over the 2000 default (server.py:46) burns ~50 sequential list calls (~15-25 s) just to show the max-results modal — precisely the heavy-user first-run scenario. (Also flagged in the py-quality low list; single issue.)
- Evidence: gmail.py:128-139, 151-197; pipeline.py:95-96; server.py:46
- Fix: pass maxResults to list() and break pagination as soon as the cap is exceeded.

### PERF-8 · low, not verified — Hover-prefetch has no in-flight dedupe: hover + click + star can triple-fetch the same Bandcamp page, each rewriting the full embed cache
The 200 ms debounce cancels only the timer, not in-flight fetches (dashboard.js:914-928, 426-455); 2-3 concurrent identical /embed-meta requests each do an independent fetch + parse + whole-file rewrite. Bounded by dwell time (~2-3 req/s max); amplification comes from the duplicate multiplier and racing writes. Overlaps JS-6/PERF-2.
- Fix: Map<url, Promise> in ensureEmbed; cache-first /embed-meta.

## Investigated, not issues

Concerns raised in the audit brief or by finders that verification explicitly cleared. Recorded so nobody re-litigates or "fixes" them.

- **SSE log injection into the DOM (XSS via populate log)** — not a live vector: the SSE handler renders server log lines via textContent (dashboard.js:1473); only client-built, date-input-constrained strings hit innerHTML (dashboard.js:564, 571).
- **release_cache.json full-rewrite cost** — happens once per populate run (pipeline.py:123), not per batch, and 5k releases serialize to ~1.5 MB even with indent=2; a non-issue.
- **Quadratic dedupe_by_url inside persist_release_metadata** — quadratic only per day-bucket, and day buckets are tiny; fine at any realistic scale.
- **Single viewed/starred toggle write cost** — 3 ms of file I/O at 5k viewed URLs; invisible. Only the bulk-concurrent case is a problem (PERF-1).
- **Single renderCalendar/setViewed re-render** — ~10 ms + a 49-node rebuild; imperceptible in isolation. Only per-row loops are a problem (JS-11/ARCH-6).
- **/releases server-side latency** — 39 ms per request even fully enriched; the real concern is payload-size trajectory and reload-after-populate (PERF-5), not current latency.
- **SSE missing keep-alive** — fine for this deployment: same-machine localhost, no proxy, browsers do not idle-out silent EventSource connections, longest silent gap is tens of seconds. (The lock-released-on-disconnect problem is separate and real — PY-8/JS-10.)
- **5-second health poll and per-request config fetch** — trivial overhead; not worth changing.
- **Secrets in the repo** — credentials.json/token.pickle are properly gitignored and live outside the repo in the OS data dir; nothing sensitive is tracked.
- **Raw-HTML injection through the markdown renderer** — text segments are html.escape'd, blocking it; only the link-scheme gap remains (SEC-8).
- **SSRF reading local files via /embed-meta** — requests has no file:// adapter, so SEC-2 is bounded to network SSRF (still high for network targets).
- **PyInstaller assets guaranteed to 500** — refuted detail of ARCH-9: `Path(__file__)` resolution likely works under _MEIPASS; the debt is fragility and dead code, not certain breakage.
- **Calendar glow ring on populated/selected days as a live style** — the `.calendar-day.scraped` rules (dashboard.css:539-561) are dead code; JS applies `unseen-day` (dashboard.js:1260). Delete rather than restyle (UI-5). The unseen-dot glow (dashboard.css:528) is live (UI-8).
- **Populate button restored with the wrong label on stream error** — refuted detail of JS-10: `original` is captured before the 'Populating…' swap; 'Populate' is only an empty-label fallback.
- **'Descriptionless Bandcamp pages are refetched forever'** — corrected in PERF-2: live pages always yield a description (auto-generated credits/og:description); the forever-refetch population is failed/deleted pages, and the cache-first + negative-cache fix still applies.
