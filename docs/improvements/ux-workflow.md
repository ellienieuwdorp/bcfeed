# UX / Workflow Improvement Plan

Scope: user-facing language, onboarding, the populate/preload/cache core loop, flow dead ends, and error states. No code changes in this document — it is the specification for a later implementation pass.

Evidence base: `docs/audit` findings (UX-1..15, UI-*, JS-*, ARCH-*, PY-*, SEC-*, PERF-* as cited per item), the product map, and the screenshot set in `docs/current-state/screenshots/`.

**Revalidated against `e363bf4`** (2026-07-05, IMAP-provider merge). All string locations below were re-verified against the current files: `dashboard.js` grew 1,725 → 2,113 lines (existing refs shifted +3 past line 60; a provider/IMAP settings controller was appended at js:1702-2093), the settings panel in `dashboard.html` was rebuilt (html:116-263), the credential modals moved (html:274-301), pipeline log lines are now provider-parameterized (`{Gmail|IMAP}`), and `gmail.py` refs became `gmail_client.py`. New IMAP/provider strings are mapped in the new *Email provider settings* subsection of UXP-1. UXP-5 is re-scoped (the settings redesign landed upstream); UXP-3/4 now describe both provider onboarding paths.

Product brief constraints honored throughout:

- **Preserve:** dense sortable table, calendar-as-coverage-map, star-triggers-preload, keyboard shortcuts, local-first privacy, the honest progress information in the SSE stream (the information is right; the primitive is wrong).
- **Remove:** technical vocabulary (populate, preload, cache, scraped, token, credentials, embed, proxy, parse, query), the raw log box as primary feedback channel, alert()-based errors, dead-end disabled states.
- **Tone target:** a calm, reliable local utility a semi-technical music fan can use without reading docs.

Sizing: S = under half a day, M = half a day to two days, L = more than two days. Sizes are for the UX-visible work; backend enablers are flagged as dependencies with their own audit IDs.

UX first principles referenced: *visibility of system status*, *match between system and the user's mental model*, *recognition over recall*, *user control and freedom*, *error prevention*, *help users recognize, diagnose, and recover from errors*, *consistency*.

---

## 1. De-technicalization

### UXP-1 — Complete wording map: replace every implementation term with the user's language

**Rationale.** The single root problem identified in the UX audit (UX-6): every load-bearing noun on screen is an engineering term. The user's mental model is "show me new music from artists I follow on Bandcamp"; the UI forces them to reason about a scrape ledger, two kinds of prefetching, an email quota, and OAuth plumbing. *Match between system and the real world* says the interface should speak the user's language; *recognition over recall* says a label should tell the user what happens, not what subsystem runs. This is a rename pass — behavior unchanged — and it is the cheapest high-leverage item in the whole plan.

**Vocabulary rules (apply to all UI copy, tooltips, log lines, and docs):**

| Banned term | Allowed replacements |
|---|---|
| populate / populated | get releases, check(ed), fetched, up to date |
| preload / embed / embed data | load player(s), release details |
| cache / cached / CACHED | downloaded, saved, ready (or say nothing) |
| scrape(d) / parse / query | check(ed), search, read |
| credentials / token | Google access file (for the JSON), Gmail connection (for auth state) |
| provider / configuration | connection, mail settings (but `IMAP` itself is allowed — see the provider-settings subsection) |
| keychain backend / secure storage | your Mac's Keychain (macOS user vocabulary) |
| proxy / stream / SSE | (never user-visible) |
| "days" as scrape units | dates / dates checked |

Sentence case everywhere; no ALL-CAPS microcopy (ties into the visual plan). Dates shown to users are always inclusive calendar dates — never the end-exclusive Gmail query dates (the current log shows "2026-06-01 to 2026-07-01" for a June selection, which reads as a bug; pipeline.py:88-104).

**Wording map.** Current string (location) → replacement. Where an item says *(replaced by primitive)*, the string disappears entirely per UXP-2/UXP-9 and the replacement listed is the fallback text inside that primitive.

*Primary controls (sidebar):*

| Current | Location | Replacement |
|---|---|---|
| `Populate release list` | dashboard.html:52 | `Get releases` |
| `Populating…` | dashboard.js:1434 | `Checking mail…` (provider-neutral since e363bf4; with progress, UXP-2) |
| `Release list populated` (disabled label) | dashboard.js:580 | `Up to date` + secondary `Check again` action (UXP-11) |
| tooltip `All dates in this range are already populated` | dashboard.js:581 | `You've already fetched releases for these dates` |
| `Preload release data` | dashboard.html:53 | Button removed as a primary action (UXP-8). Interim rename if kept: `Load all players` |
| `Preloading…` | dashboard.js:1504 | `Loading players…` (with progress, UXP-9) |
| `Release data preloaded` | dashboard.js:637 | `Players ready` |
| tooltip `All embeds already cached for this range` | dashboard.js:638 | `All players are ready for these dates` |
| tooltip `Fetch embed data for releases in this range` | dashboard.js:642 | `Download the player and description for each release` |
| tooltip `Populate this range before preloading embeds` | dashboard.js:646 | `Get releases first` |
| tooltip `Preload unavailable` | dashboard.js:650 | (state removed with UXP-8) |
| `Mark as seen` / `Mark as unseen` | dashboard.html:54-55 | `Mark shown as seen` / `…as unseen`, with live count (UXP-16) |

*Calendar panel:*

| Current | Location | Replacement |
|---|---|---|
| legend `Populated` | dashboard.html:42 | `Checked` |
| legend `Unseen` | dashboard.html:44 | `New releases` (it marks days with unplayed releases, not day state) |
| `SHIFT-CLICK TO SELECT RANGE` | dashboard.html:39 | `Shift-click to select a range` (sentence case, quieter placement) |
| `SELECTED DATE RANGE:` | dashboard.html:51 | `Selected dates` |
| `Today` button (selects yesterday) | dashboard.js:1374-1390 | `Latest` (see UXP-18) |

*Status / selection messages (currently raw log text, dashboard.js:562-588):*

| Current | Replacement |
|---|---|
| `Select a date range to display.` | `Pick dates on the calendar to see releases.` |
| `Selected time period: … Date range fully populated. Displaying all releases in this date range.` | Inline under calendar: `Jun 1 – Jun 30 · all dates checked` |
| `{n} of {m} selected days not yet populated.` | `Jun 1 – Jun 30 · {n} dates not checked yet` |
| `Click "Populate release list" to populate all dates in the selected range.` | (replaced by primitive — the enabled `Get releases` button *is* the call to action; empty state carries the sentence on first run, UXP-14) |
| `Showing all populated releases (only from previously downloaded date ranges).` | `Showing everything fetched so far` |
| Star-then-browse tip (blue paragraph in log, dashboard.js:572-576) | Move to a dismissible one-time hint near the table: `Tip: star releases you're curious about — their players load in the background.` |

*Fetch progress (currently raw pipeline log lines, pipeline.py:104-183; gmail_client.py:273; imap_provider.py:72,159; server.py:680-688). Since e363bf4 these lines are provider-parameterized — `{provider}` renders as `Gmail` or `IMAP`:*

| Current | Replacement |
|---|---|
| `The following date ranges will be downloaded from {provider}:` + range list (pipeline.py:109-111) | (replaced by primitive — progress bar label `Checking Jun 10 – Jun 11…`) |
| `This date range has already been scraped; no {provider} download needed.` (pipeline.py:113) | `These dates were already checked — nothing new to fetch.` |
| `Querying {provider} for 2026-06-01 to 2026-07-01...` (pipeline.py:130) | `Searching your mail…` (inclusive dates only, if shown at all) |
| `Found 214 messages for …` (pipeline.py:152) | `Found 214 release emails` |
| `Downloading messages 0 to 20` (gmail_client.py:273, imap_provider.py:159) | (replaced by primitive — determinate bar `Downloading 20 of 214`) |
| `IMAP UID search: SINCE … BEFORE …` (imap_provider.py:72) | (details view only — not user-facing) |
| `Parsing messages...` (pipeline.py:27) | `Reading emails…` |
| `Checking for releases with identical URLS...` (pipeline.py:82) | (details view only — not user-facing) |
| `Warning: failed to parse one message: {exc}` / `Skipped {n} message(s) due to parse errors.` (pipeline.py:59,84 — new) | details view; aggregate only in the completion toast if `{n}` > 0: `{n} emails couldn't be read` |
| `Warning: Failed to fetch message {id}: {e}` (imap_provider.py:167 — new) | details view only |
| `Parsed {n} releases from {provider} for …` (pipeline.py:159) | `Found {n} releases` |
| `Loaded 205 unique releases including cache.` / `Loaded {n} unique releases from cache.` (pipeline.py:118,180) | (replaced by primitive — completion toast `Added {new} releases · {total} in this range`, UXP-13) |
| `No messages found for {after} to {before}` (pipeline.py:149) | `No release emails found for these dates.` |
| `Populate completed.` (server.py:680 — new) | (replaced by primitive — completion toast, UXP-13) |
| `Maximum results reached ({found}/{max}).` (server.py:684 — reworded in the merge; the client still substring-matches `"Maximum results"`, dashboard.js:1468) | see UXP-21 |
| `ERROR: Authentication failed: {exc}` / `ERROR: Unexpected error: {exc}` (pipeline.py:164, server.py:688 — new) | banner primitive, UXP-19 (typed error event, ARCH-4) |

*Preload / player loading (dashboard.js:1493-1543):*

| Current | Replacement |
|---|---|
| `Preloading embeds for {n} releases…` | `Loading players for {n} releases…` |
| `Nothing to preload for this range.` | `All players are already loaded.` |
| `({i}/{total}) {title}` log lines | (replaced by primitive — per-release status + aggregate chip, UXP-9) |
| `No embed found for {title}` | row-level `Player unavailable` state |
| `Preload complete. Cached: 8/12, failed: 4.` | `Players loaded for 8 of 12 releases. 4 couldn't be loaded.` |
| `CACHED` badge | Removed from default UI (UI-13); dev toggle label `Show cached badges` → `Show 'player ready' tags` |

*Connection / credentials (dashboard.html:274-301, dashboard.js:303-368, server.py:568-622 & 650-660, gmail_client.py, credential_store.py):*

| Current | Replacement |
|---|---|
| modal title `Credentials Needed` (html:277) | `Connect your email` |
| `Email credentials not configured. Configure your provider settings in the Settings panel to continue.` (html:280 — reworded provider-neutral in the merge) | `bcfeed isn't connected to your email yet. It needs a one-time setup to read your Bandcamp notification emails.` + button `Set up now` (UXP-3/4 — opens the provider choice) |
| modal title `Load Credentials` (html:289) | `Connect Gmail` |
| `You will be prompted to load your "client_secret_XXXXXXX.json" credentials file.` (html:292) | `Choose the Google access file you downloaded (it's named client_secret_….json).` |
| `The sensitive contents are stored securely in your system keychain.` (html:293 — new) | `bcfeed keeps this file in your Mac's Keychain — it never sits in a plain file.` (reassurance stays; "system keychain" is acceptable macOS vocabulary) |
| `This must be downloaded from Google Cloud.` (html:294 — the typo was fixed upstream) | folded into the "Choose the Google access file" sentence |
| `Show setup instructions` (plain-text link, html:296) | `How do I get this file?` styled as a visible link/button (UI-15) |
| button `Load credentials file` (html:158 — renamed in the merge) | `Connect Gmail…` |
| button `Clear credentials` (html:159 — deletes token + client config) | `Disconnect Gmail` |
| `Revoke Gmail Authorization` (html:164 — now a danger-styled button) + help text `Revokes access on Google's side. To remove local tokens, use "Clear credentials".` (html:165) | `Remove bcfeed's access in your Google account ↗`; help text: `This removes access on Google's side. To disconnect inside bcfeed, use Disconnect Gmail.` |
| `Credentials loaded.` / `Credentials loaded and authenticated.` (dashboard.js:357-359) / `Credentials uploaded and authenticated.` (server.py:610) | toast `Gmail connected` |
| `Saved Gmail credentials to secure storage. Authenticating…` (server.py:608 — new) | waiting-state copy, UXP-4: `Waiting for you to finish signing in with Google…` |
| `Credentials reloaded.` (dashboard.js:322) / `Credentials cleared.` (server.py:580) | toast `Gmail disconnected` |
| `Failed to load credentials.` (dashboard.js:326,352) | banner `Couldn't connect Gmail.` + specific reason + retry (UXP-19) |
| `Gmail credentials not found. Reload credentials in the settings panel.` (server.py:654) | banner `bcfeed can't find your Google access file.` + button `Open Settings` |
| `Gmail token missing. Reload credentials in the settings panel to re-authenticate.` (server.py:656) | banner `Gmail isn't connected.` + button `Connect Gmail` |
| `IMAP credentials not configured. Please configure IMAP settings (host, username, password, folder) in the settings panel.` (server.py:660 — new) | banner `Your mail connection isn't set up.` + button `Open Settings` |
| `Gmail refresh failed: {exc}` (gmail_client.py:231) | `Your Google sign-in expired. Reconnect Gmail to continue.` + button |
| `Gmail access was revoked or expired. Reload credentials in the settings panel to re-authorize.` (gmail_client.py:228) | same as above |
| `Stored Gmail token is invalid…` / `Saved Gmail token is unreadable…` / `Stored Gmail credentials are invalid…` (gmail_client.py:113,144,155 — new) | banner `Gmail isn't connected.` + button `Connect Gmail` (detail to the log disclosure) |
| `Could not find credentials.json. Reload credentials file in the settings panel to regenerate it.` (gmail_client.py:160) | same as "can't find your Google access file" above |
| `System keychain access is unavailable. Configure a supported keychain backend and try again.` (credential_store.py:32 — new) | `bcfeed couldn't reach your Mac's Keychain. Restart bcfeed and try again.` (the "configure a backend" advice is developer-speak) |
| `…Try reducing batch size using argument --batch.` (gmail_client.py:285 — flag still doesn't exist) | `Gmail is rate-limiting requests. Wait a minute and try again.` (backend retry per PY-9 makes this rare) |

*Settings (dashboard.html:116-263 — panel rebuilt in the merge; see UXP-5 for the structural re-scope):*

| Current | Replacement |
|---|---|
| `Clear cache & reset database` (html:260 — renamed in the merge) + `Clear all cached release data, images, and viewing history. Your settings and credentials will be preserved.` (html:256-257) | `Delete downloaded data…` (confirmation + stars/history split per UXP-7 — the new description is more honest but the action is still one-click and still deletes stars) |
| section `Email Configuration` (html:142) | `Email connection` |
| `Dark mode` toggle (html:131 — the theme toggle is now a visible setting, resolving part of UXP-5) | keep; default should follow `prefers-color-scheme` |
| `Show cached badges` (html:135 — now a visible Appearance setting, default on) | `Show 'player ready' tags`; return it to default-off/dev-only per UI-13 |

*Email provider settings (new surface at e363bf4 — dashboard.html:141-248, dashboard.js:1702-2093, server.py:717-817). Most of this copy is already plain-language and inline (status text sits next to the button that caused it — the UXP-2 pattern); the map below lists only the strings needing changes:*

| Current | Replacement |
|---|---|
| `Provider` label + options `Gmail API (OAuth)` / `IMAP` (html:145-149) | `How bcfeed reads your email` + `Google sign-in (Gmail only)` / `Mail server (IMAP — most providers)` |
| `Connect using the official Google API. Requires a client_secret.json file.` (html:154-156) | `Sign in with Google. You'll need a one-time Google access file (about 20 minutes to set up).` |
| `Folder To Scan` (html:225 — title case) | `Folder to scan` (sentence case, UIR-25) |
| `Save IMAP Configuration` (html:245 — title case) | `Save mail settings` |
| `Manual Folder Name` (html:237 — title case) | `Folder name` |
| `Connect & load folders` / `Reload folders` / `Connecting…` (dashboard.js:1724-1725, js status) | keep — already plain |
| `Connection verified. Review the folder selection, then save.` (js) | keep |
| `A recommended folder has been preselected. Review it, then save the configuration.` (js) | `We've picked the folder that looks right. Check it, then save.` |
| `Enter the IMAP server and username before loading folders.` / `Enter your IMAP password before loading folders.` (js) | keep — plain and actionable |
| `IMAP configuration saved.` (js) | `Mail settings saved.` |
| `Load folders and save the IMAP configuration to switch providers.` (js) | `Load folders and save your mail settings to finish switching.` |
| `Connection details changed. Reload folders before saving.` (js) | keep |
| password tooltip `Some providers require an app-specific password for IMAP access. Check your provider's documentation.` (html:209) | keep, but surface as visible `.help-text` rather than a hover-only `title` (hover tooltips are invisible on the field the user is stuck on) |
| `IMAP host is required.` / `IMAP username is required.` / `IMAP password is required.` / `Choose an IMAP folder to scan before saving.` / `Enter your IMAP password to load folders.` (server.py:728-733,794-801) | keep — field-level and actionable; render inline next to the field, not as a generic banner |
| `Failed to load IMAP folders: {exc}` (server.py:752) | `Couldn't connect to your mail server.` + reason + `Try again` (UXP-19 banner anatomy, inline in the panel) |

Vocabulary note: `IMAP` itself is **allowed** — it is the term the user's mail provider uses in its own settings and docs, so hiding it would hurt recognition. `Provider`, `configuration`, and `credentials` remain banned in favor of `connection` / `settings` / `access file`.

*Modals / system errors:*

| Current | Replacement |
|---|---|
| `Exceeded the maximum number of results per Gmail search. Try again with a shorter date range.` | see UXP-21 |
| `Please restart the app to use bcfeed.` | see UXP-20 |
| `Another populate is already running` | `A check is already running — hang on.` (inline, not alert) |
| `Populate failed (stream error)` | see UXP-19 |
| `Embed proxy not configured.` | (internal state; user sees generic `Something went wrong loading players. Restart bcfeed and try again.`) |
| `No embed available. Is the app still running?` | `Couldn't load the player.` + link `Open on Bandcamp` |
| `Could not clear disk cache (proxy not reachable). Run the app/proxy and try again.` | `Couldn't reach bcfeed. Make sure it's still running, then try again.` |
| `Populate requires EventSource support. Please use a modern browser.` | `This browser is too old for bcfeed. Please use a current version of Chrome.` |

**Acceptance criteria.**

- Grep of `dashboard.html`, `dashboard.js`, `pipeline.py`, `gmail_client.py`, `gmail_provider.py`, `imap_client.py`, `imap_provider.py`, `provider_factory.py`, `credential_store.py`, `server.py` for user-visible strings finds zero instances of the banned terms in any string a user can see (code identifiers may keep internal names).
- All date ranges shown to the user are inclusive and match the calendar selection exactly.
- Every disabled control's tooltip states, in plain language, *why* it is disabled and *what would enable it*.
- The docs (README/SETUP) are updated in the same pass so UI names and doc names match (the docs have drifted before — commit bdc1b4a).
- A `docs/copy.md` (or comment block) records the vocabulary rules table so future strings stay consistent.

**Size:** M (the strings themselves are S; the pipeline log lines require the typed-event work in UXP-2 to land fully).

---

### UXP-2 — Retire the raw Status log as the primary channel; map each signal to a proper primitive

**Rationale.** The Status box currently performs five jobs — selection summary, tutorial, live progress, per-item preload progress, credential results, and errors — all as colored text lines in a fixed 200px box at the bottom-right, physically across the screen from the sidebar controls it narrates (UX-7, UI-9). *Visibility of system status* requires feedback at the locus of action, in a form matched to the signal (progress → progress bar, outcome → toast, failure → banner). The information quality is good; only the rendering primitive is wrong. The log is also fought over by two writers, so calendar clicks mid-populate wipe streamed progress (JS-3).

**Signal map.** Every signal the log currently carries, and what it becomes:

| Signal | Today | Becomes |
|---|---|---|
| Date-range confirmation / "N days not checked" | Blue text in log, bottom-right | Inline one-line summary **inside the calendar panel**, directly under the grid: `Jun 1 – Jun 30 · 3 dates not checked`. Updates live with selection. |
| "What ranges will be fetched" preamble | Log lines | Progress-bar phase label on the `Get releases` button group: `Checking Jun 10 – Jun 11…` |
| Gmail search progress (`Querying…`, `Found 214 messages`) | Log lines | Same determinate progress bar, phase 1: `Searching Gmail… found 214 emails` |
| Download batch progress (`Downloading messages 0 to 20`) | Log lines | Progress bar, phase 2: `Downloading 40 of 214` with a real percentage (the backend already knows totals and batch index — ARCH-4) |
| Parse counts (`Parsed 12 releases…`) | Log lines | Progress bar, phase 3: `Reading emails…`; final count goes to the completion toast |
| Completion (`Loaded 205 unique releases…`) | Log line + full page reload | Toast: `Added 12 releases · Jun 1 – Jun 30` + in-place table refresh (UXP-13) |
| Errors (`ERROR: …`, alert()) | Log line + blocking alert | Persistent inline banner with an action (UXP-19) |
| Preload per-item progress (`(3/12) Title…`) | Log lines | Per-release row status + aggregate progress chip with pause (UXP-9) |
| Credential operation results | Log lines + alert | Toasts (success) / banner (failure) |
| Star-then-browse tutorial tip | Blue paragraph in log | Dismissible one-time hint near the table (UXP-1) |

The raw log does not disappear: it becomes a collapsed **"Details"** disclosure under the progress area, collapsed by default, appended-only during a run, monospace, no fake-link blue (UI-9). It is the debug view, not the primary channel. Power users and bug reports keep full fidelity.

**Acceptance criteria.**

- No user-relevant signal exists *only* in the log; each row of the signal map above renders in its stated primitive.
- The progress bar is determinate during download (shows fraction of messages downloaded) and indeterminate only for the search phase.
- Progress state has a single owner: calendar interaction during a run cannot erase progress, change the button label, or re-enable the button (fixes JS-3); the selection summary and the run progress are separate DOM regions.
- The progress region has `aria-live="polite"`; a screen reader hears phase transitions and completion (JS-7 partial).
- Toasts auto-dismiss (≈6s), never stack more than 2, and are not used for errors; banners persist until dismissed or resolved.
- The Details disclosure survives run completion until the next run starts (no reload wipe — depends on UXP-13).

**Dependencies:** typed SSE events `{log, progress, error, complete}` (ARCH-4); worker exception surfacing (ARCH-3/PY-2).

**Size:** L (new toast + banner + progress components, SSE handler rewrite; the backend enabler is ~90 lines per ARCH-4).

---

## 2. Onboarding

### UXP-3 — Replace the Credentials-modal→Settings dump with a guided first-run checklist

**Re-scoped at e363bf4:** the first-run funnel now **branches by provider**. The merge added an IMAP path (settings → Email Configuration → IMAP) that is radically shorter than the Gmail path — host/username/password/folder, with automatic folder discovery and a recommended folder preselected (`/imap/discover`, server.py:717-752) — no Google Cloud project, no OAuth consent hop. The modal itself went provider-neutral (`Email credentials not configured…`, html:280) but the funnel shape is unchanged: modal → dismiss → auto-open Settings (dashboard.js:1029-1035) → a settings panel that now at least has labeled sections, but still no sequencing, time expectations, or progress state.

**Rationale.** First value on the Gmail path is ~15 manual steps away; on the new IMAP path it is ~4 fields away, *provided* the user knows to create an app-specific password (the one trap on that path — currently explained only in a hover tooltip, html:209). The in-app funnel still ends in a flat settings panel with the setup guides buried behind links (UX-1). The Gmail constraint is real and stays: users must self-provision a Google OAuth client (privacy-by-user-owned-credentials is a core product principle). Given that, the design job is *choice framing, sequencing, and expectation-setting*: present the two paths with honest costs, then a checklist that externalizes progress (*recognition over recall*), makes the chosen gauntlet feel bounded, and keeps the user away from destructive controls on day one (*error prevention*).

**Design.**

- On launch with no email connection, the **main content area** (not a modal) shows a first-run panel, step 1 of which is the provider choice:
  1. **Choose how bcfeed reads your email** — two cards:
     - `Mail server (IMAP)` — `~5 minutes. Works with most providers. You may need an app-specific password — we'll show you how.` (recommended default for non-Gmail users; honest hint that Gmail-via-IMAP also needs an app password)
     - `Google sign-in (Gmail API)` — `~20 minutes, one time only. Google requires this so that only you can read your own email.` Button: `Open the step-by-step guide` (existing `/setup-gmail` docs; the IMAP card links `/setup-imap`). Sub-hint: `You'll finish with a file named client_secret_….json`.
  2. **Connect it to bcfeed** — Gmail: button `Choose file…` (the existing upload). IMAP: the four connection fields + `Connect & load folders` + folder confirmation (reuse the shipped panel, re-hosted in the checklist). Steps are numbered, not gated — returning users can jump straight to 2.
  3. **Get your first releases** — `Pick dates on the calendar, then press Get releases.` Optionally a one-click starter: `Check the last 30 days`.
- Step state is detected, not stored: step 2 shows ✓ when a Gmail token or a saved-and-verified IMAP config exists (`/provider-config` GET already reports both — `has_gmail_credentials`, `has_password`); step 3 shows ✓ when any release data exists. The panel disappears once step 3 completes and never returns (re-reachable from Settings → `Set up email again`).
- The current `Credentials Needed` modal is deleted; the current auto-open-Settings-on-dismiss behavior (dashboard.js:1029-1035) is deleted.
- The calendar/sidebar remain visible but visually quieted behind the checklist so the user sees where they will land.

**Acceptance criteria.**

- A fresh install (no data dir) boots directly into the checklist; no modal appears.
- Both provider paths are visible with an honest time estimate before the user commits to either; switching paths mid-setup loses nothing.
- The IMAP app-password requirement is stated on the checklist card (visible text, not a hover tooltip).
- Each step has a single primary action; no destructive action is reachable in fewer than 2 clicks from the checklist.
- Killing the app mid-setup and relaunching restores the correct step from detected state (client config / token / IMAP config / data present).
- Completing step 2 (either path) advances the checklist without a page reload.
- The `/setup-gmail` and `/setup-imap` guides each open from step 1 in one click.
- Usability check: a test user who has never seen the app can state, from the checklist screen alone, which path they'd pick, what they must do next, and roughly how long it takes.

**Size:** L.

---

### UXP-4 — Announce and supervise the OAuth consent hop (Gmail path only)

**Scope note (e363bf4):** this item applies only to the Gmail API path — the new IMAP path has no consent hop at all (its verification is a bounded server-side connection check with inline status text, already the right shape). Verified still present: `/load-credentials` still runs the full interactive OAuth flow synchronously on the request thread (server.py:609 → `flow.run_local_server`, gmail_client.py:235, no timeout), and the upload still opens the consent tab unannounced.

**Rationale.** Uploading the Google file synchronously triggers a *separate browser tab* running Google consent — including the "Google hasn't verified this app" interstitial — while the dashboard's button spins forever if the user misses or abandons the tab (UX-2, SEC-6, JS-9). This is the single most fragile onboarding step and it is completely unannounced. *Visibility of system status* and *help users recover*: the UI must say what is about to happen, show that it is waiting, and offer a way out.

**Design.**

- Connect-Gmail modal copy, before the file picker: `After you choose the file, a Google sign-in tab will open. Google will warn that the app is unverified — that's expected, because this is your own private app. Click "Advanced", then "Go to bcfeed (unsafe)" to continue.`
- After upload, the modal enters a **waiting state**: spinner + `Waiting for you to finish signing in with Google… Check for a new browser tab.` with a `Cancel` button.
- Cancel (or a backend timeout, e.g. 3 minutes) aborts the flow and shows: `Sign-in wasn't completed. Try again` (button).
- Success closes the modal, shows the `Gmail connected` toast, and advances the checklist (UXP-3).
- Closing the modal via ✕/backdrop **cancels** — it must not open the OS file picker (JS-9 fix).

**Acceptance criteria.**

- The consent tab never appears without the UI having stated it would, one screen earlier.
- Abandoning the Google tab leaves the app recoverable within one click (Cancel) — no permanently spinning button, no blocked server thread visible to the user.
- ✕ and backdrop close the modal without opening the file picker.
- The unverified-app interstitial guidance is shown in the UI itself, not only in the docs.

**Dependencies:** backend must run the OAuth flow off the request thread with a timeout and expose its state (waiting / done / failed) — SEC-6 / ARCH-8 fix direction.

**Size:** M (frontend) + M (backend enabler).

---

### UXP-5 — Reorganize Settings: connection status first, preferences next, destructive actions last and separated

**Re-scoped at e363bf4 — largely delivered upstream.** The merge rebuilt the settings panel (html:116-267) into labeled sections in nearly the order this item specified: **Appearance** (Dark mode toggle — now exposed, resolving the hidden-theme half of this item — plus Show cached badges), **Email Configuration** (provider select, per-provider panels, revoke isolated with explanatory help text), and **Data & Storage** last with descriptive copy. What this item still owes:

**Remaining design.**

- **Connection status line first**: the Email Configuration section shows forms, not state — add `Connected` / `Not connected` at the top of the section (the backend already reports it: `/provider-config` GET returns `has_gmail_credentials` and `imap_config.has_password`; today the only state signal is the IMAP password field's `••••••••` placeholder).
- **Destructive-action confirmation**: `Clear cache & reset database` (html:260) is still one click with no confirmation and still deletes stars/seen-history (performReset hardcodes all three flags, dashboard.js:1047-1049) — UXP-7 applies unchanged; rename per the wording map.
- **Danger-zone styling**: the Data & Storage section is separated but not visually marked destructive; the new `.button.danger` class exists (css:1044-1052) but isn't applied to the reset button — pair with the visual plan's normalization (UIR-30).
- **About line**: version string still lives in the H1 (html:73); move it here (UI-8).
- **Theme default**: the Dark mode toggle works but should seed from `prefers-color-scheme` (visual plan §3).

**Acceptance criteria.**

- Settings opens showing connection state (per provider) without any action taken.
- Destructive actions are in a visually distinct final section and all require confirmation (UXP-7).
- The theme toggle respects `prefers-color-scheme` by default.
- The version string appears in Settings and nowhere else.
- First-run users following the checklist never need to open Settings at all.

**Size:** S–M (down from M — the structural reorganization shipped upstream).

---

### UXP-6 — First-run one-click starter: "Check the last 30 days"

**Rationale.** After the credentials ordeal, the user lands in an empty dashboard where the one thing they must do (select dates, press the button) is expressed only as sidebar controls (UX-5). A single pre-scoped action collapses recall into recognition and delivers first value in one click. 30 days is small enough to stay under the Gmail result cap for almost all users.

**Design.** Step 3 of the checklist (UXP-3) and the first-run empty state (UXP-14) both offer `Check the last 30 days` — it selects yesterday-minus-29 → yesterday on the calendar (visibly, so the user learns the calendar is the control) and starts the fetch.

**Acceptance criteria.**

- One click from the post-connection state starts a fetch; the calendar visibly reflects the selected range before the run starts.
- If the result cap is hit, the max-results flow (UXP-21) takes over gracefully.

**Size:** S.

---

### UXP-7 — Destructive-action safety: confirm, enumerate, and split "Clear cache"

**Rationale.** One unconfirmed click on `Clear cache & reset database` (renamed in the merge, with a description that now at least admits "viewing history" is included — html:256-260) still deletes downloaded releases *and* the user's stars and seen-history — the only state they personally created (UX-4; performReset still hardcodes all three flags, dashboard.js:1047-1049). The label promises less than it destroys. *Error prevention* demands confirmation proportional to irreversibility, and honest labels.

**Design.**

- Replace the single button with `Delete downloaded data…` opening a confirmation dialog that enumerates: `This deletes all fetched releases and players. bcfeed will forget which dates were checked.` Checkbox (default **off**): `Also delete my stars and seen/unseen history.` Confirm button restates scope: `Delete downloaded data` / `Delete everything`.
- Backend already accepts the flags independently (the both-flags bug PY-13 must be fixed so the split is real).
- Post-action toast states what was deleted.

**Acceptance criteria.**

- No destructive action executes on a single click anywhere in the app.
- Stars/seen-history survive a default "delete downloaded data" action, verified by starring a release, deleting data, re-fetching the same range, and seeing the star intact.
- The confirmation dialog names every store that will be affected in user language.

**Dependencies:** PY-13 fix (flag split honored server-side).

**Size:** S (UI) + S (backend flag fix).

---

## 3. Core-loop simplification

### UXP-8 — One mental model: "Get releases" fetches, enrichment happens in the background

**Rationale.** The core loop currently exposes three concepts — populate (Gmail fetch), preload (bulk Bandcamp scrape), cache (why some rows are instant) — where the user has exactly one intent: "show me the releases." (UX-6, UX-11.) The populate/preload distinction is an implementation seam (cheap email metadata vs. expensive page scrape) that leaked into two sibling buttons with interlocking disabled states. *Match to mental model*: one verb, `Get releases`; everything else is the app quietly finishing the job. The star-triggers-preload coupling — the app's cleverest interaction — already proves enrichment can be ambient.

**Design.**

- `Get releases` remains the single primary action (fetch from Gmail).
- **Enrichment (players + descriptions) becomes a background queue** rather than a second button: after a fetch completes, the app enriches visible/starred releases automatically at a polite rate (serial or 2-concurrent, preserving the be-polite-to-Bandcamp principle). Priority order: starred > expanded/hovered (existing prefetch) > visible rows > rest of range.
- The `Preload release data` button is removed as a top-level control. A low-key `Load all players for these dates` action remains available (e.g., in a small menu next to the range summary) for users who want to bulk-prepare an offline browse — the README's documented use case — but it is no longer a decision the user must make to use the app.
- Row-level status makes enrichment legible (UXP-9), replacing the CACHED badge concept entirely.
- The star keeps its behavior: starring immediately queues that release at the front (preserve star-triggers-preload).

**Acceptance criteria.**

- A new user can go select-dates → `Get releases` → browse → expand a row and hear music without ever encountering a second fetch concept.
- The sidebar contains exactly one primary fetch button.
- Starring a release with no player loaded starts its enrichment within 1s (behavior preserved from today).
- Bulk "load all players" is still reachable in ≤2 clicks for the power-user offline-prep workflow.
- Background enrichment never exceeds the politeness budget (max 2 concurrent Bandcamp fetches, with backoff on failure) and pauses while a Gmail fetch is running.

**Dependencies:** negative-caching + cache-first `/embed-meta` (PERF-2, JS-6) — without it, background enrichment re-fetches failures forever; in-flight dedupe (JS-6).

**Size:** L.

---

### UXP-9 — Visible per-release enrichment status + a pausable aggregate indicator

**Rationale.** Preload today is an uncancellable multi-minute loop whose only feedback is log lines (UX-11); cached-ness is broadcast as a shouting `CACHED` badge that means nothing to a listener (UX-6, UI-13). *Visibility of system status* at the right granularity: the user cares per-release ("can I play this now?") and in aggregate ("is it done yet? can I stop it?").

**Design.**

- **Per-release:** a quiet glyph in the row (visual plan defines the form): *ready* (player loaded — subtle, replaces CACHED), *loading* (in queue/in flight), *unavailable* (fetch failed / page gone — with `Open on Bandcamp` fallback in the detail row). Default state (not yet fetched) shows nothing — absence of chrome is the norm (UI-6).
- **Aggregate:** while the queue is active, a small chip near the range summary: `Loading players · 14 of 50` with a `Pause` control. Interruption is already cheap (each item persists immediately; re-runs resume incrementally per the UX-11 verification), so Pause just stops the loop.
- Failures don't interrupt the queue; the completion state reads `Players loaded for 46 of 50 · 4 unavailable`.

**Acceptance criteria.**

- During enrichment, sorting/filtering/browsing works normally; no button is held disabled by the queue.
- Pause stops network activity within one in-flight request; resuming skips already-loaded releases (no re-fetch of successes, verified via network log).
- A release whose Bandcamp page is gone shows *unavailable* and is never auto-retried this session (negative cache), rather than being silently re-fetched forever.
- Screen-reader announcement on queue completion (polite).
- The `CACHED` badge no longer appears in the default UI.

**Dependencies:** PERF-2/PERF-3 fixes (cache-first endpoint, negative cache, batched cache writes); UXP-8.

**Size:** M (given UXP-8's queue exists).

---

### UXP-10 — Auto-fetch on range selection: evaluated, and rejected in favor of a zero-friction explicit action

**Rationale (decision record).** The obvious simplification — fetch automatically whenever the selection includes unchecked dates — was evaluated against first principles and rejected:

- *User control and freedom:* calendar selection is exploratory (users click around to browse ranges they already fetched); auto-fetch would fire multi-second Gmail searches — and consume the 2000-result quota — as a side effect of looking. A misclick on a year boundary triggers a monster fetch with a modal failure (UXP-21).
- *Be polite to upstream* (product principle 5): implicit actions multiply network work; the single populate lock (server.py:662) would also serialize surprise runs behind each other, making the calendar feel broken.
- *Calm utility brief:* a tool that starts network activity uninvited is not calm. Gmail's OAuth consent makes "reads your email when you ask" vs "reads your email whenever" a trust-relevant distinction.

**What we do instead:** make the explicit action zero-friction and impossible to miss — the range summary line (UXP-2) doubles as the affordance: `3 dates not checked · **Get releases**`; the button is always enabled for such ranges, and the one-click starter (UXP-6) covers first run. Revisit auto-fetch only if telemetry-free user feedback shows people still stall here (a future opt-in `Check automatically when I select dates` preference is the compatible path).

**Acceptance criteria.**

- Selecting any range never initiates network activity by itself (verifiable via server log).
- From any selection containing unchecked dates, exactly one click starts the fetch, and that click target is adjacent to (or part of) the message stating dates are unchecked.
- The decision and its rationale are recorded (this section) so a future maintainer doesn't relitigate it blind.

**Size:** S (falls out of UXP-2's summary line placement).

---

### UXP-11 — Kill the disabled-button dead end: fully-checked ranges offer "Check again"

**Rationale.** Once a range is fully populated, the primary button becomes permanently disabled (`Release list populated`) and the append-only cache means those dates can never be re-checked except via the data-destroying reset (UX-12). A disabled primary action with no path forward violates *user control and freedom*, and the underlying "never re-fetch" principle is a caching policy, not a user contract. Interrupted runs (ARCH-2) make re-checking a genuine need, not an edge case.

**Design.**

- When the selected range is fully checked, the primary slot shows a quiet confirmation instead of a disabled button: `✓ Up to date for these dates` with a secondary action `Check again`.
- `Check again` clears checked-status for the selected dates only and re-runs the fetch; URL-level dedupe already prevents duplicates, so the operation is safe and non-destructive.
- Copy sets expectations: tooltip `Searches Gmail again for these dates. Existing releases, stars, and history are kept.`

**Acceptance criteria.**

- No permanently disabled primary button exists in any reachable state; every state shows either an available action or a positive status with a secondary action.
- `Check again` on an already-complete range finishes with `Added 0 releases` (dedupe verified) and leaves stars/seen intact.
- After an artificially interrupted fetch, `Check again` recovers the missing releases (validates the ARCH-2 pairing).

**Dependencies:** small backend endpoint to clear scrape-status for a date range (or a `force` param on the stream endpoint); ARCH-2 persist-before-mark fix strongly recommended first.

**Size:** M.

---

### UXP-12 — Make the keyboard triage loop discoverable

**Rationale.** Arrows/Enter/s/u/Escape form a complete, fast triage loop — documented nowhere in UI or docs (UX-14). A preserved strength being wasted; discoverability is the cheapest possible feature. *Recognition over recall.*

**Design.** A single muted hint line under the table: `↑↓ move · Enter play · s star · u unseen`; plus a Shortcuts section in the `?` help destination. (Optional later: `?` key opens a shortcuts overlay.)

**Acceptance criteria.**

- The hint is visible without interaction on a populated table, and does not exceed one line of muted text.
- Every implemented shortcut is listed somewhere reachable in ≤1 click; no listed shortcut is unimplemented.
- The `s`/`u` shortcuts' row-visual desync (JS-8) is fixed before advertising them.

**Size:** S.

---

## 4. Flow fixes

### UXP-13 — Fetch completes in place: no page reload, and an answer to "what did I get?"

**Rationale.** The SSE `done` handler calls `window.location.reload()`: the user watches progress, then the page blanks, scroll/sort/filters/log all reset, and the one question they had — did anything new arrive? — is never answered (UX-9, JS-10, ARCH-3). *Visibility of system status* includes outcomes, not just activity.

**Design.**

- On completion, refetch `/releases` + checked-status and re-render in place, preserving sort, filters, scroll, and expanded row.
- Completion toast: `Added 12 releases · Jun 1 – Jun 30` (count of *new* releases; the backend computes this — ARCH-4's `complete` event carries `new_count`). Zero-new case: `No new releases for these dates.`
- Newly added rows get a subtle transient highlight so "what changed" is visible in the table itself.
- The Details log (UXP-2) survives completion.

**Acceptance criteria.**

- No full page reload occurs on fetch completion (verifiable: scroll position and active sort persist across a run).
- The toast count matches rows actually added (test: run on a range with known cached overlap).
- A fetch failing mid-run surfaces the error banner (UXP-19) — never a success toast (requires ARCH-3/PY-2 fix: failures must not emit `done`).

**Dependencies:** ARCH-4 typed `complete` event; ARCH-3 error event.

**Size:** M.

---

### UXP-14 — Branch the empty state: "never fetched" is not "filtered to nothing"

**Rationale.** A never-populated install shows `No releases match the current filter.` — factually wrong at the moment of highest drop-off, and it serves two opposite situations needing opposite actions (UX-5). *Help users diagnose*: an empty view must say which kind of empty it is and what to do.

**Design.** Three distinct empty states:

1. **No data ever fetched:** `No releases yet. Pick dates on the calendar, then press Get releases.` + inline `Check the last 30 days` button (UXP-6).
2. **Data exists, none in selected dates:** `Nothing fetched for these dates yet.` + `Get releases` inline action (or `No releases were found for these dates` when the range is checked-but-empty).
3. **Data in range, filters exclude everything:** `All {n} releases for these dates are hidden by your filters.` + `Clear filters` link.

**Acceptance criteria.**

- Each of the three states is reproducible and shows its own copy + action; the string "No releases match the current filter." no longer appears on a fresh install.
- The "clear filters" action resets label/unseen/starred filters but not the date selection.
- Each empty-state action is a single click and visibly changes the table.

**Size:** S/M (state detection needs release-count-in-range vs. post-filter count, both already computed).

---

### UXP-15 — Label filter: one checkbox column plus a per-row "only" action

**Rationale.** Two unlabeled checkbox columns where the second silently disables the entire first column is the most confusing single control in the app (UX-8); unchecking the last box silently re-checks everything, and date navigation wipes selections (JS-5). The faceted-search idiom — one checkbox list, an `only` link per row on hover, All/None controls — is the *recognition over recall* standard for this exact job.

**Design.**

- One checkbox per label = show/hide. Hovering (or focusing) a row reveals an `only` link that checks that label and unchecks the rest — replacing the entire show-only column and its hidden mode-switch.
- `All` / `None` controls at the top of the panel.
- Unchecking the last label shows an empty table with empty-state 3 (UXP-14) — it does not silently re-check everything.
- Selections persist across date navigation: track *exclusions*, so labels newly appearing in a different range default to shown without wiping choices (JS-5 fix direction).
- Active-filter feedback: see UXP-16's filter chip.

**Acceptance criteria.**

- The panel has exactly one checkbox column; no state ever disables a whole column without explanation (the mode-switch is gone).
- `only` on any row yields exactly that label's releases in ≤1 click.
- Uncheck two labels, navigate to another month and back: the two labels remain unchecked.
- Unchecking all labels shows the filtered-empty state, not an auto-reset.
- Keyboard: checkboxes and `only` reachable/operable via Tab + Enter.

**Size:** M.

---

### UXP-16 — Mark-seen scope honesty + undo; persistent count and active-filter feedback

**Rationale.** `Mark as seen` sits under `SELECTED DATE RANGE:` but actually operates on currently *rendered* (filtered) rows, and can flip a month of history with no confirmation or undo (UX-10). Meanwhile the `N releases shown` count — the only feedback that sorting/filtering did anything — is erased moments after every load (JS-4), and active filters are invisible unless you look at the controls. *Match between label and effect*, *user control (undo)*, *visibility of system status*.

**Design.**

- Buttons carry their true scope and a live count: `Mark 17 shown as seen` / `…as unseen`; disabled with count 0. Move them out of the date-range group (they are table actions, not range actions) — place near the table header/filter row.
- After a bulk mark, toast with undo: `Marked 17 as seen · Undo` (10s window; undo restores the exact previous per-row set).
- Persistent status line under the table (single owner, never blanked): `17 releases · filtered from 49 · sorted by date ↓`. Fixes JS-4 by construction.
- When any filter is active, a dismissible chip near the count: `Filters: Unseen · 2 labels hidden — clear`.

**Acceptance criteria.**

- The button's count always equals the number of rows the action will affect (verified with filters active).
- Undo restores prior seen-state exactly, including rows that were already seen before the bulk action.
- The count line never disappears during normal use (load, sort, filter, mark, fetch-complete).
- Bulk mark of 500 rows completes without visible UI freeze and survives reload with no lost marks — requires the batch endpoint + single re-render (PERF-1/ARCH-6/JS-11).

**Dependencies:** batch viewed-state endpoint (PERF-1).

**Size:** M.

---

### UXP-17 — Calendar coverage-map legibility: one visual channel per state, gaps loudest

**Rationale.** The calendar-as-coverage-map is the app's best IA idea, but its three encodings (selected, checked, has-new-releases) share one blue-and-dot vocabulary, and the cells that answer the user's actual question — "which dates haven't been checked?" — are the *least* salient on the grid (UX-13, UI-5). *Visibility of system status* with correct salience ordering: rare/actionable states loudest, common states quietest.

**Design (semantic spec; visual plan owns the pixels):**

- **Selected range** = background fill (one channel: fill).
- **Checked date** = quiet mark (e.g., reduced-weight day number or tiny underline) — the common, calm state.
- **Unchecked date inside selection** = the loudest treatment (hollow/warning style) — it is the action-needed state, mirrored by the `n dates not checked` summary (UXP-2).
- **Has unplayed releases** = small dot, not red (red is for errors — UI-6), distinct from selection styling.
- Legend rendered with the same components as the cells, labeled `Checked` / `New releases` (UXP-1).
- Today: see UXP-18.

**Acceptance criteria.**

- Each of the four states is visually identifiable in isolation *and* in combination (a checked+selected+new-releases day shows all three channels without collapsing into "wall of blue").
- In a fully-checked selected month, the grid reads calm (no per-cell emphasis louder than the table content).
- The `n dates not checked` summary and the count of visually-marked unchecked cells always agree.
- Cells are keyboard-reachable buttons with `aria-pressed`/`aria-disabled` (JS-7 pairing; implementation may land with the a11y work).

**Size:** M (joint with the visual plan; the JS state-classing is small once semantics are fixed).

---

### UXP-18 — Stop the "Today" lie; explain today's exclusion

**Rationale.** Today is deliberately never selectable (today's emails aren't final — a sound principle), but the button labeled `Today` selects yesterday, and today's grayed-out cell is never explained (low-severity finding; commit 598a9dd). Small honesty gaps read as bugs and erode trust in a tool whose brand is reliability.

**Design.** Rename the button `Latest`; give today's disabled cell a tooltip: `Today's emails are still arriving — check back tomorrow.`

**Acceptance criteria.**

- No control claims to select today; the tooltip is present on today's cell in every month view containing today.

**Size:** S.

---

## 5. Error states

### UXP-19 — One error primitive: persistent, actionable, accurate banners

**Rationale.** Populate failures today are a blocking `alert()` plus a log line that other code freely overwrites; the observed end state of a credential-less populate is an empty status box and `0 releases shown` — the app has no error memory (UX-3). Transient SSE blips alert "Populate failed" while the server keeps working (JS-10), and real worker crashes report success (ARCH-3). *Help users recognize, diagnose, and recover*: errors need persistence (until acknowledged/resolved), plain language, and an action.

**Design.**

- A single **banner component** rendered above the table (and mirrored compactly in the sidebar near the button that failed). Anatomy: what happened (user language) → what to do → action button. Never auto-overwritten by status updates; dismissible; re-shown on retry failure.
- Canonical populate errors:
  - Not connected: `Gmail isn't connected.` → `Connect Gmail` (opens the flow, UXP-4).
  - Expired: `Your Google sign-in expired.` → `Reconnect Gmail`.
  - Rate-limited/network: `Gmail didn't respond. Wait a minute and try again.` → `Try again`.
  - Internal failure (typed `error` event): `Something went wrong while checking Jun 10 – Jun 30. Those dates weren't saved.` → `Try again` + `Show details` (opens the log disclosure).
- **Stream blip ≠ failure:** on an EventSource `error` without a terminal server event, do not declare failure; show a quiet reconnecting state, then poll checked-status once to resolve to success ("the check finished") or real failure (JS-10 fix direction).
- `alert()` is removed from the codebase's user-facing paths entirely.
- Banners have `role="alert"`.

**Acceptance criteria.**

- Populate-without-credentials leaves a persistent banner with a working `Connect Gmail` action; it survives calendar clicks, filter changes, and status refreshes until dismissed or resolved (test against the exact JS-3 overwrite path).
- Killing the server mid-populate then restarting produces either a truthful success (releases present) or a truthful failure banner — never a silent empty result (depends on ARCH-2/ARCH-3/PY-2 backend fixes).
- A simulated transient disconnect during a healthy run produces no failure banner and ends in the normal completion toast.
- Zero `alert()` calls remain in dashboard.js.

**Dependencies:** ARCH-3/PY-2 (typed terminal error event; no `done` on failure); ARCH-4 (error codes).

**Size:** M (component S; the state routing and blip/terminal distinction is the work).

---

### UXP-20 — Server-down: recoverable banner instead of a latching dead-screen modal

**Rationale.** One transient health-check failure currently latches an undismissable full-screen modal whose advice ("restart the app") cannot restore the page even when followed, because polling stops and the tab never recovers (UX-15, JS-2). For a product whose known Achilles heel is "keep the Terminal open", the disconnection moment is a *first-class flow*, not an exception. *User control and freedom* forbids undismissable dead ends.

**Design.**

- Require 2–3 consecutive failed health checks before reacting (blip tolerance).
- Show a **non-blocking banner** (the UXP-19 primitive) rather than a full-screen modal: `bcfeed isn't running. Start it from Terminal — this page will reconnect automatically.` The table stays visible (read-only browsing of already-loaded rows still works).
- Keep polling; on recovery, auto-dismiss and toast `Reconnected.` If a fetch was in flight when the server died, resolve its state per UXP-19.
- Mutating actions (star, mark seen, fetch) are disabled while disconnected, each with the tooltip `bcfeed isn't running`.

**Acceptance criteria.**

- Stopping and restarting the server with the tab open ends with a usable dashboard and zero manual reloads.
- A single failed health check (simulated 5s network stall) produces no visible change.
- While disconnected, no user action can silently fail: everything mutating is visibly disabled with an explanation.
- The full-screen `server-down-backdrop` modal is deleted.

**Size:** M.

---

### UXP-21 — Max-results: turn the quota wall into a guided range split

**Rationale.** Hitting the Gmail result cap currently costs tens of seconds of API work, then discards everything and shows a modal restating the limit in Gmail-quota language (`Exceeded the maximum number of results per Gmail search`) — the user must guess a shorter range and retry from scratch (UX-6 item 8, PERF-7). The cap is an implementation constraint; the user's job is unchanged. *Error prevention* (warn before the expensive failure) and *recovery with guidance* (propose the split, don't just demand one).

**Design.**

- Copy: `That range has too many release emails to fetch at once (over 2,000).` → primary action `Check Jun 1 – Jun 15 first` (the app proposes the first half; after it completes, the range summary shows the remainder unchecked, and `Get releases` continues naturally). Secondary: `Pick different dates`.
- Because dates are checked per-range, the natural loop "fetch half, then fetch the rest" already works — the design just names it.
- Prevention: with the PERF-7 fix (early pagination stop), the cap is detected in a few seconds instead of after full pagination; if a cheap pre-count is ever available, warn before starting.
- Delivered via the banner/modal primitive with the run's progress bar stopping honestly (`Stopped — too many results`), not via substring-sniffing the log (needs the typed `error: max_results` event — ARCH-4).

**Acceptance criteria.**

- Hitting the cap presents a one-click continuation covering a sub-range; completing it and the remainder yields the same data as an uncapped fetch would have (dedupe verified).
- The failure appears within a few seconds of the cap being determinable (PERF-7), not after full pagination.
- No Gmail-quota vocabulary ("results per Gmail search") appears; the message names the user's range and the way forward.

**Dependencies:** ARCH-4 typed error event; PERF-7 early-stop.

**Size:** M.

---

## Sequencing and dependency summary

Recommended order (UX perspective only; correctness fixes ARCH-1/2/3, PY-2/3, SEC-1/2/3 come first per the audit and are prerequisites for several items):

| Wave | Items | Why |
|---|---|---|
| 1 — words & safety | UXP-1, UXP-7, UXP-18, UXP-12 | Pure renames + guardrails; no structural change; immediate de-AI-smell and trust wins |
| 2 — feedback spine | UXP-2, UXP-13, UXP-19, UXP-20 | Requires typed SSE (ARCH-4) + error event (ARCH-3); establishes the progress/toast/banner primitives everything else reuses |
| 3 — onboarding | UXP-3, UXP-4, UXP-5, UXP-6 | Needs the banner/toast primitives and async OAuth (SEC-6 fix) |
| 4 — core loop | UXP-8, UXP-9, UXP-11, UXP-10 | Needs cache-first `/embed-meta` + negative cache (PERF-2), re-check endpoint |
| 5 — table & calendar flows | UXP-14, UXP-15, UXP-16, UXP-17 | Needs batch seen endpoint (PERF-1); calendar work pairs with the visual plan |

Backend enablers referenced: ARCH-2 (persist-before-mark — still needed at e363bf4; the rewritten pipeline still marks scraped per range but persists releases only at the end), ARCH-3/PY-2 (error event, no false `done` — *partially* mitigated upstream: the populate worker now catches all exceptions and emits `ERROR:` prose, server.py:681-690, but the stream still terminates `event: done` on failure), ARCH-4 (typed SSE `{log, progress, error, complete}` with `new_count` and error codes), PERF-1 (batch viewed endpoint), PERF-2/JS-6 (cache-first embed endpoint, negative cache, in-flight dedupe), PERF-7 (early pagination stop), PY-13 (reset-flag split), SEC-6/ARCH-8 (async OAuth with timeout — Gmail path only; the IMAP path already validates without blocking on human interaction), plus a small clear-checked-status-for-range endpoint for UXP-11.
