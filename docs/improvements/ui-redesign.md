# UI Redesign: Calm Slate

**Direction:** Calm Slate — system normalization of the existing dark-tool language.
**Status:** Approved direction (3-judge panel), full implementable spec. No code changes yet.
**Scope:** `dashboard.css` (1,058 lines), `dashboard.html` (306 lines), small template-string edits in `dashboard.js`, plus normalization of the post-merge settings/provider surface (§6.12). No framework, no build step, no new dependencies.
**Evidence:** verified audit findings (`docs/` audit set; finding IDs UI-1..15, UX-*, JS-*, ARCH-* referenced throughout) and the design-system inventory of `dashboard.css`/`dashboard.html` (line numbers cited as `css:` / `html:` / `js:` refer to the current files at commit **`e363bf4`**).

**Line-reference note (revalidated 2026-07-05 against `e363bf4`):** the IMAP-provider merge changed the three frontend files in a fortunately localized way — `dashboard.css` grew 839 → 1,058 lines *by pure append* (a new settings/provider block at css:837-1058), so **every `css:` reference ≤ 835 in this spec is unchanged and still valid**. `dashboard.html` lines 1-114 are untouched (all `html:` refs ≤ 114 stand); the settings modal region was rebuilt as html:116-267 and the credential modals moved to html:274-301 — refs in those regions are updated below. `dashboard.js` lines 1-59 are untouched; refs past line 60 shifted **+3**, and a 392-line provider/IMAP controller was appended at js:1702-2093 — all load-bearing `js:` refs below are updated. The appended CSS is a *third* styling generation with fresh token-spec violations; §6.12 (UIR-30) specifies its normalization.

Every spec chunk has a stable ID **UIR-n** and a size: **S** (< half a day), **M** (half–1 day), **L** (1–2 days).

---

## 1. Design principles (UIR-1, S — write once, enforce in review)

Derived from the product brief: *practical-first; a calm, reliable local utility; no over-the-top design, no serif display fonts, no crazy theming, no decorative flourishes.*

1. **Same tool, a year more mature.** bcfeed already has a legible identity — deep slate surfaces, a cyan signal color, a dense data grid, a calendar that doubles as a coverage map. Keep all of it. Remove everything that isn't load-bearing: wireframe hatching, neon gradients, glow rings, pill chrome, ALL-CAPS labels, emoji icons.
2. **Content is the loudest thing on screen.** Artist and title outrank every piece of state chrome. Each state (unseen, starred, saved, selected, checked/unchecked day) gets exactly **one quiet visual channel** (fixes UI-6, UI-5, UX-13).
3. **Correct by construction, not by audit.** Every color is a token defined per theme. Light mode stops being a half-ported skin (UI-4) because there is no color that *can* fail to retheme. **Review gate: no color literal may appear outside the token block** — enforceable with a single grep.
4. **The accent is a learnable signal.** One accent family. Accent means "new music / your selection / the primary action." It never appears on progress bars, toast bodies, or decorative chrome, so it stays meaningful.
5. **Shadows mean elevation, motion means feedback.** Shadows only on things that float (modals, toasts). Motion only on color/opacity, 120 ms, one token. No hover-lifts, no glows, no animations except the spinner and the indeterminate progress sweep.
6. **Plain language, proper primitives.** The raw log box becomes a status strip + progress + banner/toast system. Disabled controls explain themselves in a sentence-case hint line. Sentence case everywhere; nothing below 12 px.
7. **The settings modal is the reference surface — rebuilt properly.** Its flat, labeled, restrained look (UI-10 calls it "the right visual direction built the wrong way") becomes the system-wide language, expressed as tokens and classes instead of inline styles. The e363bf4 merge rebuilt the panel's *structure* in this direction (sections, form primitives) but wrote its *colors* outside the token discipline — §6.12 closes that gap.
8. **Preserve what works:** the dense sortable table, calendar-as-coverage-map, star-triggers-preload, keyboard shortcuts (surfaced, no longer secret — UX-14), and local-first privacy.

---

## 2. Why this direction won

Judge tally (higher = better): **Liner Notes 102.5, Calm Slate 98, Stockbook 83.5.** The panel selected **Calm Slate** as the base direction — it scored within a rounding error of the leader while being the only proposal that was a *complete, implementable normalization* of the existing system (full token spec, per-component before/after, deletion checklist) — and grafted the best ideas from the other two into it (§13).

- **Liner Notes** (highest raw score): strongest on native-platform polish (`color-scheme`, system scrollbars, legible disabled states) but under-specified the status/feedback system and defined no warn/success semantics. Its platform grafts are folded in wholesale.
- **Stockbook**: sharpest discipline ideas ("accent means new music, nothing else", one home for the release count, two-PR phasing) but too aggressive a visual departure for a brief that says "preserve what works." Its discipline rules are folded in; its visual language is not.

---

## 3. Token spec (UIR-2, M — the foundation commit)

This block **replaces** `dashboard.css:1-25` (current `:root` + `.theme-light`). Dark is the default (`:root`); light is activated by the **existing** `body.theme-light` class toggle (`js:282`) — the JS mechanism is unchanged.

**Rules:**
- No color literal (hex, `rgb()`, `rgba()`, named color) may appear anywhere in `dashboard.css`, `dashboard.html`, or `dashboard.js` outside this block. Review gate: `grep -nE '#[0-9a-fA-F]{3,8}|rgba?\(' dashboard.css dashboard.html dashboard.js` must return only this block.
- This kills, by construction: the four blues (UI-11: `--accent` cyan vs `#64a8ff` vs light `#1f7aff` vs `rgba(64,150,210,…)`), the four reds (`#ff5f5f`, `#ff6b6b`, `#b83a3a`, `#ffc5c5` — css map §1.2; the merge *re-introduced* the `#ff6b6b` family in the appended settings block, css:1045-1052 — §6.12), every hardcoded cyan tint that ignores the light theme (css:59,152,159,507-508,552-553,566,581-582,690-692, plus the appended focus ring `rgba(82,208,255,0.15)` at css:940), every white-alpha control fill that vanishes on white (css:74,124,406,424,477,492,729, plus css:876 in the appended block), and the undefined `--header-bg` (css:64).

```css
/* ===== Calm Slate tokens — dark is default (:root), light via body.theme-light ===== */
:root {
  color-scheme: dark;            /* native form controls, scrollbars, UA pickers theme for free */

  /* Neutrals */
  --bg: #0f1116;                 /* flat page bg — the radial gradients are deleted */
  --surface: #171b23;            /* cards, table body, activity strip */
  --surface-inset: #12151b;      /* sidebar, table header, log detail, expanded-row bg */
  --surface-raised: #1d222c;     /* modals, toasts */
  --border: #262c3a;
  --border-strong: #394153;      /* control borders, today ring */
  --text: #edf0f7;
  --text-muted: #9aa3b8;         /* labels, table header, meta — ~7:1 on surface */
  --text-faint: #667089;         /* de-emphasis only: unfetched day numbers, placeholder, counts, disabled labels */

  /* Single accent family (kills #64a8ff and rgba(100,168,255,…) entirely) */
  --accent: #52d0ff;
  --accent-hover: #7adcff;
  --on-accent: #0b1219;          /* text/icon on solid accent fills — ~10:1 */
  --accent-tint: rgba(82, 208, 255, 0.10);
  --accent-tint-strong: rgba(82, 208, 255, 0.18);
  --accent-border: rgba(82, 208, 255, 0.45);

  /* Semantic (kills the four unrelated reds) */
  --danger-text: #ff7b72;        /* ~6:1 on surface */
  --danger-tint: rgba(255, 107, 97, 0.10);
  --danger-border: rgba(255, 107, 97, 0.45);
  --warn-text: #e3b341;
  --warn-tint: rgba(227, 179, 65, 0.10);
  --success-text: #4ecb71;
  --success-tint: rgba(78, 203, 113, 0.10);
  --star: #e8c74f;               /* the one gold, icon fill only */

  /* Controls — solid fills per theme; no more white-alpha overlays */
  --control-bg: #212836;
  --control-bg-hover: #2a3242;
  --control-border: #394153;

  /* Rows */
  --row-hover: #1c2130;          /* replaces the 2%-alpha hover; --row-unseen-bg is deleted */
  --row-expanded: var(--surface-inset);

  /* Chrome */
  --header-bg: rgba(15, 17, 22, 0.88);   /* fixes the undefined --header-bg (css:64) */
  --backdrop: rgba(5, 7, 11, 0.55);      /* ONE backdrop for all modals */
  --focus-ring: var(--accent);

  /* Elevation — shadows ONLY here; cards use border, not shadow */
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.35);            /* sticky header edge, toasts-lite */
  --shadow-2: 0 12px 32px rgba(0, 0, 0, 0.45);          /* modals, toasts */

  /* Geometry */
  --radius-sm: 6px;              /* buttons, inputs, day cells, badges */
  --radius-md: 10px;             /* cards, panels, modals, log, table wrapper */
  --radius-full: 999px;          /* dots and progress bar only */
  --space-1: 4px;  --space-2: 8px;  --space-3: 12px;
  --space-4: 16px; --space-5: 20px; --space-6: 24px; --space-8: 32px;

  /* Type */
  --font-ui: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --font-mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --text-xs: 12px; --text-sm: 13px; --text-md: 14px; --text-lg: 16px; --text-xl: 18px;

  /* Layers */
  --z-sticky: 10; --z-header: 20; --z-modal: 100;

  /* Motion — the only duration in the app */
  --transition-fast: 120ms ease-out;
}

body.theme-light {
  color-scheme: light;

  --bg: #f6f7f9;
  --surface: #ffffff;
  --surface-inset: #eef0f4;
  --surface-raised: #ffffff;
  --border: #d9dee7;
  --border-strong: #b6bfcd;
  --text: #171b23;
  --text-muted: #5a6374;         /* ~6.4:1 on white */
  --text-faint: #8b94a6;

  --accent: #0b6e99;             /* same cyan hue family, darkened for 5.7:1 on white */
  --accent-hover: #095c80;
  --on-accent: #ffffff;          /* 5.7:1 on accent — primary button passes AA (fixes UI-1) */
  --accent-tint: rgba(11, 110, 153, 0.08);
  --accent-tint-strong: rgba(11, 110, 153, 0.16);
  --accent-border: rgba(11, 110, 153, 0.40);

  --danger-text: #b42318;        /* ~6.3:1 on white (fixes UI-3) */
  --danger-tint: rgba(180, 35, 24, 0.06);
  --danger-border: rgba(180, 35, 24, 0.35);
  --warn-text: #9a6700;
  --warn-tint: rgba(154, 103, 0, 0.08);
  --success-text: #1a7f37;
  --success-tint: rgba(26, 127, 55, 0.08);
  --star: #b45309;

  --control-bg: #ffffff;
  --control-bg-hover: #f1f4f8;
  --control-border: #c6cedb;

  --row-hover: #f3f5f9;
  --row-expanded: #eef0f4;

  --header-bg: rgba(246, 247, 249, 0.88);
  --backdrop: rgba(23, 27, 35, 0.35);
  --focus-ring: var(--accent);

  --shadow-1: 0 1px 2px rgba(23, 27, 35, 0.07);
  --shadow-2: 0 12px 32px rgba(23, 27, 35, 0.16);
}

/* Global focus (currently zero focus styles exist — UI-12) */
:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }

/* Themed scrollbars (currently OS default clashing with slate — css map §3.11) */
* { scrollbar-width: thin; scrollbar-color: var(--border-strong) transparent; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: var(--radius-full); border: 2px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-track { background: transparent; }
```

**Theme seeding (3-line JS change, ships with this block):** on startup, if no stored theme preference exists, seed from `matchMedia('(prefers-color-scheme: light)')`; a stored setting always wins. The `#theme-toggle` checkbox (now a visible "Dark mode" setting, html:130-131) and `body.theme-light` mechanism are otherwise untouched.

---

## 4. Scales

### 4.1 Typography (UIR-3, M)

**Font:** system stack via `--font-ui` (SF Pro on the target macOS/Chrome). The phantom `"Inter"` at css:37 — never loaded, silently falling back to Helvetica/Arial — is dropped. `--font-mono` is used in exactly one place: the activity-log detail well.

**Five sizes, three weights (400/500/600). Zero `letter-spacing`. Zero `text-transform`.** The current 11 sizes (22/20/18/16/15/14/13/12/11/10px — css map §1.3), five letter-spacing values (css:80,101,157,187,196,341,346,392,…), and both uppercase mechanisms (CSS `text-transform` at css:156,189,347,420,656, **plus** the merge-appended `.settings-section-title` — 11px uppercase with 0.8px tracking, css:897-903 — **and** hardcoded ALL-CAPS HTML strings at html:15,39,51,76) are all removed. Nothing renders below 12 px — the 10 px badge (css:154), today-button (css:429), and sort-indicator (css:669) sizes are eliminated, as is the 11 px section title (css:898).

| Token | Size / weight | Line-height | Used for |
|---|---|---|---|
| `--text-xl` | 18px / 600 | 1.2 | The H1 app title ("bcfeed" — version string moves out of the H1 (html:73) into Settings) |
| `--text-lg` | 16px / 600 | 1.2 | Modal titles (h2) |
| `--text-md` | 14px / 400 (600 for empty-state titles) | **1.35 in the table** (fixes the 1.05 descender clipping at css:651 while staying dense), 1.45 elsewhere | Table cells, base UI text |
| `--text-sm` | 13px / 400 (500 for buttons) | 1.45 | Buttons, filter items, modal body, detail description, status line |
| `--text-xs` | 12px / 500 (400 mono for log detail) | 1.4 | Table headers, micro-labels, legend, badges, meta/counts, hints |

**Numerics:** date and count columns/fragments get `font-variant-numeric: tabular-nums` (also protects the fixed Date column width — UI-14).

**Micro-label primitive:** one class `.micro-label` = 12px / 500 / `var(--text-muted)` / **sentence case**. Replaces every uppercase treatment in the app (see §10 for exact string edits).

### 4.2 Spacing (UIR-4, S)

Strict 4px scale via `--space-1..8` (4/8/12/16/20/24/32). The current unscaled set (2,3,5,6,7,10,14,18,22,26,28,30 — css map §1.4) is retired, including the calendar's odd 7/5/3px rhythm (css:378,384,397,401,409,435) and the four divergent modal paddings (css:597,757,767,818).

Assignments:
- Card padding **12** (`--space-3`); modal padding **20** (`--space-5`); sidebar padding **16**.
- Table cells **6px 10px** — kept dense; 6 is the *only* sanctioned half-step, for table cells only.
- Control padding **6px 12px** (`.button-sm`: 4px 10px). Grid/flex gaps **8 or 12**.
- Calendar: grid gap **4**, day-cell min-height **32px**, calendar card padding **12**.
- Modal sections separated by `gap: 16` on the panel grid — the literal spacer divs of the old settings modal were already deleted upstream when the panel was rebuilt (`.settings-group` gap, css:905-909); no new spacers may be introduced.
- Magic numbers replaced: `.layout` becomes a `100vh` grid with `grid-template-rows: auto 1fr` per column instead of `calc(100vh - 70px)` (css:173 — the 70px is a phantom header height, css map §4); calendar min-heights derive from cell size, not the `187px`/`290px` literals (css:438,381).

### 4.3 Radii (UIR-5, S)

Exactly two working values plus circles (replaces the current six: 6/8/calc(10−2)/10/12/999px — css map §1.5):
- `--radius-sm` **6px** — everything you press or type in: buttons, icon buttons, inputs, day cells, badges, banners.
- `--radius-md` **10px** — every container: cards, table wrapper, modals (max-results/server-down drop their bespoke 12px, css:598,819), activity strip, embed wrapper.
- `--radius-full` — **only** the unseen dots, calendar dots, and the progress bar. The 999px pill treatment on badges (css:151,498,689) is dropped — badges become 6px-radius rectangles: quieter, less template-y. `8px` and `calc(var(--radius) - 2px)` disappear.

### 4.4 Shadows / elevation (UIR-6, S)

Two levels, elevation only:
- `--shadow-1`: the sticky table header's bottom edge (as a border + optional 1px shadow) and toast entry.
- `--shadow-2`: modals and toasts.

Cards, table wrapper, calendar, buttons, and the sidebar use `1px solid var(--border)` and **no shadow**. Deleted (15+ one-offs, css map §1.6): button hover lift shadow (css:84), star hover (css:132), back-to-top double shadow (css:248), day hover (css:567), scraped glow rings (css:553,559), dot halos (css:522,528), inset top highlights (css:379,449), starred-row inset ring (css:679), bespoke modal shadows (css:600,821), wireframe-open shadow (css:272).

### 4.5 Motion (UIR-7, S)

One token: `--transition-fast` (120ms ease-out), applied **only** to `background-color`, `border-color`, `color`, `opacity`. All `transition: all` (css:78,127) and the 0.1/0.12/0.15/0.2s zoo (css map §1.7) collapse to it. `transform: translateY(-1px)` hover-lifts are removed everywhere — buttons (css:83), star (css:130), calendar days (css:565). The only animations in the app: spinner rotation and the indeterminate-progress sweep, both disabled under `prefers-reduced-motion`.

### 4.6 Z-index (UIR-8, S)

From tokens: `--z-sticky` (thead), `--z-header`, `--z-modal` — **one layer for all four modals**; the current 2/5/6/20/50/9999 ladder (css map §1.8) and the 50-vs-9999 split end.

---

## 5. Global platform primitives (UIR-9, S)

Ships in the token commit:
- `:focus-visible` outline (in token block above) — the app currently has **zero** designed focus styles (UI-12).
- `color-scheme: dark|light` per theme block — natively themes checkboxes, scrollbars, and any future UA controls (graft from Liner Notes).
- Themed scrollbars: standard `scrollbar-width`/`scrollbar-color` **plus** `::-webkit-scrollbar` rules (both, so Chrome keeps parity as it migrates to standard props).
- Theme seeding from `matchMedia` (§3), stored setting wins.

---

## 6. Component specs

Each spec is keyed to current selectors with line numbers. "Delete" means the rule is removed, not overridden.

### 6.1 App shell & header (UIR-10, M)

| Current | After |
|---|---|
| `body` flex + neon radial gradients (css:30-45): hot pink/cyan in dark (css:33-34), orange/blue in light (css:41-45) | `body { background: var(--bg); }` — **flat**. Gradients deleted (UI-8 item 1). |
| `.layout` `height: calc(100vh - 70px)` (css:168-174) | `100vh` grid, `grid-template-rows: auto 1fr` per column. Verify at 900/1100/full width (known-fragile area). |
| `header` references undefined `--header-bg` (css:64) | `background: var(--header-bg)` (now defined) + existing `backdrop-filter: blur(12px)` + 1px `var(--border)` bottom border, z `var(--z-header)`. |
| `<h1>bcfeed v1.0</h1>` (html:73) | `<h1>bcfeed</h1>` 18px/600. Version string moves to the Settings modal (about line). |
| Header right cluster: `SHOW ONLY:` text (html:76), toggle buttons, ⚙️ emoji (html:79), "?" text button (html:80) | "Show only" `.micro-label`, two `.toggle-button`s, gear + help `.button-icon`s (sprite icons), gap 8. |
| `aside` fixed **360px** (css:168) | **300px default** (Stockbook graft — pays for the UI-14 clipping fix with removed chrome; the calendar at 32px cells + 4px gaps fits comfortably). |
| Single 900px breakpoint (css:827-838) leaves `overflow:hidden` + fixed height in effect | At **≤1100px**: Title column `min-width` relaxes 280→200px (fixes the 1000px Date-column clipping, UI-14). At **<900px**: body regains normal scroll — `overflow:hidden` and the fixed grid height are lifted; existing stacking behavior kept. |

### 6.2 Table (UIR-11, M)

- `.table-wrapper` (css:613-622): `var(--surface)`, 1px `var(--border)`, `var(--radius-md)`, **no shadow** (drop `var(--shadow)` at css:618); remains the scroll container (`flex:1; min-height:0` kept).
- `thead` (css:641-646): `var(--surface-inset)`, sticky, z `var(--z-sticky)`, 1px border-bottom `var(--border)`. **Consolidate the duplicate thead/th sticky declarations** (css:641-664 — currently both declare sticky/bg/z).
- `th` (css:653-664): 12px/500 `var(--text-muted)` **sentence case** ("Label / page", "Artist", "Title", "Date"); uppercase + 0.6px tracking deleted. Header content wrapped in `<span class="th-inner">` (inline-flex, gap 4px) with an inline-SVG chevron (12px) replacing the absolutely-positioned 10px text sort indicator (css:665-670) — invisible until sorted, `var(--accent)` when active; the sorted column's label lifts to `var(--text)`. Sortable `th` get `tabindex="0"` so the global focus ring has a landing spot (Stockbook graft; full `aria-sort`/key handling belongs to the UX/a11y plan).
- Column widths move from inline styles (html:90-95) to CSS col classes. Date column: `font-variant-numeric: tabular-nums`, fixed short width.
- Cells: 14px, padding **6px 10px**, line-height **1.35** (css:647-652's 1.05 is the descender-clipping smell).
- **Column ink hierarchy** (Stockbook graft — the scan path): Artist and Title cells `var(--text)`; Label/page and Date cells `var(--text-muted)`. All cells stay 14px (see §13 for the rejected 12px-Date variant).

### 6.3 Rows, dots, badges (UIR-12, M)

One channel per state (fixes UI-6's chrome-outshouts-data inversion):

- **Row background:** uniform `var(--surface)`. `--row-unseen-bg` and the `.unseen` row tint (css:675-677) are **deleted** — unseen is no longer double-encoded, and the accidental zebra striping disappears.
- **Hover:** `var(--row-hover)` declared as `tr.data-row:hover` **and** `tr.data-row.unseen:hover` — fixes the specificity accident where hovering an unseen row erased its state (css:681-683; UI-6 bonus bug). (The `.unseen` class remains on rows for JS/behavior even though it no longer paints.)
- **Expanded:** expanded row + detail row `var(--row-expanded)` with 1px top/bottom border (replaces css:684-686, 703-707).
- **Unseen dot** (replaces `.row-dot`, css:103-115): encoded **only** by the interactive dot — 8px, `var(--accent)` (no more `#ff5f5f` alarm red for the default state — UI-6), `border-radius: var(--radius-full)`, centered in a **24px hit area** (Calm Slate graft — the clean recipe for the click target). When read: `visibility: hidden` at rest, but on row hover it renders as an 8px **hollow ring** (1px `var(--text-faint)`) so the toggle-back target is discoverable — fixes the invisible-but-interactive `opacity:0` + `cursor:pointer` trap (css:113-115; UI-12).
- **Starred rows:** the gold inset ring (css:678-680) is deleted — the filled star icon is the sole indicator.
- **`.star-btn`** (css:116-145): 28px borderless icon button, 16px SVG star, stroke `var(--text-muted)`; hover: `var(--control-bg-hover)` circle behind it, **no lift, no shadow, no recolor-to-accent**; `.starred`: filled `var(--star)`.
- **`.badge`** (replaces `.cached-badge`, css:146-161): text **"Saved"** (sentence case, not CACHED), 12px/500, padding 1px 6px, `var(--radius-sm)` (not a pill), bg `var(--accent-tint)`, text `var(--accent)`, 1px `var(--accent-border)`. **Hidden unless the "show cached" setting (html:134 — since the merge a visible Appearance setting, default *on*) is on; the default returns to off** — internal plumbing leaves the default view (UI-13; light-mode 3.65:1 fixed by tokens regardless).
- **Detail panel:** `.detail-desc` (css:721-733) drops its dashed border (the wireframe idiom's last outpost) for 1px `var(--border)` + `var(--surface-inset)`; `.embed-wrapper` (css:793-800) drops its shadow for the standard card border.
- Row template strings in `renderTable` (js:759-772) get the new class names — **and the escaping fix from JS-1 lands in the same templates** (same lines, one pass).

### 6.4 Calendar (UIR-13, L)

The `<details>` container becomes a standard `.panel-card` (surface, 1px `var(--border)`, `var(--radius-md)`, padding 12):
- **Delete** `.wireframe-panel` dashed border (css:261-273), `.wireframe-body` 45° hatch (css:349-361), and rename the `wireframe-*` classes (UI-7 — the single strongest "generated/unfinished" signal on screen).
- The disabled-calendar overlay (css:274-305, a second differently-angled hatch) becomes a plain semi-opaque `var(--bg)` veil at 60% + a centered 12px muted hint. No hatch.
- **Nav:** two 24px `.button-icon`s (SVG chevrons replace `‹`/`›` text glyphs); month label 12px/600 `var(--text)` with `min-width: 9ch` + `tabular-nums` (Liner Notes graft — replaces the fixed 110px at css:415); Today = `.button-sm`.
- **Day cells** (css:484-584, JS-built at js:1244-1279): 32px min, `var(--radius-sm)`, **transparent at rest** (no white-alpha fill), `tabindex="0"` (Stockbook graft — focusable ground for the UX plan's grid semantics). One visual channel per state:
  1. **Selected range:** bg `var(--accent-tint)`; endpoints `.range-edge`: `var(--accent-tint-strong)` + 1px `var(--accent-border)`. (Replaces the in-range gradient css:506-509 and its verbatim duplicate on `.selected` css:580-584 — endpoints become distinguishable, fixing the UI-5 collapse.)
  2. **Coverage:** fetched days = day number in `var(--text)`; unfetched days = `var(--text-faint)` number — gaps read as literally faded out, making them findable at a glance (UX-13). The white-on-blue date-label pill that computed to 1.26:1 in light (css:502-505; UI-2) is deleted.
  3. **Unseen releases:** a single 4px `var(--accent)` dot under the number — replaces the pill + red-dot stack (css:510-538) and its halos (UI-5/UI-2).
  4. **Today:** 1px inset ring `var(--border-strong)`.
  5. **Hover:** `var(--control-bg)` fill. No lift, no shadow (deletes css:564-568).
  6. **Disabled/future:** `var(--text-faint)`, no hover, `cursor: default` (replaces opacity+dashed at css:573-579).
- **Delete dead rules:** all `.calendar-day.scraped*` glow-ring variants (css:539-561) — confirmed dead code (UI-5 correction: js:1264 applies `unseen-day`, never `scraped`).
- **Class changes in `renderCalendar` (js:1244-1279):** two distinct channels, not one rename:
  - The scraped-driven class (js:1263-1265; misnamed `unseen-day`, renamed `populated-day` by CQ-37/WP-19 — it marks *fetched* days, not unseen ones) maps onto the coverage channel: drop it in favor of the new `unfetched` class on unfetched days (fetched days are the unmarked default). Keeping `populated-day` on fetched days is acceptable but redundant.
  - `has-unseen` is a **new** class, applied from the existing `hasUnseen` computation (js:1257-1262), driving the 4px accent dot. It is *not* a rename of `unseen-day`/`populated-day` — that would attach unseen semantics to every fetched day.
  - Add `range-edge` for range endpoints.
- **Legend** (inline-styled pills at html:38-45) rebuilt with real classes (`.legend-row`, `.legend-dot`) matching the new encodings; labels sentence-cased and de-jargoned ("Checked", "Has new releases" — final copy owned by the UX plan).
- Cells get `:focus-visible` support so the keyboard work from the UX/a11y plan lands on styled ground.

### 6.5 Buttons (UIR-14, M)

Four variants replace the base class, the remaining inline-style size hacks (html:46,52,54-55), **and** the merge-appended `.button.primary`/`.button.danger` pair (css:1035-1052 — parallel variants with literal colors that must fold into the token-built variants below; §6.12):

- **`.button`** (replaces css:70-92): `var(--control-bg)`, 1px `var(--control-border)`, `var(--radius-sm)`, 13px/500, padding 6px 12px; hover `var(--control-bg-hover)`; active slightly darker. **Disabled: never opacity** (Liner Notes graft replacing the base direction's `opacity: 0.55` — and the current `opacity:.45 + grayscale` at css:86-92): label goes `var(--text-faint)` on normal `--control-bg`, `cursor: not-allowed` — the label stays readable. Paired with the **`.hint-text` primitive**: a 12px `var(--text-muted)` line under the button carrying a plain-language reason ("These dates were already checked"), satisfying the brief's disabled-state-explanations requirement.
- **`.button-primary`** (Populate; also absorbs the appended `.button.primary` used by `Load credentials file`, `Connect & load folders`, `Save IMAP Configuration`): solid `var(--accent)` + `var(--on-accent)` text — passes AA in both themes **by token construction** (fixes UI-1's 1.34:1/1.06:1 light-mode disaster at html:52).
- **`.button-danger`** (Clear cache, Clear credentials, Revoke): transparent bg, `var(--danger-text)` + 1px `var(--danger-border)`; hover `var(--danger-tint)`. Replaces the merge's `.button.danger` and its `#ff6b6b` family (css:1044-1052) — the old `#b83a3a` inline treatment already became that class upstream (html:164).
- **`.button-sm`**: padding 4px 10px, 12px (replaces the inline `6px 10px / 12px` shrinks at html:54-55).
- **`.button-icon`**: 28–32px square, centered 16px sprite SVG (gear, help, close, nav chevrons, status chevron, back-to-top).
- `.toggle-button.toggle-active` (css:93-97) keeps its correct-by-construction accent fill + `var(--on-accent)` text — it was the only control that already themed correctly.
- No `translateY`, no hover shadows, anywhere.

### 6.6 Inputs & filter list (UIR-15, S)

- **All checkboxes:** `accent-color: var(--accent)` — kills the `#ff6b6b` show-only accent (css:223) and unifies the settings checkboxes (html:130,134). 16px, aligned with 8px gap to 13px labels.
- **Filter list** (js:486-530): the two unlabeled checkbox columns get 12px SVG column glyphs (eye = show, funnel = only) with `title` tooltips — an interim legibility fix until the UX plan reworks the control itself (UX-8). Counts right-aligned, 12px `var(--text-faint)` `tabular-nums`.
- Hidden text inputs (html:58-61, plus the hidden creds file input html:161) stay hidden. The **`.input`** spec is no longer speculative: the merge shipped `.form-input`/`.form-select`/`.form-label` (css:912-955) for the IMAP panel — these are structurally right and are *retained*, re-expressed on tokens (`var(--control-bg)`/`var(--control-border)`, `var(--radius-sm)`, padding 6px 10px, 13px; the white-alpha fill and hardcoded cyan focus ring go — §6.12).

### 6.7 Modals (UIR-16, M)

**One system for all four** (settings css:744-762 *plus* its merge-appended second generation css:841-884; credentials-needed/load-credentials css:763-783, markup now at html:274-301; max-results css:585-605; server-down css:806-826 — ending what is now a *three*-generation split of UI-10):

- `.modal-backdrop`: fixed inset-0, `var(--backdrop)`, z `var(--z-modal)`, flex-center. One backdrop opacity, one z-layer.
- `.modal`: `var(--surface-raised)`, `var(--radius-md)`, `var(--shadow-2)`, 1px `var(--border)`, padding 20, max-width 400, `display: grid; gap: 16`.
- `.modal-header`: 16px/600 title + `.button-icon` close (sprite ✕, replacing the text glyph).
- `.modal-body`: 13px/1.45. `.modal-actions`: flex-end, gap 8. `.modal-section-title`: `.micro-label`.
- **Settings panel (html:116-267, rebuilt in the merge)**: the new structure — `.settings-header`, `.settings-content-scroll`, `.settings-section`/`.settings-section-title`/`.settings-group`, per-provider `.provider-panel`s — is **kept**; its section order already matches the UX plan. This spec re-expresses its rules on tokens and removes the ~25 inline layout styles inside the IMAP panel (html:175-216: flex columns, gaps, min-widths become classes). `.settings-section-title` becomes `.micro-label` (drops 11px/uppercase/tracking, css:897-903). Destructive actions grouped **last** under a "Danger" section title, using `.button-danger`. An about line ("bcfeed v1.0") gives the version its new home.
- Max-results and server-down adopt the shared radius/backdrop/z (their content/dismissability fixes — JS-2, UX-15 — belong to the UX plan; this spec only normalizes their chrome).

### 6.8 Links (UIR-17, S)

- Links inside modals and prose: `var(--accent)` at rest **+ underline** — "Show setup instructions" stops masquerading as plain text (fixes UI-15; css:46-60).
- Table release links: text-colored at rest (content stays quiet), accent + underline on hover (js:765-767 template).

### 6.9 Empty states (UIR-18, S)

`.empty-state`: centered column, max-width 360px, gap 8 — 24px sprite icon in `var(--text-faint)`, 14px/600 title, 13px `var(--text-muted)` body, optional `.button-primary` CTA slot. Two variants: **first-run** (CTA present) and **no-match** (body text only). Replaces the single wrong-for-both string at html:100; final copy comes from the UX plan (UX-5).

### 6.10 Back-to-top (UIR-19, S)

`.button-sm` with arrow-up sprite icon, standard control colors — the `#64a8ff` one-off fill and double shadow (css:238-253) are deleted. Sticky at the bottom of the sidebar with a `var(--border)` top hairline.

### 6.11 Icon sprite (UIR-20, S)

One inline-SVG set: 16px viewBox, 1.5px stroke, `currentColor`, Lucide-style geometry, embedded as a hidden `<svg><symbol>` sprite in `dashboard.html`, referenced via `<use>` (one definition, consistent stroke width — Calm Slate graft over per-template SVG strings).

**Set (15):** gear, help-circle, x, chevron-down, chevron-left, chevron-right, star, check-circle, alert-circle, alert-triangle, arrow-up, external-link, loader (spinner), eye, funnel.

Retires all five current icon systems (UI-8 item 3): the ⚙️ emoji (html:79), the "?" text button (html:80), the `▾`/`‹`/`›` glyphs (html:22,31,33,106), ✕, and the merge's inline info-circle SVG in the IMAP panel (html:210 — 13px/2px-stroke, a fifth geometry; becomes the sprite's `help-circle` or a new `info` symbol); the inline star SVG in `dashboard.js:763` joins the sprite.

### 6.12 Settings & provider surface normalization (UIR-30, M — new at e363bf4)

The IMAP-provider merge appended a third styling generation to `dashboard.css` (css:837-1058, "Enhanced Settings Panel Styles") and rebuilt the settings markup (html:116-267). Structurally it is the best surface in the app — labeled sections, real form primitives, inline status text — and this spec **keeps its architecture**. But it was written outside the token discipline and must be normalized in the token/WP-20 pass, not grandfathered:

**Conflicts with the token spec (must change):**

1. **Rule shadowing:** the appended `.settings-panel` (css:841) silently overrides the original `.settings-panel` (css:753) by source order; `.settings-header .button` re-styles `.button` locally. Delete the superseded first-generation rules (css:753-783) and express the panel once.
2. **Color literals:** white-alpha hover fill `rgba(255,255,255,0.05)` (css:876 — invisible-on-white in light theme, the exact UI-4 failure class); hardcoded focus ring `rgba(82,208,255,0.15)` box-shadow on `.form-input:focus` (css:940 — pins the *dark* accent in both themes and duplicates the global `:focus-visible` mechanism); the `#ff6b6b` danger family (css:1045-1052) re-introducing one of UI-11's four reds. All become `var(--control-bg-hover)`, the global focus ring, and `--danger-*` tokens respectively.
3. **Parallel button variants:** `.button.primary` / `.button.danger` (css:1035-1052) duplicate UIR-14's `.button-primary` / `.button-danger` with literal colors. Fold them in (rename in html/js templates; the classes are toggled from the provider controller, js:1702-2093).
4. **Typography violations:** `.settings-section-title` is 11px uppercase with 0.8px letter-spacing (css:897-903) — below the 12px floor, both banned mechanisms. Becomes `.micro-label`. Title-case labels (`Folder To Scan`, `Save IMAP Configuration`, `Manual Folder Name`) become sentence case (copy owned by the UX plan's provider-settings map).
5. **Inline-style sprawl, second wave:** ~25 new `style=""` attributes lay out the IMAP panel's two-column form (html:175-216) — column/gap/min-width classes replace them (`.form-row`, `.form-col`).
6. **Fifth icon system:** the inline info-circle SVG (html:210, 13px/2px stroke) joins the UIR-20 sprite; the app-password hint it gates becomes visible `.hint-text` (UX plan call).

**Adopted from the new surface (kept, re-expressed on tokens):**

- `.form-input` / `.form-select` / `.form-label` (css:912-955) — this *is* UIR-15's `.input` spec, shipped early; keep the names, swap literals for `var(--control-bg)`/`var(--control-border)`/`var(--radius-sm)`.
- `.help-text` (css:957-962) — merges with UIR-14's `.hint-text` primitive (one name; `.hint-text` wins as it also serves disabled-button explanations).
- `.settings-section` / `.settings-group` layout and the scrollable `.settings-content-scroll` container.
- Inline status text placed next to the button that caused it (`.imap-status-text`) — the UXP-2 locus-of-action pattern, kept and given semantic `--success-text`/`--danger-text` classes instead of JS-set colors.

**Acceptance criteria:** the §11 greps return zero for the appended block too (no literals outside the token block, no `.button.primary`/`.button.danger`, no uppercase/letter-spacing, no sub-12px); the settings panel renders correctly in **both** themes (the current white-alpha hover and dark-pinned focus ring make light mode fail today); exactly one generation of `.settings-panel` rules exists.

---

## 7. Status/log replacement (the feedback system)

The raw 200px log box (`.calendar-log`, css:440-459 — misnamed, it is the status log under the table) is replaced by three primitives **in the same screen position**. The IA stays put; only the primitive changes. This directly retires UI-9 (fake-link blue log prose, 2.45:1 in light, hollow 200px box on first run) and gives UX-7's five jobs proper homes.

### 7.1 Activity strip (UIR-22, M)

`.activity-strip` (replaces `.calendar-log`): a single-row surface card under the table, padding 8px 12px.

- **Left:** a 16px state icon — nothing when idle, spinning loader while working, check-circle `var(--success-text)` on completion, alert-circle `var(--danger-text)` on failure — plus **one 13px status line in plain language**.
- **Right cluster:** the release count (merged in from the now-deleted bottom status bar, html:111-113; 12px `var(--text-faint)` `tabular-nums`) + a keyboard hint (`↑↓ move · s star · u unread`, 12px `var(--text-faint)` — Liner Notes graft, surfaces the secret shortcuts of UX-14) + a "Details" `.button-icon` chevron.
- **Progress:** while a populate/preload runs, a 4px progress bar (track `var(--surface-inset)`, **fill `var(--text-muted)` — not accent** (Stockbook graft: progress is not new music; accent stays a learnable signal), `var(--radius-full)`, 120–160px) sits inline before the chevron. **Determinate** once the typed SSE events land (ARCH-4 — the backend already computes done/total); **indeterminate sweep** until then.
- **Idle state:** one quiet line — `Showing 49 releases · Jun 1 – Jun 30 · 12 new` — where **only the "12 new" fragment renders in `var(--accent)`** (Stockbook graft #9). The empty hollow box from the first-run screenshots disappears, and the table reclaims ~160px of vertical space.
- **One home for the count:** the strip's right cluster is the release count's *only* home; the bottom status bar (html:111-113) is deleted. This structurally kills the JS-4 label-erasing bug (there is no second writer left to blank it).

### 7.2 Details disclosure (UIR-23, S)

The chevron expands `.activity-detail`: auto-height up to `max-height: 200px`, `var(--surface-inset)`, `var(--font-mono)` 12px `var(--text-muted)` (a log finally set in monospace), error lines `.log-error` in `var(--danger-text)`, warnings `.log-warn` in `var(--warn-text)`. **Collapsed by default, auto-expanded on error** (Calm Slate graft). This preserves the full log for the curious without making blue fake-link prose the primary channel (UI-9); the hardcoded `#64a8ff` at js:568,573 becomes these classes.

### 7.3 Banners + toasts (UIR-24, M)

- **Failures:** a persistent `.banner-danger` above the table — `var(--danger-tint)` bg, 1px `var(--danger-border)`, alert icon in `var(--danger-text)`, 13px text in `var(--text)`, optional retry `.button-sm`, dismiss `.button-icon`. Replaces both the inline-styled `#error-state` (html:85 — 1.29:1 in light, UI-3) and the `alert()` calls (UX-3).
- **Completions:** a `.toast` — fixed bottom-right, `var(--surface-raised)`, 1px `var(--border)`, `var(--shadow-2)`, `var(--radius-md)`, icon + 13px text, auto-dismiss ~6s, 120ms fade/slide (reduced-motion: fade only) — saying what changed ("Added 12 releases for Jun 1 – Jun 30"). **Toast body text is neutral `var(--text)`; the icon carries the success color; no accent in the body** (Stockbook graft).

### 7.4 Phasing note

The strip, mono detail well, semantic log-line classes, banner, and collapsed-when-idle behavior are pure CSS + small JS class changes — they **ship with the restyle**. The determinate progress bar and the "N new releases" toast depend on the typed SSE protocol and refetch-instead-of-reload (ARCH-4 / JS-10, owned by the architecture/JS plans) and slot in when that lands; the indeterminate bar is the interim.

---

## 8. Microcopy casing rules (UIR-25, S)

- **Sentence case everywhere.** No `text-transform: uppercase` (delete css:156,189,347,420,656), no letter-spacing, no bold-as-emphasis in labels.
- Hardcoded ALL-CAPS HTML strings are edited in place:
  - `FILTER BY DATE` (html:15) → **Filter by date**
  - `SHIFT-CLICK TO SELECT RANGE` (html:39) → **Shift-click to select a range**
  - `SELECTED DATE RANGE:` (html:51) → **Selected dates**
  - `SHOW ONLY:` (html:76) → **Show only**
- Table headers sentence case: **Label / page, Artist, Title, Date**.
- `CACHED` → **Saved** (and hidden by default, §6.3).
- All micro-labels use the single `.micro-label` class; no per-instance styling.
- Vocabulary replacement (populate/preload/cache/token → plain language) is specified by the UX plan (UX-6); this spec only fixes **casing and the label mechanism** so those copy edits land in one place.

---

## 9. AI-smell inventory: exact before → after

Every audited smell item, with its landing spec:

| # | Smell (audit ref) | Before (location) | After | Spec |
|---|---|---|---|---|
| 1 | Neon ambient gradients (UI-8) | body radial gradients css:33-34, 41-45 | Flat `var(--bg)`; rules deleted | UIR-10 |
| 2 | Static glow rings (UI-8) | css:528 (dot halo), 553/559 (scraped-selected) | Deleted; dot = flat 4px accent; scraped rules were dead code | UIR-13 |
| 3 | Wireframe chrome in production (UI-7) | `.wireframe-panel` dashes css:261-273, `.wireframe-body` hatch css:349-361, disabled hatch css:282-288, `.detail-desc` dashes css:726 | Standard `.panel-card`: surface + 1px `var(--border)` + `var(--radius-md)`; veil replaces hatch; classes renamed | UIR-13, UIR-12 |
| 4 | Emoji/text/glyph icon mix (UI-8) | ⚙️ html:79, "?" html:80, ▾‹› html:22,31,33,106, inline star js:763, info-circle html:210 (new) | One 15-symbol SVG sprite, `currentColor`, 1.5px stroke | UIR-20 |
| 5 | ALL-CAPS microcopy (UI-8) | CSS uppercase css:156,189,347,420,656 + literal caps html:15,39,51,76 | `.micro-label`, sentence case; strings edited | UIR-25 |
| 6 | Version in H1 (UI-8) | `bcfeed v1.0` html:73 | H1 = "bcfeed"; version → Settings about line | UIR-10, UIR-16 |
| 7 | Hover-lift on everything (UI-8) | `translateY(-1px)` css:83,130,565 | Removed; color/bg transitions only | UIR-7 |
| 8 | 999px pill chrome (UI-8) | badge css:151, date-label css:498, `.pill` css:689, legend html:41,43 | 6px-radius badges; pills/date-labels deleted | UIR-5, UIR-12, UIR-13 |
| 9 | Four blues (UI-11) | `--accent` cyan, `#64a8ff` family css:244,503,526-528 + html:52 + js:568,573, light `#1f7aff`, `rgba(64,150,210,…)` css:558-559 | One `--accent` family per theme | UIR-2 |
| 10 | Four reds (UI-11) | `#ff5f5f` css:108,534; `#ff6b6b` css:223; `#b83a3a` html:85,144; `#ffc5c5` html:85 | `--danger-*` semantic tokens; unseen state stops being red entirely | UIR-2, UIR-12 |
| 11 | Inline-style sprawl (UI-10) | ~60 `style=""` attrs across html:13-301 (the merge added ~25 in the IMAP panel, html:175-216) | All moved to classes | UIR-16, UIR-29, UIR-30 |
| 12 | Raw log box as primary feedback (UI-9, UX-7) | `.calendar-log` fixed 200px css:440-459; `#64a8ff` prose js:568,573 | Activity strip + mono details + banners/toasts | UIR-22/23/24 |
| 13 | 2%-alpha hover + specificity accident (UI-6) | css:681-683 | `var(--row-hover)` on both `:hover` selectors | UIR-12 |
| 14 | Undefined `--header-bg` (css map §5.1) | css:64 | Defined per theme | UIR-2, UIR-10 |
| 15 | Five letter-spacing values | css:80,101,157,187,196,341,346,392,419,430,611,656,659,701 | Zero letter-spacing | UIR-3 |
| 16 | 10px type | css:154,429,669 | 12px minimum | UIR-3 |
| 17 | `line-height: 1.05` descender clipping | css:211,651 | 1.35 in table, 1.45 elsewhere | UIR-3 |
| 18 | CACHED badge shouting plumbing (UI-13) | css:146-161, js:231-232,770 | "Saved" `.badge`, hidden by default | UIR-12 |
| 19 | Alarm-red unseen dots on 90% of rows (UI-6) | `.row-dot` `#ff5f5f` css:103-115 | 8px accent dot, 24px hit area, hollow ring when read | UIR-12 |
| 20 | Dead CSS | `.pill` css:687-702, `.inline-link` css:52-56, `.calendar-row` css:367-373, `.calendar-day.scraped*` css:539-561, duplicate `.selected` gradient css:580-584 | Deleted (grep checklist §11) | UIR-28 |
| 21 | Two modal generations (UI-10) | z-50/0.45 backdrop css:744-783 vs z-9999/0.75/12px css:585-605,806-826 | One `.modal` system, one backdrop, one z | UIR-16 |
| 22 | OS-default scrollbars on slate | none in css (map §3.11) | Themed, both standard + webkit | UIR-9 |
| 23 | Zero focus styles (UI-12) | none in css | Global `:focus-visible` + tabindex landing spots | UIR-9, UIR-27 |
| 24 | Phantom "Inter" font | css:37 | System stack `--font-ui` | UIR-3 |
| 25 | White-on-blue calendar pills, 1.26:1 light (UI-2) | css:502-505 | Coverage encoded by number ink (`--text` vs `--text-faint`) | UIR-13 |
| 26 | Unreadable primary button in light, 1.34:1 (UI-1) | html:52 inline | `.button-primary` solid accent + `--on-accent` | UIR-14 |
| 27 | Invisible error bar in light, 1.29:1 (UI-3) | html:85 inline | `.banner-danger` semantic tokens | UIR-24 |
| 28 | Links with no resting affordance (UI-15) | css:46-60 | Accent + underline at rest (prose/modals) | UIR-17 |
| 29 | Date column clipping at 1000px (UI-14) | html:90-95 + css:827-838 | 300px sidebar + 1100px breakpoint + tabular-nums fixed Date width | UIR-10, UIR-11 |

---

## 10. Accessibility requirements (UIR-27, M — spread across components)

Hard requirements for this restyle (deeper semantics — roles, focus traps, aria-live — are the UX/a11y plan's scope; this spec guarantees the styled ground they land on):

1. **Focus:** global `:focus-visible` ring (2px `var(--focus-ring)`, 2px offset) on every interactive element. Calendar day cells and sortable `th` get `tabindex="0"` so the ring has targets (they are currently unreachable by keyboard — UI-12 correction).
2. **Contrast:** all body text ≥ 4.5:1 in **both** themes, by token construction. The five audited light-mode failures must each be **re-measured after implementation**: UI-1 (primary button), UI-2 (calendar day numbers), UI-3 (error feedback), UI-9 (log text), UI-13 (badge).
3. **Hit targets:** the 8px unseen dot sits in a 24px hit area; icon buttons are 28–32px; day cells 32px minimum.
4. **No invisible interactivity:** the read-state dot uses `visibility: hidden` at rest with a hover-revealed hollow ring — no `opacity:0` + `cursor:pointer` targets remain.
5. **Disabled ≠ illegible:** disabled buttons keep a readable `var(--text-faint)` label (never opacity) and carry a `.hint-text` explanation.
6. **Motion:** the two animations (spinner, indeterminate sweep) and the toast slide are disabled/reduced under `prefers-reduced-motion`.
7. **Text size floor:** 12px; no letter-spacing tricks that hurt legibility at small sizes.
8. **Native controls:** `color-scheme` set per theme so checkboxes/scrollbars render correctly without custom hacks.

---

## 11. Deletion checklist (UIR-28, S — run as a grep pass before and after)

Grep-enforceable removals; each should return zero hits when done:

```
# color literals outside the token block
grep -nE '#[0-9a-fA-F]{3,8}|rgba?\(' dashboard.html dashboard.js
# (dashboard.css: only inside the token block)

# dead + deleted rules
grep -n '\.pill\b|\.inline-link|\.calendar-row|\.scraped' dashboard.css
grep -n 'wireframe' dashboard.css dashboard.html
grep -n 'letter-spacing|text-transform' dashboard.css
grep -n 'translateY' dashboard.css
grep -n 'transition: all' dashboard.css
grep -n 'style="' dashboard.html          # target: 0 (from ~60 at e363bf4)
grep -n '64a8ff\|ff5f5f\|ff6b6b\|b83a3a\|ffc5c5' dashboard.css dashboard.html dashboard.js
# note: #ff6b6b now also hits the appended settings block (css:1045-1052) — §6.12
grep -n 'unseen-day\|populated-day' dashboard.js  # gone: coverage uses the new `unfetched` class (or `populated-day` if kept); `has-unseen` is a separate NEW class from the hasUnseen boolean, not a rename
grep -n 'button primary\|button danger' dashboard.html dashboard.js   # folded into .button-primary/.button-danger (§6.12)
```

Specific deletions: body gradients (css:33-34,41-45); all `rgba(82,208,255,…)` / `rgba(100,168,255,…)` / `rgba(255,255,255,…)` literals (including css:876,940 in the appended block); glow box-shadows (css:522,528,553,559); the 15+ one-off shadows (§4.4); `.pill` (css:687-702), `.inline-link` (css:52-56), `.calendar-row` (css:367-373), `.calendar-day.scraped*` (css:539-561); the duplicate `.selected` gradient (css:580-584); duplicate thead/th sticky declarations (css:641-664); the superseded first-generation `.settings-panel` rules (css:753-783 — overridden wholesale by css:841+; §6.12); bottom status bar (html:111-113).

---

## 12. HTML/JS delta summary (UIR-29, M)

- **dashboard.html:** ~60 inline `style` attributes removed (~25 of them in the merge-added IMAP panel, html:175-216); icon sprite `<svg><symbol>` block added; ALL-CAPS strings sentence-cased; version moved out of H1; legend rebuilt with classes; bottom status bar removed; log box replaced by activity-strip markup.
- **dashboard.js:** ~10 class renames/additions in template strings — row dot + badge classes in `renderTable` (js:759-772, where the JS-1 escaping fix co-lands); log-line classes replacing the hardcoded `#64a8ff` (js:568,573); calendar state classes in `renderCalendar` (js:1244-1279): the scraped-driven class (`unseen-day`, `populated-day` after WP-19/CQ-37) folds into the coverage channel (new `unfetched` on unfetched days, or keep `populated-day`), new `has-unseen` applied from the existing `hasUnseen` boolean (js:1257-1262) for the accent dot — not a rename — plus new `range-edge`; activity-strip markup; theme seeding via `matchMedia` (3 lines). The provider/IMAP controller (js:1702-2093) needs no logic changes — only the class names it toggles (`.button.primary` → `.button-primary`, `.help-text` → `.hint-text`).
- **Because the app is a full-re-render model, every visual class lives in JS template strings** — a grep pass over `dashboard.js` for class literals must precede any rename (missing one silently unstyles a state).
- No framework, no build step, no new dependencies. All element IDs and the `body.theme-light` mechanism unchanged (zero behavior risk).

---

## 13. Judge grafts: adopted and rejected

**Adopted (folded into the specs above):**

| Graft | Source | Landed in |
|---|---|---|
| Fully-specified activity strip anatomy (icon + status line + inline progress + details chevron, auto-expand on error) | Calm Slate | UIR-22/23 |
| `.hint-text` under disabled buttons explaining *why* | Calm Slate | UIR-14 |
| `--warn`/`--success` semantic tokens | Calm Slate | UIR-2 |
| 24px hit area around the unseen dot | Calm Slate | UIR-12 |
| Dead-rule deletion list as grep checklist | Calm Slate | UIR-28 |
| SVG `<symbol>` sprite + `<use>` | Calm Slate | UIR-20 |
| Two-commit phasing inside PR 1 (tokens+components, then de-inlining) | Calm Slate | §14 |
| Sub-900px lifts `overflow:hidden` | Calm Slate | UIR-10 |
| `color-scheme` per theme block | Liner Notes | UIR-2/9 |
| Standard `scrollbar-width`/`scrollbar-color` alongside webkit | Liner Notes | UIR-9 |
| Theme seeding from `matchMedia`, stored setting wins | Liner Notes | §3 |
| Legible disabled labels (`--text-faint` on normal bg) instead of `opacity: 0.55` | Liner Notes | UIR-14 |
| Month label `min-width: 9ch` + tabular-nums (not fixed 110px) | Liner Notes | UIR-13 |
| Keyboard-shortcut hint in the activity strip right cluster | Liner Notes | UIR-22 |
| "Accent means new music" — accent off the progress fill and toast body | Stockbook | UIR-22/24 |
| Release count gets exactly one home (kills JS-4 structurally) | Stockbook | UIR-22 |
| Two-PR phasing: tokens+chrome (pure visual) then status/log replacement | Stockbook | §14 |
| `tabindex` on calendar days and sort headers | Stockbook | UIR-11/13/27 |
| Disabled states never use opacity + sentence-case explanations | Stockbook | UIR-14 |
| Only the "N new" fragment in accent in the idle status line | Stockbook | UIR-22 |
| Column ink hierarchy (Artist/Title loud, Label/page & Date muted) | Stockbook | UIR-11 |
| Sidebar 300px as the default (not just under 1100px) | Stockbook | UIR-10 |

**Rejected (with reason):**

| Graft | Source | Why rejected |
|---|---|---|
| Date column at 12px | Stockbook | Would introduce a second size inside one table row; the scan-path goal is met with `--text-muted` + tabular-nums at 14px. Partial adoption. |
| Release count right-aligned in the table header region | Stockbook | The "one home" principle is what matters; the activity strip's right cluster is that home and keeps the table header purely for columns. |
| `opacity: 0.55` disabled buttons | Calm Slate (base) | Superseded by Liner Notes' legible-label treatment — pairs better with `.hint-text`. |
| Sidebar narrowing only at the 1100px breakpoint | Calm Slate (base) | Superseded by Stockbook's 300px-default — fixes UI-14 at every width, not just below 1100px. |

---

## 14. Phasing, effort, risk

**Estimate: 3.5–4.5 developer-days** for the core normalization (raised half a day for the settings/provider surface, UIR-30), **+1–2 days later** for the determinate-progress/toast phase that depends on the typed SSE work (ARCH-4).

**Two PRs (Stockbook graft):**

- **PR 1 — tokens + chrome (pure visual, zero behavior).** Two commits: (a) token block + component classes in `dashboard.css`; (b) HTML de-inlining + sprite + string edits. Breakdown: `dashboard.css` effectively rewritten (1,058 → ~750–950 disciplined lines; cheaper than patching because a large share of the file is deletions — gradients, hatches, glow rings, dead rules, duplicate sticky declarations, plus the superseded first-generation settings rules and the appended block's normalization per UIR-30/§6.12) ≈ 2 days; `dashboard.html` de-inlining (~60 inline styles incl. the IMAP panel) ≈ 0.5–1 day; `dashboard.js` touch-ups (template classes, log-line classes, calendar renames, activity-strip markup, provider-controller class renames) ≈ 0.5–1 day; two-theme screenshot QA against the existing shot inventory (`docs/current-state/screenshots/`, including the post-merge settings/provider shots `20-postmerge-*`/`22b-postmerge-*`/`23-postmerge-*`) ≈ 0.5 day.
- **PR 2 — status/log replacement completion**, sequenced with the JS-3/JS-10 single-owner populate-state fix and the ARCH-4 typed SSE protocol: determinate progress bar, "Added N releases" toast, refetch-instead-of-reload. Indeterminate bar + banner ship in PR 1 as the interim.

**Risk (low-to-medium), concentrated in three places:**
1. **JS-injected class names** — full-re-render means every visual class lives in template strings; grep `dashboard.js` for class literals before any rename.
2. **The layout-height fix** (replacing `calc(100vh - 70px)` with an auto/1fr grid) touches the app-shell scroll model — verify at 900px / 1100px / full width; current breakpoint behavior is already fragile.
3. **Light theme is being built essentially for the first time** — tokens make it correct by default, but re-measure the five audited contrast failures (UI-1/2/3/9/13) after implementation.

**Mitigations:** keep all element IDs and the `body.theme-light` mechanism unchanged; land tokens+components and de-inlining as separate commits; reuse the existing screenshot set as a before/after harness.

---

## 15. Explicit non-goals

This spec deliberately does **not**:

- Rename product vocabulary (populate → "Check for releases", preload → "Load players", etc.) or rewrite empty-state/onboarding copy — that is the UX plan's scope (UX-5, UX-6); this spec provides the primitives (`.micro-label`, `.hint-text`, `.empty-state`, banner/toast) the copy lands in.
- Add dialog semantics, focus traps, Escape handling, aria-live, or full keyboard grid navigation — the UX/a11y plan (JS-7); this spec guarantees focusable, focus-styled ground.
- Fix behavioral bugs (JS-2 server-down latch, JS-3/JS-10 populate ownership, JS-5 filter resets, JS-9 modal cancel) — those plans reference the classes defined here.
- Change the SSE protocol or any backend code (ARCH-4 is a dependency of PR 2, not part of it).
- Introduce a framework, build step, web fonts, custom display typefaces, decorative theming, or any new dependency.
- Redesign the information architecture — the sidebar/table/strip layout, the calendar-as-coverage-map, star-triggers-preload, and the dense sortable table are preserved exactly.
- Ship new features (re-check range, preload cancel, first-run wizard) — UX plan scope.
