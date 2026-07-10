# UI polish round 2 — verified issues + fix plan

Date: 2026-07-10 · Baseline: post-v1.1.0 work (main @ d1f13cf) · Status: verified, planning.
All ten reported issues were independently reproduced against the live app (real user data, 197 releases; measurements via Playwright `getBoundingClientRect`, screenshots in the session scratchpad). Fix items carry IDs **UIP-n** for the implementation phase.

## Verified issues

### UIP-1 · Status footer changes height during activity (reported #1)
**Verified.** `#activity-strip` is 85px idle → **121px** while downloading (progress row `#activity-progress` unhides) — and it grows **upward** (strip y: 807→771), shrinking/shifting the release list. The enrich chip row (`#enrich-chip` / "N players not loaded yet · Load all players") appears/disappears the same way. Root cause: the strip is a vertical stack of conditionally-`hidden` block rows (status line / progress / meta+chip / details), so every state change re-flows its height.
**Fix direction:** fixed-height single-line strip. The progress bar becomes a 2-3px edge-attached bar (top border of the strip or overlaid under the status line) rather than a stacked row; the enrich chip renders inline in the existing line (it already shares the meta row — enforce one row总), and every conditional element gets a reserved slot (visibility/opacity swap, not display/hidden). Only the explicit "Details" disclosure may change the strip's height. Acceptance: strip height constant (to the pixel) across idle/downloading/loading-players/error; only Details expansion changes it.

### UIP-2 · "Up to date" line expands the action card (reported #2)
**Verified.** `.action-group` grows 94px → **117px** when `#up-to-date-line` unhides (+23px pushes the sidebar down).
**Fix direction:** don't stack it. Options (implementation picks the cleanest): render the up-to-date state *inside* the button's own secondary line (button subtitle), or as the button's state itself ("Check again · ✓ up to date"), or reserve the line's slot permanently with `visibility:hidden`. Acceptance: `.action-group` height identical in not-checked / checked / partially-checked states.

### UIP-3 · Date-first workflow feels unnatural; timeline main view (reported #3)
**Verified (current state).** Raw ISO strings everywhere: sidebar header "Date range: 2026-06-01 to 2026-06-30", summary "2026-06-01 – 2026-06-30 · 30 dates not checked", table date column "2026-06-30". The mental model is still "select a range, then fetch" rather than "scroll a feed".
**Direction (per maintainer, confirmed by Q&A below):** the main list becomes a **timeline feed** — newest at top, grouped headers by user-selectable granularity (day/week/month), infinite-scroll downward; scrolling past the oldest fetched day shows an inline "Check earlier releases" affordance that fetches the next chunk; if the newest days aren't checked yet, a pinned hint at the top offers "Check for new releases". Friendly date formatting throughout (group headers like "This week", "Week of 22 June", "Monday 30 June"; row dates become redundant within day groups). The calendar remains as a compact coverage/jump map, no longer the primary fetch driver.
**Bonus observation:** after a provider switch, all 30 day cells show the loud amber "gap" ring at once (correct semantics from the provider-switch re-check feature, alarming visual) — the timeline's top/bottom fetch hints subsume most of this signaling.

### UIP-4 · "Filter by date" misaligned with the app header (reported #4)
**Verified.** The h1 "bcfeed" centerline is y=32; the sidebar checkbox row centerline is y=26 — 6px off, visibly crooked across the sidebar/main boundary (different paddings: header is 66px tall, the sidebar's top row isn't matched to it).
**Fix direction:** give the sidebar a header row exactly matching the main header's height/baseline (shared token), aligning the checkbox row's centerline with the h1. (May be subsumed by UIP-8's header redesign.)

### UIP-5 · Unchecked date filter leaves a dead panel (reported #5)
**Verified.** Unchecking "Filter by date" shrinks `#scrape-panel` only 616px → **586px**: a huge empty ghost card remains (grayed "Date range…" header on a blank card).
**Fix direction:** unchecked ⇒ the panel collapses to just its one-line header (or the control moves into the filter popover per UIP-8 and the sidebar section hides entirely). Acceptance: unchecked state frees the vertical space (panel ≤ ~40px), layout below moves up.

### UIP-6 · Label filter: only alphabetical order (reported #6)
**Verified.** `filters.js` renders alphabetically; counts are displayed but not sortable. With the real dataset the frequent labels (Dekmantel ·4, Ilian Tape ·3, Kompakt ·3) are buried mid-list.
**Fix direction:** a small sort toggle on the section header: A–Z / most releases (count desc, ties A–Z). Persisted preference.
**Bonus finding (fix alongside):** case-variant duplicates appear as separate labels — the real data shows **"Ilian Tape" (3)** and **"ILIAN TAPE" (1)** as two rows. Filtering should group case-insensitively (display the most common casing).

### UIP-7 · Toolbar above the list wastes vertical space (reported #7)
**Verified.** Chrome above the first release row totals **146px**: app header 66px → toolbar 27px+margins ("Mark 197 shown as seen / Mark 197 shown as unseen … 197 releases · sorted by date ↓") → thead. The toolbar duplicates info the footer strip also has room for.
**Fix direction:** delete the toolbar row. Count + sort indicator fold into the fixed-height footer strip (UIP-1's reserved slots); the bulk mark-seen/unseen actions move into the header-row actions cluster (UIP-8) as a compact menu. Acceptance: first data row starts ≳40px higher; no information lost (count/sort/mark reachable).

### UIP-8 · Header filter buttons look raw; no unified filtering (reported #8)
**Verified.** Header-right is `Show only [Unseen] [Starred]` plain text-buttons — no date presets, no artist/label filtering, no combined view of active filters.
**Direction (confirmed by Q&A below):** one **Filter** popover (funnel icon, from the WP-20 sprite) unifying: date presets (this week / last week / this month / last month / this year + custom via mini-calendar, multiselect-capable), seen state (all/unseen/seen), starred, label/artist search-multiselect. Active filters render as dismissible chips in the header row (the quick Unseen/Starred toggles become chips of the same system). Mark-seen/unseen actions live in the same row as a compact actions menu (per UIP-7).

### UIP-9 · Bandcamp player ignores the app theme (reported #9)
**Verified.** In dark mode the expanded row shows a glaring white player. The embed URL hardcodes `bgcol=ffffff/linkcol=0687f5` in BOTH `web/js/api.js:194` and `bandcamp.py:272`.
**Constraint:** CSS injection into the cross-origin iframe is impossible. But Bandcamp's EmbeddedPlayer accepts `bgcol=` and `linkcol=` URL params.
**Fix direction:** derive the embed URL theme-side: client rewrites/derives `bgcol` (dark: `333333`-ish token-matched, light: `ffffff`) and `linkcol` (the app accent) from the active theme at iframe-insertion time; re-render open players on theme toggle. Keep the server-side builder theme-neutral (client owns presentation).

### UIP-10 · Settings modal spacing regressed; needs tabs (reported #10)
**Verified.** The panel is **742px** tall with sparse sections (Appearance = one lone checkbox after the cached-badges toggle went dev-only; a tall gray connection card; Danger zone) and uneven padding. Content panes swap when switching the provider dropdown.
**Also verified (serious):** the provider dropdown **auto-saves on change** (`saveProviderType()` fires on `change`, settings.js:513-518) — no confirmation, instant active-connection switch (this was triggered accidentally during verification and switched the live connection; restored). Combined with provider-switch re-check semantics this silently changes coverage.
**Fix direction:** three tabs — **Appearance** · **Email connection** · **Advanced** (dev toggles + danger zone). Fixed panel size across tabs and states (no jumps when panes/labels toggle); reserved space for conditional labels; provider switching becomes explicit (Save/confirm, never auto-save on dropdown change); consistent 4/8-scale padding per the Calm Slate tokens.

## Cross-cutting acceptance
- No layout element may change size due to a background state change (the UIP-1/2/10 rule); reserved slots or overlays instead of hidden-row stacking.
- All new UI on WP-20 tokens; wording per docs/copy.md; keyboard + aria per WP-21 conventions; e2e specs extended per change.
- The live user data dir is REAL now — no destructive testing against it; use seeded temp dirs.

## Q&A (maintainer decisions, 2026-07-10)
1. **Timeline (UIP-3):** evolve the existing dense table into a grouped feed — sticky group headers at user-selectable granularity (day/week/month), newest first, infinite scroll; "Check earlier releases…" affordance past the oldest fetched day; pinned top hint when the newest days aren't checked. Density + column sorting survive.
2. **Date wording:** relative + friendly ("Today", "Yesterday", "This week", "Week of 22 June", "June 2026"); absolute shown small where it matters.
3. **Filtering (UIP-8):** chips + popover. One Filter button (funnel) opens the full popover (date presets incl. multiselect, seen state, starred, label/artist search-multiselect); active filters are dismissible chips inline; Unseen/Starred remain one-click quick chips of the same system.
4. **Calendar (UIP-5/3):** shrinks to a compact coverage/jump map in the sidebar; it stops being the fetch driver; the "Filter by date" checkbox and its ghost panel are removed (date filtering lives in the Filter popover).

## Implementation plan (sequenced work packages)

Ground rules for every package: real user data lives in `~/Library/Application Support/bcfeed` — never write to or reset it (seeded temp dirs only); never POST settings/provider changes to the live server (the auto-save incident above); layout-stability acceptance applies app-wide; WP-20 tokens, docs/copy.md wording, WP-21 a11y conventions; each package updates the e2e specs it invalidates and lands with suite + lint green.

- **WPX-A — Layout stability floor (UIP-1, UIP-2, UIP-7a).** Fixed-height activity strip: progress becomes an edge-attached thin bar, enrich chip inlines into the single meta row, conditional slots reserved; only Details changes height. Count + sort indicator move INTO the strip (from the toolbar). Up-to-date line stabilized inside the action group (no growth). Acceptance: pixel-constant strip and action-group heights across all states; toolbar row's status half gone.
- **WPX-B — Player theming (UIP-9).** Client derives `bgcol`/`linkcol` from the active theme at iframe insertion; open players re-render on theme toggle. ∥ with WPX-C.
- **WPX-C — Settings tabs + polish (UIP-10).** Appearance / Email connection / Advanced (dev + danger zone) tabs; fixed panel geometry across tabs/states; provider switch requires explicit save/confirm (auto-save-on-change removed); 4/8 padding scale. ∥ with WPX-B.
- **WPX-D — Label filter sort + dedupe (UIP-6).** A–Z / by-count toggle (persisted); case-insensitive label grouping (display dominant casing).
- **WPX-E1 — Timeline feed (UIP-3 core).** Grouped sticky headers (day/week/month selector), relative+friendly dates, newest-first, scroll-driven "Check earlier releases…" + pinned "check newest" hint wired to the existing fetch/refresh endpoints and coverage ledger.
- **WPX-E2 — Filter system + header + sidebar rework (UIP-8, UIP-4, UIP-5, UIP-7b).** Filter popover + chips (date presets/seen/starred/labels), Unseen/Starred as quick chips, mark-seen actions into a compact header actions menu, sidebar header aligned to the app header, calendar compacted to a coverage/jump map, Filter-by-date checkbox + ghost panel removed.
- **WPX-F — Verification sweep.** Both-theme screenshots of every changed state, layout-stability measurements re-run, full unit + e2e, wording guard, doc status update.

Model assignment: WPX-A/B/C/D on Opus; WPX-E1/E2 on Fable (integrative redesign); orchestrator verifies each against the acceptance above before commit.
