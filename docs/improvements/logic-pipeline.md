# Improvement plan: data & logic pipeline

Scope: refresh semantics, Gmail→release pipeline, Bandcamp enrichment, identity/dedupe, the scrape-status model, and the release data model. Companion to `docs/improvements/architecture.md` (storage/locking/SSE protocol) — cross-references use that plan's ARCH-n IDs and the audit's finding IDs (`docs/current-state/known-issues.md` for the verified findings register, `docs/audit/process.md` for the method).

Sizes: **S** ≤ half a day, **M** ≤ 2 days, **L** > 2 days. Every item has a stable ID (`LOG-n`); do not renumber — retire IDs instead.

## Invariants (design ground rules)

These are the first principles every item below must respect. They generalize what the app already believes.

- **I1 — Recent history is never final.** The code already excludes *today* from the scrape ledger (`_save_date_set(drop_today=True)`, session_store.py:70-83) because today's emails are still arriving. The same reasoning applies to the last few days (delivery lag, Gmail sync, interrupted runs). "Final" must be a claim the system can actually stand behind.
- **I2 — Data before ledger.** Never record "day X is done" until the releases for day X are durably persisted. A ledger entry without data is silent permanent loss under the never-re-fetch rule (ARCH-2/PY-1).
- **I3 — Refresh is additive.** Re-scanning a range merges by identity (URL); it never deletes rows, and it never touches user-created state (stars, seen). Gmail is append-mostly; a release once seen stays seen.
- **I4 — URL is identity.** One canonical URL = one release, everywhere (release cache, viewed, starred, embed cache). Anything without a URL is not a release.
- **I5 — Failures are visible and recoverable.** A parse error skips one email (with a counted, logged skip), not the run; a corrupt store is preserved for recovery, not silently reset; a failed enrichment is recorded and retryable, not refetched forever or forgotten.

---

## 1. The append-only-cache trap: refresh semantics

Today, once a day is in `scrape_status.json`, it is never queried again (`cached_releases_for_range`, session_store.py:229-236). Emails that arrive late, runs that crashed mid-way, and parser improvements are all invisible forever; the only user recovery is "Clear cache", which also destroys stars and seen-state (UX-4, UX-12). Three complementary mechanisms fix this without abandoning the cache's purpose (don't hammer Gmail).

### LOG-1 — Persist per range, mark scraped last (S)

**Problem.** `populate_release_cache` marks each range scraped immediately after download (pipeline.py:114) but persists releases once, at the very end (pipeline.py:123). Any exception in between — and the parse stage has known crashers (PY-3, PY-7) — permanently loses those days (ARCH-2/PY-1, rated highest-severity in the audit).

**Change.** Inside the per-range loop: `persist_release_metadata(new_releases)` first, then `mark_date_range_scraped(...)`. Make this ordering an explicit invariant (I2) with a comment; the empty-range path already does it correctly (`persist_empty_date_range` persists and marks together, pipeline.py:102).

**Acceptance criteria.**
- Kill the process (or raise) after range 1 of a 3-range populate: range 1's releases are in `release_cache.json` and its days marked scraped; ranges 2-3 remain unscraped and are re-fetched next run.
- No code path writes to the scrape ledger for a day whose releases have not been persisted in the same or an earlier step.
- A fixture test covers the mid-run-failure sequence.

**Migration.** None. Users already bitten (days marked scraped, no data) recover via LOG-3.

### LOG-2 — Trailing settling window: the last N days are never final (M)

**Rationale.** The today-exclusion encodes "today's emails are still arriving" — but emails for *yesterday* can also still be arriving when the user populates at 00:10, and timezone skew (LOG-12) blurs day boundaries by up to a day. Generalize: days within `N` days of today (default `N=3`, constant, no UI needed) are never recorded as final. This is the automatic half of refresh; it makes the common late-arrival case self-healing with a bounded, tiny Gmail cost (≤ N days re-queried per populate).

**Change.**
- Days ≥ `today - (N-1)` are never written to the scrape ledger (extend the `drop_today` logic in `_save_date_set`/`mark_*` to a window).
- **Releases for window days ARE persisted** (unlike today's current behavior: `persist_release_metadata` skips `day == today`, which under this design would drop fetched data — remove the release-skip, keep the ledger-skip). Persisting is safe because merge is by URL (I3); the ledger alone decides re-querying.
- `cached_releases_for_range` must treat the ledger as the *sole* authority for "done": currently a day with any cached releases is treated as complete even if not marked scraped (`if releases_for_day: ... elif cursor not in scraped_dates`, session_store.py:229-236). Change to: `missing = day not in scraped_dates`; cached data for missing days is still returned for display/merge.
- `/scrape-status` reports window days as not-scraped (it already forces today false, session_store.py:179); the frontend's populate-button gating then keeps the button enabled when the selection touches the window — pair with a UX-plan item so the button copy explains "recent days are re-checked".

**Acceptance criteria.**
- Populate June 1–30 on July 2 (N=3): June 30 and July 1 are queried, their releases persisted, but neither appears in `scrape_status.json`; populating the same range again re-queries only June 30–July 1.
- An email that arrives a day late (within the window) appears after the next populate with no manual action.
- Re-querying a window day with unchanged Gmail results produces zero duplicate rows and zero lost stars/seen flags.
- Unit tests freeze `date.today()` (needs the test seam from ARCH-10) and cover window boundaries.

**Migration.** Lazily ignore-on-read: ledger entries inside the window are treated as unscraped even if present in the file, and dropped on next save. No one-shot rewrite needed.

### LOG-3 — Manual re-check of any range (S)

**Rationale.** The settling window can't cover everything: interrupted historical runs (pre-LOG-1 damage), parser fixes the user wants applied to old days, "I'm sure there was a release that week". The user needs a non-destructive refresh, per range or per day (UX-12). All the machinery exists — `mark_dates_not_scraped` (session_store.py:159-165) is currently dead code.

**Change.** Add `refresh=1` to `/populate-range-stream`: before computing missing ranges, remove the selected days from the scrape ledger (resurrect `mark_dates_not_scraped`) and from the empty-day record (or, after LOG-11, reset the day status). Then run the normal pipeline; merge semantics are already additive (I3). A single-day selection gives per-day re-scrape for free. Frontend exposure (a quiet "Re-check this range" affordance next to the disabled populate button / calendar context action) belongs to the UX plan; this item is the backend contract.

**Acceptance criteria.**
- A range fully marked scraped, refreshed with `refresh=1`, is re-queried from Gmail; a late-arriving email now appears as a row.
- Stars, seen-state, and embed cache entries for existing releases in the range are untouched; no duplicate rows.
- `refresh` without valid dates → same SSE error contract as other parameter errors.
- Days re-checked and found empty are recorded empty again (no infinite "missing" state).

**Migration.** None. This is also the documented recovery path for LOG-1/LOG-4 damage — release notes should say "if past populates seemed to lose days, select the range and Re-check".

### LOG-4 — Corrupt stores are preserved, not silently reset (S)

**Rationale.** `_load_cache` returns `{}` on any exception (session_store.py:39-42) and the next persist overwrites the file — corruption converts to permanent data loss, while the scrape ledger still claims those days are done (PY-12). This violates I5 and compounds the append-only trap.

**Change.** In all store loaders (session_store.py and the server.py duplicates until ARCH-8 consolidates them): distinguish "missing" from "unparseable"; on `JSONDecodeError`, rename the file to `<name>.corrupt-<timestamp>` , log loudly (and surface via the status channel on next populate), then start fresh. Document that a corrupt release cache + intact ledger is repaired by LOG-3 re-check.

**Acceptance criteria.**
- Truncate `release_cache.json` by hand: next load renames it to `release_cache.json.corrupt-<ts>`, the app keeps working, and a visible warning (log + status line) tells the user data was set aside and how to re-check.
- A missing file still loads silently as empty (first-run path unchanged).

**Migration.** None.

---

## 2. Enrichment pipeline (Bandcamp preload)

Enrichment is currently: frontend loops serially over releases, calling `/embed-meta`, which fetches bandcamp.com on *every* call (it never reads its own cache, server.py:411-444), does two full BeautifulSoup parses, rewrites the whole `embed_cache.json` per item, has no rate limiting/backoff, and permanently refetches failures (PERF-2/PERF-3, PY-10). The loop is uncancellable and reports progress only as log lines (UX-11).

### LOG-5 — Cache-first `/embed-meta` with negative caching (S)

**Rationale.** A cache written but never read is not a cache. And failures must be recorded: the audit verified the "refetch forever" population is failed fetches (404/deleted pages, missing `bc-page-properties`) — every render of a starred row refires them.

**Change.**
- `/embed-meta` consults the embed cache before any network fetch; hits return immediately.
- Every fetch outcome is recorded: success → `{status:"ok", release_id, is_track, embed_url, description, fetched_at}`; empty description → `description:""` (distinguish "fetched, none" from "never fetched"); failure → `{status:"error", code:"http_404"|"no_meta"|"network", fetched_at}` with a retry TTL (e.g., 7 days) after which one retry is allowed; permanent 404/410 can use a longer TTL.
- Wrap `extract_bc_meta`'s `ast.literal_eval` fallback so parse failures become a recorded `no_meta` error, not an uncaught 500 (PY-10).
- Frontend "is this cached?" logic keys off `status` (or `embed_url` presence), not `embed_url && description` (JS-6/PERF-2).

**Acceptance criteria.**
- Two consecutive `/embed-meta` calls for the same URL trigger exactly one bandcamp.com fetch.
- A release whose page 404s is fetched once, shows a failed state, is not refetched on render/hover/preload until TTL expiry, and is excluded from "N remaining to preload" counts (counted separately as failed).
- A page with no about/credits/og:description is never refetched (description `""` cached).

**Migration.** Existing `embed_cache.json` entries lack `status`/`fetched_at`: treat entries with `embed_url` as `status:"ok"` on read; write the new shape on next touch. No sweep required.

### LOG-6 — Server-side preload job: worker pool, progress, cancel, resume (M)

**Rationale.** The preload loop belongs next to the cache and the rate limiter, not in the browser: a page reload currently orphans knowledge of what's in flight, per-item whole-file rewrites cost O(cache) each (38 ms at 5k entries — ~1.4 GB written per 200-item run, PERF-3), and politeness policy can't be enforced client-side. The populate SSE pattern is the precedent.

**Change.**
- New endpoint (SSE, mirroring `/populate-range-stream`): `GET /preload-range-stream?start&end`. Server selects candidates = releases in range with no `ok`/fresh-`error` embed entry, fetches with a small worker pool (2–3 workers) through the shared fetch function (LOG-7).
- Emits typed events (adopt the ARCH-4 JSON event protocol from day one): `{type:"progress", done, total, url, title, status:"ok"|"failed", reason}` per release, plus terminal `{type:"complete", ok, failed, skipped}`.
- Cancellable: a cancel request (or client disconnect) stops scheduling new fetches; in-flight ones finish and persist.
- Resumable by construction: each result is persisted as it completes (batched flush every ~10 items plus on completion to avoid per-item whole-file rewrites); a re-run skips everything already recorded. No job state beyond the cache itself.
- Guard with its own non-reentrant lock (one preload at a time), released by the worker, not the SSE generator (avoid repeating the POPULATE_LOCK disconnect bug, ARCH-1).
- Keep client-side `ensureEmbed` for on-demand single fetches (hover/expand/star) — with an in-flight promise dedupe (JS-6, UX/JS plan).

**Acceptance criteria.**
- Preloading 50 releases issues ≤ 3 concurrent Bandcamp requests, respects the LOG-7 pacing, and completes several times faster than the serial baseline without burst-hammering Bandcamp.
- Killing the browser mid-run: fetches already completed are persisted; re-running preloads only the remainder.
- Cancel stops the run within one in-flight request; the UI receives a terminal event with accurate ok/failed/skipped counts.
- Total bytes written to `embed_cache.json` for an n-item run is O(cache × n/10), not O(cache × n).

**Migration.** None (embed cache shape already migrated by LOG-5). The frontend serial loop is deleted in the same change.

### LOG-7 — One polite Bandcamp fetch function (S)

**Rationale.** All Bandcamp traffic (on-demand `/embed-meta`, preload workers) must share one choke point, or politeness is unenforceable. Today there is no throttle, no 429 handling, no response-size cap, redirects and schemes are unvalidated (SEC-2), and the page is parsed twice (PERF-3).

**Change.** A single `fetch_release_page(url)` used by every caller:
- Token-bucket rate limit (e.g., max 1 request/s sustained across all callers), `User-Agent: bcfeed/<version>`.
- On 429/503: honor `Retry-After`, else exponential backoff with jitter, bounded retries (2), then a recorded failure (LOG-5).
- Timeout (10 s), response-size cap (e.g., 5 MB), redirect limit; scheme/host allowlist (https, `*.bandcamp.com` + the release's own custom domain) with private-IP blocking — implements the SEC-2 fix at the same seam.
- Parse the body once into one soup passed to both `extract_bc_meta` and `extract_bandcamp_description`.

**Acceptance criteria.**
- A synthetic 429 with `Retry-After: 2` delays and succeeds on retry; a persistent 429 records a retryable failure and does not abort a preload run.
- Concurrency 3 with the rate limiter never exceeds the sustained rate in a 60 s window (testable with a fake clock).
- `/embed-meta?url=http://192.168.1.1/` and non-Bandcamp/non-release hosts are rejected without any network fetch.
- One BeautifulSoup parse per page (measurable: parse time roughly halves per item).

**Migration.** None.

---

## 3. Identity & dedupe semantics

### LOG-8 — A release without a URL is not a release (S)

**Rationale.** URL is the primary key for dedupe, stars, seen-state, and enrichment (I4). Yet the pipeline caches null-URL rows: when `scrape_info_from_email` returns all-None (no release link, or subject gate rejected a "Re:" reply), the separately-parsed `date` defeats the all-None guard (pipeline.py:38, PY-5), producing unusable rows that can never be starred, seen, or enriched, and that `dedupe_by_url` keeps unconditionally (util.py:59-64).

**Change.** Gate on `release_url is not None` in `construct_release_list`; count and log skips (`"Skipped 3 emails with no release link"` — feeds the typed progress events). Remove the `without_url` passthrough in `dedupe_by_date`/`dedupe_by_url` once the producer is fixed (they should never see one; assert/log if they do).

**Acceptance criteria.**
- A fixture email with no `/album/`/`/track/` link produces zero cached rows and one counted skip line.
- `/releases` never returns a row with `url: null`.

**Migration.** One-time sweep on load (or a small migration step): drop entries with null `url` from `release_cache.json`. Nothing references them (they can't be starred/viewed), so removal is invisible except junk rows disappearing.

### LOG-9 — Canonical URL normalization across all URL-keyed stores (M)

**Rationale.** Identity must be stable across representations. Today the key is "first matching href, args+fragment stripped" (gmail.py:225-235). `http://` vs `https://`, host case, and trailing slashes each fork identity: the same release starred under one form and re-fetched under another silently loses its star and re-enriches. Gmail link formats have changed before and will again.

**Change.** One `canonical_release_url(url)` in util.py: lowercase scheme+host, force https, strip query/fragment (already done), strip trailing slash, preserve path case (Bandcamp slugs are case-sensitive in principle; lowercase only the authority). Apply at parse time and at every store lookup (viewed, starred, embed cache, `/embed-meta` param).

**Acceptance criteria.**
- `HTTP://Artist.Bandcamp.com/album/X/?from=email#top` and `https://artist.bandcamp.com/album/X` dedupe to one row and share star/seen/embed state.
- Property test: `canonical(canonical(u)) == canonical(u)`.
- All four URL-keyed stores are keyed by canonical form after migration.

**Migration.** Required, one-shot at startup (guarded by the LOG-21 schema version): rewrite keys/values of `release_cache.json` (each release's `url`), `viewed_state.json`, `starred_state.json`, `embed_cache.json` through the canonicalizer, merging collisions (union for sets; keep=last for cache rows). Back up each file to `<name>.pre-v2` before rewriting.

### LOG-10 — Codify dedupe tie-breaking and `is_track` precedence (S)

**Rationale.** `dedupe_by_date(keep="last")` (pipeline.py:117, util.py:68-95) is correct but undocumented, and it crashes on a malformed cached date (util.py:81 → bricks every populate touching that day, PY-7). The rationale worth writing down: the same URL can arrive on multiple dates (announcement email, then release email; or re-sends); *keep=last* means the row lands on the most recent notification date — closest to actual availability, and it converges regardless of the order cached+new lists are combined (`>=` makes later-processed equal-date entries win deterministically). `is_track` has two sources: URL path heuristic at parse time (gmail.py:241-242) and Bandcamp's own `item_type` via the embed overlay (server.py:435, 309) — precedence is currently implicit.

**Change.**
- `dedupe_by_date`: use `parse_date(..., allow_none=True)`; items with missing/unparseable dates are logged, counted, and treated as "oldest" (never win a keep=last conflict) rather than raising.
- Docstring the keep=last rationale (above) in util.py.
- Document precedence: embed metadata (`item_type`) is authoritative for `is_track`; the path heuristic is the pre-enrichment fallback. Keep the read-time overlay (no write-back into `release_cache.json` — one owner per field; see LOG-20).

**Acceptance criteria.**
- A hand-corrupted date on one cached release no longer aborts populate; the run logs one skip and completes.
- Unit tests: announced-then-released same-URL pair keeps the later date; order-of-combination does not change the result.

**Migration.** None.

---

## 4. Scrape-status model

### LOG-11 — One per-day ledger; fold `no_results_dates.json` into it (M)

**Rationale.** "Which days are done" is one fact stored in two files: every empty range is written to *both* `no_results_dates.json` and `scrape_status.json` (`persist_empty_date_range` calls `mark_date_range_scraped`, session_store.py:257-273), and `cached_releases_for_range` unions them back together (224). Two stores encoding one fact, updated non-transactionally, is a consistency hazard for zero benefit — the "empty" distinction is derivable (`scraped and no cache entries for that day`). **Per-day vs. ranges:** keep per-day. Days are the natural unit (per-day re-scrape, calendar rendering, window exclusion); ranges are derived cheaply (`collapse_date_ranges` exists) and a range representation would complicate every partial-invalidation path LOG-2/LOG-3 need. Volume is trivial (10 years ≈ 3,650 ISO strings).

**Change.** Single ledger file (`scrape_status.json`, values per day: `"checked"` — or a `{date: status}` map if a future per-day state like `"failed"` is wanted; a plain set is acceptable). Delete `no_results_dates.json` and its helpers; "empty day" = in ledger, no cache bucket. `persist_release_metadata` no longer maintains the empty set (its remove-from-empty logic, session_store.py:206-208, becomes moot).

**Acceptance criteria.**
- Populating an empty range marks days checked with no second file written; the calendar and `cached_releases_for_range` behave identically to before (fixture parity test: same inputs → same missing-ranges output).
- `paths.py` no longer defines `EMPTY_DATES_PATH`; grep finds no readers.
- LOG-3 refresh of a formerly-empty day works (day leaves the ledger, is re-queried, and can become non-empty).

**Migration.** One-shot: union `no_results_dates.json` into the ledger (it already is a subset, so effectively: delete the file). Guarded by the LOG-21 schema version; keep a `.pre-v2` backup.

### LOG-12 — Timezone-coherent day bucketing (M)

**Rationale.** Three clocks currently define "a day": (1) Gmail's `before:`/`after:` operators interpret dates in the *Gmail account's* timezone; (2) the bucket date comes from the email `Date` header via `parsedate_to_datetime(...).date()` (gmail.py:187-192) — i.e., the *sender's* (Bandcamp's) timezone offset; (3) today-exclusion and the calendar use the *local machine's* date (`date.today()`, six call sites). A release email sent 23:30 PST can bucket to a different day than the one the query fetched it under, and than the one the user sees on the calendar. Consequences: boundary emails silently missed at range edges, releases persisted under days outside the queried range, and the today/window exclusion misfiring by one day. This is exactly the class of invisible loss the append-only cache turns permanent — the settling window (LOG-2) masks the trailing edge but not historical range edges.

**Change.**
- Bucket by the user's local date: convert the parsed `Date` header to the local timezone before `.date()` (one-line change at pipeline.py:32 / gmail.py:187-192); document that all day semantics are local-time.
- Pad Gmail queries for *refresh* scans (LOG-2 window days, LOG-3 re-checks) by ±1 day; persistence is merge-by-URL so over-fetch is harmless (I3), and the pad absorbs the Gmail-account-timezone skew. First-time scans keep the current `after:start`/`before:end+1d` bounds (already end-inclusive) — padding everything would mark unqueried days' neighbors inconsistently, so the pad applies to query bounds only, never to which days get marked scraped.
- Unify "today" logic behind one helper (also the freeze-point for tests, ARCH-10).

**Acceptance criteria.**
- A fixture email with `Date: ... 23:30:00 -0800` buckets to the user-local date, matching what the calendar shows.
- Refresh of a range whose boundary email sits in the skew window picks the email up (fixture with a mocked Gmail search).
- No code path calls `datetime.date.today()` directly except the shared helper.

**Migration.** Historical buckets may differ by one day from the new rule; no rewrite (dates are cosmetic-plus-grouping, and rewriting can't recover the original timestamps from the cache — the header is gone). A LOG-3 re-check of a range re-buckets its releases correctly because `dedupe_by_date(keep="last")` migrates a URL to its newly-parsed date. Document this: "re-check a range to fix off-by-one dates".

### LOG-13 — Codified write ordering + store locking (S, shared with architecture plan)

**Rationale.** The multi-file persist (`cache → empty-dates → ledger`, session_store.py:209-212) is non-transactional, all stores are load-modify-save with no locks (PY-8/ARCH-1), and POPULATE_LOCK is released by the SSE generator while the worker still runs (server.py:619-620) — two populates can interleave. The storage consolidation itself (single store module or SQLite) is ARCH-7/ARCH-8 territory; this item pins the *logic-level contract* any storage answers to.

**Change.** Write the contract into the store module and enforce it: (a) data before ledger, always (I2 — after LOG-11 that's `cache → ledger`); (b) one process-wide lock around every store load-modify-save; (c) unique temp names per write; (d) populate/preload run-locks are released by the worker's `finally`, never by client disconnect.

**Acceptance criteria.**
- A concurrency test (threads hammering viewed/starred toggles + a populate) loses zero writes.
- Client-disconnects mid-populate: a second populate request is rejected until the worker actually finishes.
- Crash injection between store writes never yields ledger-ahead-of-data.

**Migration.** None.

---

## 5. Gmail search & parse hardening

### LOG-14 — Two-stage matching: structural classification instead of English-copy gates (M)

**Rationale.** Both the query (`subject:(New release from)`, pipeline.py:89) and the parser gate (`subject.lower().startswith("new release from")`, gmail.py:218) hard-code Bandcamp's current English subject copy. Failure modes: (a) Bandcamp rewords or localizes its notification subjects (Bandcamp's site ships in ~9 languages; a non-English account may receive localized notifications — **unverified**, see investigation below) → the app finds nothing and confidently records the days as *empty*, the worst kind of wrong under I1; (b) `subject:(...)` is unquoted, so Gmail matches the words anywhere in the subject — replies ("Re: New release from…") enter the pipeline and currently become junk rows (PY-5/LOG-8); (c) other `noreply@bandcamp.com` mail (receipts, fan-mail digests) also contains `/album/` links, so the sender alone cannot be the filter.

**Change.**
- **Investigate first (part of this item):** collect real Bandcamp notification samples — non-English account locale, current subject/body copy, custom-domain artists — and commit them (redacted) as fixtures. This decides how much of the following is needed now vs. speculative.
- Query stage (recall-oriented): search `from:noreply@bandcamp.com` with an OR of known subject forms (quoted phrases; extend list per investigation) rather than one unquoted phrase; date bounds unchanged.
- Parse stage (precision-oriented): classify each email by structure, not copy: has a release link (`/album/` or `/track/` path) **and** notification-shaped body (e.g., the "just released/announced" phrase list per locale, or link-wraps-artwork structure), **and** subject not reply/forward-prefixed. Rejected emails are counted skips (LOG-8), never junk rows, never a reason to abort.
- Keep the strict-prefix check as one signal among several rather than a binary gate.

**Acceptance criteria.**
- Fixture suite: current-format release email (album, track, custom domain, "by artist" variant), receipt email, reply email, fan-mail digest — classifier accepts exactly the release notifications.
- A subject-copy change that defeats the phrase list degrades to counted skips, visible in the run summary ("N emails matched search but were not release notifications") — not to silently-empty days.
- If localized notifications exist (per investigation), at least one non-English fixture parses or is explicitly documented as unsupported with a visible skip.

**Migration.** None. Re-parsing improvements reach old days via LOG-3 re-check.

### LOG-15 — Release-link selection: candidates, not first-anchor-wins (S)

**Rationale.** The first `<a>` whose path contains `/album/` or `/track/` wins (gmail.py:225-235). A footer/marketing link with those fragments would beat the real release link; the actual notification repeats the release link several times (artwork, title, button). Custom-domain support (path heuristic) is correct and must be kept.

**Change.** Collect all candidate release links (canonicalized per LOG-9); pick the most frequent; prefer candidates whose anchor wraps an `<img>` or the italic title as a tiebreak. Log when candidates disagree.

**Acceptance criteria.**
- Fixture email with a decoy `/album/` link in the footer resolves to the repeated real link.
- Custom-domain fixture still resolves correctly.

**Migration.** None.

### LOG-16 — Early-stop Gmail search with a real cap (S)

**Rationale.** `search_messages` paginates every result page before the cap is checked and the ids discarded (gmail.py:128-148 vs pipeline.py:95-96, PERF-7) — the heavy first-run backlog case burns ~50 list calls just to show the "too many results" modal. Also `max_results` from the query string is unclamped and un-validated (`int(...)` 500s on garbage; nothing enforces `GMAIL_MAX_RESULTS_HARD`, server.py:559/46).

**Change.** Pass `maxResults=min(500, cap+1)` to `messages().list`, stop paginating once `len > cap`; clamp and validate the client-supplied `max_results` server-side (parse failure → SSE error event, value → `min(value, GMAIL_MAX_RESULTS_HARD)`).

**Acceptance criteria.**
- An over-cap search performs ≤ `ceil((cap+1)/500)` list calls before raising `MaxResultsExceeded`.
- `max_results=abc` → SSE error event, not an HTML 500; `max_results=10000000` behaves as the hard cap.

**Migration.** None.

### LOG-17 — Batch download: public API, backoff, honest errors (M)

**Rationale.** `get_messages` reads the private `batch._responses` attribute and hand-parses raw bodies (gmail.py:158-174) — one library upgrade from breaking outright; a single 429 aborts the whole populate with an error message telling the user about a `--batch` CLI flag that does not exist (PY-9). Under LOG-1 that abort is no longer data loss, but it's still a failed run for a transient condition.

**Change.** Use `batch.add(request, callback=...)` (parsed responses + per-message exceptions); on 429/5xx, retry the failed subset with exponential backoff + jitter (bounded, e.g., 3 attempts), then fail the run with an accurate message; per-message permanent errors (404 on one message) skip that message with a counted log, not the run.

**Acceptance criteria.**
- Simulated single-batch 429 → run completes after backoff; user sees a "rate-limited, retrying…" progress line.
- No references to `_responses` or the `--batch` text remain.
- One 404 message out of 200 yields 199 parsed emails and one counted skip.

**Migration.** None.

### LOG-18 — Parse stage never aborts a run (S)

**Rationale.** One weird email currently kills the whole populate (and, pre-ARCH-3 fixes, silently): no-HTML-part emails crash on `soup.find_all` (gmail.py:222/226/235, PY-3 — empirically reproduced); a missing/garbled `Date` header raises at pipeline.py:32 (PY-7); the speculative `quopri.decodestring` pass can silently corrupt legitimate HTML the Gmail API already decoded (gmail.py:66-72, PY-11). Per-email failure must cost one email (I5).

**Change.**
- Early-return the None-tuple when `email_text` is falsy (before `_find_bandcamp_release_url`).
- Wrap the per-email parse in `construct_release_list` in try/except: log, count, skip.
- `parse_date(email.get("date"), allow_none=True)`; date-less emails are skipped with a count (they can't be bucketed).
- Delete the unconditional quopri pass (the API already reverses Content-Transfer-Encoding); also delete the dead `s = email_text.decode()` block (gmail.py:211-215).

**Acceptance criteria.**
- A plain-text-only email in a 50-email fixture run yields 49 releases + 1 counted skip; run completes.
- An email whose HTML contains `id=3D` style sequences round-trips byte-identical into the parser (no quopri mangling).
- Run summary reports skip counts by reason (no-html, no-date, no-link, classifier-reject).

**Migration.** None. Previously-corrupted titles/URLs in old cache rows self-heal via LOG-3 re-check.

---

## 6. Release model completion

### LOG-19 — Decide `img_url`: drop from the email-parse model; add `art_url` to enrichment (S)

**Rationale.** `img_url` is declared but never assigned (gmail.py:204), threaded through pipeline/util, and serialized as `null` forever — a dead field that misleads readers (audit low-severity cluster). Two honest options: (a) delete it end-to-end; (b) actually populate artwork. Artwork *is* cheaply available at enrichment time — the release page is already being fetched and carries `og:image` — whereas scraping the email's `<img>` adds a second, less reliable source. **Decision: (b)-at-enrichment + delete the parse-time field.** Rationale: zero extra network cost, one owner (the embed/enrichment store, per LOG-20), and it gives the UI plan the option of small thumbnails in the detail row without committing the table to them (product brief: calm, dense table — artwork display is a UI-plan decision; this item only makes the data available).

**Change.** Remove `img_url` from `construct_release`/`scrape_info_from_email`/pipeline; capture `og:image` as `art_url` in the embed-cache record during LOG-5/LOG-7 fetches; expose via the same overlay path as `embed_url`.

**Acceptance criteria.**
- `img_url` appears nowhere in the codebase; `/releases` rows no longer carry it (frontend never read it — verify with grep).
- After enriching a release, its embed-cache record contains `art_url` when the page provides `og:image`; absent otherwise (`null`, cached — no refetch loop).
- Old cache rows with `img_url: null` load fine (unknown keys ignored / stripped on next persist).

**Migration.** Lazy: stale `img_url` keys in `release_cache.json` are dropped whenever a day bucket is rewritten; no sweep needed. (If LOG-9's one-shot rewrite runs anyway, strip them there.)

### LOG-20 — Embed/description lifecycle contract (S)

**Rationale.** Enrichment data currently has no defined lifecycle: `embed_url` is stored even though it is derivable from `release_id`+`is_track` (`build_embed_url`, bandcamp.py:61-66) — stored derived state can go stale if the embed format ever changes; `description` rides along on every `/releases` response (6× payload inflation at scale, PERF-5) though only the expanded row needs it; and "fetched but empty" is indistinguishable from "never fetched" (PERF-2/JS-6). One paragraph of contract removes a whole class of bugs.

**Change.** Define and enforce: the embed-cache record is the *single owner* of enrichment state, with `status ∈ {ok, error}` + `fetched_at` (LOG-5), fields `release_id`, `is_track`, `description` (`""` allowed), `art_url` (LOG-19). `embed_url` is derived at response time from `release_id`+`is_track`, not stored. `/releases` overlays only the light fields (`release_id`, `is_track`, `embed_url`, `art_url`, `has_description` flag); the description itself is fetched per-release by the existing detail path (now cache-first per LOG-5). Parse-time release rows never gain enrichment fields (one owner per field; read-time overlay only).

**Acceptance criteria.**
- `/releases` payload contains no `description` bodies; expanding a row shows the description via one (cached) request; measured payload for an enriched library drops accordingly (~6× at 5k releases per PERF-5 benchmarks).
- No `embed_url` key exists in `embed_cache.json` after migration; embeds still render (derived).
- The states unenriched / ok / ok-no-description / failed-retryable are each visibly distinct to the frontend via `status`/`has_description`.

**Migration.** On first load with the new schema version: rewrite `embed_cache.json` dropping `embed_url`, adding `status:"ok"`/`fetched_at:null` to legacy entries (same pass as LOG-5's lazy upgrade — do them together).

### LOG-21 — Schema version + `source` field (S)

**Rationale.** Three items above (LOG-9, LOG-11, LOG-20) need one-shot migrations; without a recorded schema version each has to sniff shapes. And the release model has no `source` field, which ARCH-5 flags as the cheapest future-proofing for the roadmap's IMAP/multi-source idea — adding it now means cached data never needs a second migration.

**Change.** Add `{"schema": 2, ...}` (or a sidecar `meta.json`) to the versioned stores; a single `migrate()` at startup runs pending steps in order, backing up each touched file to `<name>.pre-v<n>`. Add `source: "gmail-bandcamp"` to `construct_release` and default it on read for old rows.

**Acceptance criteria.**
- Fresh install: stores created at schema 2, no migration log.
- Upgrade from current files: one migration pass, backups present, app functions identically; second start performs no migration.
- Every release row served by `/releases` carries `source`.

**Migration.** This item *is* the migration machinery; its own change is additive.

---

## Sequencing & dependencies

Correctness first (all small), then the model changes that need migrations, then throughput.

| Order | Items | Why first |
|---|---|---|
| 1 | LOG-1, LOG-18, LOG-4, LOG-8 | Stop the active data loss / run-aborting crashers. No migrations. Pairs with ARCH-3 (SSE error reporting). |
| 2 | LOG-21 → LOG-11, LOG-9, LOG-20 (+ LOG-5) | Migration machinery, then the three schema changes in one release. |
| 3 | LOG-2, LOG-3, LOG-12, LOG-13 | Refresh semantics + day-boundary coherence on top of the fixed ledger. LOG-3 is also the user-facing recovery story — ship before or with the UX plan's "Re-check" affordance. |
| 4 | LOG-7 → LOG-6, LOG-14, LOG-15, LOG-16, LOG-17, LOG-10, LOG-19 | Throughput, politeness, and robustness; LOG-6 depends on LOG-5/LOG-7; LOG-14's fixture investigation can start anytime. |

Test prerequisite for nearly everything here: the `BCFEED_DATA_DIR` seam and frozen-today helper from ARCH-10 — schedule that alongside step 1.
