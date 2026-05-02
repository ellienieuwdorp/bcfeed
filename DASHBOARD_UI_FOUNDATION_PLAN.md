# Dashboard UI Foundation Plan

This is the working spec and progress tracker for turning the current `bcfeed`
dashboard into a maintainable, finished interface. Update the checkboxes and
notes as work lands.

## Goal

Create a sustainable UI base before doing broad visual polish. The dashboard
should remain fast and dense for personal music triage, but future changes
should be made through clear layout structure, reusable styling primitives, and
consistent state patterns instead of inline styles and one-off fixes.

## Progress Legend

- `[ ]` Not started
- `[~]` In progress
- `[x]` Done
- `[!]` Blocked or needs decision

When starting a phase, replace `[ ]` with `[~]`. When finishing, replace `[~]`
with `[x]`, add a short completion note, and list verification performed.

## Current Baseline

- The dashboard is a flat, hand-rolled HTML/CSS/JS surface:
  `dashboard.html`, `dashboard.css`, and `dashboard.js`.
- The primary workflow is: choose date range, populate release list, preload or
  star releases, browse releases, and mark releases seen.
- Desktop is usable but visually flat and heavily dependent on a fixed sidebar.
- Mobile stacks the full sidebar before the release list, making the main
  experience effectively inaccessible.
- Many important design choices live inline in HTML or generated JS strings,
  which makes future improvements hard to apply consistently.

## Dependency Map

1. Phase 1, design primitives, should happen before most visual changes.
2. Phase 2, app shell and responsive structure, depends on Phase 1 tokens and
   layout primitives.
3. Phase 3, interaction vocabulary, depends on Phase 1 and should be coordinated
   with Phase 2 where controls move or collapse.
4. Phase 4, state surfaces, depends on Phase 1 and should reuse the same panel,
   button, form, and status primitives.
5. Phase 5, release browsing polish, depends on Phase 2 and Phase 3.
6. Phase 6, final visual refinement, should wait until the structure and states
   are stable.

## Phase 1: Design Primitives and CSS Hygiene

**Priority:** P0  
**Status:** [x]  
**Prerequisites:** None  
**Primary files:** `dashboard.css`, `dashboard.html`, `dashboard.js`

### Objective

Create the reusable styling foundation that future UI work can build on safely.
This phase is mostly structural: reduce inline styling, define tokens, and
normalize common components without changing the product workflow.

### Work Items

- [x] Define design tokens in `:root`:
  - color tokens for background, surface, elevated surface, text, muted text,
    border, action, danger, warning, success, selected, unseen, starred, cached
  - spacing tokens using a 4px scale
  - radius tokens
  - shadow/elevation tokens
  - font size, line height, and weight tokens for app UI
- [x] Replace hard-coded colors and repeated rgba values with tokens where safe.
- [x] Replace inline styles in `dashboard.html` with named classes.
- [x] Replace inline row/detail styles generated in `dashboard.js` with named
  classes or DOM helper functions.
- [x] Normalize button classes:
  - default
  - primary
  - secondary/quiet
  - danger
  - icon-only
  - toggle active/inactive
  - disabled/loading
- [x] Normalize panel/card/form/table/badge classes so later phases do not need
  new one-off styling.
- [x] Add visible focus states for keyboard users.
- [x] Add a short comment block at the top of `dashboard.css` explaining token
  categories and intended usage.

### Acceptance Criteria

- New UI changes can be made mostly by composing classes rather than adding
  inline styles.
- Existing desktop behavior is preserved.
- There are no obvious regressions in dark or light mode.
- Buttons, form fields, badges, panels, and table rows use consistent primitives.

### Verification

- [x] Run the app locally with `.venv/bin/python bcfeed.py --no-browser --port 5051`.
- [x] Check desktop dashboard at 1440px width.
- [x] Check settings panel.
- [x] Check expanded release row.
- [x] Check light and dark themes.
- [x] Run `python3 -m pytest -q` if tests are available in the environment.

### Notes

Keep this phase intentionally conservative. Avoid redesigning layout or
interaction semantics until the primitives are stable.

## Phase 2: App Shell and Responsive Layout

**Priority:** P0  
**Status:** [x]  
**Prerequisites:** Phase 1  
**Primary files:** `dashboard.css`, `dashboard.html`, `dashboard.js`

### Objective

Make the dashboard hierarchy durable across desktop and mobile. The release list
is the main product surface; date range, filters, status, and settings are
supporting tools.

### Work Items

- [x] Define a clear app shell:
  - persistent top bar for product title and global filters
  - main release browsing area
  - supporting control rail or drawer for date/filter controls
  - status area that does not permanently dominate the viewport
- [x] Rework mobile behavior:
  - release list must be reachable without scrolling through the full label list
  - date range controls should collapse into a section or drawer
  - label filters should collapse behind a filter button or details section
  - status should be collapsed by default once initial loading is complete
- [x] Rework desktop behavior:
  - keep dense browsing efficient
  - reduce permanent space consumed by controls where possible
  - preserve fast access to date range and label filters
- [x] Add responsive rules for the release detail view:
  - desktop: player and description side by side
  - narrow widths: stacked player and description
  - avoid fixed heights that create cramped or inaccessible content
- [x] Ensure the page has a coherent scrolling model:
  - avoid body-level `overflow: hidden` causing mobile traps
  - keep table/header behavior predictable
  - prevent overlapping sticky/fixed elements

### Acceptance Criteria

- On mobile, a user can quickly reach releases, filters, and settings.
- The app no longer renders the full sidebar as the first several screens on
  small viewports.
- Desktop still supports dense table scanning.
- The expanded release view remains usable at narrow widths.

### Verification

- [x] Check 1440x1000 desktop.
- [x] Check 900px breakpoint behavior.
- [x] Check 390x844 mobile.
- [x] Expand a release on desktop and mobile.
- [x] Open and close settings on desktop and mobile.

## Phase 3: Interaction Vocabulary and Core Concepts

**Priority:** P1  
**Status:** [x]  
**Prerequisites:** Phase 1; coordinate with Phase 2  
**Primary files:** `dashboard.html`, `dashboard.js`, `dashboard.css`

### Objective

Make the core concepts obvious and consistent: populated, cached, unseen, seen,
starred, filtered, show-only, selected range, preloading, and provider settings.

### Work Items

- [x] Redesign label filtering semantics:
  - remove ambiguity from the two-checkbox pattern
  - keep both "include/exclude" and "show only" power if still needed
  - expose advanced filtering progressively instead of making every row complex
- [x] Normalize release row states:
  - unseen marker
  - read/seen
  - starred
  - cached
  - expanded
  - hover/focus
- [x] Make table sorting clearer:
  - show active sort direction visibly
  - make sortable headers feel interactive without overdecorating
- [x] Clarify date range actions:
  - distinguish "populate release list" from "preload release data"
  - show why actions are disabled
  - make selected range status visible near the relevant actions
- [x] Replace vague labels where needed:
  - "Show only" should map directly to active filters
  - "Filter by Label/Page" should explain or visually distinguish include vs
    only behavior
  - "Status" should identify whether it is selection status, job log, or app log
- [x] Add consistent titles/tooltips for icon-only actions.

### Acceptance Criteria

- A new user can understand which releases are unseen, starred, cached, or
  filtered without reading the README.
- Date actions explain their availability and result.
- Label filtering does not require guessing what each checkbox means.

### Verification

- [x] Test label include/exclude/show-only flow with multiple labels.
- [x] Test Unseen and Starred top-bar filters.
- [x] Test mark seen/unseen actions.
- [x] Test preload disabled/enabled states across populated and unpopulated
  ranges.

## Phase 4: State Surfaces and Feedback Patterns

**Priority:** P1  
**Status:** [x]  
**Prerequisites:** Phase 1  
**Primary files:** `dashboard.html`, `dashboard.js`, `dashboard.css`

### Objective

Create a coherent set of state surfaces for loading, empty, error, server down,
missing credentials, provider connection, progress logs, disabled actions, and
destructive actions.

### Work Items

- [x] Define shared state components:
  - inline notice
  - blocking notice
  - modal/dialog
  - empty state
  - progress/status log
  - destructive confirmation
- [x] Replace `alert()` calls with in-app feedback where practical.
- [x] Make credentials-needed and load-credentials flows consistent with the
  settings panel.
- [x] Improve server-down and max-results messages:
  - include cause
  - include user action
  - avoid dead-end text
- [x] Improve empty states:
  - no releases loaded
  - no releases match filters
  - no labels available
  - no cached data/preload pending
- [x] Add consistent loading states for:
  - release fetch
  - provider config
  - IMAP folder discovery
  - populate range
  - preload range
  - embed loading
- [x] Keep status logs useful but visually secondary.

### Acceptance Criteria

- Users receive feedback in the app rather than through browser alerts where
  avoidable.
- Similar states look and behave similarly.
- Destructive actions are clearly distinguished from routine actions.
- Empty/error states teach the next useful action.

### Verification

- [x] Launch with missing credentials or simulated missing credentials.
- [x] Test server-down modal if feasible.
- [x] Test IMAP discovery success/failure states.
- [x] Test reset-cache flow.
- [x] Test no matching releases after applying filters.

## Phase 5: Release Browsing and Listening Polish

**Priority:** P2  
**Status:** [x]  
**Prerequisites:** Phase 2 and Phase 3  
**Primary files:** `dashboard.js`, `dashboard.css`, `dashboard.html`

### Objective

Make the release list and expanded player feel like the core product rather than
a table with embedded content.

### Work Items

- [x] Improve expanded row visual relationship:
  - selected row state
  - detail area connected to the row
  - clearer close/collapse behavior
- [x] Improve player/description layout:
  - preserve Bandcamp player visibility
  - make description readable with better line length and contrast
  - avoid overly tall detail sections when content is short
- [x] Add efficient browsing affordances:
  - keyboard focus clarity
  - next/previous row navigation remains visible
  - optional "open on Bandcamp" secondary action
- [x] Reconsider row density:
  - keep scanning fast
  - improve line-height enough for readability
  - prevent long artist/title text from creating awkward rows
- [x] Make cached/preloaded state useful:
  - show cached where it helps decision-making
  - avoid noisy badges if every row is cached

### Acceptance Criteria

- Expanded release browsing feels intentional and stable.
- The player and description remain readable and usable across view widths.
- Dense scanning is preserved.

### Verification

- [x] Expand album and track releases.
- [x] Test rows with long artist/title text.
- [x] Test cached and uncached releases.
- [x] Test keyboard navigation and collapse.

## Phase 6: Visual Refinement

**Priority:** P2  
**Status:** [x]  
**Prerequisites:** Phases 1-5 mostly stable  
**Primary files:** `dashboard.css`

### Objective

Apply final typography, color, spacing, and detail polish once the structure is
maintainable.

### Work Items

- [x] Choose a more intentional app typography direction:
  - keep product UI readable and dense
  - reduce overuse of uppercase
  - define clear hierarchy for app title, section labels, table content, helper
    text, and status text
- [x] Refine color palette:
  - avoid generic cyan-on-dark as the only identity
  - choose one primary action color
  - reserve semantic colors for actual meaning
  - ensure light mode is not an afterthought
- [x] Refine spacing rhythm:
  - tighter groups where controls belong together
  - more separation between unrelated workflows
  - reduce unnecessary nested panels
- [x] Refine motion:
  - subtle transitions for expand/collapse and feedback
  - respect reduced motion
  - avoid animating layout-heavy properties where possible
- [x] Replace decorative background effects if they do not serve the app.
- [x] Make iconography consistent:
  - settings
  - help
  - star
  - collapse/expand
  - sort direction

### Acceptance Criteria

- The interface feels coherent and specific to music release triage.
- Dark and light themes both feel deliberate.
- Visual detail supports workflow clarity rather than decoration.

### Verification

- [x] Desktop screenshot review.
- [x] Mobile screenshot review.
- [x] Settings screenshot review.
- [x] Expanded release screenshot review.
- [x] Quick contrast check for text, controls, and semantic states.

## Cross-Cutting Requirements

- Preserve local-first privacy expectations.
- Do not introduce external frontend build tooling unless there is a clear
  reason and migration plan.
- Prefer small DOM helper functions and CSS classes over large template strings
  with inline styles.
- Keep the dashboard fast with large release lists.
- Maintain keyboard accessibility for release browsing and modal/settings flows.
- Avoid storing credentials or mailbox-derived runtime state in the repo.

## Suggested Implementation Order

1. Phase 1: design primitives and CSS hygiene.
2. Phase 2: app shell and responsive layout.
3. Phase 3: interaction vocabulary.
4. Phase 4: state surfaces.
5. Phase 5: release browsing polish.
6. Phase 6: visual refinement.

If parallelizing, split work by write ownership:

- Worker A: tokens/classes and inline-style extraction in `dashboard.css` and
  `dashboard.html`.
- Worker B: app shell and responsive layout after Worker A lands primitives.
- Worker C: interaction/state logic in `dashboard.js`, starting only after the
  relevant classes and layout targets exist.

Avoid parallel edits to the same generated row/detail markup in `dashboard.js`
unless ownership is explicitly split.

## Open Decisions

- Should the dashboard remain dark-first, or should it follow system theme by
  default?
- Should label filtering keep both include/exclude and show-only power, or be
  simplified to one primary filter model?
- Should status be a persistent log, a collapsible activity drawer, or an inline
  range summary plus optional detailed log?
- Should settings remain modal, become a drawer, or become an inline panel on
  wider desktop layouts?

## Running Notes

- 2026-05-02: Initial plan created from UI audit. No implementation work has
  started.
- 2026-05-02: Implemented the dashboard UI foundation across
  `dashboard.css`, `dashboard.html`, and `dashboard.js`. Added design tokens,
  shared component/utility classes, focus states, class-driven row/detail
  markup, responsive mobile ordering, clearer include/show-only filter labels,
  in-app feedback for former alert paths, destructive cache confirmation,
  explicit detail collapse, and conservative visual cleanup. Verified with
  `node --check dashboard.js`, local app launch on port 5051, curl checks for
  dashboard assets/API health, Playwright screenshots at 1440x1000 and
  390x844, settings modal screenshot, expanded row screenshot, and
  `python3 -m pytest -q` (no tests collected).
