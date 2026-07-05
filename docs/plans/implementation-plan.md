# bcfeed — Master implementation plan

Date: 2026-07-05 · Baseline commit: 598a9dd · Status: approved plan, **no code changes yet**.

This is the execution graph for an orchestration agent driving subagents. It composes the five
approved specs into ordered, verifiable work packages (WPs). **The dependency graph is normative:
follow it literally.** Where a WP says "sequential after X", that means X's branch is merged before
this WP's branch is cut.

Input specs (every implementing agent reads the ones its WP cites):

| Prefix | Spec |
|---|---|
| PY/JS/ARCH/UI/UX/SEC/PERF | `docs/current-state/known-issues.md` (verified findings; evidence lines) |
| CQ | `docs/improvements/code-quality.md` |
| ARC | `docs/improvements/architecture.md` |
| LOG | `docs/improvements/logic-pipeline.md` |
| UXP | `docs/improvements/ux-workflow.md` |
| UIR | `docs/improvements/ui-redesign.md` (winning direction: **"Calm Slate"** with Liner Notes/Stockbook grafts) |

## Global rules (apply to every WP)

1. **Do not break the verified strengths:** the fast no-framework table, star-triggers-preload,
   calendar-as-coverage-map, honest SSE progress *content*, hover-prefetch, in-app docs, keyboard
   triage shortcuts, local-first storage. Every WP's verification must confirm these still work.
2. **No new machinery:** no web framework, no frontend build step, no TypeScript, no async rewrite,
   no telemetry (architecture plan "Explicit non-goals" is binding).
3. **Same-file = sequential.** Two WPs that touch the same source file never run concurrently.
   `server.py` is a hub until WP-11 lands; the Phase-1 lanes below encode that.
4. **Each WP lands with its tests.** From WP-03a onward, an AC phrased as "pytest: …" means the WP
   adds that test in its own `tests/test_<wp-topic>.py` file (avoids test-file merge conflicts).
5. **Formatting first.** WP-01 (ruff format / prettier) lands before any other code WP, alone, and
   its commits go into `.git-blame-ignore-revs`. No other WP reformats untouched code.
6. **Wording rules apply from day one.** All *new* user-visible strings written in any WP follow the
   UXP-1 vocabulary table (no populate/preload/cache/scraped/token in new copy). WP-26 is the final
   enforcement sweep of pre-existing strings, not a license to write banned terms earlier.
7. **Definition of done** (per WP): all acceptance criteria demonstrably pass · the WP's stated
   verification method was actually run (paste output/screenshots in the PR) · `ruff check` +
   `ruff format --check` + `prettier --check` clean · full pytest suite green · Playwright smoke
   green once it exists (WP-03b onward) · traceability table (Appendix A) updated.

---

## Phase graph

```
PHASE 0 — foundations
  WP-01 (format, exclusive)
    → WP-02 (test seam + 127.0.0.1 bind, exclusive)
       ├─→ WP-03a (test harness core) ──→ WP-04 → WP-05 → WP-06 ┐   (backend lane, sequential)
       ├─→ WP-07 (frontend fixes) ───────────────────────────────┼─→ WP-08 (lockdown, last)
       ├─→ WP-03b (test breadth + Playwright; goldens after WP-09)
       └─→ WP-09 (docs/licensing, parallel-safe)

PHASE 1 — protocol + backend        (starts when Phase 0 fully merged)
  WP-10 (typed SSE, exclusive first)
    ├─→ Lane A: WP-11 → WP-12 ┐
    └─→ Lane B: WP-16 ────────┼─→ WP-13 → WP-14 → WP-17 → WP-15
        (A ∥ B: disjoint files)

PHASE 2 — UI system                 (WP-18 may start once WP-10, WP-13, WP-17 are merged;
  WP-18 → WP-19 → WP-20 → WP-21      it runs concurrently with WP-15 — disjoint files)

PHASE 3 — UX flows
  WP-22 → { WP-23 ∥ WP-24 ∥ WP-25 } → WP-26
  (23/24/25 in worktrees; fixed merge order 23, 24, 25 — see orchestration notes)

PHASE 4 — polish + packaging
  WP-27 (perf polish)      — after WP-14 + WP-18
  WP-28 (packaging prep)   — after WP-09; any time from Phase 1 on (own lane, see files note)
  WP-29 (.app bundle)      — after WP-28 + WP-30's version tag
  WP-30 (docs + release)   — after WP-26
  WP-31 (SQLite, OPTIONAL) — decision gate; after WP-03b + WP-05 + WP-14
```

### Cross-phase edges (hard dependencies, spelled out)

| Edge | Why |
|---|---|
| WP-02 → every WP with pytest ACs | `BCFEED_DATA_DIR` seam is the test isolation point |
| WP-03a → WP-04, WP-05 | conftest + email fixtures needed by their ACs |
| WP-03b → WP-18 | Playwright smoke is the regression net for the module split (ARC-4 AC 5) |
| WP-07 → WP-18 | escaping (CQ-21) must exist before `table.js` is born from that code |
| **WP-10 → WP-22** | **structured SSE must land before the progress UI** (determinate bar consumes typed events) |
| WP-10 → WP-18 | `populate.js` is written once, against the final protocol (ARC-4 dep) |
| WP-11 → WP-24 | negative caching before background enrichment, or failures re-fetch forever (UXP-8 dep) |
| WP-12 → WP-17 | preload workers must go through the polite fetcher |
| WP-13 → WP-23 | async OAuth + status endpoint before the onboarding "waiting" UI (UXP-4 dep) |
| WP-13, WP-17 → WP-18 | last Phase-1 edits to `dashboard.js` must merge before it is split/deleted |
| WP-14 → WP-15, WP-17, WP-27, WP-31 | schema v2 (ledger, canonical URLs, embed record) underlies all of these |
| WP-15 → WP-24 | re-check backend (`refresh=1`) before the "Check again" UI (UXP-11) |
| WP-16 → WP-24 (soft) | early pagination stop makes UXP-21's cap detection fast; UI works without it |
| WP-17 → WP-24, WP-25 | preload job SSE + batch viewed endpoint consumed by enrichment UI and bulk mark-seen |
| WP-06 → WP-23 | `/reset-caches` flag split (CQ-03) before the delete-data dialog trusts the flags |
| WP-20, WP-21 → WP-22 | banner/toast/progress styled from tokens; a11y primitives reused |
| WP-26 → WP-30 | docs are synced to final copy once |
| WP-28 → WP-29 | resource paths/version/pins before bundling |

---

## Phase 0 — Foundations

### WP-01 · Formatting baseline
- **Goal:** every later diff is reviewable; lint infrastructure exists.
- **Scope:** CQ-50, CQ-51.
- **Files:** `pyproject.toml` (new, ruff config), `.prettierrc` + `.prettierignore` (new),
  `.git-blame-ignore-revs` (new), plus format-only commits across all `.py`, `dashboard.js/.css/.html`, `templates/`.
- **Depends:** none. **Parallel:** NO — exclusive, lands first (touches every file).
- **Size:** S.
- **Acceptance:** `ruff format --check .`, `ruff check .` (autofix-only rules applied; remaining
  violations get targeted `# noqa` with reason — the fixes themselves belong to WP-04/05/06),
  `npx prettier --check dashboard.js dashboard.css dashboard.html templates/` all clean; app boots
  and populates identically (behavior-neutral).
- **Verify:** manual smoke (launch, render dashboard, open a doc page) + grep the diff for
  non-whitespace changes (should be formatting-only).

### WP-02 · Test seam + localhost bind
- **Goal:** the codebase becomes testable; the live LAN exposure is closed on day one.
- **Scope:** CQ-40, ARC-7a, ARC-5a / SEC-1; single `today()` helper (LOG-12 prerequisite).
- **Files:** `paths.py` (env override, lazy mkdir), `util.py` (`today()`), call-site swaps in
  `session_store.py`, `pipeline.py`, `server.py` (also: `make_server("127.0.0.1", …)` at
  server.py:247 and `find_free_port` host at 256/259), `bcfeed.py` (docstring fix from CQ-02 may
  land here or WP-06).
- **Depends:** WP-01. **Parallel:** NO — exclusive (touches the backend hub files briefly).
- **Size:** S.
- **Acceptance:** `BCFEED_DATA_DIR=/tmp/x python -c "import session_store"` touches only `/tmp/x`
  and creates no directory at import time; no source file calls `datetime.date.today()` outside
  `util.today()` (grep); `curl http://<lan-ip>:<port>/health` from another interface fails while
  localhost succeeds.
- **Verify:** manual curl check + a first micro-pytest (import isolation) that becomes the seed of WP-03a.

### WP-03a · Test harness core
- **Goal:** conftest, fixture assets, CI skeleton — the substrate WP-04/05 tests plug into.
- **Scope:** CQ-44 fixture *assets* (redacted real emails: HTML release email, plain-text-only,
  linkless, track vs album, "by artist" variant), CQ-45 fixture page (+ one without
  `bc-page-properties`), CQ-47 / ARC-7d CI workflow, shared conftest (tmp data dir, frozen `today()`).
- **Files:** `tests/conftest.py`, `tests/fixtures/emails/*`, `tests/fixtures/pages/*`,
  `.github/workflows/ci.yml`, `requirements-dev.txt`.
- **Depends:** WP-02. **Parallel:** YES with WP-07, WP-09 (disjoint files).
- **Size:** S/M.
- **Acceptance:** `pytest` green locally in <30 s with no network; CI runs ruff + pytest on push and
  goes red on a deliberately broken test; fixtures contain no personal data (reviewed).
- **Verify:** pytest + CI run on a draft PR.

### WP-03b · Test breadth + Playwright smoke
- **Goal:** the regression net for everything after Phase 0.
- **Scope:** CQ-41 (pure-function tier), CQ-42 (store round-trips — current behavior), CQ-43
  (markdown goldens), CQ-46 (Flask test-client smokes of *current* contracts), ARC-7b remainder,
  ARC-7c Playwright smoke (launch with seeded `BCFEED_DATA_DIR` → rows render → expand → star →
  reload persists → calendar day filters → keyboard s/u work).
- **Files:** `tests/test_util.py`, `tests/test_store.py`, `tests/test_docs_render.py` (goldens in
  `tests/goldens/`), `tests/test_routes.py`, `tests/e2e/smoke.spec.ts` (or `.py`), CI job 2.
- **Depends:** WP-03a; **soft-dep WP-09** (generate SETUP/README goldens *after* WP-09's doc edits
  merge, or regenerate — orchestrator schedules WP-09 merge first).
- **Parallel:** YES with the WP-04→06 backend lane and WP-07 (disjoint files; per rule 4, later WPs
  add their own test files rather than editing these).
- **Size:** M.
- **Acceptance:** ~25 cases green; goldens for SETUP.md/README.md/GMAIL_SETUP.md checked in;
  Playwright smoke green headless in CI; smoke asserts the do-not-break list items it can reach
  (star-triggers-preload request observed, keyboard shortcuts, persistence across reload).
- **Verify:** pytest + Playwright in CI.

### WP-04 · Pipeline correctness batch (data-loss / silent-failure trio)
- **Goal:** stop active data loss; failures become visible; one bad email costs one email.
- **Scope:** CQ-10 / LOG-1 / PY-1 / ARCH-2 (persist-per-range before mark-scraped) · CQ-11 / PY-2 /
  ARCH-3 *worker half* (catch-all `except Exception` → enqueue `ERROR: {exc}` line — the client
  already routes `ERROR:` lines; the typed terminal event supersedes this in WP-10) · CQ-12 /
  LOG-18 / PY-3 (no-HTML-part guard, per-email try/except-skip-count) · CQ-13 / LOG-8 / PY-5
  (null-URL gate) · CQ-14 / LOG-10-crash-half / PY-7 (`parse_date(..., allow_none=True)` at both
  sites; date-less items never win keep-last) · CQ-16 / PY-11 (delete speculative quopri pass +
  dead decode block).
- **Files:** `pipeline.py`, `gmail.py`, `util.py`, `server.py` (worker function only),
  `tests/test_pipeline_correctness.py`.
- **Depends:** WP-03a. **Parallel:** YES with WP-03b, WP-07, WP-09; NO with WP-05/06/08 (backend lane).
- **Size:** M.
- **Acceptance (all pytest):** raise injected after range 1 of a 3-range run → range 1 persisted+marked,
  ranges 2–3 unmarked and re-fetched next run; worker raising `RuntimeError` yields an `ERROR:` line
  on the stream, never a bare success; plain-text-only email in a 50-email fixture run → 49 releases
  + 1 counted skip; linkless email → zero cached rows; `{'date':'garbage'}` in cache no longer
  aborts dedupe; email HTML containing `=E2`/`id=3D` round-trips byte-identical.
- **Verify:** pytest; manual kill-mid-populate + re-populate recovers the interrupted days.

### WP-05 · Store hardening floor
- **Goal:** the JSON correctness floor (ARC-2a): locking, atomic unique-tmp writes, corruption
  sidestep, one persistence implementation, worker-owned populate lock.
- **Scope:** CQ-17 / LOG-4 / PY-12 · CQ-18 / PY-8 / ARCH-1 / LOG-13 / PERF-6 · CQ-30 / PY-15 ·
  POPULATE_LOCK released in worker `finally` (server.py:619-620 half of JS-10's server side).
- **Files:** `json_store.py` (new), `session_store.py`, `server.py` (delete `_load_set`/`_save_set`/
  `_load_embed_cache`/`_save_embed_cache`, repoint; lock lifetime), `tests/test_store_hardening.py`.
- **Depends:** WP-04 (same-file lane; also the pipeline reorder must precede the lock test that
  exercises it). **Parallel:** NO within the backend lane.
- **Size:** M.
- **Acceptance (pytest):** 50 concurrent viewed-state POSTs via threaded test client lose zero
  marks; truncated `release_cache.json` is renamed `*.corrupt-<ts>` (missing file stays quiet);
  no `json.dump`/`open(` file IO left in `server.py` outside bootstrap; simulated client disconnect
  mid-populate keeps the lock held until the worker exits (second populate rejected meanwhile).
- **Verify:** pytest.

### WP-06 · Backend quick wins + hygiene
- **Goal:** the mechanical S-fixes with unambiguous answers.
- **Scope:** CQ-01 (dead code — **deviation: do NOT delete `mark_dates_not_scraped`; WP-15/LOG-3
  resurrects it — leave with a pointer comment**) · CQ-02 (untruthful messages, `--batch` text) ·
  CQ-03 / PY-13 (reset-flag split) · CQ-04 (max_results clamp + JSON 400) · CQ-08 / PY-14
  (`/clear-credentials` → `/clear-token`, one-line JS call-site update) · CQ-15 / PY-6 (markdown
  link mangling) + SEC-8 (`javascript:` href scheme filter) · CQ-32 / LOG-19-delete-half (remove
  parse-time `img_url`; `art_url` capture arrives in WP-12/WP-14).
- **Files:** `gmail.py`, `pipeline.py`, `server.py`, `session_store.py`, `util.py`, `bcfeed.py`,
  `dashboard.js` (one line), `tests/test_quick_wins.py`, golden updates (deliberate, reviewed).
- **Depends:** WP-05 (backend lane); merge **after WP-07** for the one-line `dashboard.js` edit.
- **Parallel:** NO (lane). **Size:** S/M.
- **Acceptance:** pytest flag-matrix on `/reset-caches` (only the named store cleared);
  `max_results=abc` → JSON 400, `max_results=999999` clamped; golden test shows exactly one
  well-formed anchor for `[label](https://…)` and rejects `javascript:` hrefs; grep clean for
  `--batch`, `img_url`, dead blocks; endpoint name/log/behavior agree.
- **Verify:** pytest + manual smoke (docs pages render, settings buttons work).

### WP-07 · Frontend correctness batch
- **Goal:** the dashboard.js safety and honesty fixes that don't restructure anything.
- **Scope:** CQ-21 / JS-1 / SEC-7 (esc()/DOM-build all release fields; http(s)-only href/iframe src) ·
  CQ-05 / JS-9 / UX-16 (X/backdrop cancel; typo) · CQ-06 (clearTimeout in schedulePreload) · CQ-07
  (backdrop semantics) · CQ-25-client-half / JS-6 / PERF-8 (`Map<url,Promise>` in-flight dedupe;
  cache-guard keyed off `embed_url` presence) · CQ-26 / JS-8 (one `setRowReadState`; clear
  `expandedKey`) · CQ-22-minimal / JS-2 / UX-15-partial (2–3 consecutive failures before modal,
  keep polling, auto-recover, retry affordance, derive apiHost once post-config — the banner
  redesign is WP-22) · JS-4 (count label persists) · CQ-27 / JS-14 (degrade on auxiliary fetch
  failure; surface persistence-POST failures).
- **Files:** `dashboard.js`, `dashboard.html` (typo, modal buttons).
- **Depends:** WP-02. **Parallel:** YES with WP-03a/03b, WP-04→05 lane, WP-09 (disjoint files).
- **Size:** M.
- **Acceptance:** a release titled `<img src=x onerror=alert(1)>` with `javascript:` URL renders as
  literal text with a dead link; existing rows render pixel-identically (dark-mode screenshot
  compare); X/backdrop close the creds modal with no file picker; hover+focus+click on a row = one
  `/embed-meta` request (network tab); `u` on a seen row restores dot AND row tint; stop+restart
  server → UI recovers without manual reload; count label survives initial load.
- **Verify:** manual checklist with devtools network log + screenshots; Playwright smoke assertions
  extended where cheap (escaping fixture row).

### WP-08 · Localhost lockdown completion
- **Goal:** finish the security batch that needs coordinated server+client edits.
- **Scope:** ARC-5b / SEC-4 (delete `_corsify` ACAO:* incl. SSE; Host validation → 403) · ARC-5c
  (custom `X-BCFeed-Request: 1` header on all mutating routes; JS fetch wrapper adds it — SSE GET
  stays header-free, protected by Host check) · SEC-9 (generic error bodies; details to server log;
  404 not 500 for missing files).
- **Files:** `server.py`, `dashboard.js` (fetch wrapper), `tests/test_lockdown.py`.
- **Depends:** WP-06 (server lane) AND WP-07 (js lane) — last code WP of Phase 0.
- **Parallel:** NO. **Size:** S/M.
- **Acceptance (pytest + curl):** `Host: evil.example` → 403; `curl -X POST /reset-caches` without
  the header → 403; no `Access-Control-Allow-Origin` on any response; error bodies contain no
  paths/exception classes; app fully functional same-origin (Playwright smoke green).
- **Verify:** pytest + Playwright smoke + manual cross-origin fetch attempt from a scratch page.

### WP-09 · Docs, licensing, dependency hygiene
- **Goal:** repo-metadata debts cleared; no code.
- **Scope:** CQ-60 (requirements dedupe/pin, `beautifulsoup4` not `bs4` shim; `requirements-dev.txt`
  itself is owned by WP-03a — WP-09 does not touch it) ·
  CQ-61 (README/SETUP one job each) · CQ-62 (LICENSE — **decision required from maintainer: MIT
  recommended**) · CQ-63 (TODO.rtf → `docs/TODO.md`, preserve DONE history) · CQ-64-docs-half (one
  Python version story — 3.11; the VERSION constant code lands in WP-28) · CQ-65 pre-note staged
  (final verification in WP-30).
- **Files:** `README.md`, `SETUP.md`, `GMAIL_SETUP.md`, `privacy.md` (note only), `LICENSE` (new),
  `docs/TODO.md` (new), `TODO.rtf` (deleted), `requirements.txt`, `.python-version`.
- **Depends:** WP-01 only. **Parallel:** YES with everything in Phase 0 **except** WP-03b's golden
  generation (WP-09 merges first; see WP-03b soft-dep).
- **Size:** S/M.
- **Acceptance:** clean-venv `pip install -r requirements.txt` runs the app; no paragraph duplicated
  across README/SETUP; grep `3.10` hits nothing (or only `.python-version` if kept); LICENSE at
  root and referenced from README.
- **Verify:** manual venv install + in-app docs pages render (goldens updated deliberately in WP-03b).

---

## Phase 1 — Protocol + backend

Phase 1 is deliberately lane-ordered: `server.py` is a hub file until WP-11's layering shrinks it.
Lanes A and B run concurrently (disjoint files); everything else in this phase is sequential.

### WP-10 · Typed SSE protocol + in-place completion
- **Goal:** the populate stream carries structure, not prose; failure can never render as success;
  the page never reloads on completion. **This WP gates the entire Phase 3 progress UI.**
- **Scope:** ARC-1 / ARCH-4 (JSON payloads `{v, phase, current, total, message, level, text}`;
  terminal `event: done {new_releases, days_scraped}` / `event: error {code, message}` with codes
  `auth|max_results|gmail|parse|internal`; emitter object replacing `log=queue.put`) · CQ-24 /
  JS-10 / UX-9-mechanics (client: typed-event handling, blip-vs-terminal distinction, refetch
  `/releases`+status instead of `window.location.reload()`) · re-point the max-results modal and
  error routing at `code` fields (kills substring sniffing) · PERF-5-partial (no reload = no
  wholesale re-download).
- **Files:** `server.py`, `pipeline.py`, `dashboard.js`, `tests/test_sse_protocol.py`.
- **Depends:** WP-04 (worker catch-all), WP-05 (lock lifetime). **Parallel:** NO — exclusive start
  of Phase 1 (touches both hub files and the JS).
- **Size:** M.
- **Acceptance:** no substring matching on SSE payloads anywhere in the client (grep `"Maximum
  results"`, `"ERROR:"` → nothing); injected mid-run exception → `event: error` visible in UI, log
  intact, never `done`; 3-range populate delivers `current/total` sufficient for a determinate bar
  (pytest over the stream); on `done` the table updates in place — scroll/sort/filters survive
  (Playwright); disconnect mid-run + immediate re-request → "already running".
- **Verify:** pytest (stream content) + Playwright (no-reload assertion) + manual slow-populate run.

### WP-11 · Backend layering + cache-first embeds (Lane A)
- **Goal:** non-HTTP concerns leave `server.py`; the embed cache is finally read; failures are
  negative-cached in the LOG-20 record shape from day one.
- **Scope:** ARC-3 / ARCH-8 (extract `docs_render.py` = CQ-31; `bandcamp.get_embed_meta(url)` =
  cache lookup → fetch → parse → cache write; all remaining store IO through `session_store`/
  `json_store`; **one static-dir route** for future `web/js/` replacing per-file routes — pre-work
  for WP-18 so it never touches server.py) · CQ-20 / PY-10 (guarded `literal_eval`, generic JSON
  error bodies, cache-first) · LOG-5 / PERF-2 (negative caching: `status ∈ {ok,error}` + `code` +
  `fetched_at` + retry TTL; `description:""` distinguishes fetched-none from never-fetched; lazy
  upgrade of legacy entries on read).
- **Files:** `server.py`, `docs_render.py` (new), `bandcamp.py`, `session_store.py`,
  `tests/test_layering.py`, `tests/test_embed_cache.py`.
- **Depends:** WP-10. **Parallel:** YES with WP-16 (Lane B — disjoint files). NOT with WP-12/13.
- **Size:** M/L.
- **Acceptance:** `grep -n "requests\.\|BeautifulSoup\|json.dump\|open(" server.py` → no hits
  outside bootstrap; two `/embed-meta` calls for one URL → one network fetch (mocked requests,
  pytest); a 404 page is fetched once, recorded `status:error`, and not refetched before TTL;
  non-literal meta attr → JSON `{error}` 502, not HTML 500; `docs_render` importable without Flask;
  goldens still pass.
- **Verify:** pytest; manual embed playback on a real starred release (iframe still renders —
  see risk R4).

### WP-12 · Polite fetcher + SSRF allowlist (Lane A)
- **Goal:** one choke point for all Bandcamp traffic; SSRF closed.
- **Scope:** LOG-7 (token-bucket ~1 rps, UA string, Retry-After/backoff, 10 s timeout, size cap,
  single soup passed to both extractors = PERF-3 parse-once) · ARC-5f / SEC-2 (https-only;
  `*.bandcamp.com` direct; other hosts only if the exact URL is a release-cache key; resolve-and-
  block private/link-local IPs; redirects: max one hop, re-validated; require `bc-page-properties`
  before caching; uniform error bodies) · LOG-19-capture-half (`og:image` → `art_url` in the embed
  record).
- **Files:** `bandcamp.py`, `server.py` (route uses the validated entry point), `util.py` (if the
  validator lives there), `tests/test_fetcher.py`.
- **Depends:** WP-11. **Parallel:** YES with WP-16. **Size:** M.
- **Acceptance (pytest, mocked network + fake clock):** `http://…`, `https://192.168.1.1/…`,
  `https://example.com/album/x` (not cached) all 400 with identical bodies; cached custom-domain
  URL succeeds; synthetic 429 + `Retry-After: 2` delays then succeeds; sustained rate ≤ 1 rps over
  a fake 60 s window; one BeautifulSoup parse per page; `art_url` present when the fixture page has
  `og:image`.
- **Verify:** pytest.

### WP-16 · Gmail robustness (Lane B — concurrent with WP-11/12)
- **Goal:** the Gmail edge survives library upgrades, rate limits, copy drift, and big backlogs.
- **Scope:** LOG-17 / CQ-19 / PY-9 (batch callback API, no `_responses`, 429 backoff, per-message
  404 = counted skip) · LOG-16 / PERF-7 / CQ-04-followup (early pagination stop, `maxResults`
  passed through) · LOG-14 (two-stage matching: **starts with the fixture investigation** — collect
  redacted real samples incl. locale variants; quoted-OR query; structural classifier; rejects =
  counted skips) · LOG-15 (candidate link selection, decoy-footer resistant).
- **Files:** `gmail.py`, `pipeline.py`, `tests/test_gmail_robustness.py`, `tests/fixtures/emails/*`
  (additions).
- **Depends:** WP-04, WP-03a. **Parallel:** YES with WP-11→12 (Lane A). NOT with WP-10/13/14/15/17.
- **Size:** M/L.
- **Acceptance (pytest, mocked service):** callback pairing correct; single-batch 429 retries and
  completes with a visible "retrying" progress event; over-cap search performs ≤ `ceil((cap+1)/500)`
  list calls; classifier fixture matrix (release album/track/custom-domain/"by artist" accepted;
  receipt/reply/digest rejected as counted skips); decoy-footer fixture resolves the real link;
  grep `_responses` → nothing.
- **Verify:** pytest + one manual real populate of a known range (counts match pre-change run).

### WP-13 · OAuth: scope, token format, async connect
- **Goal:** read-only scope as documented; token stored as 0600 JSON; no request thread ever blocks
  on a human. **The one WP that forces user-visible re-auth — ship as a single release (risk R1).**
- **Scope:** ARC-5d / SEC-3 (`gmail.readonly`; legacy-scope token detected on load → deleted →
  `has_token:false` flow) · ARC-5e / SEC-5 (`Credentials.to_json()` atomic 0600; pickle→JSON
  one-time migration then unlink; no `import pickle` left) · SEC-6 / ARC-3-slice / UXP-4-backend
  (`/load-credentials` validates client-secret shape, saves atomically, returns immediately;
  interactive flow runs off-thread with ~3 min timeout; new status surface `waiting|done|failed`
  for the UI; `run_local_server` never invoked on a request thread).
- **Files:** `gmail.py`, `server.py`, `dashboard.js` (minimal status polling; full modal UX is
  WP-23), `tests/test_oauth.py`, `privacy.md` + `SETUP.md`/`GMAIL_SETUP.md` scope strings (CQ-65).
- **Depends:** WP-12 AND WP-16 merged (touches both `server.py` and `gmail.py`). **Parallel:** NO.
- **Size:** M.
- **Acceptance:** grep finds exactly one scope string, `gmail.readonly`; legacy full-scope token
  invalidated on first launch and reconnect flow triggers; `stat -f %Lp token.json` = 600; no
  `import pickle`; `/load-credentials` returns < 1 s regardless of OAuth state (pytest with a
  stubbed flow); abandoning the consent tab times out to `failed` without a stuck thread.
- **Verify:** pytest + one real end-to-end reconnect on the maintainer's account (manual, gated).

### WP-14 · Schema v2: migrations + model completion
- **Goal:** one migration event covering all store-shape changes; the data model gains its future.
- **Scope:** LOG-21 (schema version + ordered `migrate()` + `.pre-v2` backups + `source` field) ·
  LOG-11 (single per-day ledger; `no_results_dates.json` folded in and deleted) · LOG-9 (canonical
  URL normalization applied at parse + all four store lookups; one-shot key rewrite with collision
  merge) · LOG-20 (embed record is the single owner of enrichment state; `embed_url` derived not
  stored; `/releases` overlays light fields + `has_description` only — descriptions leave the
  payload = PERF-5 core) · LOG-10-contract-half (keep-last docstring; `item_type` precedence) ·
  LOG-19 finish (strip stale `img_url` keys in the rewrite).
- **Files:** `session_store.py`, `util.py`, `pipeline.py`, `gmail.py`, `server.py`,
  `migrations.py` (new, or in `json_store.py`), `paths.py` (EMPTY_DATES_PATH removed),
  `dashboard.js` (detail row fetches description lazily — small), `tests/test_migrations.py`.
- **Depends:** WP-13 (lane). **Parallel:** NO. **Size:** L.
- **Acceptance (pytest):** migration round-trip on fixture stores incl. starred+viewed overlap and
  URL-case collisions (union merge, star preserved); second start performs no migration; fresh
  install writes schema 2 directly; `HTTP://Artist.Bandcamp.com/album/X/?x=1#f` and
  `https://artist.bandcamp.com/album/X` share one row + star/seen/embed state; grep finds no
  `EMPTY_DATES_PATH` reader; `/releases` payload carries no description bodies and every row
  carries `source`; expanding a row still shows the description via one cached request (Playwright).
- **Verify:** pytest + Playwright smoke + manual upgrade test against a copied real data dir
  (backups present, stars intact) — risk R2.

### WP-17 · Server-side preload job + batch viewed endpoint
- **Goal:** enrichment moves server-side (resumable, polite, cancellable); bulk mark-seen gets its
  batch endpoint. Last `dashboard.js` edit before the split.
- **Scope:** LOG-6 (SSE `GET /preload-range-stream` with the WP-10 event protocol; 2–3 workers via
  WP-12's fetcher; batched flush ~10 items = PERF-3; own non-reentrant lock released by worker;
  cancel on request/disconnect) · PERF-1-backend / ARCH-6-enabler (`POST /viewed-state/batch
  {urls, viewed}` single-lock single-write) · client: delete the serial preload loop, consume the
  stream minimally (progress lines into the existing log; real UI in WP-24).
- **Files:** `server.py`, `bandcamp.py`, `session_store.py`, `dashboard.js`,
  `tests/test_preload_job.py`.
- **Depends:** WP-14 (embed record shape final), WP-12 (fetcher). **Parallel:** NO (lane).
- **Size:** M.
- **Acceptance (pytest):** 50-release preload issues ≤3 concurrent fetches at ≤1 rps; kill client
  mid-run → completed items persisted, re-run skips them; cancel stops within one in-flight request
  with accurate terminal counts; embed-cache bytes written per n-item run is O(cache × n/10);
  batch endpoint marks 300 URLs in one write with zero lost under concurrent toggles.
- **Verify:** pytest + manual preload of a real month (observe pacing in server log).

### WP-15 · Refresh semantics: settling window + re-check + timezone coherence
- **Goal:** the append-only-cache trap is dismantled: recent days self-heal, any range is
  re-checkable, day bucketing matches the calendar.
- **Scope:** LOG-2 (trailing N=3 settling window: ledger-skip not release-skip; ledger is sole
  authority in `cached_releases_for_range`; `/scrape-status` reports window days unchecked) ·
  LOG-3 / UX-12-backend / UXP-11-backend (`refresh=1` on the stream endpoint; resurrect
  `mark_dates_not_scraped`; empty-again days recorded empty) · LOG-12 (bucket by local date; ±1 day
  query pad for refresh scans only; all "today" logic via `util.today()`).
- **Files:** `session_store.py`, `pipeline.py`, `gmail.py`, `server.py`,
  `tests/test_refresh_semantics.py`.
- **Depends:** WP-14 (unified ledger), WP-10 (SSE error contract), WP-04 (LOG-1 ordering).
- **Parallel:** **YES with WP-18** (disjoint files — WP-18 no longer touches `server.py` thanks to
  WP-11's static route, and WP-15 touches no JS). NOT with any Phase-1 WP.
- **Size:** M.
- **Acceptance (pytest, frozen clock):** populate Jun 1–30 on Jul 2 → Jun 30/Jul 1 queried and
  persisted but absent from the ledger; re-populate re-queries only those days; `refresh=1` on a
  fully-checked range re-queries Gmail, late email appears, zero duplicate rows, stars/seen/embeds
  untouched; `Date: … 23:30:00 -0800` fixture buckets to the local date; refresh pad picks up the
  boundary email; no direct `date.today()` calls (grep).
- **Verify:** pytest + manual re-check of a real range ("Added 0 releases" on an unchanged range).

---

## Phase 2 — UI system

Strictly sequential chain (all four WPs share `web/js`/`dashboard.html`/`dashboard.css`). WP-18 may
begin as soon as WP-10, WP-13 and WP-17 are merged (it runs concurrently with WP-15 only).
All Phase-2 agents read `docs/improvements/ui-redesign.md` (UIR-*) **before** starting.

### WP-18 · ES-module split + render/ownership discipline
- **Goal:** the mechanical decomposition (behavior-preserving, smoke-verified) plus the single-owner
  and coalescing rules that fix the render-storm family.
- **Scope:** ARC-4 / ARCH-5 (module layout `web/js/{config,api,state,table,calendar,status,modals,
  populate,main}.js`; endpoints derived once post-config; DocumentFragment full rebuilds; in-place
  row-state mutation; `scheduleRender` microtask coalescing; day→unseen-count map per render;
  delegated tbody listeners = PERF-4-partial) · CQ-23 / JS-3 (isPopulating; button/log single
  writer) · JS-11 / ARCH-6 / PERF-1-frontend (state-mutate → one batched POST via WP-17's endpoint
  → one render) · CQ-33 / JS-12 (dead-code sweep) · CQ-34 / JS-15 (one log write API) · CQ-36 /
  ARCH-11 / JS-13-partial (fake config keys + dead branches deleted; theme decision itself lands in
  WP-20).
- **Files:** `web/js/*` (new), `dashboard.js` (deleted), `dashboard.html` (script tag). **No
  server.py changes** (static route pre-landed in WP-11).
- **Depends:** WP-10, WP-13, WP-17 (last JS edits), WP-07 (escaping), WP-03b (smoke). **Parallel:**
  YES with WP-15 only. **Size:** L.
- **Sequencing inside the WP:** commit 1 = verbatim extraction (smoke green), later commits =
  ownership/coalescing changes (smoke green after each) — keeps bisection possible.
- **Acceptance:** no IIFE; no module >~450 lines; `dashboard.js` gone; endpoint derivation exactly
  once; populate-button state written from one module (grep); mark-all-seen on 300 rows = 1 POST,
  ≤2 renders, main thread <100 ms (measured); row toggle does not rebuild tbody (node-identity
  assertion in smoke); Playwright smoke green with identical assertions before/after.
- **Verify:** Playwright smoke + PERF-1 micro-benchmark + manual full-feature pass.

### WP-19 · Inline-style extraction + naming corrections
- **Goal:** the mechanical substrate for the retheme — after this WP, WP-20 edits (almost) one file.
- **Scope:** CQ-35 (every inline `style=` → classes; JS-injected colors → classes; 1:1
  computed-style preservation, no redesign) · CQ-37 / UI-5-partial (`unseen-day` → `populated-day`;
  dead `.calendar-day.scraped` glow rules deleted; `wireframe-*` → neutral names) · UI-17-partial
  (dead CSS rules purged, `--header-bg` defined or removed).
- **Files:** `dashboard.html`, `dashboard.css`, `web/js/*` (class writes).
- **Depends:** WP-18. **Parallel:** NO. **Size:** M.
- **Acceptance:** `grep 'style=' dashboard.html` → only JS-managed `display` toggles or zero; no
  color/size literals injected from JS; grep clean for `unseen-day`/`wireframe`/dead `.scraped`;
  before/after screenshots pixel-identical in dark mode.
- **Verify:** Playwright screenshot diff (dark) + smoke.

### WP-20 · Token system + CSS rewrite ("Calm Slate")
- **Goal:** the full visual re-skin per the UIR spec: calm, both themes correct, zero AI-smell.
- **Scope:** all UIR-* token/component items, implementing fixes for UI-1, UI-2, UI-3, UI-4, UI-6,
  UI-7, UI-8, UI-9-visual, UI-10, UI-11, UI-13-visual, UI-14 (≈1100px breakpoint), UI-15, UI-16,
  UI-17, UI-18, UI-12-focus-tokens · JS-13 / UX-18 / ARCH-11-remainder (theme toggle exposed,
  `prefers-color-scheme` default, persist only explicit choice — or, if UIR declares dark-only v1,
  no dead theme knob anywhere) · version string leaves the H1 (rendered target moves to Settings;
  the constant itself is WP-28).
- **Files:** `dashboard.css` (rewritten), `dashboard.html`, `web/js/theme+status bits`.
- **Depends:** WP-19; UIR spec final. **Parallel:** NO. **Size:** L.
- **Acceptance:** every audit-measured contrast pair passes 4.5:1 **in both themes** (primary
  button, calendar day numbers, error bar, log text, badges — the exact UI-1..UI-4 sample set);
  no ambient gradients/glow shadows/hatches (grep for `radial-gradient`, `box-shadow.*rgba` glow
  patterns, repeating-linear-gradient); one SVG icon system (no emoji-as-icon, no text carats); one
  accent token + one danger token (grep `#64a8ff` → nothing); sentence-case microcopy classes; at
  1000px width no clipped column; `:focus-visible` visible on all interactive elements.
- **Verify:** scripted contrast check on the token table + Playwright screenshots of the audit's
  screenshot set **in both themes** compared against `docs/current-state/screenshots/` for intent
  (manual review) + smoke. Risk R3 applies — light theme gets equal review time.

### WP-21 · Accessibility semantics
- **Goal:** the structural a11y pass, built as semantic elements not ARIA bolt-ons.
- **Scope:** JS-7 / UI-12-remainder (calendar days = real buttons in `role=grid` with arrow keys;
  modals = `role=dialog` + focus trap + Escape in `modals.js` once; sort `th` → button +
  `aria-sort`; read-dot loses invisible-but-clickable semantics; `aria-expanded` on rows) ·
  UXP-17-cell-pairing (aria-pressed/-disabled on day cells).
- **Files:** `web/js/calendar.js`, `web/js/table.js`, `web/js/modals.js`, `dashboard.html`,
  `dashboard.css` (focus styles from WP-20 tokens).
- **Depends:** WP-20. **Parallel:** NO. **Size:** M.
- **Acceptance:** full keyboard traversal: select a range, sort a column, open/close a modal, and
  triage rows without a pointer (Playwright keyboard script); modal focus trapped and restored on
  close; VoiceOver spot-check hears day states and sort direction.
- **Verify:** Playwright keyboard spec + manual VoiceOver pass.

---

## Phase 3 — UX flows

WP-22 first (it builds the primitives everyone reuses). WP-23/24/25 then run in **parallel
worktrees** with the fixed merge order 23 → 24 → 25 (they own disjoint feature modules but all
touch `state.js`/`main.js` trivially — later merges rebase; see orchestration notes). WP-26 last.

### WP-22 · Feedback primitives: progress, toasts, banners; log demoted to Details
- **Goal:** every signal renders in its proper primitive at the locus of action; the raw log becomes
  a collapsed debug view. **Consumes WP-10's typed events — the canonical cross-phase edge.**
- **Scope:** UXP-2 (full signal map: inline calendar summary line, determinate progress bar on the
  action group, completion toast, Details disclosure) · UXP-19 / UX-3 (banner component: persistent,
  actionable, `role=alert`; canonical populate errors mapped from WP-10 `code`s; blip ≠ failure;
  **zero `alert()` left**) · UXP-20 / UX-15 / JS-2-finish (server-down becomes a non-blocking
  banner; table stays browsable; mutations disabled with explanation; auto-reconnect toast;
  full-screen backdrop deleted) · UXP-13-finish / UX-9 (toast `Added N releases · <range>`;
  transient new-row highlight) · UI-9-replacement (Details view: monospace, muted, auto-height,
  collapsed empty) · aria-live on progress region (JS-7 slice).
- **Files:** `web/js/status.js`, `web/js/populate.js`, new `web/js/feedback.js` (toast/banner),
  `web/js/main.js`, `dashboard.html`, `dashboard.css` (component styles from WP-20 tokens).
- **Depends:** WP-10 (hard), WP-18, WP-20, WP-21. **Parallel:** NO (first of Phase 3). **Size:** L.
- **Acceptance:** populate-without-credentials leaves a persistent banner with a working connect
  action that survives calendar clicks/filter changes (the exact JS-3 overwrite path, Playwright);
  simulated transient disconnect mid-run → no failure banner, normal completion toast; kill+restart
  server mid-populate → truthful success or truthful failure banner, never silent empty; progress
  bar determinate during download phase; calendar interaction mid-run cannot erase progress or
  re-enable the button; grep `alert(` in `web/js` → nothing; stop/restart server with tab open ends
  usable with zero manual reloads.
- **Verify:** Playwright (banner persistence, no-reload, server-down recovery) + pytest already
  covers the server side + manual screen-reader spot check.

### WP-23 · Onboarding (parallel worktree A)
- **Goal:** first run is a sequenced checklist, the OAuth hop is announced and supervised,
  Settings is reorganized, destructive actions are confirmed and honest.
- **Scope:** UXP-3 / UX-1, UX-5-partial (first-run panel in main content; detected step state;
  `Credentials Needed` modal + auto-open-Settings deleted) · UXP-4-UI / UX-2, UX-16 (pre-announce
  consent tab + unverified-app guidance; waiting state with Cancel; timeout → retry) · UXP-5 /
  UX-18-settings (status first, Preferences, danger zone last; version in About) · UXP-6 (Check the
  last 30 days starter — selects the range visibly, then fetches) · UXP-7 / UX-4 (delete-data
  dialog enumerating scope; stars/seen checkbox default OFF; post-action toast).
- **Files:** `web/js/onboarding.js` (new), `web/js/modals.js`, `web/js/settings.js` (split from
  modals if cleaner), `dashboard.html`, `dashboard.css`.
- **Depends:** WP-22 (primitives), WP-13 (async OAuth status), WP-06 (CQ-03 flags). **Parallel:**
  worktree-parallel with WP-24/25; merges first. **Size:** L.
- **Acceptance:** fresh data dir boots into the checklist, no modal; kill mid-setup + relaunch
  restores the detected step; consent tab never appears unannounced; abandoning the Google tab is
  recoverable in one click; ✕/backdrop never open the file picker; no destructive action within 2
  clicks of the checklist; star → delete-downloaded-data (default) → re-fetch → star intact
  (Playwright); one click from post-connect state starts a 30-day fetch with the calendar visibly
  selected.
- **Verify:** Playwright first-run spec (fresh `BCFEED_DATA_DIR`) + manual real-OAuth walkthrough.

### WP-24 · Core loop: one mental model + enrichment visibility (parallel worktree B)
- **Goal:** "Get releases" is the only fetch concept; enrichment is ambient, visible per-row,
  pausable in aggregate; no dead-end disabled states; the quota wall becomes a guided split.
- **Scope:** UXP-8 / UX-6-partial (Preload button removed as primary; background queue consuming
  WP-17's job: priority starred > expanded/hover > visible > rest; pauses during Gmail fetch;
  star-front-of-queue preserved; bulk "load all players" reachable ≤2 clicks) · UXP-9 / UX-11 /
  UI-13 (row-level ready/loading/unavailable glyphs per UIR; CACHED badge gone from default UI;
  aggregate chip with Pause; failures never auto-retried in-session) · UXP-11 / UX-12 ("Up to
  date" + `Check again` via WP-15's `refresh=1`; no permanently disabled primary anywhere) ·
  UXP-21 (max-results → proposed half-range continuation, via WP-10's `max_results` code; fast
  detection via WP-16) · UXP-10 (decision record only — no auto-fetch; summary line is the
  affordance).
- **Files:** `web/js/populate.js`, `web/js/enrich.js` (new), `web/js/table.js` (row glyph hooks),
  `web/js/status.js` (chip), `dashboard.html`, `dashboard.css`.
- **Depends:** WP-22, WP-17, WP-15, WP-11; soft WP-16. **Parallel:** worktree-parallel with
  WP-23/25; merges second. **Size:** L.
- **Acceptance:** new-user path select → Get releases → browse → expand → hear music with no second
  fetch concept (Playwright); starring an unloaded release starts its fetch within 1 s; queue never
  exceeds politeness budget and pauses during a Gmail run (server-log assertion); Pause stops within
  one in-flight request, resume skips successes; 404 release shows unavailable + `Open on Bandcamp`,
  never auto-retried this session; `Check again` on a complete range → "Added 0 releases", stars
  intact; artificially interrupted fetch then `Check again` recovers the gap; cap hit → one-click
  half-range continuation whose combined result equals an uncapped fetch (dedupe verified).
- **Verify:** Playwright + pytest (already covers backend halves) + manual month-scale run.

### WP-25 · Table & calendar flows (parallel worktree C)
- **Goal:** honest scope labels, filter sanity, legible coverage map, discoverable shortcuts.
- **Scope:** UXP-14 / UX-5 (three distinct empty states with actions) · UXP-15 / UX-8 / JS-5 (one
  checkbox column + hover `only` + All/None; exclusion-set persistence across navigation; empty
  selection = empty table, no auto-reset) · UXP-16 / UX-10, JS-4-finish, PERF-1-UI (`Mark 17 shown
  as seen` live count; moved out of the range group; undo toast 10 s exact-set restore; persistent
  count/filter status line; filter chip) · UXP-17 / UX-13, UI-5-finish (one visual channel per
  calendar state, unchecked-inside-selection loudest, legend from real components — pixels per
  UIR) · UXP-18 / UX-17 (Today → `Latest`; tooltip on today's cell) · UXP-12 / UX-14 (shortcut
  hint line + `?` help section — JS-8 already fixed in WP-07).
- **Files:** `web/js/table.js`, `web/js/calendar.js`, `web/js/filters.js` (new or within table),
  `dashboard.html`, `dashboard.css`.
- **Depends:** WP-22, WP-21, WP-17 (batch endpoint for undo/bulk). **Parallel:** worktree-parallel
  with WP-23/24; merges third. **Size:** L.
- **Acceptance:** each empty state reproducible with its own copy+action; "No releases match the
  current filter." gone from fresh installs; uncheck two labels → navigate months → still
  unchecked; `only` yields exactly one label in one click; mark-seen count always equals affected
  rows under active filters; undo restores the exact prior per-row set; count line survives load/
  sort/filter/mark/fetch; four calendar states identifiable alone and in combination; summary count
  and marked-unchecked cells always agree; no control claims to select today; every implemented
  shortcut listed, none listed unimplemented.
- **Verify:** Playwright (filters, undo, empty states) + manual calendar review in both themes.

### WP-26 · Wording enforcement sweep + copy record
- **Goal:** zero banned vocabulary anywhere a user can see; docs and UI speak the same language.
- **Scope:** UXP-1 / UX-6 full map over all remaining strings in `web/js`, `dashboard.html`,
  `pipeline.py`, `gmail.py`, `server.py` (SSE `message` fields; the protocol's `text` mirror field
  is deleted here — ARC-1's one-release migration window closes) · inclusive date display
  everywhere (no end-exclusive leak) · every disabled control's tooltip says why + what enables it ·
  `docs/copy.md` records the vocabulary rules · README/SETUP updated so UI names match docs.
- **Files:** all of the above + `docs/copy.md` (new), README/SETUP touch-ups, golden updates.
- **Depends:** WP-23, WP-24, WP-25 (sweeps the final UI). **Parallel:** NO — deliberately last and
  exclusive (touches everything).
- **Size:** M.
- **Acceptance:** scripted grep of user-visible strings finds zero instances of
  populate/preload/cache(d)/scrape(d)/parse/query/token/credentials/embed/proxy in any user-facing
  string (code identifiers exempt); all displayed date ranges inclusive and matching the selection;
  `text` field gone from SSE payloads and unused client-side; goldens updated deliberately.
- **Verify:** the grep script (checked into `tests/`) + full Playwright suite + manual read-through
  of every screen against the UXP-1 tables.

---

## Phase 4 — Polish + packaging

### WP-27 · Performance polish
- **Goal:** close the remaining measured PERF items and re-run the audit benchmarks.
- **Scope:** PERF-4-finish (default date filter = most recent month, not min→max; re-measure first
  paint at 5k) · PERF-5 verification (payload now ~1.2 MB at 5k per the audit benchmark — confirm) ·
  PERF-8 verification (dedupe landed in WP-07 — confirm closed) · re-run `perfbench`-style checks
  for the PERF-1 and PERF-3 acceptance numbers.
- **Files:** `web/js/state.js`/`table.js` (default range), `tests/` bench notes.
- **Depends:** WP-14, WP-18; ideally after Phase 3 merges quiet down. **Parallel:** YES with WP-28
  (disjoint files). **Size:** S/M.
- **Acceptance:** first paint at a 5k-release fixture library < 1 s for the default view; `/releases`
  payload for that library ≤ ~1.5 MB; mark-300-seen numbers from WP-18 still hold post-Phase-3.
- **Verify:** scripted benchmark against a generated 5k fixture data dir + Playwright timing.

### WP-28 · Packaging prep hygiene
- **Goal:** the tiny prerequisites that are valuable even if the .app never ships.
- **Scope:** ARC-6-prereqs / ARCH-9 / CQ-64-code-half (single `VERSION` constant surfaced via
  `/config.json` and Settings→About; `pyproject.toml` with pinned deps as the single source
  superseding requirements.txt; `resource_path()` in `paths.py` used by all bundled assets; delete
  the dead `_MEIPASS` credentials branch in `gmail.py`).
- **Files:** `paths.py`, `gmail.py`, `server.py`, `web/js/settings` bit, `pyproject.toml`,
  `requirements.txt` (pointer or generated).
- **Depends:** WP-09; schedule after Phase 1 (server.py quiet) — any time before WP-29. **Parallel:**
  YES with WP-27. **Size:** S/M.
- **Acceptance:** one version string: Settings/About == `/config.json` == git tag (at release);
  grep `_MEIPASS` → nothing; all asset loads route through `resource_path()`; clean-venv
  `pip install .` runs the app.
- **Verify:** pytest (paths) + manual venv install.

### WP-29 · macOS .app bundle
- **Goal:** double-clickable distribution per ARC-6's decision: PyInstaller onedir `.app`, optional
  `rumps` menu-bar shell; Homebrew tap stays primary.
- **Scope:** ARC-6 (spec file with `datas` via `resource_path()`; hidden-imports for
  `googleapiclient` — time-boxed, see risk R5; ad-hoc signing + documented right-click-open;
  GitHub release asset). Optional 6b: `rumps` menu-bar entry (Open dashboard / Quit).
- **Files:** `bcfeed.spec` (new), `packaging/` scripts, docs section.
- **Depends:** WP-28, WP-30 (version/tag). **Parallel:** NO (release train). **Size:** L.
- **Acceptance:** `open dist/bcfeed.app` on a clean macOS account → browser opens the dashboard, all
  doc routes render; quit leaves no orphan process; data dir untouched by bundle location;
  `credentials.json` absent from the bundle (grep + inspection); Homebrew CLI path still works
  identically.
- **Verify:** manual on a clean user account (the only honest test for Gatekeeper behavior).

### WP-30 · Docs final pass + release
- **Goal:** docs match the shipped product; a tagged, consistent release exists.
- **Scope:** CQ-65 (privacy/SETUP/GMAIL_SETUP state `gmail.readonly` + one-time re-auth note) ·
  UXP-12-docs (shortcuts section) · README workflow section refreshed with final vocabulary +
  new screenshots · tag `v1.1.0` (or maintainer's choice) in this repo · Homebrew formula
  regenerated from pinned deps.
- **Files:** `README.md`, `SETUP.md`, `GMAIL_SETUP.md`, `privacy.md`, screenshots, tag + formula
  (tap repo).
- **Depends:** WP-26, WP-28. **Parallel:** NO. **Size:** S/M.
- **Acceptance:** docs/UI vocabulary identical (spot grep); goldens green; scope strings agree with
  code; formula installs the tagged version cleanly.
- **Verify:** golden tests + clean `brew install` from the tap.

### WP-31 · SQLite storage (OPTIONAL — maintainer decision gate)
- **Goal:** ARC-2b end state: one `bcfeed.db`, WAL, transactional multi-table populate commits.
- **Scope:** ARC-2b / ARCH-7 (schema per the architecture plan incl. `source` and
  `fetch_failed/fetched_at`; startup import of schema-v2 JSON stores inside one transaction; JSON
  renamed `*.imported.bak`; store function signatures preserved; optional `--export-json`).
- **Files:** `storage.py` (new), `session_store.py` (facade), `json_store.py` (import-only),
  `tests/test_sqlite_migration.py`.
- **Depends:** WP-03b, WP-05, WP-14 (imports the v2 shapes — do NOT schedule before WP-14 or the
  migration must be written twice). Best after Phase 3 stabilizes. **Parallel:** NO.
- **Size:** M/L.
- **Acceptance:** round-trip fixture import/export compare incl. starred+viewed overlap; no
  `json.dump` on the write path outside migration/export (grep); `PRAGMA integrity_check` clean
  after kill -9 during populate; mark-100-seen = one transaction.
- **Verify:** pytest + manual upgrade against a copied real data dir.

---

## Orchestration notes

**Agent-per-WP mapping.** One subagent per WP, specialized by lane:

| Agent profile | WPs |
|---|---|
| tooling/format | WP-01 |
| backend-python | WP-02, WP-04, WP-05, WP-06, WP-08(server), WP-10..WP-17, WP-28, WP-31 |
| test-engineer | WP-03a, WP-03b (and reviews every WP's test additions) |
| frontend-js | WP-07, WP-08(js), WP-18, WP-21, WP-22..WP-25 |
| css/visual | WP-19, WP-20 (must read UIR spec + screenshots before writing a line) |
| docs | WP-09, WP-26(copy assist), WP-30 |
| packaging | WP-29 |

**Worktree isolation.** Every WP runs in its own git worktree branched from the current integration
head (`git worktree add ../bcfeed-wpNN wpNN`). WPs marked parallel merge in the order listed in
their phase section; a later-merging parallel WP rebases before merge. The declared "files touched"
list is a contract: an agent needing a file outside its list must stop and report to the
orchestrator (this is how same-file conflicts are prevented, not discovered).

**What each agent must read first, in order:**
1. This file (its own WP entry + phase context + global rules).
2. `AGENTS.md` and `CLAUDE.md` at repo root (project conventions).
3. The improvement-spec sections for every ID in its WP scope (table at top of this file).
4. The cited finding entries in `docs/current-state/known-issues.md` (evidence file:line anchors).
5. UI-facing WPs (07, 18–26): `docs/improvements/ui-redesign.md` and
   `docs/current-state/screenshots/` (read the PNGs — light AND dark pairs).

**Definition of done** is global rule 7. The orchestrator does not merge a WP whose PR lacks pasted
verification output. After each phase completes, run the full suite + Playwright + a manual smoke of
the do-not-break list before opening the next phase.

**Ordering rationale.**
- *Format before everything*: any parallel work before WP-01 creates whole-file conflicts.
- *Seam before tests before fixes*: WP-02's three-line seam is what makes WP-04/05's data-loss ACs
  testable at all; landing the highest-severity fixes without their tests would leave the two big
  refactors (WP-14, WP-18) without a net.
- *Protocol before UI*: WP-10 first in Phase 1 so `populate.js` (WP-18) and the progress UI (WP-22)
  are each written exactly once against the final contract.
- *Layering early in Phase 1*: WP-11 shrinks the `server.py` hub and pre-lands the static route,
  which is what makes WP-18 ∥ WP-15 and the Phase-3 parallelism legal.
- *Backend enablers before their UX consumers*: every Phase-3 WP's hard deps are Phase-1 WPs; the
  cross-phase edge table is exhaustive — if an edge isn't listed, it doesn't exist.
- *Copy sweep last*: strings churn in every Phase-3 WP; enforcing the vocabulary once at WP-26 is
  cheaper than policing three parallel worktrees, while rule 6 keeps new strings from regressing.
- *Packaging last*: it depends on everything being stable and adds no user value until it is.

**Known plan-level conflicts already resolved (do not re-litigate):**
- CQ-01 says delete `mark_dates_not_scraped`; LOG-3 resurrects it → WP-06 keeps it (comment pointer).
- CQ-11's terminal error event vs ARC-1's typed protocol → WP-04 ships only the worker catch-all +
  `ERROR:` line (client already routes it); WP-10 supersedes with `event: error`.
- LOG-5's embed-record shape vs LOG-20's contract → WP-11 writes the LOG-20 shape from day one;
  WP-14 only performs the one-shot key rewrite and drops stored `embed_url`.
- UXP-10 auto-fetch: **rejected** (decision recorded in the UX plan); no WP implements it.
- CQ-37 renames `unseen-day` → `populated-day` (WP-19); UIR-13's `has-unseen` is a **new** class
  driven by the `hasUnseen` boolean (dashboard.js:1254-1259), not a rename of the same class (WP-20).
- Investigated-not-issues list in known-issues.md is binding: do not "fix" release_cache rewrite
  cost, SSE keep-alive, per-toggle write cost, etc.

---

## Risk register

| ID | Risk | WPs | Likelihood / impact | Mitigation |
|---|---|---|---|---|
| R1 | **Scope/token migration forces re-auth**: WP-13 invalidates every existing token (readonly scope + JSON format). A user mid-backlog is bounced to Google; the unverified-app interstitial may strand them. | WP-13, WP-23 | Certain / medium | Ship 5d+5e together (exactly one re-auth event). Legacy detection routes into the existing missing-token flow, which WP-23 later upgrades. Release notes + banner copy ("bcfeed now asks for read-only access; please reconnect"). Manual end-to-end reconnect test before release. |
| R2 | **Cache-format migrations lose user state**: WP-14's canonical-URL rewrite merges keys across four stores; a bug loses stars/seen permanently (the only user-created data). WP-31 repeats the exposure. | WP-14, WP-31 | Medium / high | `.pre-v2` / `.imported.bak` backups before any rewrite (never delete). Round-trip pytest on fixture stores incl. collision cases. Manual upgrade rehearsal on a copy of the maintainer's real data dir before merging. Recovery documented: restore backup + re-check range (LOG-3). |
| R3 | **CSS rewrite regressions, especially the light theme**: the current skin was dark-designed and light-broken; a rewrite can invert or repeat that. Playwright pixel-diffs will churn. | WP-19, WP-20, WP-25 | High / medium | WP-19 is 1:1 mechanical (dark screenshot-identical) so WP-20's diff is tokens/components only. WP-20 AC requires the audit's exact measured contrast pairs to pass **in both themes**, verified by script not eyeball. Smoke re-baselined once per WP, both themes captured. |
| R4 | **Embed iframe behavior changes**: deriving `embed_url` instead of storing it (LOG-20), http(s) whitelisting of iframe src (CQ-21), and negative-cache TTLs could break playback or wrongly mark live releases unavailable. Bandcamp can also change its embed format at any time. | WP-11, WP-14, WP-07, WP-24 | Medium / medium | Keep `build_embed_url` derivation covered by a fixture test against a saved real page; manual playback check in WP-11/WP-14/WP-24 verification; negative-cache TTL (7 d) bounds any false "unavailable"; `Open on Bandcamp` fallback always present. |
| R5 | **Packaging unknowns**: PyInstaller × google-api-python-client hidden imports, Gatekeeper/notarization friction, app translocation. A prior attempt already failed on asset paths. | WP-29 | High / low (CLI unaffected) | WP-28 fixes the actual prior failure (paths) independently. Time-box WP-29; the Homebrew CLI remains the primary channel and blocks nothing. Ad-hoc signing + honest right-click-open docs; notarization deferred. |
| R6 | **server.py contention stalls Phase 1**: nearly every backend WP touches the hub file; a slow WP serializes the phase. | WP-10..WP-17 | Medium / low | Lanes A/B defined above; WP-11 shrinks the hub early; WP-16 absorbs slack in Lane B. Orchestrator watches lane length, not phase length. |
| R7 | **Parallel Phase-3 worktrees collide on shared modules** (`state.js`, `main.js`, `status.js`). | WP-23/24/25 | Medium / low | Fixed merge order (23→24→25) with rebase-before-merge; files-touched contract; shared-module edits kept additive (new functions, not signature changes) — anything structural goes through the orchestrator. |
| R8 | **Settling window changes populate-button semantics**: after WP-15, recent days always re-check, so "Up to date" appears less often and Gmail is re-queried (≤3 days) on every run. Could read as a bug and slightly raises API usage. | WP-15, WP-24 | Medium / low | Bounded cost by design (N=3). WP-24's copy explains "recent days are re-checked". Quota interaction covered by WP-16's early-stop. |
| R9 | **Typed-SSE migration window**: between WP-10 (server) and WP-26 (deletes the `text` mirror), both fields exist; a partial merge could leave the client reading a deleted field. | WP-10, WP-22, WP-26 | Low / medium | Server+client halves of WP-10 ship in one PR; `text` removal happens only in WP-26 after all consumers use `message`; grep AC enforces. |
| R10 | **Golden-test churn**: WP-09 (docs), WP-06 (renderer fix), WP-26 (copy) each legitimately change goldens; careless regeneration could mask a real renderer regression. | WP-03b, WP-06, WP-26, WP-30 | Medium / low | Goldens regenerate only in WPs that state it in scope, with the diff reviewed line-by-line in the PR; renderer *unit* cases (link, autolink, scheme filter) never regenerate. |

---

## Appendix A — Traceability (finding/item → owning WP)

Primary owner listed first; secondary WPs finish or consume the item. The orchestrator checks this
table off as WPs merge.

| Item | WP | Item | WP | Item | WP |
|---|---|---|---|---|---|
| PY-1 | 04 | JS-1 | 07 | UI-1..4 | 20 |
| PY-2 | 04→10 | JS-2 | 07→22 | UI-5 | 19→25 |
| PY-3 | 04 | JS-3 | 18 | UI-6 | 20→24 |
| PY-4 | 02+03 | JS-4 | 07→25 | UI-7, UI-8 | 20 |
| PY-5 | 04 | JS-5 | 25 | UI-9 | 20→22 |
| PY-6 | 06 | JS-6 | 07+11 | UI-10, UI-11 | 20 |
| PY-7 | 04 | JS-7 | 21 | UI-12 | 20+21 |
| PY-8 | 05 | JS-8 | 07 | UI-13 | 20→24 |
| PY-9 | 06(msg)+16 | JS-9 | 07 | UI-14..18 | 20 |
| PY-10 | 11 | JS-10 | 10 | UX-1 | 23 |
| PY-11 | 04 | JS-11 | 17+18 | UX-2 | 13→23 |
| PY-12 | 05 | JS-12 | 18 | UX-3 | 22 |
| PY-13 | 06 | JS-13 | 18→20 | UX-4 | 23 |
| PY-14 | 06 | JS-14 | 07 | UX-5 | 25 |
| PY-15 | 05 | JS-15 | 18 | UX-6 | 26 |
| SEC-1 | 02 | ARCH-1 | 05 | UX-7 | 22 |
| SEC-2 | 12 | ARCH-2 | 04 | UX-8 | 25 |
| SEC-3 | 13 | ARCH-3 | 04→10 | UX-9 | 10→22 |
| SEC-4 | 08 | ARCH-4 | 10 | UX-10 | 25 |
| SEC-5 | 13 | ARCH-5 | 18 | UX-11 | 17→24 |
| SEC-6 | 13 | ARCH-6 | 17+18 | UX-12 | 15→24 |
| SEC-7 | 07 | ARCH-7 | 31 (opt) | UX-13 | 25 |
| SEC-8 | 06 | ARCH-8 | 11+13 | UX-14 | 25 |
| SEC-9 | 08 | ARCH-9 | 09+28 | UX-15 | 07→22 |
| PERF-1 | 17+18 | ARCH-10 | 02+03 | UX-16 | 07→23 |
| PERF-2 | 11+07 | ARCH-11 | 18→20 | UX-17 | 25 |
| PERF-3 | 12+17 | LOG-1 | 04 | UX-18 | 20+23 |
| PERF-4 | 18→27 | LOG-2, LOG-3 | 15 | UXP-1 | 26 |
| PERF-5 | 14+10 | LOG-4 | 05 | UXP-2 | 22 |
| PERF-6 | 05 | LOG-5 | 11 | UXP-3 | 23 |
| PERF-7 | 16 | LOG-6 | 17 | UXP-4 | 13+23 |
| PERF-8 | 07 | LOG-7 | 12 | UXP-5..7 | 23 |
| CQ-01,02 | 06 | LOG-8 | 04 | UXP-8, 9 | 24 |
| CQ-03,04 | 06 | LOG-9 | 14 | UXP-10 | 24 (record) |
| CQ-05..07 | 07 | LOG-10 | 04+14 | UXP-11 | 15+24 |
| CQ-08 | 06 | LOG-11 | 14 | UXP-12 | 25+30 |
| CQ-10..14,16 | 04 | LOG-12 | 15 | UXP-13 | 10+22 |
| CQ-15 | 06 | LOG-13 | 05 | UXP-14..18 | 25 |
| CQ-17,18,30 | 05 | LOG-14..17 | 16 | UXP-19, 20 | 22 |
| CQ-19 | 16 | LOG-18 | 04 | UXP-21 | 24 |
| CQ-20,31 | 11 | LOG-19 | 06+12+14 | UIR-* | 20 (19, 21–25 consume) |
| CQ-21 | 07 | LOG-20 | 14 (shape in 11) | ARC-1 | 10 |
| CQ-22 | 07→22 | LOG-21 | 14 | ARC-2a / 2b | 04+05 / 31 |
| CQ-23 | 18 | CQ-40..47 | 02+03 | ARC-3 | 11+13 |
| CQ-24 | 10 | CQ-50,51 | 01 | ARC-4 | 18 |
| CQ-25 | 07+11 | CQ-60..63 | 09 | ARC-5a..f | 02/08/08/13/13/12 |
| CQ-26,27 | 07 | CQ-64 | 09+28 | ARC-6 | 28+29 |
| CQ-32..37 | 06/18/18/19/18/19 | CQ-65 | 13+30 | ARC-7a..d | 02+03 |
