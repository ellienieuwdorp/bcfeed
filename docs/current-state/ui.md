# Current state: UI and visual design

Factual description of the UI as shipped, based on a full read of `dashboard.css` (839 lines), `dashboard.html` (185 lines), cross-checks against `dashboard.js`, contrast measurements, and the screenshots in [`screenshots/`](screenshots/). Finding IDs (UI-n, UX-n, JS-n) refer to the verified audit results. This document describes; it does not prescribe.

## 1. The design language, honestly

The intended aesthetic is a dark, mildly neon "mission-control" dashboard: deep slate surfaces (`#0f1116` / `#181b22`), a cyan accent, pill badges, uppercase letterspaced microlabels, a dense 14px data grid with 1.05 line-height, ambient pink/cyan radial glows behind the content (css:33-34), and a universal `translateY(-1px)` hover-lift (css:83, 130, 565). As a dark-mode-only artifact it is reasonably coherent — see `screenshots/10-populated-dark.png` — and every load-bearing contrast pair sampled in dark mode passes AA comfortably (Populate button 8.7:1, log text 6.7:1, error bar 10.8:1).

The language was designed once, on dark, and never finished. It is visibly stratified into **three generations** that coexist on screen:

### Generation 1 — the tokenized base layer

The oldest and most systematic code: the `:root` token block, table, rows, base buttons, filter list, and detail panel. These consistently consume `--surface` / `--border` / `--radius` / `--shadow` and share the hover-lift idiom. This layer *is* the design system, such as it exists.

### Generation 2 — the wireframe-promoted calendar layer

The calendar/date-range sidebar is literal wireframe scaffolding promoted to production (UI-7). The class names say so: `.wireframe-panel` (dashed 1px border, css:261-273) and `.wireframe-body` (diagonal repeating-gradient hatch fill, css:349-361). This layer invents its own conventions on top of half-used tokens:

- its own 3/5/7px spacing rhythm (css:378, 384, 397, 401, 409, 435) against the app's 4/8/12/16 elsewhere
- its own 6px button radius (css:408, 426) against the base 8px/10px
- a second, untokenized blue family (`#64a8ff` / `rgba(100,168,255,…)`) for the most important actions
- the app's only glow rings (static box-shadows, css:528, 553, 559 — no keyframe animations exist anywhere)
- 10-11px type (css:413, 429, 436)

Visible in every sidebar screenshot: `screenshots/02-empty-dashboard-light.png`, `screenshots/10-populated-light.png`, `screenshots/15-label-filters.png`. The dashed idiom also leaks into the expanded row's description box (`.detail-desc`, css:721-733; `screenshots/12-row-expanded-light.png`).

### Generation 3 — the inline-styled modal layer

The newest surfaces — Settings, Credentials Needed, Load Credentials — have the best taste and the worst discipline (UI-10). Visually they are the calmest, most product-like part of the app (flat token surface, plain labeled buttons, red-outlined destructive action; `screenshots/03-settings-light.png`, `screenshots/04-load-creds-modal-light.png`, `screenshots/01-first-run-light.png`). But they are built almost entirely from inline styles in HTML: spacer divs `style="height:12px"` instead of gap (html:130, 134), per-button inline paddings (html:132, 137, 141, 144), an inline-styled section label (html:135), and an ad-hoc danger red `#b83a3a` (html:144) that exists in no token and matches none of the app's three other reds. The sibling max-results and server-down modals abandon the modal conventions entirely: 12px radius instead of the 10px token, bespoke shadows, 0.75 backdrop instead of 0.45, z-index 9999 instead of 50 (css:585-605, 806-826).

Net: the older code defined tokens and mostly used them; the newer code bypasses the token layer and styles ad hoc in HTML. The divergence is architectural (inline styles vs classes), chromatic (untokenized reds and blues), and geometric (new radius/backdrop/z conventions).

## 2. Design token inventory (summary)

### CSS variables — the only tokenized values

Eleven variables, defined in `:root` (dark = default, css:1-13) and re-declared by `.theme-light` (css:14-25): `--bg`, `--surface`, `--panel`, `--accent` (dark `#52d0ff` cyan / light `#1f7aff` blue), `--text`, `--muted`, `--border`, `--shadow`, `--radius` (10px), `--row-bg`, `--row-unseen-bg`.

**Bug:** `var(--header-bg)` is referenced at css:64 but never defined anywhere — the header background silently computes to transparent and relies only on `backdrop-filter: blur(12px)` (css:65).

### Everything not in a variable

- **Hardcoded accent-cyan copies** of dark `--accent`: `rgba(82,208,255,…)` at css:33, 59, 152, 159, 507-508, 552-553, 566, 581-582, 682, 690-692. These never retheme (see §4).
- **A second blue family, never tokenized:** `#64a8ff` / `rgba(100,168,255,…)` on the Populate button (html:52), calendar unseen pills and dots (css:503, 526-528), back-to-top (css:244-245), and every log line (dashboard.js:565, 570). Light mode adds `rgba(64,150,210,…)` (css:558-559) — a fourth blue overall (UI-11).
- **Four unrelated reds, no danger token:** `#ff5f5f` (row/calendar dots, css:108, 534), `#ff6b6b` (show-only checkbox accent, css:223), `#b83a3a` (error border + Revoke link, html:85, 144), `#ffc5c5` (error text, html:85).
- **Gold star family:** `#f2d45c` + tints (css:135-138, 679).
- **Decorative gradient tints:** hot pink `rgba(255,105,180,0.08)` in the dark body background (css:34), orange `rgba(255,171,64,0.1)` in light (css:43).
- **White-alpha overlays everywhere** (`rgba(255,255,255,0.02…0.36)`) as control fills, hatches, and inset highlights (css:74, 124, 282-288, 355-359, 406, 424, 477, 492, 540-542) — all dark-tuned; they vanish on white (see §4).

### Typography

- One font stack, `"Inter", "Helvetica Neue", Arial, sans-serif` (css:37) — Inter is never loaded (no `@font-face`/`<link>`), so machines without it silently fall back. No monospace stack anywhere, including the status log.
- **11 font sizes with no scale** (22/20/18/16/15/14/13/12/11/10px), 10px minimum on the CACHED badge, Today button, and sort indicator (css:154, 429, 669).
- Five micro letter-spacing values (0.2-0.6px), no system.
- `line-height: 1.05` on table cells and filter items (css:211, 651).
- Uppercase implemented two ways: CSS `text-transform` (css:156, 189, 347, 420, 656) *and* literally capitalized HTML text ("FILTER BY DATE" html:15, "SHIFT-CLICK TO SELECT RANGE" html:39, "SELECTED DATE RANGE:" html:51, "SHOW ONLY:" html:76).

### Spacing, radii, shadows, motion, z-index

- **Spacing:** 18 distinct px values, untokenized; calendar cluster uses 3/5/7px while everything else uses 4/8/12/16; four different modal paddings; literal spacer divs in HTML (html:130, 134).
- **Radii:** six values — 6, 8, `calc(10px - 2px)`, 10 (token), 12, 999px/50%. Buttons (8px) never match panels (10px) or the newest modals (12px) (§1.5 of the CSS map).
- **Shadows:** a `--shadow` token exists and is used 5 times; 15+ hardcoded one-off shadows exist anyway (css:84, 132, 248, 272, 553, 559, 567, 600, 821, …).
- **Motion:** no `@keyframes` at all. Four near-identical hover durations (0.1/0.12/0.15/0.2s), `transition: all` on `.button` and `.star-btn` (css:78, 127), hover-lift as the universal cue.
- **Z-index:** 2 / 5 / 6 / 20 / 50 / 9999, unscaled; the 50-vs-9999 split is why server-down always beats settings (css:592, 751, 813).

### Dead CSS

`.pill` (css:687-702), `.inline-link` (css:52-56), `.calendar-row` (css:367-373) are unreferenced in HTML and JS (grep-verified). `.calendar-day.selected` duplicates `.calendar-day.in-range` verbatim (css:580-584 vs 506-509). Notably, the entire `.calendar-day.scraped` family — including its glow-ring rules (css:539-561) — is dead: dashboard.js:1260 applies the class `unseen-day`, not `scraped`, so the only live "populated" treatment is the blue pill from `.unseen-day .date-label` (UI-5 correction).

## 3. Component styling map (summary)

| Component | State | Key facts |
|---|---|---|
| Table | Gen 1, solid | Dense 14px grid, sticky header, sortable `th` with absolute sort indicator; column widths inline in HTML (html:90-95). The best-working surface. |
| Rows | Gen 1, one bug | Unseen = red dot + `--row-unseen-bg` tint (double-encoded). `tr.data-row:hover` (css:681-683) has equal specificity but later order than `.unseen`, so hovering an unseen row erases its tint; the 2%-alpha hover itself is nearly invisible (UI-6). |
| Badges | Mixed | `.cached-badge` is JS-injected, 10px bold uppercase, hardcoded cyan tints (css:146-161). Legend swatches are inline-styled ad-hoc pills (html:41, 43). |
| Buttons | Fragmenting | One `.button` base (css:70-92) defeated by ~10 inline overrides in HTML: Populate restyled to the untokenized blue (html:52), "Select entire month" to panel-bg (html:46), mark seen/unseen shrunk (html:54-55), all settings buttons re-padded inline (html:132-144). Three size variants exist only as inline styles. |
| Inputs | Inconsistent | Sidebar checkboxes styled via `accent-color` (`--accent` and hardcoded `#ff6b6b`, css:220-223); settings checkboxes are unstyled native (html:123, 127). No styled text input exists in the app. |
| Calendar | Gen 2 | Lives inside wireframe chrome (dashed border + hatch, css:261-273, 349-361). Eight visual day states declared, several dead (§2). Second hatch pattern for the disabled overlay (css:282-305). Magic numbers: `min-height:187px` grid, `min-height:290px` card, 110px month label. |
| Status log | Misnamed, misplaced | `.calendar-log` is the status log under the table, not part of the calendar (css:440, html:102). Fixed 200px tall whether full or empty; log lines injected with hardcoded `color:#64a8ff` (dashboard.js:565, 570); proportional font, no timestamps, no severity colors (UI-9). Error state's entire visual identity is inline (html:85). |
| Modals | Two generations | Settings/missing-token/load-creds: token radius/shadow, 0.45 backdrop, z-50 — but inline-styled content (html:116-147). Max-results/server-down: 12px radius, bespoke shadows, 0.75 backdrop, z-9999 (css:585-605, 806-826). |
| Detail panel | Gen 1 + wireframe leak | Token card, two-column grid, Bandcamp embed shell — but `.detail-desc` has the dashed wireframe border (css:726; `screenshots/12-row-expanded-light.png`). |
| Scrollbars, tooltips | Absent | No custom scrollbar styling anywhere; tooltips are native `title` attributes only. |

Layout: `body` is `overflow:hidden` with a 2-col grid `360px 1fr` sized `calc(100vh - 70px)` — 70px is a magic number for a header that actually lives *inside* `main` (css:168-174, html:69-83), and the header's `position:sticky` is inert because `main` never scrolls (css:61-69). Exactly one breakpoint (900px, css:827-838), not updated for the fixed-height shell. At 1000px — an unhandled width — the Date column clips mid-header and mid-value (`screenshots/19-narrow-1000px.png`, UI-14).

## 4. Theming state: light mode is ~60% broken

Mechanism: JS toggles `.theme-light` on `<body>` (dashboard.js:279) from the settings checkbox; `.theme-light` re-declares the 11 variables. Only three light-specific rule overrides exist beyond the variables (body gradient plus two calendar rules that target the dead `.scraped` class). Everything else "themes" only if it happens to consume a variable — and much of it does not. Dark is the only finished theme (UI-4).

Concrete breakages, with measured WCAG ratios:

1. **The primary Populate button is unreadable: 1.34:1, ~1.06:1 disabled** (UI-1, high). Inline white text on `rgba(100,168,255,0.35)` (html:52) composites to white-on-pale-blue; with `.button:disabled` opacity 0.45 (css:86-92) the label is effectively invisible. See `screenshots/01-first-run-light.png`, `screenshots/06-populate-error-light.png`, `screenshots/10-populated-light.png` — the same button is 8.70:1 in `screenshots/10-populated-dark.png`.
2. **Populated calendar-day numbers are illegible: 1.26:1** (UI-2, high). `.unseen-day .date-label` forces `#fff` on `rgba(100,168,255,0.28)` over a white card (css:502-505). In `screenshots/10-populated-light.png` nearly every June day number is a ghost digit while the few unpopulated days (10, 11) are crisp black.
3. **Error feedback is unreadable: 1.29:1** (UI-3, high). Inline `#ffc5c5` on `rgba(184,58,58,0.1)` (html:85) is tuned for dark (10.78:1 there). Compounding: the captured error state (`screenshots/06-populate-error-light.png`) shows an empty Status box and no error text anywhere — the failure produced zero visible feedback (UX-3).
4. **Status log text: 2.45:1** at 12px (fails AA; dark is 6.70:1) — hardcoded `#64a8ff` (dashboard.js:565, 570).
5. **CACHED badge: 3.65:1** at 10px bold (fails AA; dark is 7.23:1) — hardcoded cyan tints under light-blue accent text (css:146-161; UI-13).
6. **Control fills vanish.** Every button/nav/scrollbox fill is a `rgba(255,255,255,0.02-0.06)` overlay (css:74, 124, 406, 424, 477, 492) — white on white in light mode, so "Mark as seen", calendar nav, and Today reduce to hairline outlines (`screenshots/02-empty-dashboard-light.png`).
7. **Accent tints don't retheme.** Every cyan tint is hardcoded to dark's `#52d0ff` (css:59, 152, 159, 507-508, 566, 581-582, 690-692), so light mode mixes its `#1f7aff` accent with leftover cyan chrome — up to four blues visible simultaneously in `screenshots/10-populated-light.png` (UI-11).

Root causes are exactly two: white-alpha fills that assume a dark backdrop, and hardcoded accent/blue literals that bypass `--accent`. The one control that themes correctly by construction is `.toggle-button.toggle-active` (`background: var(--accent); color: var(--bg)`, css:93-97).

## 5. Accessibility state

- **No designed focus state exists.** Zero `:focus`/`:focus-visible` rules in the stylesheet (grep-verified). Nothing removes outlines either, so keyboard-focusable elements (data rows with `tabIndex=0`, star buttons, links) show browser-default rings — but that is inherited luck, not design (UI-12).
- **Core controls are unreachable by keyboard, not just unstyled:** calendar day cells are JS-built `<div>`s with click listeners only — no tabindex, role, or key handling, and the shift-click range gesture has no keyboard equivalent (dashboard.js:1235-1322); sortable headers are plain `<th>`s with click handlers, no `aria-sort` (dashboard.js:950-963) (JS-7, UI-12).
- **Modals have no dialog semantics:** no `role="dialog"`, no `aria-modal`, no focus trap, no Escape handling (dashboard.js:1012-1041; html:116-180); the server-down modal has no dismissal at all (JS-7, UX-15).
- **The status log — the app's sole feedback channel — has no `aria-live`,** so the longest-running operation is silent to assistive tech (html:102-110).
- **Invisible-but-interactive target:** `.row-dot.read { opacity:0 }` keeps `cursor:pointer` and its click handler on an invisible 10px control (css:113-115; dashboard.js:900-911).
- **Contrast failures** in light mode as itemized in §4 (five failing pairs on primary surfaces); in dark mode, sampled pairs pass.
- **Sub-legible type:** 10px text on the CACHED badge, Today button, and sort indicators (css:154, 429, 669); `line-height:1.05` on data cells risks clipped descenders (css:651).
- Counterpoint worth preserving: the row-level keyboard loop (arrows, Enter/Space, `s`, `u`, Escape — dashboard.js:852-898) is complete and shows the intent existed. It is documented nowhere in UI or docs (UX-14).

## 6. The "AI smell" inventory

The complete list of generated-mockup residue, in rough order of visual damage. Each item is live in production today.

1. **Wireframe scaffolding shipped to production** (UI-7). Dashed 1px borders and diagonal hatch-gradient fills frame the entire sidebar (`.wireframe-panel` css:261-273, `.wireframe-body` css:349-361 — the class names literally say "wireframe"); a second, differently-angled hatch covers the disabled calendar (css:282-288); the expanded row's description box repeats the dashed idiom (css:726). The sidebar reads as an unfinished mockup in `screenshots/02-empty-dashboard-light.png`, `screenshots/10-populated-light.png`, `screenshots/15-label-filters.png`, and `screenshots/12-row-expanded-light.png`.
2. **Ambient neon body gradients** (UI-8). Hot-pink + cyan radial glows behind the dark UI (css:33-34), orange + blue in light (css:41-45). Faintly visible in the corners of `screenshots/10-populated-dark.png` and `screenshots/05-docs-readme.png`.
3. **Static glow rings** (UI-8, UI-5). Halo box-shadows on unseen calendar dots (css:528) and on the scraped/selected day rules (css:553, 559). No pulses or keyframe animations exist — the glows are static. Caveat: the scraped-day glow rules are dead code (JS applies `unseen-day`, not `scraped`; dashboard.js:1260), so the live glow is the dot halo; the dead rules remain in the shipped stylesheet.
4. **Emoji-as-icon and four icon systems in one viewport** (UI-8). A ⚙️ emoji button next to a text "?" button (html:79-80), unicode glyphs `▾ ‹ ›` for carats and nav (html:22, 31, 33, 106), and an inline SVG star (dashboard.js:759). Visible in the header of every screenshot.
5. **ALL-CAPS microcopy, hardcoded in HTML** (UI-8). "FILTER BY DATE", "SHIFT-CLICK TO SELECT RANGE", "SELECTED DATE RANGE:", "SHOW ONLY:" as literal capitalized text (html:15, 39, 51, 76) alongside CSS `text-transform: uppercase` elsewhere — five uppercase labels visible at once in `screenshots/10-populated-dark.png`.
6. **Version string in the H1**: "bcfeed v1.0" as the product title (html:73), hardcoded and unrelated to any release metadata (UI-8, ARCH-9).
7. **Uniform hover-lift on everything**: `translateY(-1px)` applied identically to buttons, star buttons, and calendar cells (css:83, 130, 565) (UI-8).
8. **999px pill maximalism**: badges, legend swatches, and calendar date labels all rendered as fully-rounded pills (css:151, 498, 689; html:41, 43) (UI-8).
9. **Inline-style sprawl**: ~40 `style=""` attributes in a 185-line HTML file (html:13, 19-20, 26, 38-47, 49-56, 72-80, 84-85, 90-95, 102-112, 118-144, 156-161, 168-176); three button size variants and the entire error-state identity exist only inline.
10. **Chrome outshouting content** (UI-6). In `screenshots/10-populated-light.png`/`-dark.png` the loudest pixels are state chrome: alarm-red dots on ~90% of rows for the *default* state of a new release, shouting 10px-bold CACHED pills inline with album titles (4 of 5 rows in `screenshots/13-starred-filter.png`), and a calendar where populated + selected + unseen stack into a uniform wall of blue pills and red dots — while the artists and albums the user came for are the quietest text on screen.
11. **Developer vocabulary as UI copy** (UX-6). "Populate release list", "Preload release data", "CACHED", "3 of 30 selected days not yet populated.", legend "Populated", "Gmail token missing", raw pipeline lines ("Parsing messages...", "Checking for releases with identical URLS...") in the Status box (`screenshots/18-populate-success-log.png`, `screenshots/10-populated-dark.png`), including a leaked end-exclusive date range ("2026-06-01 to 2026-07-01" for a June selection).
12. **A raw log box as the primary feedback organ** (UI-9, UX-7). A fixed-200px bordered rectangle bottom-right, permanently reserving ~25% of vertical space, carrying selection summaries, tutorials, progress, and errors as uniform blue fake-link text (dashboard.js:559-572, 565) — rendered ~900px away from the sidebar controls it describes, and an empty hollow frame on first run (`screenshots/02-empty-dashboard-light.png`, `screenshots/06-populate-error-light.png`).
13. **Prototype leftovers in the stylesheet**: the undefined `--header-bg` (css:64), three dead rule blocks, a verbatim-duplicated gradient, the dead `.scraped` state family, and the misnamed `.calendar-log`/`.settings-row`/`.missing-token-modal` classes (§2, §3) — drift between the CSS and the JS-rendered DOM consistent with generated-then-edited code.

## 7. What holds up

For balance, the parts of the current UI that are genuinely working and are explicitly to be preserved: the dense sortable table (the strongest surface in the app), the calendar-as-coverage-map concept (UX-13 calls it the app's best IA idea, even though its current encodings collapse), star-triggers-preload, the complete row-level keyboard loop, the served docs pages (`screenshots/05-docs-readme.png` — arguably the most polished surfaces in the product), and the settings-modal generation's calm, flat visual direction (`screenshots/03-settings-light.png`), which is the right target language for a practical local tool even though its implementation (inline styles) is the wrong construction.
