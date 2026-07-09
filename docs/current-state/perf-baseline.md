# Performance baseline (WP-27)

Re-baseline of the audit's 5,000-release performance numbers at the Phase-0–3
HEAD, after PERF-4 (default filter = most recent month) landed. Reproduce with:

```
BCFEED_APP_PYTHON=/path/to/.venv/bin/python \
    /path/to/pwvenv/bin/python scripts/bench_5k.py
```

`scripts/bench_5k.py` generates a throwaway schema-v2 data dir (5,000 releases,
1,500 enriched with embed records + descriptions; 300 of them in the most recent
month), launches the real app on an ephemeral port over it, and measures the
three PERF acceptance numbers below. It is a hand-run script, not a CI test — a
5k library is too heavy for the smoke net. The behavioural invariants it exercises
are guarded cheaply in `tests/e2e/test_perf.py` (default-view = most recent month,
saved range wins, hover+click embed dedupe).

## Machine caveat

Numbers below were measured on the development machine (Apple Silicon, macOS,
headless Chromium) on a warm run. Treat them as an order-of-magnitude baseline,
not a spec — absolute timings vary with hardware, browser build, and load. The
relative wins (a month of rows instead of the whole library; descriptions off the
payload; one batch write) are the durable results.

## Results (5,000-release fixture)

| Metric | Result | Target | Notes |
| --- | --- | --- | --- |
| `/releases` payload | **1.39 MB** (1,459,380 bytes) | ≤ ~1.5 MB | 5,000 rows, no description bodies (PERF-5) |
| description field in payload | **absent** | absent | bodies fetched lazily via `/embed-meta` on expand |
| `/releases` server build time | 22 ms | — | not a bottleneck (PERF-5 investigated note) |
| first paint (nav → default rows) | **287 ms** | < 1 s | default view = most recent month (PERF-4) |
| default view row count | **300** of 5,000 | ≪ 5,000 | most recent month only, not min→max (PERF-4) |
| mark-300-seen main-thread | **22 ms** | < 100 ms | click handler + coalesced render flush |
| mark-300-seen table renders | **1** | 1 | WP-18 render discipline held (PERF-1/JS-11/ARCH-6) |
| mark-300-seen batch POSTs | **1** | 1 | one `/viewed-state/batch` write, not one-per-row |

## PERF item status

- **PERF-4 (default filter spanned the whole dataset)** — CLOSED. `setDefaultDateFilters`
  (`web/js/calendar.js`) now defaults to the most recent month of data; the whole
  library is never rendered on first paint. An explicit saved calendar selection
  (localStorage `bc_calendar_state_v1`) still wins, and the user widens the range
  normally. First paint at 5k dropped from the audit's 1–3 s to ~0.29 s.
- **PERF-5 (`/releases` shipped the whole enriched library incl. descriptions)** —
  CONFIRMED closed post-WP-14. Payload for 5k is 1.39 MB (audit measured
  7.6 MB → ~1.2 MB); no description body rides the table payload.
- **PERF-8 (hover-prefetch had no in-flight dedupe)** — CONFIRMED closed.
  `ensureEmbed` (`web/js/api.js`) keys in-flight fetches in `embedFetchesInFlight`
  (`Map<url, Promise>`); a hover + click + star on one row issues exactly one
  `/embed-meta` fetch. Asserted in `tests/e2e/test_perf.py::test_hover_click_dedupe_single_fetch`.
