# Known issues

Canonical defect and debt register for bcfeed, distilled from the 2026-07-05 full audit (7 dimensions, every medium/high finding adversarially verified). This is the list the improvement plans reference — IDs are stable; do not renumber.

> **Revalidated against `e363bf4` (2026-07-05).** The original register was written against `598a9dd`; the repo has since merged the IMAP-provider PR (`e363bf4`, +2724/−464 across 21 files: email-provider abstraction, `gmail.py` split into `gmail_client.py`/`gmail_provider.py`, `bandcamp_email_parser.py` extraction, keychain credential storage via `credential_store.py`, a redesigned settings UI, markdown-it-py replacing the hand-rolled docs renderer, and an empty-email-body crash fix). Every finding was re-checked against `e363bf4`: findings the merge fixed moved to **“Fixed upstream”** below; changed findings carry a **“Changed at e363bf4”** note; still-valid findings have refreshed `file:line` evidence. New findings from auditing the merged code are appended to their sections as **PY-16..18, SEC-10..11, UI-19**.

- **Status labels:** `confirmed` = evidence held exactly on verification; `partial` = held with corrections (the corrected reading is what appears below); `low, not verified` = low-severity findings that were not adversarially re-verified; `new (e363bf4)` = found in the merged IMAP/provider code during revalidation.
- **Evidence:** file:line references are to the repo at commit e363bf4 (the original audit cited 598a9dd; the “Fixed upstream” section preserves that history). Screenshots live in `docs/current-state/screenshots/` (superset in the audit scratchpad `shots/`); shots numbered ≤19 predate the settings redesign, the `20`/`22b`/`23` series are post-merge captures.
- **Cross-references:** the same root cause sometimes surfaced in multiple dimensions (e.g. unlocked JSON stores → PY-8 / ARCH-1 / PERF-6). Entries are kept separate as-found and cross-linked; a fix should close the whole cluster.
- **Do not break while fixing:** the fast no-framework table, star-triggers-preload (dashboard.js:253), the calendar-as-coverage-map, honest SSE progress content, hover-prefetch, in-app docs, keyboard triage shortcuts, local-first storage, and — new at e363bf4 — the token-consuming, correctly-theming redesigned Settings panel (the working proof that the token system can carry the rest of the app) are verified strengths, not accidents.

## Fixed upstream (e363bf4 IMAP merge)

Findings resolved by the merged PR. Kept here as history (IDs remain reserved); original evidence cited 598a9dd.

### PY-3 · was high · FIXED — Emails without an HTML part crash the whole populate run
Fixed: `parse_release_email` early-returns the None-tuple on an empty/`"none"` body (bandcamp_email_parser.py:29-30), `construct_release_list` skips falsy HTML (pipeline.py:48-50), and the per-email parse is wrapped in try/except-count-continue (pipeline.py:52-60) — one bad email no longer aborts a run.

### PY-5 · was medium · FIXED (producer) — Emails yielding no release URL become null-URL junk rows
Fixed: `construct_release_list` now skips any email without a Bandcamp release URL (`if not release_url: skipped += 1; continue`, pipeline.py:63-65), and the parser returns the None-tuple when no `/album/`|`/track/` link is found (bandcamp_email_parser.py:52-53). Residual (tracked in the logic plan, LOG-8): a one-time sweep of pre-existing null-URL rows in `release_cache.json`, plus the now-defensive-only `without_url` passthroughs (util.py:74-79, 94) and the dead always-true all-None guard (pipeline.py:67).

### PY-6 · was medium · FIXED — Markdown renderer mangles standard `[label](https://…)` links
Fixed: the hand-rolled renderer is gone; docs render via markdown-it-py with a link-rewrite render rule (server.py:70-84, templates/docs.html).

### SEC-5 · was medium · FIXED — OAuth token stored as unencrypted pickle with default file perms
Fixed: the Gmail token is stored as JSON in the system keychain via `keyring` (credential_store.py; gmail_client.py:102-126 load/persist). Residual: a one-time legacy `token.pickle` migration path still uses `pickle.load` — see the new SEC-10.

### SEC-8 · was low · FIXED — Hand-rolled markdown renderer allows `javascript:` link hrefs on doc pages
Fixed by replacement: markdown-it-py's default `validateLink` rejects `javascript:`/`vbscript:`/`file:` hrefs. Note the renderer is instantiated with `{"html": True}` (server.py:71), so raw HTML in shipped .md files passes through unescaped — same low risk profile as before (docs are repo-shipped), worth revisiting if docs ever become user-supplied.

## Python backend

### PY-1 · high · confirmed — Dates are marked scraped before releases are persisted; a mid-populate failure permanently loses those days
Still present at e363bf4 — the rewritten pipeline kept the ordering. `populate_release_cache` marks each range scraped inside the loop (pipeline.py:163) but persists releases only once at the end (pipeline.py:183). Any failure between the two (provider fetch error on a later range, parse crash at pipeline.py:40, bad cached date at util.py:81) leaves days marked done with no data; `cached_releases_for_range` (session_store.py:215-237) then never re-fetches them. Same root as ARCH-2; compounds with PY-2/PY-7.
- Evidence: pipeline.py:163 vs 183; session_store.py:215-237; pipeline.py:127-163
- Fix: persist per-range inside the loop and make mark-scraped the last step of each range (the empty-range path at pipeline.py:149-151 already does this correctly).

### PY-2 · medium (downgraded from high at e363bf4) · partial — Populate failures still terminate the SSE stream with `event: done`; the swallow-everything hole is closed
Changed at e363bf4: the worker now catches `GmailAuthError`, `MaxResultsExceeded`, `AuthenticationError`/`ProviderError`, **and a final `except Exception`** that queues `ERROR: Unexpected error: {exc}` (server.py:679-688) — nothing escapes silently anymore, and the client intercepts `ERROR:` lines (dashboard.js:1479-1483). What remains: the stream still always ends with `event: done` (server.py:702) — there is no distinct terminal `event: error` — so error-vs-success is carried only by prose sniffing (ARCH-4), and the client reloads on `done` regardless (JS-10/UX-9).
- Evidence: server.py:668-704; dashboard.js:1479-1487
- Fix: distinct terminal `event: error` vs `event: done`; part of the typed-SSE move (ARCH-4).

### PY-4 · high · confirmed — Zero tests on a heuristics-heavy codebase; module structure actively resists testing
Still present — and the untested surface grew by ~1,200 lines of provider/IMAP/credential code. The most fragile code (email-copy regexes bandcamp_email_parser.py:59-106, Bandcamp DOM selectors bandcamp.py:11-58, IMAP header/date extraction imap_provider.py:171-244) has zero fixtures. paths.py still binds store paths at import time with a mkdir side effect, and `date.today()` is called inline throughout session_store. Same root as ARCH-10.
- Evidence: no tests/ or .github/ in repo; paths.py:16-35; session_store.py (inline `date.today()` sites); new untested modules email_provider.py, imap_client.py, imap_provider.py, provider_factory.py, credential_store.py
- Fix: BCFEED_DATA_DIR env override in `get_data_dir()`, ~20 pytest cases over the pure seams with saved email/page fixtures (run against both providers), one CI job (ruff + pytest).

### PY-7 · medium · partial — One malformed date can still brick every future populate of the affected range, silently
Changed at e363bf4 — narrowed but unfixed. The merge *added* an `allow_none` parameter to `parse_date` (util.py:6-30) but neither crash site uses it: (1) the legacy-dict path's `parse_date(email.get("date"))` (pipeline.py:40) sits outside the per-email try that starts at pipeline.py:52; (2) `dedupe_by_date` still calls `parse_date(item.get("date"))` bare (util.py:81), so a bad per-item date in release_cache.json raises on every populate covering that range. The provider `EmailMessage.date` path is safe for garbled headers (pre-formatted string, imap_provider.py:193-201) — but produces the new PY-18 instead. Newly fetched ranges are already marked scraped before the crash (PY-1), so that data is marked done but never persisted.
- Evidence: pipeline.py:40, 52; util.py:6-30, 81; imap_provider.py:193-201
- Fix: flip both call sites to `parse_date(..., allow_none=True)` + skip-with-log; treat date-less items as counted skips.

### PY-8 · medium · confirmed — No locking on any JSON store, and the populate lock is released while the worker still runs
Still present. Every store is unsynchronized load-modify-save under a threaded server (server.py:186): viewed/starred (server.py:208-228, 256-276), embed cache (server.py:130-166). Concurrent writers share one tmp path per store (server.py:105, 141) with a non-atomic fallback papering over the race (server.py:108-113). POPULATE_LOCK is still released in the SSE generator's `finally` on client disconnect while the daemon worker keeps running (server.py:703-704), allowing two concurrent populate workers. Same cluster as ARCH-1 / PERF-6.
- Evidence: server.py:104-166, 208-228, 256-276, 703-704; session_store.py:209-212
- Fix: one lock per store (or one global), unique tmp names, release POPULATE_LOCK in the worker's finally.

### PY-9 · medium · confirmed — get_messages depends on the private `batch._responses` attribute; its 429 message references a nonexistent `--batch` flag
Still present, relocated to gmail_client.py. Lines 278-281 read the private google-api-python-client attribute and ride on dict insertion order; the 429 message still says "Try reducing batch size using argument --batch" (gmail_client.py:285), which does not exist (bcfeed.py defines only --port/--no-browser; batch size is hardcoded at server.py:676, `batch_size=20`). No retry/backoff, so one rate limit aborts the populate (and strands ranges per PY-1). Gmail-only: the IMAP path fetches per-message with its own error handling (imap_provider.py:156-169).
- Evidence: gmail_client.py:267-290; bcfeed.py; server.py:676
- Fix: use `batch.add(request, callback=...)`; retry 429s with backoff; delete the --batch text.

### PY-10 · medium · partial — /embed-meta error handling is fragile: uncaught ast.literal_eval, no cache read, raw exception passthrough
Changed at e363bf4 — partly improved, core issues intact. Fetch failures now return corsified JSON 502s, but with the raw exception text embedded (server.py:487-494; feeds SEC-9). `extract_bc_meta`'s `ast.literal_eval` fallback (bandcamp.py:17-20) is still uncaught at the call site (server.py:496) → raw Flask 500. The endpoint still writes embed_cache.json but never reads it before fetching (server.py:479-512; see PERF-2).
- Evidence: bandcamp.py:17-20; server.py:479-512
- Fix: wrap literal_eval, consult `_load_embed_cache()` first, return generic error messages.

### PY-11 · medium · confirmed — Speculative quopri double-decode can corrupt legitimate email HTML
Still present, now Gmail-only. gmail_client.py:196-200 applies `quopri.decodestring` to already-CTE-decoded Gmail bodies under a bare except; any legitimate `=XX` sequence is silently transmuted and propagates into cached release data. The IMAP path decodes correctly per the declared CTE (imap_provider.py:234-244) — a fix must not regress it (provider parity, CQ-71).
- Evidence: gmail_client.py:181-214; imap_provider.py:234-244
- Fix: delete the Gmail quopri pass, or apply only when headers declare quoted-printable.

### PY-12 · medium · confirmed — Corrupted JSON stores are silently reset to empty and then overwritten
Still present. All loaders return empty on any exception (session_store.py:39-42, 66-67; server.py:100-101, 136-137), so a truncated release_cache.json is overwritten with only the new run's data — while scrape_status.json still marks the lost dates as scraped (PY-1), making them unrecoverable.
- Evidence: session_store.py:30-50, 66-67; server.py:94-101, 130-137; interaction with session_store.py:215-237
- Fix: on JSONDecodeError, rename to `*.corrupt-<timestamp>` and log loudly before starting fresh.

### PY-13 · medium · confirmed — /reset-caches clears both viewed and starred state when either flag is set
Still present, verbatim: `if clear_viewed or clear_starred:` unlinks both paths (server.py:559-564). Latent because the frontend hardcodes all flags true (dashboard.js:1046-1058), but any future granular UI silently destroys data.
- Evidence: server.py:559-564; dashboard.js:1046-1058
- Fix: split into two independent `if` blocks.

### PY-14 · low, not verified — Dead and misleading code cluster
Changed at e363bf4 — re-scoped. Still present: `mark_dates_not_scraped` never called (session_store.py:159-165); `img_url` dead end-to-end (bandcamp_email_parser.py:16, 106; util.py:37, 44); `GMAIL_MAX_RESULTS_HARD` not enforced against client input (server.py:631, unhandled ValueError on non-numeric `max_results`); the all-None guard at pipeline.py:67 is now always-true dead code (`release_url` is guaranteed above it). Resolved: `/clear-credentials` now genuinely clears credentials — keychain client config + token + legacy files (server.py:568-584; gmail_client.py:76-99); the `unused import os` lived in the deleted gmail.py. Inverted: the email decode block the audit called dead is now **live** in the parser feeding the empty-body guard (bandcamp_email_parser.py:23-27) — do not delete it.
- Fix: delete the remaining dead branches/field; clamp and validate max_results.

### PY-15 · low, not verified — Two parallel atomic-JSON persistence implementations
Still present: server.py:94-166 duplicates session_store.py:30-83 with divergent details (non-atomic fallback only in server.py; different indents). Any store fix must land twice or silently diverge — this is how the shared-tmp-path bug was written twice.
- Fix: one json_store helper module used from both.

### PY-16 · medium · new (e363bf4) — The scrape ledger is provider-blind: switching providers never re-queries "done" days
`scrape_status.json` records only dates, but "done" now depends on which provider answered. Populate June via Gmail, then switch the active provider to IMAP (`/provider-config`, server.py:755; provider_factory.py): the ledger still says June is done, so IMAP is never queried for it — even though the two mailboxes may have different coverage (folders, retention, which account gets the Bandcamp mail). `provider_type` is used only for log wording (pipeline.py:105-106), never for correctness. The never-refetch model (PY-1/UX-12) turns the mismatch into silent permanent gaps. (Logic plan LOG-22.)
- Evidence: session_store.py:89-98, 129-157; pipeline.py:100-106; server.py:755-817
- Fix: record the producing provider and prompt a re-check/reset on provider switch (cheap), or key the ledger by (provider, date).

### PY-17 · medium · new (e363bf4) — A mis-selected IMAP folder or weak server SEARCH silently records empty days that are never re-checked
IMAP `SEARCH` is server-dependent and weaker than Gmail's; the onboarding auto-ranks and preselects a folder (commit d5ca589; server.py:351-395). If the wrong folder is selected or the server's `SUBJECT`/`SINCE` matching under-returns, `search()` yields nothing → `persist_empty_date_range` marks the whole range checked-and-empty (pipeline.py:148-151) → under the never-refetch model those days are never re-queried. No corroboration ("does this folder contain any noreply@bandcamp.com mail at all?") and no diagnostics in the run summary. (Logic plan LOG-23.)
- Evidence: pipeline.py:148-151; imap_provider.py:94-112; server.py:351-395; session_store.py:257-273
- Fix: don't trust a zero-result first-time IMAP scan without folder validation; surface "searched folder X, matched 0" diagnostics; pairs with a re-check action (UX-12).

### PY-18 · medium · new (e363bf4) — IMAP messages with unparseable Date headers produce date:None releases that crash dedupe
On a missing/garbled `Date` header the IMAP adapter sets `date=""` (imap_provider.py:193-201); `construct_release_list` maps falsy date to None (pipeline.py:35) and still constructs the release (the guard at pipeline.py:67 passes on the other fields). The `date: null` row then reaches `dedupe_by_date`, where `parse_date(None)` raises (util.py:81) — the PY-7 crash class, reachable through the normal IMAP path — or, if it survives, an unbucketable null-date row is persisted. (Logic plan LOG-24.)
- Evidence: imap_provider.py:193-201; pipeline.py:35, 67; util.py:81
- Fix: skip-with-count date-less messages at construction; make `dedupe_by_date` tolerate None via the existing `allow_none`.

## Frontend JavaScript

Line-shift note: dashboard.js lines 1-59 are unchanged from the audit baseline; refs past line 60 shifted +3, and a 392-line provider/IMAP controller was appended (dashboard.js:1706-2093, with `initData` moved to 2091). All refs below are re-verified against e363bf4.

### JS-1 · high · confirmed — Unescaped innerHTML interpolation of email/scraped data (HTML injection / XSS) in row and detail templates
Still present. renderTable interpolates `page_name`, `artist`, `title`, `url` into innerHTML with zero escaping (dashboard.js:759-772); the detail fallback and iframe src likewise (dashboard.js:843, 847). Data originates from parsed email HTML (both providers) and Bandcamp scrapes. Both a rendering bug and an XSS vector in an origin that can drive /load-credentials, /reset-caches, /provider-config, and the SSRF-capable /embed-meta. Same as SEC-7.
- Evidence: dashboard.js:759-772, 843, 847; bandcamp_email_parser.py:59-106; server.py:479-512
- Fix: build cells with createElement/textContent (renderFilters already does) or an esc() helper; whitelist http(s) for href/src.

### JS-2 · high · confirmed — Server-down modal: one transient health-check failure permanently blocks the UI; the localhost guard is dead code
Still present. One failed 4s health check shows an undismissable full-screen modal (dashboard.html:268-270) and `serverDownShown` latches so polling stops (dashboard.js:30, 136-141, 160) — no recovery even when the server returns. The intended local-host guard is inert: `apiHost` is a const computed from the hardcoded default before config load (dashboard.js:11-17; re-derivation at 48-58 never recomputes it), so the check at dashboard.js:178-180 is always true. Same event as UX-15.
- Evidence: dashboard.js:11-17, 30, 135-141, 159-184; dashboard.html:268-270
- Fix: keep polling, auto-hide on recovery, require 2-3 consecutive failures, add a retry/reload affordance, derive apiHost after config.

### JS-3 · medium · confirmed — Interacting with the calendar mid-populate wipes the streaming log and re-enables the Populate button
Still present. Any calendar click during a stream runs `updateSelectionStatusLog`, which overwrites `populateLog.innerHTML` (dashboard.js:567, 574) and resets the button's disabled/label state (dashboard.js:578-583). Clicking Populate again opens a second EventSource that the backend rejects ("Another populate is already running", server.py:659-660), surfacing a spurious "failure" for a run that is actually progressing.
- Evidence: dashboard.js:536-583 vs 1421-1491; server.py:659-660
- Fix: an `isPopulating` flag; single owner for button state; selection-summary writes become no-op/append-only during a stream.

### JS-4 · medium · confirmed — 'N releases shown' label is erased moments after every page load
Still present. `fetchScrapeStatus` (fired un-awaited from initData, dashboard.js:2105) calls `updateHeaderRange()` with no count (dashboard.js:1590), and the null branch blanks the label (dashboard.js:595-612). renderTable sets it correctly at dashboard.js:936.
- Evidence: dashboard.js:595-612, 936, 1590, 2105; shots/10b-populated-full-light.png
- Fix: cache the last count in updateHeaderRange, or recompute internally.

### JS-5 · medium · confirmed — Label filter selections silently reset: unchecking the last 'show' box re-checks everything; date navigation wipes selections
Still present. `state.showLabels` is refilled with all labels when it empties (dashboard.js:479-481) and replaced wholesale when the visible label signature changes with the date range (dashboard.js:476-478, fed by the render-time filter at 732-734). User filter state cannot survive normal navigation.
- Evidence: dashboard.js:460-482, 732-734
- Fix: store exclusions instead of inclusions; treat empty selection as explicit 'show none'.

### JS-6 · medium · confirmed — Starred releases lacking cached description/embed are re-fetched over the network on every table render; no in-flight dedupe
Still present. ensureEmbed short-circuits only when BOTH embed_url and description are set (dashboard.js:429-433), and renderTable calls it for every starred row (dashboard.js:789-791) — with /embed-meta never consulting its own cache (server.py:479-512), each such row triggers a live Bandcamp fetch on every sort/filter/toggle. No per-URL in-flight promise; schedulePreload arms duplicate timers (dashboard.js:917-931). See PERF-2 for the verified scope.
- Evidence: dashboard.js:429-458, 789-791, 917-931; server.py:479-512
- Fix: per-URL in-flight promise + negative caching; cache-first /embed-meta.

### JS-7 · medium · confirmed — Core interactions are keyboard/AT-inaccessible: mouse-only calendar, untrapped modals, click-only sort headers, silent status log
Still present (the redesigned settings form added the app's only designed focus state, but it covers only `.form-input`/`.form-select` and removes the native outline — see UI-12/UI-19). Calendar day cells are plain divs with click listeners only (dashboard.js:1243-1330); modals have no dialog role, focus trap, or Escape (dashboard.js:370-393, 1002-1044; dashboard.html:116-301); sort headers are bare `<th>`s (dashboard.js:952-966); the populate log has no aria-live (dashboard.html:102-110); expandable rows lack aria-expanded.
- Evidence: dashboard.js:952-966, 1002-1044, 1243-1330; dashboard.html:90-95, 102-110, 116-301
- Fix: day cells as buttons in a role=grid with arrow keys; role=dialog + trap + Escape; th → button + aria-sort; aria-live=polite on the log.

### JS-8 · medium · confirmed — 'u' (mark unread) shortcut desyncs row visuals from state; contains an unreachable branch
Still present. The u handler (dashboard.js:881-894) clears the dot's read class but never restores the row's `unseen` class, diverging from the dot-click path (dashboard.js:903-915). The else branch creating a new .row-dot is unreachable. Escape/re-render row-close paths leave a stale `state.expandedKey` (dashboard.js:742-744, 855-859).
- Evidence: dashboard.js:881-894 vs 903-915, 742-744; dashboard.css:675-676
- Fix: one `setRowReadState(tr, release, isRead)` helper for both paths; clear expandedKey in closeOpenDetailRows.

### JS-9 · medium · confirmed — Load-credentials modal cannot be cancelled: X and backdrop still open the OS file picker
Still present (the modal's copy was retouched at e363bf4, its behavior was not). `hideLoadCredsModal` unconditionally calls `openLoadCredsFile()` (dashboard.js:383-388), and every dismissal routes through it (dashboard.js:389-393). Heavyweight consequence given the backend then blocks on the OAuth flow (UX-2). See also UX-16.
- Evidence: dashboard.js:370-393; dashboard.html:286-301
- Fix: only Continue opens the picker; X/backdrop just close.

### JS-10 · medium · partial — SSE populate lifecycle is brittle: transient stream errors alert 'failed' while the server keeps working; success is a full page reload
Still present. handleError is bound to the EventSource error event (dashboard.js:1448-1465, 1483), which also fires on transient disconnects; it closes the stream and alerts, while the server worker keeps running with POPULATE_LOCK released (server.py:668-704) — a second concurrent run is then possible. The done handler is `window.location.reload()` (dashboard.js:1484-1487), discarding sort/filters/scroll/log. Same reload issue as UX-9.
- Evidence: dashboard.js:1436-1491; server.py:625-711
- Fix: distinguish terminal errors from blips; refetch /releases on done instead of reloading.

### JS-11 · medium · confirmed — Mark-as-seen over a visible range performs N full calendar rebuilds plus N unbatched POSTs
Still present. setViewed calls `renderCalendar('range')` per toggle (dashboard.js:219) with a per-cell scan of the whole releases array (dashboard.js:1256-1266); markVisibleRows loops it per visible row (dashboard.js:1125-1145) and fires one POST per row against an unlocked store. Quantified at scale in PERF-1; same cluster as ARCH-6.
- Evidence: dashboard.js:210-220, 1125-1145, 1256-1266; server.py:104-113, 208-228
- Fix: precompute a date→unseen-count map per render; render once after the batch; bulk viewed-state endpoint.

### JS-12 · low, not verified — Leftover generality and dead code
Still present, with the anchor lines shifted (+3 past line 60) and one growth: the 392-line provider controller was appended into the same IIFE (dashboard.js:1706-2093). Representative items re-verified: single-entry `calendars` map with a threaded type parameter (dashboard.js:1161+); `scrapeStatus.notScraped` written, never read (dashboard.js:1587); performReset's hardcoded flags and dead local resets with UI/server desync on the error path (dashboard.js:1046-1084); endpoints derived twice with stale `apiHost` (dashboard.js:9-21 vs 48-58); two settingsBtn click listeners (dashboard.js:1002, 1019); fetchScrapeStatus relies on an implicit server sort-order contract (dashboard.js:1574-1592).
- Fix: inline the single calendar, delete dead code, derive endpoints once after config, extract shared helpers.

### JS-13 · low, not verified — Theme handling: dark is still force-defaulted and persisted as if user-chosen; default_theme config is still dead
Changed at e363bf4 — the toggle is now exposed. The Settings redesign put a "Dark mode" toggle in a visible Appearance section (dashboard.html:127-131), so light theme is finally reachable by end users (resolving half of the old finding; see UX-18). Still broken: with no saved theme, non-dev users are forced to dark regardless of the shipped `default_theme:'light'` (dashboard.js:288-293; server.py:407), and `applyTheme` persists that forced choice to localStorage as if user-chosen (dashboard.js:280-287). No prefers-color-scheme support.
- Fix: honor default_theme/prefers-color-scheme; persist only on explicit user choice.

### JS-14 · low, not verified — All-or-nothing init and silent persistence: an auxiliary state-fetch failure hides the whole dashboard; state POST failures only console.warn
Still present. A failed viewed/starred fetch replaces the entire UI with the error bar even when /releases succeeded (dashboard.js:2091-2112); persistViewedRemote/persistStarredRemote failures are silent, so toggles look successful but vanish on reload (dashboard.js:186-208).
- Fix: degrade gracefully on auxiliary fetch failure; surface persistence failures (revert or note in the log).

### JS-15 · low, not verified — Status log has two write protocols (textContent vs innerHTML); modal dismissal semantics differ between modals
Still present. innerHTML writes with `<br>` and inline color (dashboard.js:536-576) interleave with plain-text appends (dashboard.js:152-158, 1473-1478), flattening line breaks and leaking color; the max-results backdrop dismisses on any click including its own text (dashboard.js:403-405) unlike every other modal. The new provider controller adds a third pattern: JS-set inline status colors (dashboard.js:1736-1745; see UI-19).
- Fix: single log append/replace API owning formatting via classes; reuse the shared backdrop-click pattern.

## Architecture

### ARCH-1 · high · confirmed — JSON store writes are load-modify-save with no locking; 'Mark as seen' realistically loses updates
Still present. Threaded server (server.py:186) + unsynchronized read-mutate-rewrite on every mutable store (server.py:208-228, 256-276, 130-166), while the frontend itself generates the concurrency (markVisibleRows fires one POST per visible row, dashboard.js:1125-1145). Shared tmp path can raise FileNotFoundError, papered over by a non-atomic fallback (server.py:104-113). POPULATE_LOCK releases on client disconnect while the worker keeps running (server.py:703-704). Cluster: PY-8, PERF-6, PERF-1.
- Evidence: server.py:186, 104-166, 208-228, 256-276, 703-704; dashboard.js:1125-1145, 210-220
- Fix: per-store locks, unique tmp names, worker-owned POPULATE_LOCK release, batch mark-seen endpoint.

### ARCH-2 · high · confirmed — Populate marks dates scraped before persisting releases: a mid-run crash silently and permanently loses those days
Still present — the provider rewrite preserved the ordering. Same defect as PY-1, viewed at the state-machine level: mark-scraped at pipeline.py:163 vs persist at pipeline.py:183, with the never-re-fetch principle (session_store.py:215-237) converting transient failure into permanent silent loss. The three stores per persist are updated non-transactionally (session_store.py:209-212).
- Evidence: pipeline.py:127-183; session_store.py:209-237, 30-50
- Fix: persist per-range before marking scraped — a ~5-line reorder.

### ARCH-3 · high · partial — Populate-worker exceptions no longer vanish, but failures still end the stream with `event: done`
Changed at e363bf4 — the worst half is fixed upstream. The worker now has a final `except Exception` that queues `ERROR: Unexpected error: {exc}` (server.py:685-688), and the client intercepts `ERROR:` lines (dashboard.js:1479-1483), so parse-stage and persist failures are surfaced rather than silent. What remains is the protocol defect: the terminal event is always `done` (server.py:702), the client reloads on it (dashboard.js:1484-1487), and error-vs-success is distinguished only by English-prefix sniffing. Same defect as PY-2; the fix is ARCH-4's typed events.
- Evidence: server.py:668-704; dashboard.js:1479-1487
- Fix: distinct `event: error` terminal; client refetches /releases instead of reloading.

### ARCH-4 · medium · confirmed — SSE carries only prose log strings, forcing the UI into English-substring sniffing
Still present — the prose is now provider-parameterized (`{Gmail|IMAP}`), which makes wording-coupling riskier, not better. The client parses meaning out of sentences ('Maximum results' → modal, dashboard.js:1468; 'ERROR:' → handleError, dashboard.js:1479); any wording change silently breaks UI behavior. The backend already computes everything a determinate progress bar needs (pipeline.py:100-183; server.py:625-711).
- Evidence: server.py:625-711; dashboard.js:1436-1491; pipeline.py:100-183
- Fix: typed JSON events ({log, progress, error, complete}) with a text field kept for migration.

### ARCH-5 · medium · confirmed — The single IIFE is past its breaking point (now 2,113 lines); the data model still has no source concept
Changed at e363bf4 — worse on one axis, half-delivered on another. The backend got exactly the provider abstraction this finding wanted (email_provider.py interface; gmail_provider/imap_provider; provider_factory) — but the frontend grew from 1,725 to 2,113 lines in the same IIFE (the provider controller appended at dashboard.js:1706-2093), button state still has multiple owners (dashboard.js:578-583 vs the populate handler), logic exists in duplicate, endpoints are derived twice (9-21 vs 48-58), refresh is a page reload, and releases still carry **no `source` field** recording which provider fetched them (util.py:33-52).
- Evidence: dashboard.js:6-2113; util.py:33-52
- Fix: mechanical split into ~6 ES modules (no build step; app is same-origin-served); add a `source` field to construct_release now that two providers ship.

### ARCH-6 · medium · confirmed — Per-row work triggers whole-app re-renders: mark-all-seen is O(rows × releases) with N network calls and N calendar rebuilds
Still present. Same defect as JS-11/PERF-1 at the design level: renderCalendar per toggle (dashboard.js:219, 1256-1275), renderTable rebuilding all rows and listeners per render (dashboard.js:724-940), against the app's own stated volume assumptions (server.py:60).
- Evidence: dashboard.js:210-220, 1125-1145, 1256-1275, 724-940
- Fix: coalesce renders per microtask; batch the POST; precompute per-day unseen counts.

### ARCH-7 · medium · confirmed — SQLite (stdlib, one file) is warranted: simpler than the current seven JSON stores plus two duplicate persistence layers
Still valid — the merge added a seventh store (provider_config.json) and kept both persistence layers. One bcfeed.db with WAL gives transactional multi-table updates (fixing ARCH-1/ARCH-2 at the storage layer), indexed range queries, and deletes ~150 lines of bespoke persistence — zero new dependencies. Not a substitute for the pipeline ordering fix. If JSON is kept, the correctness floor is: one lock, one store module, unique tmp files, per-range persistence.
- Evidence: paths.py; server.py:94-166; session_store.py:30-83, 116-127, 209-212
- Fix: storage.py with sqlite3, keeping existing store function signatures.

### ARCH-8 · medium · partial — server.py mixes in-route scraping, a duplicate persistence layer, a blocking OAuth flow — and now ~120 lines of IMAP plumbing
Changed at e363bf4 in both directions. Resolved: the 65-line markdown engine is gone (markdown-it-py + templates/docs.html, server.py:70-84). Still present: Bandcamp fetching inline in /embed-meta which never reads the cache it writes (server.py:479-512), duplicate store IO (server.py:94-166 vs session_store.py:30-83), and /load-credentials synchronously running interactive OAuth in the request thread with no timeout (server.py:611 → gmail_client.py:216-236). Regressed: server.py grew from 626 to 822 lines and gained a new non-HTTP concern — IMAP/provider plumbing (`_coerce_imap_port`, `_build_imap_config`, `_open_imap_client`, `_imap_folder_rank`, `_discover_imap_folders`; server.py:278-395) that belongs in the provider layer.
- Evidence: server.py:278-395, 479-512, 94-166, 587-623; gmail_client.py:216-236
- Fix: move fetch+cache into bandcamp.py with cache-first get_embed_meta; collapse JSON IO into one module; make /load-credentials save-and-return; relocate the IMAP helpers into imap_provider/imap_client.

### ARCH-9 · medium · partial — Distribution is split-brained: version, Python version, and dependency pins each have multiple conflicting answers
Changed at e363bf4 — one fix, wider drift. Fixed: the duplicate `requests` in requirements.txt is gone. Widened: the merge added `keyring`, `markdown-it-py`, and `linkify-it-py` as unpinned runtime deps that the fully-pinned Homebrew formula (keinobjekt tap @ v1.0-beta2) does not yet carry. Still confirmed: three Python version stories (formula python@3.11, .python-version 3.10.19, SETUP.md "3.10 or newer"); the hand-maintained 'bcfeed v1.0' in the H1 (dashboard.html:73); the dead `sys._MEIPASS` credentials branch survives the refactor (gmail_client.py:52-55).
- Evidence: requirements.txt; dashboard.html:73; gmail_client.py:45-59; .python-version; SETUP.md
- Fix: single VERSION constant surfaced via /config.json; pin or sync requirements; delete the _MEIPASS branch or add a shared resource_path(); tag releases in this repo.

### ARCH-10 · medium · confirmed — Zero tests, no CI, partial typing; import-time side effects block the cheapest tests
Still present — and the merge added ~1,200 untested lines (providers, IMAP client, credential store, parser). Same defect as PY-4 with the architecture remedy spelled out: BCFEED_DATA_DIR injection point in paths.py (3 lines) unlocks tmp-dir isolation for the whole persistence/pipeline layer; fixture-based tests should run the parse seams against both provider paths.
- Evidence: no tests/ or .github/; paths.py:16-19; pipeline.py:28-92
- Fix: env-var data-dir override, fixtures for email/page parsing, minimal CI.

### ARCH-11 · low, not verified — config.json is fake config: all flags are hardcoded server constants
Changed at e363bf4 — the contradiction narrowed but the config is still fake. GET /config.json returns literals (server.py:398-411); show_dev_settings requires a source edit; `title` is never consumed; the shipped `default_theme:'light'` is still overridden by the JS force-dark default (dashboard.js:288-293) — though the theme is now at least user-switchable via the exposed Appearance toggle (see JS-13/UX-18).
- Fix: delete the dead/contradictory flags and JS branches; gate dev settings on an env var if ever needed.

## UI visual

Line-reference note: dashboard.css grew 839 → 1,058 lines by pure append (settings/provider block at css:837-1058), so css refs ≤835 below are unchanged and re-verified. dashboard.html is unchanged through line 114; the settings modal was rebuilt at html:116-267 and the credential modals moved to html:274-301. Screenshots numbered ≤19 predate the settings redesign.

### UI-1 · high · confirmed — Primary action button is unreadable in light mode (1.34:1; disabled ~1.06:1)
Still present. 'Populate release list' is inline-styled white text on a 35%-alpha blue fill (dashboard.html:52); in light mode it computes to 1.34:1 (AA needs 4.5:1), and the disabled state (dashboard.css:86-92) makes the label literally invisible. Dark computes 8.70:1 — designed on dark, never checked on light.
- Evidence: dashboard.html:52; dashboard.css:86-92; shots 01-first-run-light.png, 06-populate-error-light.png vs 10-populated-dark.png
- Fix: solid accent fill via a .button-primary class; never white text on sub-50%-alpha fills.

### UI-2 · high · confirmed — Populated calendar-day numbers are illegible in light mode (1.26:1)
Still present. `.calendar-day.unseen-day .date-label` forces #fff on rgba(100,168,255,0.28) (dashboard.css:502-505) — ghost digits across the entire populated month.
- Evidence: dashboard.css:502-505; shots 10-populated-light.png vs 10-populated-dark.png
- Fix: theme-aware pill text routed through --accent; ≥4.5:1 in both themes.

### UI-3 · high · confirmed — Error feedback is visually broken: 1.29:1 error bar in light mode, and the captured error state shows nothing at all
Still present. Inline error-bar styling (dashboard.html:85) computes to 1.29:1 on light. The captured credential-less populate failure (06-populate-error-light.png) shows an empty Status box and no visible error anywhere. Interacts with UX-3.
- Evidence: dashboard.html:85; shots 06-populate-error-light.png, 06b-log-closeup.png
- Fix: semantic danger tokens per theme applied via a class; errors written to an always-visible surface.

### UI-4 · high · confirmed — Light theme is a half-ported skin of a dark-first design
Still present outside the redesigned Settings panel. Every sampled load-bearing pair passes dark and fails light (button 8.70 vs 1.34; day pills ~7 vs 1.26; log 6.70 vs 2.45; error bar 10.78 vs 1.29; CACHED 7.23 vs 3.65). Mechanical causes: white-alpha control fills that vanish on white (dashboard.css:74, 124, 406, 477, 492) and hardcoded dark-cyan/#64a8ff accent literals ignoring the light accent (dashboard.css:59, 152, 159, 507-508, 566, 581-582). Changed at e363bf4: the redesigned Settings modal consumes the tokens and themes correctly in both modes (shots 22b-postmerge-settings-panel-{light,dark}.png) — proof the failures above are token bypasses, not token incapacity. Its one gap is the new hardcoded focus ring (see UI-19).
- Evidence: dashboard.css:14-25, 74, 124, 406, 507-508; shots 02-empty-dashboard-light.png, 10-populated-light.png vs dark counterparts; 22b-postmerge-settings-panel-light.png
- Fix: per-theme tokens (--control-bg, --accent-tint, --danger-*) replacing every hardcoded rgba literal — or declare dark-only for v1.

### UI-5 · medium · partial — Calendar 'populated' treatment is indistinguishable from 'selected'; the glow-ring/scraped styles are dead code
Still present. Selected days duplicate the in-range gradient exactly (dashboard.css:580-584 vs 506-508); in a populated+selected month every cell is a blue pill and most carry a red dot. `.calendar-day.scraped` styles including the glow ring (dashboard.css:539-561) are dead — JS applies `unseen-day` (dashboard.js:1264), a misnamed class.
- Evidence: dashboard.css:484-584; dashboard.js:1264; shots 10-populated-light.png
- Fix: one channel per state (fill = selected, dot = unseen, subdued mark = populated); delete dead/misnamed state classes.

### UI-6 · medium · confirmed — Populated table: chrome outshouts data — red dots on ~90% of rows, shouting CACHED badges, pseudo-zebra tints
Still present. The alarm-red unseen dot is the highest-chroma element on screen for the default state of new releases; unseen is double-encoded (dot + row tint, dashboard.css:675-677) yet destroyed on hover by a specificity accident (dashboard.css:681-683). The CACHED badge still outranks album titles when shown — though it is now user-toggleable (see UI-13).
- Evidence: dashboard.css:103-115, 146-161, 675-683; shots 10-populated-light.png, 13-starred-filter.png
- Fix: single subtle unseen encoding; demote/relocate CACHED; `tr.data-row.unseen:hover` fix.

### UI-7 · medium · confirmed — Wireframe scaffolding shipped to production: dashed borders and hatch fills frame the whole sidebar
Still present. `.wireframe-panel` dashed border (dashboard.css:261-273), `.wireframe-body` diagonal hatch (dashboard.css:349-361), dashed `.detail-desc` (dashboard.css:721-733), plus a second hatch for the disabled overlay (dashboard.css:282-288).
- Evidence: dashboard.css:261-273, 349-361, 721-733; shots 02-empty-dashboard-light.png, 12-row-expanded-light.png
- Fix: standard 1px var(--border) + var(--surface) card treatment; delete the hatches; rename the classes.

### UI-8 · medium · confirmed — AI-smell inventory: neon body gradients, glow shadows, five icon systems, ALL-CAPS microcopy, version in H1, uniform hover-lift
Still present; the icon count grew. Ambient pink/cyan radial gradients (dashboard.css:33-34, 41-45); glow shadows (the dot glow at dashboard.css:528 is live; 539-561 are dead per UI-5); ⚙️ emoji + text '?' + unicode carats + inline SVG star + — new at e363bf4 — an inline info-circle SVG in the IMAP panel (dashboard.html:22, 31, 79-80, 210; dashboard.js:762) — five icon systems in one app; hardcoded ALL-CAPS literals (dashboard.html:39, 51, 76); 'bcfeed v1.0' in the H1 (dashboard.html:73); translateY hover-lift on everything (dashboard.css:83, 130, 565).
- Evidence: as cited; shots 10-populated-dark.png, 02-empty-dashboard-light.png
- Fix: kill gradients/glows; one SVG icon system; sentence-case microcopy via one class; version to Settings/about.

### UI-9 · medium · confirmed — Status log: sole feedback channel styled as low-contrast fake links in a fixed 200px box
Still present. Log lines hardcode #64a8ff (dashboard.js:568, 573) — 2.45:1 on light, and blue proportional text reads as hyperlinks that aren't. The box is fixed 200px whether empty or full (dashboard.css:441-450).
- Evidence: dashboard.js:568, 573; dashboard.css:440-459; shots 06b-log-closeup.png, 02-empty-dashboard-light.png
- Fix: 12px monospace in --muted with semantic warn/error colors; auto-height to a max; collapsed when empty.

### UI-10 · medium · partial — The settings-modal visual direction is now largely realized the right way; its sibling modals still diverge
Changed at e363bf4 — the core of this finding was delivered upstream. The redesigned Settings panel (html:116-267) replaced its inline-style sprawl with real, token-consuming classes (dashboard.css:841-1058) and themes correctly in both modes — it graduated from "right direction, wrong construction" to the app's reference surface (preserve it; normalize its literals per UI-19). Still present: the Credentials Needed / Load Credentials modals remain inline-styled (dashboard.html:274-301), and the max-results/server-down modals still abandon shared radius/backdrop/z conventions (dashboard.css:585-605, 806-826).
- Evidence: dashboard.html:116-267, 274-301; dashboard.css:585-605, 806-826, 841-1058; shots 22b-postmerge-settings-panel-light.png vs 03-settings-light.png (pre-merge)
- Fix: extract .modal/.modal-actions/.button-danger classes from the new generation; retrofit the remaining modals.

### UI-11 · medium · partial — Two competing accent blues (three in light mode) and four unrelated reds split the color system
Still present, slightly reshuffled at e363bf4. Token accent is #52d0ff/#1f7aff, but the most important actions use untokenized #64a8ff (dashboard.html:52; dashboard.css:244-245, 503, 526-528; dashboard.js:568) — and the redesign added a new hardcoded cyan focus ring (dashboard.css:940). The four reds persist with no danger token: #ff5f5f (dots), #ff6b6b (now also the redesign's `.button.danger`, dashboard.css:1044-1052 — the old inline #b83a3a Revoke link is gone), #b83a3a (error bar html:85; JS-set status color dashboard.js:1739), #ffc5c5 (error text).
- Evidence: as cited; shot 10-populated-light.png
- Fix: one accent token + derived tint scale; one semantic danger token; delete the #64a8ff family.

### UI-12 · medium · partial — Calendar days and sort headers are completely unreachable by keyboard; invisible-but-interactive read dots
Still present; one narrow designed focus state appeared. Calendar day divs (dashboard.js:1243-1284) and sortable `<th>`s (dashboard.js:952-966) have no tabindex or key handling at all, and the read dot at opacity:0 keeps a live cursor:pointer click target (dashboard.css:113-115; dashboard.js:903-915). Rows keep browser-default focus rings. New at e363bf4: `.form-input:focus/.form-select:focus` (dashboard.css:937-941) is the app's only designed focus state — scoped to the settings form, it removes the native outline and substitutes a non-retheming cyan ring (see UI-19).
- Evidence: dashboard.css:113-115, 484, 653-664, 937-941; dashboard.js:952-966, 1243-1284
- Fix: :focus-visible token; calendar days as real buttons; visibility:hidden (or no pointer semantics) for the read dot.

### UI-13 · medium · partial — CACHED badge fails AA in light mode (3.65:1 at 10px) while broadcasting internal plumbing
Changed at e363bf4 — the gate this finding asked for now exists, as a user-visible setting. The badge is rendered only when "Show cached badges" is on (dashboard.html:133-136; dashboard.js:770, 1148-1156) — but it is **checked by default and force-enabled for non-dev users** (dashboard.js:301-306), so the default experience is unchanged: 10px 700-weight uppercase accent text on hardcoded cyan tint (dashboard.css:146-161), below AA in light mode, surfacing cache-layer state next to album titles.
- Evidence: dashboard.css:146-161; dashboard.html:133-136; dashboard.js:301-306, 770; shots 10-populated-light.png, 13-starred-filter.png
- Fix: default the toggle off (or gate behind dev settings); if kept, ≥11px non-bold theme-aware tint.

### UI-14 · medium · confirmed — At 1000px the Date column is clipped mid-header and mid-value
Still present. Fixed 360px sidebar + inline table min-widths (dashboard.html:90-95) exceed a half-screen window; the single 900px breakpoint (dashboard.css:827-838) doesn't fire at 1000px. DATE truncates to 'DAT' with no scroll hint.
- Evidence: dashboard.html:90-95; dashboard.css:168-174, 827-838; shot 19-narrow-1000px.png
- Fix: intermediate ~1100px breakpoint narrowing the sidebar and relaxing Title min-width; protect the date column.

### UI-15 · medium · confirmed — Links have no resting affordance: 'Show setup instructions' looks like plain text
Still present. `a.link` is body-colored with a transparent underline until hover (dashboard.css:46-60); the first-run escape hatch to setup docs (dashboard.html:296) is visually identical to paragraph text. Same for table release links (dashboard.js:768-770).
- Evidence: dashboard.css:46-60; dashboard.html:296; shot 04-load-creds-modal-light.png (pre-merge modal, same treatment)
- Fix: resting accent color or persistent underline.

### UI-16 · low, not verified — Geometry and rhythm entropy: 6 radii, 3-vs-4px spacing systems, 3 inline-only button sizes, 11 font sizes
Still present; the appended settings generation adds its own scales (see UI-19). Radii from 6px to 999px with buttons never matching their panels; the calendar cluster runs a 3/5/7px rhythm against 4/8/12/16 elsewhere (dashboard.css:378-435); sidebar button sizing exists only as inline styles.
- Fix: 4px spacing scale, two radii (8px control / 12px surface), 4-step type scale as tokens.

### UI-17 · low, not verified — Legend and dead chrome: mismatched legend swatches, undefined --header-bg, dead CSS rules
Still present — and the redesign added a new dead-rule class: the original `.settings-panel` block (dashboard.css:753-783) is now shadowed by the appended one (dashboard.css:841) by source order. Legend swatches are ad-hoc inline spans at different sizes than the calendar dots they explain (dashboard.html:40-44); `header` references a never-defined --header-bg (dashboard.css:64).
- Fix: render the legend with the calendar's own components; define or delete --header-bg; purge dead rules (including css:753-783).

### UI-18 · low, not verified — Disabled and enabled buttons are nearly indistinguishable in the sidebar action stack
Still present. opacity:0.45 + grayscale disabled treatment (dashboard.css:86-92) lands next to ghost buttons that are already white-on-white in light mode; the preload button additionally sets inline opacity/cursor per state (dashboard.js:652-653).
- Fix: visible fill for enabled buttons in both themes; reserve reduced opacity for disabled.

### UI-19 · medium · new (e363bf4) — The appended settings/provider styles are a third styling generation with fresh token violations
The redesign's 220-line CSS block (dashboard.css:837-1058) is structurally the best surface in the app — keep its architecture — but it was written outside the token discipline: it shadows the original `.settings-panel` rules (css:841 vs the now-dead css:753-783); uses a white-alpha hover fill invisible in light mode (css:876 — the exact UI-4 failure class); pins a dark-cyan focus ring in both themes while removing the native outline (css:937-941); duplicates button variants as `.button.primary`/`.button.danger` with literal colors (css:1035-1052); sets 11px uppercase section titles (css:897-903); lays out the IMAP form with ~25 new inline styles (dashboard.html:175-216); adds a fifth icon system (inline info SVG, html:210); and the provider controller writes status colors as inline styles including the untokenized #b83a3a (dashboard.js:1736-1745).
- Evidence: dashboard.css:841-1058 (876, 897-903, 937-941, 1035-1052); dashboard.html:175-216, 210; dashboard.js:1736-1745
- Fix: normalize onto the token system in the retheme pass (swap literals for tokens, fold button variants into the shared classes, classes for the IMAP grid, semantic status-text classes) — do not grandfather it, do not discard its architecture.

## UX flow

### UX-1 · high · partial — First value is a long walk away; the funnel improved at e363bf4 but still has no sequencing
Changed at e363bf4 — meaningfully eased, not resolved. The Settings panel is now organized into labeled sections (Appearance / Email Configuration / Data & Storage) and offers **IMAP as an alternative that sidesteps the Google Cloud Console entirely** — host/username/password plus a "Connect & load folders" step that auto-discovers and recommends a folder (server.py:717-752; IMAP_SETUP.md). For Gmail users the 20-30-minute Google Cloud gauntlet is unchanged, the first-run empty state still gives no cue, and the funnel still has no sequencing, time expectations, or progress state — a first-run user still lands one misclick from 'Clear cache' (UX-4).
- Evidence: dashboard.html:116-267; server.py:717-752; shots 20-postmerge-first-run-light.png, 23-postmerge-provider-imap-light.png; GMAIL_SETUP.md
- Fix: first-run wizard (choose provider → guided setup → one-click 'fetch last 30 days'); destructive actions into a confirmed danger zone.

### UX-2 · high · confirmed — Credential upload blocks on a browser OAuth flow the UI never mentions, with no timeout and no recovery
Still present (Gmail path only — the IMAP path's verification is a bounded server-side connection check with inline status, already the right shape). POST /load-credentials synchronously runs `flow.run_local_server` (server.py:611 → gmail_client.py:216-236): a Google consent tab opens unannounced (including the 'unverified app' interstitial), and if the user misses or closes it, the POST blocks forever and the Load button never re-enables.
- Evidence: server.py:587-623; gmail_client.py:235; dashboard.html:286-301; dashboard.js:335-402
- Fix: warn in the modal about the sign-in window and interstitial; run auth off-thread with a timeout and streamed state.

### UX-3 · high · confirmed — Populate failures surface only as a blocking alert() plus a log line that other code freely overwrites
Still present. handleError alerts and appends to a log that updateSelectionStatusLog rewrites wholesale (dashboard.js:1448-1465, 536-576). The captured credential-less populate ends at '0 releases shown', empty Status box, no persistent error anywhere. The error copy improved slightly (provider-aware, e.g. server.py:653-660) but is still plumbing vocabulary with no link to the Settings it references.
- Evidence: shots 06-populate-error-light.png (pre-merge; behavior unchanged); dashboard.js:1448-1465, 536-576; server.py:649-660
- Fix: persistent inline error banner with an 'Open Settings' action; never clobber an unacknowledged error.

### UX-4 · high · confirmed — 'Clear cache' silently deletes the user's stars and seen-state with no confirmation
Still present. performReset hardcodes clear_cache, clear_viewed, clear_starred to true and fires on a bare click (dashboard.js:1046-1084). One click irreversibly destroys the only state the user personally created. The redesign moved the button into a labeled "Data & Storage" section with descriptive copy but added no confirmation.
- Evidence: dashboard.js:1046-1084; shot 22b-postmerge-settings-panel-light.png
- Fix: confirmation dialog enumerating what is deleted; split 'clear downloaded data' from 'reset stars and seen history' (backend flags already separate — see PY-13).

### UX-5 · high · confirmed — Empty dashboard says 'No releases match the current filter' — factually wrong and CTA-free at the moment of highest drop-off
Still present. The single generic empty message (dashboard.html:100) serves both 'never fetched anything' and 'filters exclude everything'. Post-credentials, nothing cues the next step.
- Evidence: dashboard.html:100; shots 01-first-run-light.png, 20-postmerge-first-run-light.png
- Fix: branch the empty state — never-fetched gets a directive CTA with an inline button; filtered-out keeps the current message plus 'clear filters'.

### UX-6 · medium · partial — Every surface speaks the implementation's language (populate, preload, cache, token, scraped days)
Still present, marginally softened at e363bf4. The missing-credentials modal's 'Gmail token missing' became the provider-neutral "Email credentials not configured. Configure your provider settings…" (dashboard.html:280) — still jargon, less raw — and pipeline log lines are now provider-parameterized (`{Gmail|IMAP}`, pipeline.py:104-130). Everything else stands: Populate/Preload buttons and tooltips, CACHED badges, 'not yet populated' status text, 'Populated' legend, raw pipeline log verbatim, and the leaked end-exclusive query range (user selects to 06-30, reads 'to 2026-07-01' — pipeline.py:127-131).
- Evidence: dashboard.html:52-53, 280; dashboard.js:536-583, 632-653, 770; pipeline.py:104-131
- Fix: one rename pass (Populate → 'Get releases'; Preload → 'Load players'; 'populated' → 'checked'; humanized log lines with user-facing date ranges).

### UX-7 · medium · confirmed — The Status log is the primary feedback organ, doing five jobs that all have proper UI primitives, from the wrong corner of the screen
Still present. It carries selection summary, the only workflow tutorial, live populate progress, preload progress, credential results, and errors (dashboard.js:536-576, 1466-1483, 1516-1543, 307-360) — rendered bottom-right, ~900px from the sidebar controls it describes, with no aria-live. (The new IMAP panel, by contrast, puts status text inline next to its buttons — the right pattern, worth generalizing.)
- Evidence: shots 10-populated-light.png; dashboard.html:102-110; dashboard.js:536-576, 1516-1543
- Fix: rehouse each job into its primitive (inline summary, progress bar, toasts, banners); keep the raw log as a collapsible 'Details' debug view.

### UX-8 · medium · confirmed — Label filter uses two unlabeled checkbox columns with hidden modal behavior ('show' vs 'show only')
Still present. No column headers; checking any 'show only' box silently disables/grays the entire other column (dashboard.js:484, 496), which reads as breakage; no all/none affordance; unchecking the last 'show' box silently re-checks everything (dashboard.js:479-481; see JS-5).
- Evidence: dashboard.js:460-534; shot 15-label-filters.png
- Fix: one checkbox column + per-row 'only' link on hover, plus All/None.

### UX-9 · medium · confirmed — Populate ends in a full page reload with no outcome summary
Still present. `window.location.reload()` on done (dashboard.js:1484-1487) discards scroll, sort, filters, expanded row, and the log the user was reading, and never answers 'how many new releases?'. Same mechanism as JS-10.
- Evidence: dashboard.js:1484-1487; shots 18-populate-success-log.png vs 10-populated-light.png
- Fix: refetch /releases + scrape-status, re-render in place, toast 'Added N releases for <range>'.

### UX-10 · medium · confirmed — 'Mark as seen/unseen' sits under 'SELECTED DATE RANGE:' but actually operates on currently rendered (filtered) rows
Still present. markVisibleRows iterates the DOM (dashboard.js:1125-1145), so active label/starred/unseen filters silently change scope; the label carries no object and there is no confirmation or undo for flipping a month of unread state.
- Evidence: dashboard.html:54-55; dashboard.js:1125-1145
- Fix: name the scope ('Mark 17 shown as seen'), move out of the date-range group, add undo toast.

### UX-11 · medium · partial — Preload is an uncancellable multi-minute sequential loop whose only feedback is log lines
Still present. One awaited /embed-meta fetch per release (dashboard.js:1493-1543), a few seconds each — minutes for a month, with no progress bar, ETA, or cancel. Interruption remains low-cost (each embed persists server-side immediately, server.py:506-507; a re-run resumes incrementally) — the fix needed is visibility and control, not crash-safety.
- Evidence: dashboard.js:1493-1543; server.py:506-507; README.md
- Fix: progress bar with count + Cancel (AbortController + loop flag); optionally 2-3× concurrency with politeness delays.

### UX-12 · medium · confirmed — A populated range can never be re-checked; the only 'refresh' path is the data-destroying Clear cache
Still present — and now sharper with two providers (see PY-16: a provider switch cannot re-query "done" days). Once all days are marked scraped the button is permanently disabled ('Release list populated', dashboard.js:578-583); the append-only-per-day model is documented only in the README. Interacts badly with PY-1 (interrupted populates leave gaps that can never be refilled) and PY-17 (IMAP empty-day false negatives).
- Evidence: dashboard.js:578-583, 1046-1049; README.md
- Fix: low-key 'Re-check this range' action clearing scrape-status for just those days; URL dedupe already exists.

### UX-13 · medium · confirmed — The calendar's three encodings (selected, populated, unseen) share one blue-and-dot vocabulary; data gaps are the least salient cells
Still present. Not-yet-populated days — the exact answer to 'where are my gaps?' — are the quietest cells on the grid, while the legend's 'Unseen' collides with the 'SHOW ONLY: Unseen' filter term and the internal class is misnamed `unseen-day` (dashboard.js:1264). The calendar-as-coverage-map idea itself remains the app's best IA and must be kept.
- Evidence: shots 10-populated-light.png, 15-label-filters.png; dashboard.js:1245-1284; dashboard.html:38-45
- Fix: distinct treatment per state with unchecked-inside-selection loudest; rename legend terms ('Checked', 'Has unlistened releases').

### UX-14 · medium · confirmed — Keyboard shortcuts exist (arrows, Enter/Space, s, u, Escape) but are documented nowhere
Still present. Implemented at dashboard.js:855-901; zero mentions in README/SETUP/GMAIL_SETUP/IMAP_SETUP or the UI. The fastest triage loop in the app is a secret.
- Evidence: dashboard.js:855-901; grep of docs returns nothing; dashboard.html:80
- Fix: one-line hint under the table or a shortcuts section behind the '?' button.

### UX-15 · medium · confirmed — Server-down modal is undismissable, permanently latches, and its advice cannot restore the page
Still present. Same event as JS-2, user-facing consequence: after restarting the server as instructed, the modal never clears (polling stopped, no reload button), bricking the tab until the user guesses to reload — against the product's known 'keep the Terminal open' Achilles heel.
- Evidence: dashboard.html:268-270; dashboard.js:136-141, 159-185, 1702-1703
- Fix: keep polling, auto-recover, add an explicit Reload button.

### UX-16 · low, not verified — Load-credentials modal: forced file picker on cancel; its most important link hidden
Changed at e363bf4 — the typo is fixed ("This must be downloaded from Google Cloud.", dashboard.html:294). Still present: all three dismissal paths open the OS file picker (JS-9) and 'Show setup instructions' renders as plain text (UI-15; dashboard.html:296).
- Fix: true cancel; style the setup link as a link/secondary button.

### UX-17 · low, not verified — The 'Today' button selects yesterday, and today's exclusion is never explained
Still present. Today is deliberately unselectable (getLastSelectableDate; cells disabled at dashboard.js:1245-1248) but the button labeled 'Today' jumps to the last selectable day (dashboard.js:1374-1390) and the grayed-out today cell carries no explanation.
- Fix: rename to 'Latest'/'Yesterday'; tooltip on today's cell ("Today's emails are still arriving").

### UX-18 · low, not verified — Settings preferences and theme
Changed at e363bf4 — largely resolved. The redesigned panel exposes real preferences (Appearance: Dark mode + Show cached badges, dashboard.html:125-136) and groups Email Configuration and Data & Storage into labeled sections, so Settings is no longer "nothing but destructive actions". Remaining: dark is still force-defaulted with no prefers-color-scheme support (dashboard.js:288-293; JS-13), and the destructive actions still lack confirmation (UX-4).
- Fix: default to prefers-color-scheme; confirm destructive actions.

## Security and privacy

### SEC-1 · high · confirmed — Server binds 0.0.0.0, exposing the entire unauthenticated API to the whole LAN
Still present at e363bf4 — in both places. make_server("0.0.0.0", ...) (server.py:186) and find_free_port binding "" (server.py:195, 199), plus the `__main__` app.run(host="0.0.0.0") (server.py:822), with no auth/Origin/Host gate on any route: any LAN peer can read release history, wipe caches, delete credentials, upload rogue credentials, drive populate, **reconfigure the email provider and trigger outbound IMAP connections (/provider-config, /imap/discover — see SEC-11)**, and use /embed-meta as an SSRF proxy. bcfeed.py only ever opens localhost, so nothing needs the LAN bind.
- Evidence: server.py:186, 192-199, 822; bcfeed.py
- Fix: bind 127.0.0.1 in all three places.

### SEC-2 · high · confirmed — /embed-meta is an unauthenticated SSRF proxy with no scheme/host allowlist
Still present. requests.get on the raw client-supplied URL with redirects followed (server.py:487-494); error bodies form a port open/closed/filtered oracle (the raw exception text is returned, server.py:494), and internal pages' meta descriptions are extracted and returned verbatim (bandcamp.py:23-58). Also poisons embed_cache.json with arbitrary keys (server.py:507). Bounded to network SSRF (requests has no file:// adapter).
- Evidence: server.py:479-512; bandcamp.py:23-58
- Fix: allowlist https + *.bandcamp.com, resolve-and-block private/link-local IPs, disable redirects, cap response size.

### SEC-3 · high · confirmed — App requests FULL Gmail scope (read/send/delete) while docs and privacy page promise read-only
Still present at e363bf4, relocated: gmail_client.py:217 hardcodes `https://mail.google.com/` (with an in-code comment saying exactly that) while GMAIL_SETUP.md instructs registering `gmail.readonly` and privacy.md promises read-only. The app only calls messages().list/get. A documented-behavior violation that over-provisions the token — now keychain-stored (SEC-5 fixed) but still full-scope. One-string fix (existing tokens must be re-issued).
- Evidence: gmail_client.py:217, 244-265; GMAIL_SETUP.md; privacy.md
- Fix: scope → `gmail.readonly`; document re-auth.

### SEC-4 · medium · confirmed — CORS ACAO:* plus no Host validation lets any website drive the local API and enables DNS-rebinding reads
Still present — and the mutating surface grew. `_corsify` sets Access-Control-Allow-Origin:* on all JSON routes and SSE (server.py:87-91, 637, 707); no route validates Host/Origin, and /config.json reflects request.host_url (server.py:400). CSRF state-mutation (reset-caches, clear-credentials, load-credentials as a CORS-simple multipart POST, **and the new /provider-config and /imap/discover JSON POSTs** — see SEC-11) plus rebinding reads of /releases and state. Compounded by SEC-1.
- Evidence: server.py:87-91, 400, 637, 707, 717-817
- Fix: drop ACAO:*, validate Host against localhost:port, require a custom header or CSRF token on mutating routes (including the two new ones).

### SEC-6 · medium · confirmed — /load-credentials accepts any JSON, overwrites credentials, and blocks the request thread on a human OAuth flow
Still present, modestly hardened at e363bf4: empty uploads and non-UTF-8 files are now rejected (server.py:598-619), but there is still no validation that the payload is a Google client-secret document; the existing token is cleared before the new flow (server.py:608), and `gmail_authenticate()` still synchronously runs `flow.run_local_server` with no timeout in the request thread (server.py:611 → gmail_client.py:235). Reachable cross-origin as a CORS-simple request (SEC-4): a malicious page can brick the user's auth and pop an unexpected consent window. Error paths return raw exception text (SEC-9).
- Evidence: server.py:587-623; gmail_client.py:216-236
- Fix: validate the client-secret shape before saving; run auth off-thread with a timeout; gate behind same-origin checks.

### SEC-7 · medium · confirmed — Release fields injected via innerHTML without escaping (stored injection from third-party email content)
Still present. Same defect as JS-1, security framing: title/artist/page derive from get_text()/regexes over third-party-authored Bandcamp email HTML (bandcamp_email_parser.py:59-106 — both providers feed the same parser), get_text() un-escapes entities, and innerHTML executes img/svg onerror payloads in the origin with full API access. Note: the SSE populate-log path is safe — textContent at dashboard.js:1473-1478.
- Evidence: dashboard.js:759-772, 843; bandcamp_email_parser.py:59-106
- Fix: escape or DOM-build all release fields; validate release.url as http(s).

### SEC-9 · low, not verified — Error responses leak absolute filesystem paths and raw exception text to clients
Still present; the pattern survived the refactor and extends into the new routes. /embed-meta returns the raw fetch exception (server.py:494), /clear-credentials and /load-credentials return `str(exc)` (server.py:584, 617-623), /provider-config and /imap/discover return provider/keyring exception text (server.py:717-817), and the keyring wrapper's `CredentialStoreError` messages pass through verbatim — username/path disclosure to any LAN peer under SEC-1 and the oracle for SEC-2.
- Fix: generic client messages, details to the server log; 404 (not 500) for missing files.

### SEC-10 · low · new (e363bf4) — Legacy token.pickle migration path keeps a pickle.load of a user-writable file
`gmail_authenticate` falls back to `pickle.load` of a legacy `token.pickle` when the keychain has no token (gmail_client.py:128-144 via 216-230) — an arbitrary-deserialization sink over a default-perms file in the data dir. Mitigations already in place: the file is unlinked after successful migration (gmail_client.py:136-140) and /clear-credentials deletes it too (gmail_client.py:89-93), so the window is the one-time migration. Still, a file an attacker can write in the data dir becomes code execution on next auth while the path exists. (Code-quality plan CQ-70.)
- Evidence: gmail_client.py:128-144, 216-230
- Fix: time-box the migration path (remove after a release or two); or parse the pickle's payload defensively / require explicit user action to migrate.

### SEC-11 · medium · new (e363bf4) — /provider-config and /imap/discover extend the unauthenticated mutating surface: any LAN peer or website can reconfigure the provider and make the server dial out
Both new routes accept JSON POSTs with no auth/Origin/Host checks (CORS-simple; rides SEC-1/SEC-4). `/imap/discover` opens an authenticated IMAP connection to a request-supplied host:port with request-supplied credentials (server.py:717-752) — an outbound-connection primitive and a port/host oracle via the returned error text. `/provider-config` swaps the active provider and rewrites provider_config.json plus the keychain-stored IMAP password (server.py:755-817) — a cross-site POST can silently break or redirect the user's email source (and interacts with PY-16: the old provider's scrape ledger stays authoritative). One genuine mitigation verified: the stored IMAP password is only reused when the posted connection signature (host/port/username/ssl) matches the saved config (server.py:338-343), so attacker-chosen hosts do not receive the stored password.
- Evidence: server.py:87-91, 316-349, 717-817
- Fix: same as SEC-4 (Host/Origin validation, custom-header requirement on mutating routes) plus bind loopback (SEC-1); never combine stored credentials with request-supplied connection targets.

## Performance

The audit's 5,000-release benchmarks below were measured at 598a9dd and were not re-run at e363bf4; none of the merged code changes the hot paths (calendar unseen-scan, /embed-meta rewrite, /releases payload, full re-render), so the numbers still describe the trajectory. One new data point: IMAP fetch is per-message rather than batched (imap_provider.py:156-169), so IMAP populate is slower than Gmail's batched download for the same message count.

### PERF-1 · high · confirmed — 'Mark as seen' does a full calendar re-render per row plus N racing POSTs — multi-second freeze and lost viewed-state at scale
Still present. Benchmarked at 5,000 releases: one calendar scan = 9.8 ms; marking 300 visible rows ≈ 3 s of blocked main thread (500 rows ≈ 5 s) plus hundreds of DOM grid rebuilds, while up to 6 concurrent unlocked load-modify-write POSTs silently drop marks that reappear as unseen after reload. Cluster: JS-11, ARCH-6, ARCH-1.
- Evidence: dashboard.js:1125-1143, 210-220, 1256-1266; server.py:208-228, 186; calbench.js at 5k releases (598a9dd)
- Fix: batch endpoint (urls list) or server-side lock; update state directly and render once at the end.

### PERF-2 · medium · partial — Releases whose embed fetch fails are re-fetched from bandcamp.com forever; /embed-meta never reads its own cache
Still present. Fetch/extraction failures — deleted/404 pages or pages without bc-page-properties — cache nothing and are refired on every starred-row render (dashboard.js:790), hover (920-931), and preload run (guard at 1508-1510), permanently inflating failure counts. /embed-meta still does a full page download + double BeautifulSoup parse (~204 ms measured at 598a9dd) + whole-file cache rewrite per call with no cache read (server.py:479-512), and the unlocked read-modify-write (server.py:130-166) can drop entries under the per-starred-row burst.
- Evidence: dashboard.js:429-458, 789-791, 1508-1510; server.py:479-512, 130-166; bandcamp.py:11-58
- Fix: negative-cache failures (description:"" / fetched flag), cache-first /embed-meta, per-URL in-flight dedupe.

### PERF-3 · medium · confirmed — Preload is strictly serial at ~1 s/release with a full cache rewrite per item — 200 releases takes 2-4 minutes
Still present. Per item: bandcamp.com GET (0.3-0.8 s) + two full parses (~204 ms) + whole-file embed_cache.json rewrite (38 ms at 7 MB, measured at 598a9dd). No 429/Retry-After handling; per-item rewrite cost grows quadratically with library size.
- Evidence: dashboard.js:1493-1543; server.py:479-512, 139-166; bandcamp.py:11-58
- Fix: parse once (one soup for meta+description), batch cache flushes, modest concurrency with 429 handling.

### PERF-4 · medium · confirmed — Default date filter spans the entire dataset: first paint builds every row with 8 listeners each, repeated on every interaction
Still present. setDefaultDateFilters uses min→max of all dates (dashboard.js:1545-1558); renderTable wipes and rebuilds all rows with innerHTML parse + per-row addEventListener calls (dashboard.js:724-940) — on the order of 1-3 s at 5,000 rows, re-run on every filter/sort/toggle.
- Evidence: dashboard.js:724-940, 1545-1558
- Fix: default the range to the most recent month (or virtualize); event delegation on tbody.

### PERF-5 · medium · confirmed — /releases ships the entire enriched library on every page load — 7.6 MB at 5k preloaded releases, re-downloaded after every populate via full page reload
Still present. Full flatten + full embed-cache overlay including all descriptions (server.py:230-254; session_store.py:116-127), fetched with cache:no-store (dashboard.js:125-134), then re-downloaded wholesale by the post-populate reload (dashboard.js:1484-1487). Descriptions — the bulk of the payload — are only needed in the expanded row and are already lazily fetchable.
- Evidence: server.py:230-254; session_store.py:116-127; dashboard.js:125-134, 1484-1487; perfbench.py (7.61 MB vs 1.20 MB at 598a9dd)
- Fix: omit descriptions from /releases; refetch instead of reload after populate; optional date-range param.

### PERF-6 · medium · confirmed — Every mutation rewrites an entire JSON store with no locking — O(library) I/O per toggle and lost updates under frontend-generated concurrency
Still present. Measured at 598a9dd: viewed toggle 3 ms/307 KB (fine singly); embed-cache write 38 ms/7 MB (not fine at per-request frequency). The frontend routinely creates the racing writers (markVisibleRows bursts, un-deduped ensureEmbed calls). Storage-layer view of the ARCH-1/PY-8 cluster.
- Evidence: server.py:94-166, 208-228; session_store.py:45-50, 185-212
- Fix: one lock per store or SQLite (ARCH-7); batch embed writes; client-side in-flight dedupe.

### PERF-7 · medium · confirmed — search_messages downloads every Gmail result page before the max_results cap is checked
Still present, relocated. Pagination runs to exhaustion without passing maxResults (gmail_client.py:244-257); the cap is enforced afterward in the pipeline (pipeline.py:140-142), discarding everything. A first-run backlog over the 2000 default (server.py:60) burns ~50 sequential list calls just to show the max-results modal — precisely the heavy-user first-run scenario. The IMAP path likewise returns all matching IDs before the cap is applied.
- Evidence: gmail_client.py:244-257; pipeline.py:140-142; server.py:60
- Fix: pass maxResults to list() and break pagination as soon as the cap is exceeded.

### PERF-8 · low, not verified — Hover-prefetch has no in-flight dedupe: hover + click + star can triple-fetch the same Bandcamp page, each rewriting the full embed cache
Still present. The 200 ms debounce cancels only the timer, not in-flight fetches (dashboard.js:917-931, 429-458); 2-3 concurrent identical /embed-meta requests each do an independent fetch + parse + whole-file rewrite. Overlaps JS-6/PERF-2.
- Fix: Map<url, Promise> in ensureEmbed; cache-first /embed-meta.

## Investigated, not issues

Concerns raised in the audit brief or by finders that verification explicitly cleared. Recorded so nobody re-litigates or "fixes" them. (Re-checked against e363bf4 where line-dependent.)

- **SSE log injection into the DOM (XSS via populate log)** — not a live vector: the SSE handler renders server log lines via textContent (dashboard.js:1473-1478); only client-built, date-input-constrained strings hit innerHTML (dashboard.js:567, 574).
- **release_cache.json full-rewrite cost** — happens once per populate run (pipeline.py:183), not per batch; a non-issue.
- **Quadratic dedupe_by_url inside persist_release_metadata** — quadratic only per day-bucket, and day buckets are tiny; fine at any realistic scale.
- **Single viewed/starred toggle write cost** — 3 ms of file I/O at 5k viewed URLs; invisible. Only the bulk-concurrent case is a problem (PERF-1).
- **Single renderCalendar/setViewed re-render** — ~10 ms + a 49-node rebuild; imperceptible in isolation. Only per-row loops are a problem (JS-11/ARCH-6).
- **/releases server-side latency** — 39 ms per request even fully enriched; the real concern is payload-size trajectory and reload-after-populate (PERF-5), not current latency.
- **SSE missing keep-alive** — fine for this deployment: same-machine localhost, no proxy. (The lock-released-on-disconnect problem is separate and real — PY-8/JS-10.)
- **5-second health poll and per-request config fetch** — trivial overhead; not worth changing.
- **Secrets in the repo** — nothing sensitive is tracked; at e363bf4 secrets moved further out of reach (system keychain via credential_store.py).
- **Raw-HTML injection through the old markdown renderer** — the renderer is gone; note the markdown-it-py replacement runs with `html: True` over repo-shipped docs (see the SEC-8 entry in Fixed upstream).
- **SSRF reading local files via /embed-meta** — requests has no file:// adapter, so SEC-2 is bounded to network SSRF (still high for network targets).
- **PyInstaller assets guaranteed to 500** — refuted detail of ARCH-9: `Path(__file__)` resolution likely works under _MEIPASS; the debt is fragility and dead code (now gmail_client.py:52-55), not certain breakage.
- **Calendar glow ring on populated/selected days as a live style** — the `.calendar-day.scraped` rules (dashboard.css:539-561) are dead code; JS applies `unseen-day` (dashboard.js:1264). Delete rather than restyle (UI-5). The unseen-dot glow (dashboard.css:528) is live (UI-8).
- **Populate button restored with the wrong label on stream error** — refuted detail of JS-10: `original` is captured before the 'Populating…' swap; 'Populate' is only an empty-label fallback.
- **'Descriptionless Bandcamp pages are refetched forever'** — corrected in PERF-2: live pages always yield a description (auto-generated credits/og:description); the forever-refetch population is failed/deleted pages, and the cache-first + negative-cache fix still applies.
- **The email decode block flagged as dead code** — inverted at e363bf4: the `email_html.decode()` block is now live in the parser feeding the empty-body guard (bandcamp_email_parser.py:23-27). Do not delete it (see PY-14).
