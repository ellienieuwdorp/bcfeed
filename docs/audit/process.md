# Audit process — July 2026

How the full-project audit behind `docs/current-state/`, `docs/improvements/` and `docs/plans/` was performed. Recorded so the method is reproducible and the evidence trail is auditable.

## Goal

Assess bcfeed end-to-end — product intent, architecture, code quality, and (most importantly) user-interface design — then produce improvement plans per area plus a sequenced, parallelizable implementation plan. No code changes were made as part of the audit.

## Environment

- macOS (Darwin 25.5), Homebrew Python 3.12 (`brew install python@3.12`), project venv from `requirements.txt`.
- App run in dev mode: `.venv/bin/python bcfeed.py --no-browser`, server on `localhost:5050`.
- The user data directory (`~/Library/Application Support/bcfeed/`) was empty before the audit; it was populated with mock data (below) and can be safely cleared afterwards.

## Method

### 1. Deep-read (4 parallel readers)
Independent full reads of (a) `dashboard.js` + `dashboard.html`, (b) `dashboard.css` design system, (c) all backend Python modules, (d) product docs + full git history (285 commits, 2022-05 → 2026-01). Each produced a structured map with `file:line` citations. These maps ground every later claim.

### 2. Live app + mock data + screenshots
- First-run/empty states captured *before* any data was injected (Credentials Needed modal, empty dashboard, settings, load-credentials modal, in-app docs pages, populate-without-credentials behavior) — light and dark themes.
- Mock dataset: 49 releases across June 2026 (21 days, 2 known-empty days, a 2-day unscraped gap), 10 real labels, mixed viewed/unseen/starred. 11 releases are *real* Bandcamp URLs fetched through the app's own `GET /embed-meta` endpoint, so `embed_cache.json` was populated by the production scraping path and the player embeds in screenshots are genuine.
- 31 Playwright (headless Chromium, 1440×900 + 1000px narrow) screenshots covering: populated table, calendar states (populated/unseen/unscraped/empty month), expanded row with live embed, all filters, sorting, empty-filter state, both settings/credentials modals, status-log states (empty, error, simulated successful-populate transcript using the exact strings `pipeline.py` emits), both themes. A curated set is committed under `docs/current-state/screenshots/`.

### 3. Multi-dimension audit with adversarial verification
Seven parallel auditors, each reading the maps + code + screenshots relevant to its dimension: Python quality, frontend JS quality, architecture, visual design, UX/workflow, security & privacy, performance. All findings carry evidence (`file:line` or screenshot) and a severity. Findings were then deduplicated and every medium/high finding was passed to an independent adversarial verifier instructed to *refute* it against the actual code/screenshots (subjective design findings were instead judged for fairness against the product brief: practical-first, no over-the-top design). Only confirmed/partially-confirmed findings appear in `docs/current-state/`.

### 4. Improvement synthesis
Per-area improvement plans drafted from confirmed findings, with a judge-panel comparison of candidate UI design directions against the brief, and a completeness critic pass. Results in `docs/improvements/` and the sequenced plan in `docs/plans/implementation-plan.md`.

## Reproducing the screenshot harness

```bash
python3 -m venv pwvenv && pwvenv/bin/pip install playwright && pwvenv/bin/playwright install chromium
# seed ~/Library/Application Support/bcfeed/*.json (see AGENTS.md "Data stores"), then drive
# http://localhost:5050/dashboard with Playwright; pre-set localStorage:
#   bc_dashboard_theme = light|dark
#   bc_calendar_state_v1 = {"from":"2026-06-01","to":"2026-06-30"}
```

Gotchas discovered while driving the UI: dismissing the first-run "Credentials Needed" modal auto-opens Settings (clicks behind it are intercepted); "Populate release list" is silently disabled until a calendar day is clicked; the calendar only allows dates up to yesterday.
