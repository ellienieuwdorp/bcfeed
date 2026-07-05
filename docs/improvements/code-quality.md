# Code-quality remediation plan

Scope: the confirmed **py-quality** and **js-quality** findings from the 2026-07-05 verified audit
(`docs/audit/process.md` describes the process; finding IDs `PY-n` / `JS-n` and the low-severity
clusters are from the audit results). Where a finding is co-owned by another dimension
(architecture, security, performance, UX), the overlap is cross-referenced and the primary owner
noted — implement once, in one place.

Numbering: `CQ-n` IDs are stable; do not renumber. Audit finding IDs (PY-n/JS-n/etc.) are defined
in `docs/current-state/known-issues.md`.

Sizes: **S** ≤ ~1 h, **M** = half-day, **L** = 1+ days.

Product constraint (applies throughout): bcfeed is a calm, local, single-user utility. Fixes here
must not add frameworks, build steps, or dependencies beyond dev-only tooling (pytest, ruff,
prettier). Preserve the working strengths: dense sortable table, calendar coverage map,
star-triggers-preload, keyboard shortcuts, local-first privacy.

---

## 1. Quick wins (mechanical, low-risk)

Safe to land immediately, individually, with no test prerequisite. Each is a small diff with an
unambiguous correct answer.

### CQ-01 — Delete Python lint-level dead weight (S)
Refs: py-quality low-severity cluster.
- **What:** Remove: unused `import os` (gmail.py:2); dead decode block `s = email_text; try: s = s.decode()...` where `s` is never used again (gmail.py:211-215); unreachable str-shaped email fallback in `construct_release_list` (pipeline.py:28-30 — it would crash at line 34 anyway and `get_messages` only returns dicts); uncalled `mark_dates_not_scraped` (session_store.py:159-165); f-string with no placeholder (pipeline.py:78). Rename variables shadowing builtins (`format`, `id`, gmail.py:151, 159). Replace `type(exc) == HttpError` with `isinstance` (gmail.py:141, 145).
- **Why:** Dead and misleading code costs more than it weighs: every future reader (including the audit itself) must disprove it before trusting the live paths. All of these were verified unreachable or inert; deletion carries zero behavior risk and shrinks the surface every later fix touches.
- **Acceptance:** `grep` finds none of the listed symbols/blocks; `ruff check` (CQ-50) passes F401/F841/F541 with no per-line ignores; app populates and renders as before.

### CQ-02 — Fix untruthful error messages (S)
Refs: PY-9 (message part), py-quality low cluster.
- **What:** The Gmail 429 handler tells the user to "Try reducing batch size using argument --batch" — no such flag exists (gmail.py:169; bcfeed.py defines only `--port`/`--no-browser`). Replace with honest text ("Gmail rate limit hit — wait a minute and try again"). Also fix `launch_dashboard`'s docstring, which claims it starts the server when it only opens a browser tab (bcfeed.py:17-23).
- **Why:** An error message is an instruction to a user at their most confused; instructing them to do something impossible converts a recoverable failure into a support incident. Truthful messages are free.
- **Acceptance:** No user-facing string references a nonexistent flag or behavior; grep for `--batch` returns nothing.

### CQ-03 — /reset-caches: honor `clear_viewed` and `clear_starred` independently (S)
Refs: PY-13.
- **What:** `if clear_viewed or clear_starred:` unlinks BOTH `VIEWED_PATH` and `STARRED_PATH` (server.py:491-495). Split into two independent `if` blocks.
- **Why:** The API contract advertises three independent flags; the implementation silently widens the blast radius. Currently latent (frontend hardcodes all three true, dashboard.js:1044-1046) but the first granular UI — "clear seen history but keep my stars", a natural product ask per UX-4 — would destroy user-created data. Fixing latent contract violations while they are latent is when they are cheapest.
- **Acceptance:** pytest (CQ-46): POST with only `clear_viewed` leaves `starred_state.json` intact, and vice versa; all-flags behavior unchanged.

### CQ-04 — Clamp and validate `max_results` in /populate-range-stream (S)
Refs: py-quality low cluster item 8.
- **What:** `GMAIL_MAX_RESULTS_HARD` (server.py:46) is not enforced: the route accepts any client-supplied `max_results` unclamped, and `int(...)` on a non-numeric value raises an unhandled ValueError → raw Flask 500 (server.py:559). Wrap in try, clamp with `min(value, GMAIL_MAX_RESULTS_HARD)`.
- **Why:** A constant named `_HARD` that is not a hard limit is a trap for the next maintainer; unvalidated input on a network-reachable route (the server currently binds 0.0.0.0 — SEC-1) should never produce a stack-trace 500.
- **Acceptance:** `max_results=999999` is clamped; `max_results=abc` returns a JSON 400, not HTML 500. Covered by a CQ-46 smoke test.

### CQ-05 — Load Credentials modal: X/backdrop must cancel, not open the file picker; fix typo (S)
Refs: JS-9; ux-flow low cluster (typo).
- **What:** `hideLoadCredsModal` unconditionally calls `openLoadCredsFile()` (dashboard.js:380-385), so X, Continue, and backdrop-click all open the OS file dialog. Only Continue should. Also fix "This must downloaded from Google Cloud." (dashboard.html:173).
- **Why:** Close controls that perform the action they exist to avoid violate the most basic UI contract (dismissal = no-op). Given the backend then runs a blocking OAuth flow on upload (SEC-6/UX-2), an accidental selection is heavyweight.
- **Acceptance:** Clicking X or the backdrop hides the modal and nothing else happens; Continue opens the picker; typo gone.

### CQ-06 — `schedulePreload` must clear the previous timer (S)
Refs: JS-6 (timer part only; the cache/dedupe part is CQ-25).
- **What:** `mouseenter` + `focus` on the same row arm two debounce timers of which only the last is cancellable (dashboard.js:916-924). `clearTimeout` the stored handle before setting a new one.
- **Why:** A debounce that can leak timers is not a debounce; this is the textbook one-line form of the pattern.
- **Acceptance:** Hover+focus then quick mouse-out fires at most one `/embed-meta` request (verify in devtools network tab).

### CQ-07 — Unify modal backdrop-dismiss semantics (S)
Refs: js-quality low cluster item on modal dismissal.
- **What:** The max-results backdrop dismisses on ANY click including its own text (dashboard.js:400-402); every other modal checks `e.target === backdrop`. Use the shared pattern.
- **Why:** One interaction idiom per app; a modal that vanishes when you click its text reads as breakage.
- **Acceptance:** Clicking the max-results modal body does not dismiss it; clicking the backdrop does.

### CQ-08 — Rename `/clear-credentials` to match what it does (S)
Refs: py-quality low cluster item 7.
- **What:** The endpoint deletes `token.pickle`, not `credentials.json`, yet is named clear-credentials and logs "Credentials cleared." (server.py:500-514). Either rename to `/clear-token` (and update the one JS call site + button label wording with the UX plan) or make it delete both files; pick one and make name, log line, and behavior agree.
- **Why:** Names are the API's documentation. This one is currently a lie in three places at once, and it sits next to a real credentials-file-writing endpoint — a confusion multiplier during incident debugging.
- **Acceptance:** Endpoint name, log message, and file(s) deleted are consistent; JS call site updated; CQ-46 smoke test asserts which files remain.

---

## 2. Behavior-affecting fixes (need care + tests)

These change observable behavior. Each should land with the test(s) named in its acceptance
criteria — most depend on the test seam CQ-40 (§4). The first three (CQ-10/11/12) are the audit's
highest-leverage changes in the whole codebase; they compound each other (a crash happens, is
reported as success, and permanently poisons the scrape ledger) and are all small diffs.

### CQ-10 — Persist releases per-range BEFORE marking the range scraped (S, high care)
Refs: PY-1; same defect as ARCH-2 (this plan owns the fix).
- **What:** `populate_release_cache` marks each range scraped inside the loop (pipeline.py:114) but persists releases once at the very end (pipeline.py:123). Move `persist_release_metadata` for that range's releases inside the loop, and make `mark_date_range_scraped` the LAST step of each range. The pattern already exists: empty ranges are persisted immediately (pipeline.py:102).
- **Why:** A ledger must never claim work that isn't durably stored — that is the definition of a ledger. Under the app's own never-re-fetch principle, marking-before-persisting converts ANY mid-run failure (and PY-3/PY-7 prove several are reachable) into permanent, silent data loss whose only recovery is the nuclear cache reset.
- **Acceptance:** pytest: a pipeline run in which persistence of range 2 raises leaves range 1 both persisted AND marked, and range 2 neither marked nor partially persisted; a run that crashes mid-parse leaves the crashed range unmarked so re-populate retries it. Manual: kill the server mid-populate, restart, re-populate → the interrupted days are re-fetched.

### CQ-11 — Populate worker: catch-all exception handler + distinct terminal SSE error event (M)
Refs: PY-2; same defect as ARCH-3 (this plan owns the worker fix; the typed-event protocol ARCH-4 is the architecture plan's).
- **What:** The worker catches only `GmailAuthError` and `MaxResultsExceeded` (server.py:601-604); anything else escapes the thread and the generator still emits `event: done` (server.py:618), which the client answers with `window.location.reload()`. Add `except Exception as exc:` that enqueues an error, carry a success/failure flag, and emit `event: error` (with the message) instead of `done` on failure. Client: route `event: error` into the existing `handleError` path so the log line and button state survive.
- **Why:** A system must never report success it cannot prove. Silent failure is strictly worse than a crash: the user's mental model ("populate finished, nothing new this month") is corrupted, and combined with the pre-CQ-10 ordering it masked permanent data loss. Failure reporting is the cheapest reliability feature that exists.
- **Acceptance:** pytest with a worker whose pipeline raises `RuntimeError`: the SSE stream ends with `event: error` carrying the message, never `event: done`. Manual: force a crash (e.g. corrupt a date in the cache pre-CQ-14) → UI shows a persistent error, no page reload, log intact.

### CQ-12 — Guard emails without an HTML part; skip-and-log per-email parse failures (S)
Refs: PY-3.
- **What:** `scrape_info_from_email(None, ...)` crashes with `AttributeError: 'NoneType' object has no attribute 'find_all'` (guarded soup at gmail.py:222, unconditional dereference at 226/235) — empirically reproduced; one plain-text email aborts the whole populate. Early-return the None-tuple when `email_text` is falsy, and wrap the per-email parse in `construct_release_list` in try/except that logs ("Skipped 1 email that couldn't be read") and continues.
- **Why:** A pipeline over third-party input must be resilient per-item: one malformed unit may cost that unit, never the batch. Bandcamp does not guarantee HTML-part emails, and forwarded/reply variants already match the Gmail query.
- **Acceptance:** pytest fixture: a message dict with no HTML part yields a skip (no raise) and the remaining messages still produce releases. The 6-None early return is unit-tested directly.

### CQ-13 — Drop null-URL junk rows at construction time (S)
Refs: PY-5.
- **What:** The junk guard `if not all(x is None for x in [date, ...])` (pipeline.py:38) is defeated because `date` is parsed separately and non-None; linkless emails become `{'url': None, 'title': None, ...}` rows that are cached, served, and can never be starred/viewed (URL is the primary key everywhere). Gate on `if release_url is not None:` instead.
- **Why:** An entity without its primary key is not an entity. Persisting it pollutes every downstream consumer for the lifetime of the append-only cache.
- **Acceptance:** pytest: a linkless email produces zero releases; a mixed batch produces only the URL-bearing ones. Follow-up noted for the maintainer: existing caches may already contain null-URL rows — `/releases` may filter them defensively (one-line) rather than migrating.

### CQ-14 — Tolerate malformed dates: `allow_none` + skip-with-log at both unguarded `parse_date` sites (S)
Refs: PY-7.
- **What:** (1) pipeline.py:32 `parse_date(email.get('date'))` raises on a missing/unparseable Date header; (2) `dedupe_by_date` (util.py:81 via pipeline.py:117) raises on any bad `date` field in the cache — permanently bricking populates for that range, silently (pre-CQ-11). Use `allow_none=True` semantics, log-and-skip unparseable items, and treat date-less items like the existing `without_url` bucket.
- **Why:** Data read back from disk is input, not invariant: the cache is a hand-editable JSON file and older versions wrote different shapes. A single bad record must degrade to a single skipped record, and the failure mode ("every populate of June silently does nothing, forever") is among the worst in the app.
- **Acceptance:** pytest: `dedupe_by_date` over a list containing `{'date': 'garbage'}` and `{'date': None}` returns the parseable items plus the skipped ones (or excludes them, per chosen semantics) without raising; a message with no Date header is skipped with a log line, not fatal.

### CQ-15 — Fix markdown renderer link mangling (autolink-inside-href) (S)
Refs: PY-6.
- **What:** `_format_setup_inline` rewrites `[label](https://…)` to an anchor (server.py:71) and THEN runs the bare-URL autolink regex over the result (server.py:72-76), producing nested broken anchors — empirically reproduced. Tokenize markdown links out first (placeholder substitution), autolink the remainder, re-insert.
- **Why:** Multi-pass string rewriting over its own output is order-fragile by construction; this is latent only because no shipped .md file currently uses an external markdown link, so the first normal doc edit breaks the rendered /setup page — exactly the page a stuck first-run user is reading.
- **Acceptance:** Golden test (CQ-43): `_format_setup_inline('Go to [Google Cloud Console](https://console.cloud.google.com/) and sign in.')` yields exactly one well-formed anchor; full-file goldens of SETUP.md/README.md/GMAIL_SETUP.md render unchanged (byte-identical) apart from intended fixes.

### CQ-16 — Remove the speculative quopri double-decode (S)
Refs: PY-11.
- **What:** gmail.py:66-72 unconditionally applies `quopri.decodestring` to the already-CTE-decoded Gmail body under a bare `except: pass`; any legitimate `=XX` hex sequence in URLs or text is silently corrupted and the corruption propagates into cached data. Delete the pass, or apply it only when the part's headers actually declare `Content-Transfer-Encoding: quoted-printable`. Remove the bare except either way.
- **Why:** Decoding must be driven by declared encoding, never by guessing — a decoder that "usually works" on non-encoded input is a data corruptor with a delay. The bare except also hides the only case where the guess visibly fails.
- **Acceptance:** pytest fixture: an email whose HTML contains `id=123` and `=E2` sequences round-trips byte-identically; a genuinely quoted-printable-declared part (if support is kept) still decodes. No bare `except:` remains in gmail.py (`ruff` E722).

### CQ-17 — Surface, don't swallow: corrupted JSON stores get sidestepped, not silently emptied (S)
Refs: PY-12.
- **What:** `_load_cache`/`_load_date_set`/`_load_set`/`_load_embed_cache` return empty on ANY exception (session_store.py:39-42, 66-67; server.py:161-162, 197-198), so a truncated `release_cache.json` is overwritten with only the next run's data while `scrape_status.json` still claims the lost days are done. On `JSONDecodeError`, rename the file to `*.corrupt-<timestamp>`, log loudly, then start fresh. Distinguish file-missing (fine, quiet) from file-corrupt (loud).
- **Why:** "Missing" and "corrupt" are opposite situations: one is a fresh start, the other is recoverable user data mid-destruction. A rename costs one line and converts permanent loss into a support-recoverable state.
- **Acceptance:** pytest: loading a truncated JSON file leaves a `.corrupt-*` sibling and returns empty; loading a missing file returns empty with no sidestep file; the next save does not touch the sidestepped copy.

### CQ-18 — Store-level locking, unique tmp names, and populate-lock lifetime (M)
Refs: PY-8; same defect family as ARCH-1/PERF-6 (this plan owns the minimum-viable locking; SQLite ARCH-7 is the architecture plan's, and the batch seen-endpoint PERF-1 lives in the architecture/logic plans (ARC-4 render coalescing, LOG-6/LOG-7) and WP-27 of the implementation plan).
- **What:** (1) One module-level `threading.Lock` around every load-modify-save (a single shared lock is fine at this traffic); (2) unique tmp names via `tempfile.NamedTemporaryFile(dir=store_dir)` instead of the shared `path.with_suffix('.tmp')` — and delete the non-atomic `FileNotFoundError` fallback (server.py:169-173) that exists only to paper over the shared-tmp race; (3) release `POPULATE_LOCK` in the worker's `finally`, not the SSE generator's (server.py:619-620), so client disconnects can't allow two concurrent populate workers.
- **Why:** The server is threaded and the frontend itself generates concurrent writers (mark-all-seen fires N parallel POSTs), so last-writer-wins update loss is a live bug, not a theoretical one. A lock is the honest minimum; anything cleverer belongs to the storage decision in the architecture plan.
- **Acceptance:** pytest: 50 threads concurrently toggling distinct URLs through the viewed-store path lose zero updates; only one `.tmp`-pattern file can exist per writer (unique names); a simulated client disconnect during populate leaves `POPULATE_LOCK` held until the worker exits (second populate rejected meanwhile).

### CQ-19 — Replace `batch._responses` private-attr access with the callback API; add 429 backoff (M)
Refs: PY-9.
- **What:** gmail.py:162-165 reads google-api-python-client's private `BatchHttpRequest._responses` and manually json.loads raw bodies, with response pairing riding on private dict insertion order. Use `batch.add(request, callback=...)`, which delivers parsed responses and per-request exceptions. On 429, sleep-and-retry the batch with exponential backoff (2-3 attempts) instead of aborting the run.
- **Why:** Private attributes are not API: any minor library upgrade can break message download outright, and the failure would present as "populate broken" with no code change on our side. The supported callback interface is the same amount of code. Retry on rate-limit matters because (pre-CQ-10) an aborted run stranded earlier ranges.
- **Acceptance:** pytest with a mocked batch: responses arrive via callbacks in correct pairing; a single 429 triggers a retry then succeeds; `grep _responses` returns nothing. Manual: a real 200-email populate completes.

### CQ-20 — /embed-meta: cache-first read, guarded `literal_eval`, generic error body (M)
Refs: PY-10; overlaps PERF-2 (cache-first is also its top fix) and SEC-2 (URL allowlisting is the architecture plan's ARC-5 security batch — coordinate, one PR ideally).
- **What:** (1) The endpoint writes `embed_cache.json` but never reads it — check `_load_embed_cache().get(release_url)` first and return the hit; (2) `ast.literal_eval` fallback (bandcamp.py:17-20) is uncaught at server.py:429 → raw Flask HTML 500 breaking the endpoint's JSON contract — wrap in the same try, return None on failure; (3) the fetch-failure path returns the raw exception string (server.py:426) — map to a generic message, log details server-side.
- **Why:** A cache that is written but never read is pure cost; every hover/star of an uncached release currently hits bandcamp.com live. Error contracts must be uniform (JSON in, JSON out) or the client's error handling is fiction. Raw exception strings leak internals and (per SEC-2) form a port-scan oracle.
- **Acceptance:** pytest: second request for the same URL performs no network fetch (mock `requests.get`, assert one call); a page whose meta attribute is not a Python literal returns JSON `{error: ...}` with status 502, not HTML; error bodies contain no exception class names or paths.

### CQ-21 — Escape release fields at render time; whitelist URL schemes (M)
Refs: JS-1; same defect as SEC-7 (this plan owns the implementation).
- **What:** Row/detail templates interpolate `release.page_name/artist/title/url` and `pageUrlFor(release)` straight into `innerHTML` and attribute contexts (dashboard.js:756-769, 840, 844). Add a tiny `esc()` helper (text + attribute contexts) or build cells with `createElement`/`textContent` (the pattern `renderFilters` already uses); allow only `http(s):` for hrefs and the iframe `src`.
- **Why:** Never interpolate untrusted text into markup — these strings come from third-party-authored email content and scraped pages. Even ignoring the XSS angle (executing origin can drive credential and SSRF endpoints), it is a plain rendering bug: a legitimate title containing `<` or a URL containing `"` breaks the row today.
- **Acceptance:** A release titled `<img src=x onerror=alert(1)>` with URL `javascript:alert(1)` renders as literal text with a dead link; a title containing `& < > "` displays verbatim; all existing rows render pixel-identically. (Add one DOM-level regression check via a small JS test or a documented manual fixture release in the dev cache.)

### CQ-22 — Health check: recoverable, non-latching, honest (S/M)
Refs: JS-2.
- **What:** One failed 4s health check latches `serverDownShown`, stops all future checks, and shows an undismissable full-screen "restart the app" modal (dashboard.js:30, 133-134, 156-181; dashboard.html:148-150). Require 2-3 consecutive failures before showing; keep polling; auto-hide (or show "Reconnected — Reload") on recovery; add a dismiss/retry control. Also delete the dead `isLocalHost` guard or derive `apiHost` after config load (the const is computed from the hardcoded default before config applies, so the check at line 175 is always true and the comment at 177 describes unimplemented behavior).
- **Why:** A watchdog that can never un-fire is a self-inflicted outage: transient blips (sleep/wake, port contention) are normal on a laptop, and the current modal's advice cannot restore the page even when followed. Dead guards with misleading comments actively misinform the next maintainer.
- **Acceptance:** Manually stop the server → modal appears after ≥2 failed polls; restart the server → modal clears (or offers one-click reload) without user archaeology; the stale-`apiHost` derivation is gone (single derivation point, post-config).

### CQ-23 — Single ownership of the Populate button and status log during a run (M)
Refs: JS-3; interacts with JS-10/CQ-24 and the UX plan's progress-UI work (UX-7) — land this state fix first, the UX skin can follow.
- **What:** Calendar interaction mid-populate rewrites `populateLog.innerHTML` (wiping streamed progress; next SSE line appends onto the replacement) and re-enables the Populate button mid-stream (dashboard.js:559-579 vs 1418-1487). Introduce an `isPopulating` flag; while set, `updateSelectionStatusLog` is a no-op (or appends), and the button's disabled state/label has exactly one writer.
- **Why:** Shared mutable UI state with two independent writers is a race by design; the visible symptom (user clicks the re-enabled button, gets a spurious "populate already running" alert for an operation that is succeeding) teaches users to distrust the app's feedback.
- **Acceptance:** During a streaming populate: clicking calendar days neither wipes the log nor changes the button; the button re-enables only on terminal `done`/`error`. Verified manually with a slow populate (large range).

### CQ-24 — SSE client lifecycle: distinguish transient blips from terminal errors; refetch instead of reload (M)
Refs: JS-10; overlaps UX-9 (outcome summary — UX plan) and ARCH-4 (typed events — architecture plan). This item = client-side minimum that works with today's protocol.
- **What:** The EventSource `error` event (which also fires on auto-reconnectable disconnects) is treated as fatal: stream closed + `alert('Populate failed (stream error)')` while the server worker keeps running (dashboard.js:1445-1462, 1480). Treat only explicit server error events / `ERROR:` lines as terminal; let transient errors reconnect (or silently re-attach). On `done`, refetch `/releases` + scrape-status and re-render in place instead of `window.location.reload()` (dashboard.js:1481-1484).
- **Why:** Telling the user an in-progress operation failed is worse than saying nothing — it prompts retries that collide with the still-running worker (and pre-CQ-18, corrupt stores). Full page reload as a refresh mechanism throws away all UI state and the log the user was reading; the refetch primitive (`fetchReleases`) already exists.
- **Acceptance:** Kill and restart the network path mid-populate (devtools offline toggle) → no alert, stream resumes or ends cleanly; on completion the table updates in place, log intact, scroll/sort/filters preserved. Coordinated with CQ-11 so server terminal errors arrive as `event: error`.

### CQ-25 — `ensureEmbed`: in-flight dedupe + negative caching (M)
Refs: JS-6; overlaps PERF-2 (its verified fix direction is identical — implement once).
- **What:** (1) Keep a `Map<url, Promise>` and return the pending promise to duplicate callers (hover + click + star currently triple-fetch); (2) the cached-guard requires `embed_url && description`, so releases whose fetch/extraction fails (404, page without bc-page-properties) are refetched on EVERY render of a starred row and every preload run, forever — record the negative (e.g. `description: ""` / `fetched: true`) in the embed cache and treat "embed_url present" as cached. Server side of negative caching lands with CQ-19.
- **Why:** Idempotent lookups must be deduplicated and memoized including failures, or the miss path becomes a permanent network tax that grows with the starred set — and it hammers Bandcamp from a user's IP with no backoff.
- **Acceptance:** With a starred release whose page 404s: sorting the table twice produces zero new `/embed-meta` requests after the first attempt; hover+click on one row = one request; preload's candidate list excludes fetched-but-empty releases.

### CQ-26 — Route both mark-read paths through one helper; clear `expandedKey` on close (S)
Refs: JS-8.
- **What:** The `u`-key handler clears the dot's `read` class but not the row's `unseen` class, desyncing dot from row background, and contains an unreachable dot-creation branch plus `classList.toggle('read', false)` obfuscation (dashboard.js:878-891 vs the correct dot-click path at 900-912). Extract `setRowReadState(tr, release, isRead)` used by both. Also clear `state.expandedKey` inside `closeOpenDetailRows` so Escape/re-render closes don't leave a stale hide-viewed exemption (dashboard.js:739-741, 853-856).
- **Why:** Two code paths for one state transition is how the desync was written; a single transition function makes the invariant (dot, row class, state set, server) structurally enforced rather than remembered.
- **Acceptance:** Pressing `u` on a seen row restores BOTH dot and row tint to the unseen presentation, identical to dot-click; after Escape-closing a detail row, toggling hide-viewed no longer exempts the previously expanded row.

### CQ-27 — Init resilience and persistence-failure visibility (S/M)
Refs: js-quality low cluster (all-or-nothing init / silent persistence).
- **What:** (1) A failed viewed/starred fetch currently hides the ENTIRE dashboard behind the error bar even when `/releases` succeeded (dashboard.js:91-121, 1702-1723) — degrade instead: empty sets, a status note, table still usable. (2) `persistViewedRemote`/`persistStarredRemote` failures only `console.warn` (dashboard.js:183-206) — revert the toggled UI or surface a note so marks that will vanish on reload aren't shown as saved.
- **Why:** Availability of the core view should degrade gracefully with the least-critical dependency, not fail with it; and UI that displays unsaved state as saved silently breaks the user's trust ledger.
- **Acceptance:** With `/viewed-state` mocked to 500: table renders with badges defaulted and a visible notice; with `/viewed-state` POST failing: the toggle visibly reverts or flags the failure.

### CQ-28 — Deferred/co-owned js-quality findings (pointer, no work here)
- **JS-7** (keyboard/AT-inaccessible calendar, modals, sort headers, silent log) — owned by the UI/UX improvement plans; code-quality note: implement via semantic elements (`<button>`, `role=grid`, `aria-live`) rather than ARIA bolt-ons.
- **JS-11** (mark-as-seen O(rows×releases) rebuilds + N POSTs) — owned by the architecture/logic plans (ARC-4 render coalescing, LOG-6/LOG-7) and WP-27 of the implementation plan (PERF-1); CQ-18's locking is its correctness prerequisite.
- **JS-4** ("N releases shown" label erased by `fetchScrapeStatus` calling `updateHeaderRange()` argless, dashboard.js:1587, 606-608) — trivially fixable while in the area: cache the last count or recompute internally. **(S)** — include with CQ-23's PR.
- **JS-5** (label filter selections silently reset on date navigation / last-uncheck, dashboard.js:457-479) — fix by storing an exclusion set so unknown labels default to shown; coordinate semantics with the UX plan's filter redesign (UX-8). **(M)**

---

## 3. Structural cleanups (no behavior change intended)

Land after §2 where they touch the same lines, so the behavior fixes stay reviewable.

### CQ-30 — One JSON persistence module (S/M)
Refs: py-quality low cluster (duplicate persistence); enabler for ARCH-8's extraction and whichever storage decision ARCH-7 lands.
- **What:** server.py:155-205 duplicates session_store.py:30-83 (load-or-empty / tmp-then-replace) with divergent details. Extract one `json_store.py` (`load(path, default)`, `save_atomic(path, obj)`, the CQ-17 corruption sidestep, the CQ-18 lock) and use it everywhere. Drop the `CACHE_PATH`/`EMPTY_PATH` aliases (session_store.py:22-23).
- **Why:** The same-tmp-path bug was written twice because the pattern exists twice; every future persistence fix (fsync, locking, corruption handling) must otherwise be applied in two places or silently diverge.
- **Acceptance:** `server.py` contains no direct JSON file IO; one implementation of atomic write in the codebase; CQ-42 round-trip tests run against the shared module; behavior byte-identical for well-formed stores.

### CQ-31 — Extract the markdown renderer to its own module (S)
Refs: ARCH-8 item 1; pairs with CQ-15/CQ-43.
- **What:** Move `_render_markdown_html`/`_format_setup_inline`/`_rewrite_doc_link` (server.py:61-145) into `docs_render.py` with a pure `render(md_text) -> html` entry point.
- **Why:** It renders three user-facing pages and demonstrably had a latent bug (PY-6); pure text-to-text functions are the cheapest code in the app to golden-test, but only once they are importable without the Flask module.
- **Acceptance:** server.py imports the renderer; CQ-43 goldens import `docs_render` directly (no Flask app needed); rendered pages byte-identical pre/post move.

### CQ-32 — Remove the dead `img_url` field end-to-end (S)
Refs: py-quality low cluster item 2.
- **What:** Declared but never assigned in `scrape_info_from_email` (gmail.py:204), threaded through pipeline.py:34-42 and `construct_release` (util.py:37,44), serialized as `null` to the frontend forever. Delete the field at all sites; tolerate its presence in old cached data on read.
- **Why:** A field that is always null is a standing question every reader must answer ("where does this get set?" — nowhere). Removing it also shrinks the payload PERF-5 worries about, marginally.
- **Acceptance:** `grep img_url` returns nothing in source; `/releases` output for an existing (old) cache still parses; pipeline tests pass.

### CQ-33 — JS dead-code and duplicate-logic sweep (M)
Refs: js-quality low cluster (leftover generality).
- **What:** Delete/inline, each verified dead by the audit: the single-entry `calendars` map + threaded `type` parameter (dashboard.js:1158-1160, 1209-1219, 1326-1334); `scrapeStatus.notScraped` (populated, never read) and the doubled `data.not_scraped || data["not_scraped"]` (1582-1584, 418); `input` listeners on permanently hidden date inputs (1680-1681; dashboard.html:58-61); `performReset`'s hardcoded params and post-reset local state work discarded by the unconditional reload (1043-1081 — also fixes the error-path desync where local state clears without the server reset having happened); the double endpoint derivation (9-21 vs 48-55 — coordinate with CQ-22); duplicated pairs: cached-badge logic (225-231 vs 767), range-fully-scraped loop (551-556 vs 614-628), preload-pending computation (567-568 vs 610-611), `state.viewed.add` immediately followed by `setViewed` re-adding (830-834); the two separate `settingsBtn` click listeners (999, 1016).
- **Why:** This residue actively misleads — comments and parameters describe behavior that does not exist, which taxes every future change (and misled the audit's own first pass more than once). Dead listeners and duplicate expressions are also where real bugs hide (the stale `apiHost` came from exactly this pattern).
- **Acceptance:** Each listed symbol/branch removed or unified; no UI behavior change (manual smoke of calendar nav, reset, settings, badges); file shrinks measurably (expect ~100+ lines).

### CQ-34 — Status log: one write API (S/M)
Refs: js-quality low cluster (two write protocols); prerequisite polish for the UX plan's log redesign (UX-7).
- **What:** The log is written via `textContent` appends in some paths and `innerHTML` with `<br>`s + inline `#64a8ff` styling in others (dashboard.js:149-155, 559-572, 1470-1475); appends after an innerHTML write flatten the `<br>`s and the inline color leaks. Provide `log.append(line, level)` / `log.replace(lines)` owning all formatting via CSS classes; delete `populateLog.style.color` writes.
- **Why:** A channel with two encodings has undefined output; single-writer APIs are how CQ-23's ownership rule stays enforced.
- **Acceptance:** All log writes go through the API (grep: no direct `populateLog.innerHTML`/`.style.color` outside it); mixed selection-summary → SSE-append sequences render line-separated and correctly colored.

### CQ-35 — Extract inline styles from dashboard.html/JS into CSS classes (M)
Refs: UI-1/UI-8/UI-10/UI-11 implementation substrate; ~30 `style=` attributes in dashboard.html (incl. the Populate button's untokenized `rgba(100,168,255,…)` at html:52, error bar at html:85, legend swatches at 40-44, three inline button size variants) plus JS-injected `color:#64a8ff` (dashboard.js:565, 570).
- **What:** Move every inline style into `dashboard.css` classes; route colors through the token system. Do NOT redesign here — this item is a mechanical 1:1 extraction that preserves computed styles, so the UI plan's retheme (which owns the actual color/contrast decisions) becomes a tokens-only diff.
- **Why:** Inline styles are unlintable, unthemeable, and the direct mechanical cause of the light-mode contrast failures (hardcoded literals ignore theme tokens). Extraction is pure enablement: after it, the UI plan edits one file.
- **Acceptance:** `grep 'style=' dashboard.html` returns only `display:none` toggles managed by JS (or zero, with JS using classes); no color/size literals injected from dashboard.js; before/after screenshots pixel-identical in dark mode.

### CQ-36 — Delete fake config and its dead frontend branches (S/M)
Refs: architecture low cluster (config.json is fake config); js-quality low cluster (dead `default_theme`).
- **What:** `/config.json` returns hardcoded literals; `show_dev_settings`/`clear_status_on_load`/`title` gate dead or contradictory logic, and `default_theme:'light'` is unreachable because JS forces dark when dev settings are hidden (server.py:339-350; dashboard.js:56-73, 285-290). Delete the three dead keys and their JS branches; keep config.json as the runtime endpoint map it actually is. The theme-exposure decision (ship the toggle, honor `prefers-color-scheme`) belongs to the UI/UX plans — this item only removes the lie.
- **Why:** Config that does nothing is worse than no config: a maintainer flipping `default_theme` sees no effect and loses trust in every other knob. If dev settings return, gate on an env var read once.
- **Acceptance:** config.json response contains only consumed keys; no `.dev-setting` branch keyed on a deleted flag; app boots and themes exactly as before the change.

### CQ-37 — Naming corrections (S)
Refs: UI-5 correction (dead `.scraped` styles / misnamed class), UX-13, py/js low clusters.
- **What:** Rename the calendar day class `unseen-day` → `populated-day` (it marks populated days; "unseen" is the red dot — the blur is called out in three separate findings) and delete the dead `.calendar-day.scraped` rules incl. the glow ring (dashboard.css ~539-561, never applied by JS). Rename `wireframe-*` classes to neutral card/panel names (visual changes themselves are the UI plan's). Endpoint rename per CQ-08.
- **Why:** These names encode wrong theories of the code; the audit itself documents the maintainer's concepts blurring around them. Renames are cheap now and expensive after more code accretes.
- **Acceptance:** grep: no `unseen-day`, no dead `.scraped` block, no `wireframe` in class names; rendering unchanged.

---

## 4. Minimal test plan

Zero tests exist today (PY-4/ARCH-10), and the codebase is dominated by exactly the code that
regresses silently: email-copy regexes, Bandcamp DOM selectors, date bookkeeping, a hand-rolled
markdown renderer. Three audit bugs (PY-3, PY-5, PY-6) would each have been caught by one small
fixture test. Priority below is ordered by (protection value ÷ setup cost).

### CQ-40 — Prerequisite: `BCFEED_DATA_DIR` seam in paths.py; defer dir creation (S)
- **What:** paths.py binds all store paths at import time and creates the data dir as an import side effect (paths.py:16-19), so importing `session_store` in a test touches the real `~/Library` data dir. Read a `BCFEED_DATA_DIR` env var inside `get_data_dir()` before defaulting; create the directory lazily (first write), not at import.
- **Why:** One 3-line injection point unlocks `tmp_path`-isolated tests for the entire persistence and pipeline layer — the single structural obstacle to testing named by the audit.
- **Acceptance:** `BCFEED_DATA_DIR=/tmp/x pytest` runs without touching the real data dir; importing any module creates no directories.

### CQ-41 — Tier 1: pure-function tests, no fixtures (S)
- **Targets & why first:** `util.parse_date` (incl. `allow_none`, garbage input — locks CQ-14), `dedupe_by_url` (null-URL items — locks CQ-13), `dedupe_by_date` (bad/missing dates — locks CQ-14), `collapse_date_ranges` (the today-exclusion edge cases behind the most recent bugfix commit 598a9dd). These are pure leaves with zero IO — the cheapest possible tests protecting the highest-churn logic.
- **Acceptance:** ~10-12 cases green; each §2 fix that cites a util behavior has a named test.

### CQ-42 — Tier 1: session_store round-trips against tmp dirs (S/M)
- **Targets & why:** save→load round-trips for release cache, date sets, viewed/starred; corruption sidestep (CQ-17); `cached_releases_for_range` day semantics; the per-range persist-then-mark ordering (CQ-10) via a fake-pipeline test. Persistence is where the audit's permanent-data-loss family lives; round-trips are the regression net under every storage change up to and including a future SQLite migration (ARCH-7).
- **Acceptance:** ~8-10 cases green using `BCFEED_DATA_DIR=tmp_path`; monkeypatched `date.today()` where the six inline call sites require it (or a `today()` injection refactor if cheaper).

### CQ-43 — Tier 1: markdown renderer goldens (S)
- **Targets & why:** golden HTML render of SETUP.md, README.md, GMAIL_SETUP.md plus unit cases: markdown link (locks CQ-15), bare-URL autolink, `javascript:` href rejection (the architecture plan's ARC-5 security batch owns the scheme filter), heading/list/inline-code paths. A hand-rolled renderer with no spec is defined BY its goldens; without them every edit is a gamble on three user-facing pages.
- **Acceptance:** goldens checked into `tests/goldens/`; deliberate renderer change requires a reviewed golden update.

### CQ-44 — Tier 2: Gmail email-parsing fixtures (M)
- **Targets & why:** `scrape_info_from_email` + `construct_release_list` against 3-4 saved, redacted real Bandcamp notification emails: with/without HTML part (locks CQ-12), track vs album, "by artist" copy variants, linkless email (locks CQ-13), quoted-printable-looking content (locks CQ-16). This is the app's most fragile, most load-bearing heuristic code; fixtures are the only way upstream Bandcamp copy changes become detectable before users see empty fields.
- **Acceptance:** fixtures in `tests/fixtures/emails/` with personal data scrubbed; parse output asserted field-by-field.

### CQ-45 — Tier 2: Bandcamp page-extraction fixture (S)
- **Targets:** `extract_bc_meta` / `extract_bandcamp_description` against one saved release page (and one pathological page: missing bc-page-properties, non-literal meta attr — locks CQ-19's `literal_eval` guard).
- **Acceptance:** fixture HTML checked in; both extractors covered incl. failure returns.

### CQ-46 — Tier 2: Flask `test_client` smoke tests (M)
- **Targets:** `/releases` (shape, null-URL filtering), `/viewed-state` POST validation, `/reset-caches` flag independence (locks CQ-03), `/populate-range-stream` max_results validation (locks CQ-04), `/embed-meta` cache-first + JSON error contract (locks CQ-19-20). No Gmail/OAuth mocking at this tier — routes that need credentials are asserted only for their no-credentials error shape.
- **Acceptance:** suite runs with `BCFEED_DATA_DIR=tmp_path`, no network (mock `requests.get`), < 5 s total.

### CQ-47 — CI: one GitHub Actions job (S)
- **What:** `.github/workflows/ci.yml`: checkout, setup-python (the single version decided in CQ-65), `pip install -r requirements.txt -r requirements-dev.txt`, `ruff check .`, `ruff format --check .`, `pytest`. Optionally `npx prettier --check` for js/css/html.
- **Why:** Tests that don't run on every push decay into documentation. One job, no matrix, no services — proportionate to the project.
- **Acceptance:** CI green on main; a deliberately broken test turns the badge red.

---

## 5. Linting & formatting proposal

Dev-only tooling; nothing ships to users. Land each format pass as an isolated commit and record
it in `.git-blame-ignore-revs`.

### CQ-50 — Python: ruff (lint + format) (S)
- **Why ruff:** one tool replaces flake8+isort+pyupgrade+black at near-zero config, no plugins to manage — right-sized for a 1,600-line backend. Several audit findings are literally ruff rules: bare except (E722, gmail.py:71), unused import (F401), f-string without placeholder (F541), builtin shadowing (A001-ish via flake8-builtins if enabled).
- **Config sketch** (`pyproject.toml`):
  ```toml
  [tool.ruff]
  line-length = 100
  target-version = "py311"   # per CQ-65's single-version decision

  [tool.ruff.lint]
  select = [
    "E", "W",   # pycodestyle
    "F",        # pyflakes
    "I",        # isort
    "B",        # bugbear (mutable defaults, useless expressions)
    "UP",       # pyupgrade
    "SIM",      # simplify
    "RET",      # return consistency
  ]
  ignore = ["E501"]  # long lines: formatter handles what matters

  [tool.ruff.format]
  quote-style = "double"
  ```
- **Rollout:** (1) `ruff format .` as one commit; (2) `ruff check --fix` for safe autofixes as a second; (3) remaining violations fixed by hand under CQ-01 or with targeted `# noqa` + reason.
- **Acceptance:** `ruff check .` and `ruff format --check .` clean; both enforced in CI (CQ-47).

### CQ-51 — JS/CSS/HTML: prettier; optional ESLint follow-up (S, +M optional)
- **Why prettier:** the frontend is a 1,725-line hand-formatted file; a formatter makes every subsequent diff (the §2/§3 surgery above) reviewable. No build step is introduced — run via `npx`, or pin in a tiny dev-only `package.json`.
- **Config sketch** (`.prettierrc`):
  ```json
  {
    "printWidth": 100,
    "singleQuote": true,
    "trailingComma": "es5"
  }
  ```
  Plus `.prettierignore`: `docs/`, `__pycache__/`, `*.md` (keep hand-wrapped docs stable).
- **Optional follow-up (M):** ESLint with `eslint:recommended` only — it would have flagged several audit findings mechanically (unused vars, duplicate conditions, unreachable branches in CQ-33). Defer until after the ES-module split proposed by ARCH-5, when per-file scopes make it far more useful.
- **Acceptance:** `npx prettier --check dashboard.js dashboard.css dashboard.html templates/` clean; format commit isolated and blame-ignored.

---

## 6. Docs hygiene

### CQ-60 — Fix requirements.txt (S)
Refs: ARCH-9c (confirmed).
- **What:** Current file lists `requests` twice (lines 3 and 7), depends on the `bs4` shim instead of `beautifulsoup4`, and pins nothing while the Homebrew formula pins everything (and vendors both beautifulsoup4 AND the shim because of this). Dedupe; replace `bs4` → `beautifulsoup4`; pin with `>=,<` ranges or exact pins; add `requirements-dev.txt` (pytest, ruff). Consider `pyproject.toml` as the single dependency source later — not required now.
- **Why:** The dev environment and the distributed environment currently drift arbitrarily; the shim dependency is a well-known packaging smell that also bloats the formula.
- **Acceptance:** `pip install -r requirements.txt` in a clean venv runs the app; no duplicate lines; formula regenerated from the fixed list at next release; `bs4` shim gone from both.

### CQ-61 — De-duplicate README.md / SETUP.md; assign each doc one job (S/M)
- **What:** Both files open with competing product descriptions (README: "a Python app that generates a dashboard…"; SETUP: "A macOS desktop app…") and overlapping install/run/keep-terminal-open guidance. Assign: **README** = what it is, screenshot, the workflow section (which is good), pointers to SETUP/GMAIL_SETUP/privacy; **SETUP** = install + run + troubleshooting only; **GMAIL_SETUP** = Google credentials only (already true). One canonical product description, written once, in README; SETUP links to it.
- **Why:** Two half-overlapping truths guarantee one goes stale — they already disagree on what kind of app this is and on the Python version story. In-app docs rendering (the `/setup`, `/readme` routes) makes doc quality a product surface, not repo garnish.
- **Acceptance:** No paragraph appears in two files; each file's first heading states its single job; the in-app docs pages still render correctly (CQ-43 goldens updated deliberately).

### CQ-62 — Add a LICENSE (decision required) (S)
- **What:** There is no LICENSE file. Default copyright law therefore applies (all rights reserved) — which technically makes the existing Homebrew tap's redistribution of the source unlicensed, and prevents any contributor from safely submitting patches. Recommendation: MIT (permissive, zero maintenance, matches the hobby/local-first ethos); the maintainer may prefer AGPL if server-side reuse matters to them. Add the file, reference it from README, include it in the formula's release artifact.
- **Why:** This is the only item in this plan with a legal dimension; it costs five minutes and unblocks both distribution correctness and outside contribution.
- **Acceptance:** LICENSE at repo root; README license section; next tagged release includes it.

### CQ-63 — Convert TODO.rtf to markdown (or GitHub issues) (S)
- **What:** `TODO.rtf` is 34 lines of RTF — not diffable, not viewable on GitHub, not greppable, and invisible to the in-app docs renderer. Convert to `docs/TODO.md` (preserve the DONE history — it documents deliberate de-scoping decisions the audit found valuable) or migrate open items to GitHub issues and delete the file.
- **Why:** A plan file the tooling can't read is a plan only its author can follow. RTF in a git repo also produces useless diffs on every edit.
- **Acceptance:** `TODO.rtf` gone; content preserved in markdown or issues; referenced from README's contributing/roadmap note if kept.

### CQ-64 — Single version + Python-version story (S)
Refs: ARCH-9a/b (corrected form) — the release/tagging mechanics belong to the architecture plan; the docs-consistency slice is owned here.
- **What:** Three Python versions are advertised (formula `python@3.11`, `.python-version` `3.10.19`, SETUP.md "3.10 or newer") and the UI hardcodes "bcfeed v1.0" (dashboard.html:73) while the tap references `v1.0-beta2` on a different repo. Pick one Python floor (recommend 3.11 to match the formula), state it in exactly one doc sentence + `.python-version` + `requires-python` if pyproject lands; introduce a single `VERSION` constant surfaced via `/config.json` and rendered into the header instead of the hardcoded string.
- **Why:** Version answers are the first thing a bug report needs; today the project gives three. A hardcoded UI version is a guaranteed future lie.
- **Acceptance:** grep for `3.10` returns only `.python-version` (or nothing); UI version string comes from the constant; docs state one supported Python.

### CQ-65 — privacy.md accuracy follow-through (pointer) (S)
- **What:** privacy.md promises read-only Gmail access while the code requests full `mail.google.com` scope (SEC-3 — the scope fix itself is the architecture plan's ARC-5 security batch's one-word change). Docs task here: after the scope fix lands, verify privacy.md, SETUP.md:106, and GMAIL_SETUP.md:53 all state `gmail.readonly` and add the "you must re-authorize once" migration note.
- **Acceptance:** All three docs agree with the code's actual scope string; migration note present.

---

## 7. Suggested sequencing

| Wave | Items | Rationale |
|---|---|---|
| 0 — Foundation | CQ-40, CQ-41, CQ-50, CQ-51 (format commits first), CQ-47 | Test seam + pure-function tests + tooling make every later diff safe and reviewable. |
| 1 — Quick wins | CQ-01…CQ-08 | Independent, mergeable in any order; several are prerequisites-in-spirit for wave 2 reviews. |
| 2 — Correctness core | CQ-10, CQ-11, CQ-12, CQ-13, CQ-14 (+CQ-42, CQ-44) | The compounding data-loss/silent-failure trio and its input guards, landed test-first. Highest leverage in the codebase. |
| 3 — Robustness | CQ-15…CQ-19 (+CQ-43, CQ-45, CQ-46), CQ-21, CQ-22 | Renderer, decoding, locking, Gmail API, embed endpoint, escaping, health check. |
| 4 — Frontend state | CQ-23, CQ-24, CQ-25, CQ-26, CQ-27 (+JS-4/JS-5 from CQ-28) | Single-owner populate flow and embed caching; coordinate with UX plan's progress-UI work. |
| 5 — Structure | CQ-30…CQ-37 | Dedup, dead-code sweep, inline-style extraction — enablers for the UI retheme and the ARCH-5 module split. |
| Parallel | CQ-60…CQ-65 | Docs hygiene has no code dependencies; CQ-62 (LICENSE) should not wait. |

Explicitly out of scope here (owned elsewhere, do not duplicate): SEC-1/2/3 network+scope fixes
(the architecture plan's ARC-5 security batch), PERF-1/3/4/5 batching/virtualization (the
architecture/logic plans — ARC-4 render coalescing, LOG-6/LOG-7 — and WP-27 of the implementation
plan), ARCH-4/5/7 typed SSE /
ES-module split / SQLite (architecture plan), all visual/vocabulary changes (UI/UX plans). Where a
CQ item is the implementation substrate for one of those (CQ-19↔SEC-2, CQ-18↔PERF-1, CQ-35↔UI
retheme), land the CQ item first or in the same PR.
