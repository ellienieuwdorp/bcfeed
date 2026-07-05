# bcfeed: product overview

What bcfeed is, who it is for, how it got here, and what it believes. Condensed from the full audit evidence base (2026-07-05); citations are file:line in this repo, screenshots in `docs/current-state/screenshots/`, and finding IDs from the verified audit.

## Goal

Bandcamp fans who follow many artists and labels get one "New release from X" email per release — unmanageable at volume in a Gmail inbox. bcfeed turns that email pile into a local, browsable, filterable, sortable release dashboard with inline Bandcamp players: a personal "new releases feed" Bandcamp itself doesn't offer (README.md:5-7).

Technically it is a local Flask server (server.py, 626 lines) launched from a CLI entrypoint that opens `http://localhost:5050/dashboard` in the default browser (bcfeed.py:22-51), serving a no-framework single-page app (dashboard.html 185 lines, dashboard.js 1724 lines, dashboard.css 838 lines). The "local database" is six JSON files in `~/Library/Application Support/bcfeed` (paths.py:22-27): release cache keyed by date, embed cache keyed by URL, viewed/starred sets, scrape-status ledger, empty-dates memo.

## Target user

A music lover with a Bandcamp fan account and a Gmail account. The docs address two personas — "power users (Homebrew already installed)" (SETUP.md:8) and "beginners" who are told what a Terminal is (SETUP.md:20) — but the realistic user is semi-technical: comfortable keeping a Terminal window open and able to survive the Google Cloud Console. The primary user is plainly the author; single-user, personal-use assumptions are everywhere ("Since this OAuth client is used **only by you**", GMAIL_SETUP.md:71; one global viewed/starred set; one populate at a time, server.py:45). Volume assumptions: heavy follower, hundreds of notification emails a month, hard cap of 2000 Gmail results per search (server.py:46).

## Core loop

1. Select a date range on the calendar (which doubles as a coverage/unread map — the app's best IA idea).
2. Click "Populate release list": Gmail search + email parse, with SSE-streamed progress into a status log (README.md:17-26).
3. Browse the table (Label/Page, Artist, Title, Date; dashboard.html:88-99). Expand a row to lazily load the Bandcamp embed player and scraped description/credits — or click "Preload release data" to prefetch in bulk, or star releases (starring triggers background preload) and browse via the Starred filter.
4. Mark seen / filter by label, unseen, starred. Keyboard triage shortcuts exist (arrows, s, u, Escape) but are documented nowhere (UX-14).

See `10-populated-dark.png` (populated table + calendar), `12-row-expanded-dark.png` (embed detail), `13-starred-filter.png`. Caching is aggressive and permanent: a populated range never needs re-populating, and — documented caveat — never *can* be re-populated except via destructive cache reset (README.md:30; UX-12). Bandcamp scraping is slow by design ("a few seconds per release", README.md:37-47), so the whole workflow section of the README is really a coping guide: preload, or star-then-browse.

## Development history: three eras

285 commits, 2022-05 → 2026-01. Author: TJ Hertz (keinobjekt); the two external PRs ever merged are Hank Jackson's (2024). This working copy's remote points at ellienieuwdorp/bcfeed with no tags.

**Era 1 — CLI script (2022-05 → 2024-09).** `BandcampSummary.py`: batch-download Gmail messages, emit static HTML pages of player widgets. Key reversal: 2022's "scrape the Bandcamp page for details" (7964240) was undone in 2025 (af03402) — the mature design scrapes cheap metadata from the email and defers expensive Bandcamp scraping to lazy/preload time. `credentials.json` was committed and removed twice in this era.

**Era 2 — App-ification burst (2025-12, 155 commits in one month).** After a 14-month gap the script became a product: the dashboard SPA, embed proxy, tkinter server GUI, settings modal (added mid-history, b69662c), session persistence, seen/starred state, rename to **bcfeed** (7c23b77), data moved to Application Support, first PyInstaller packaging attempts (abandoned), then a late-December UX blitz: calendar range panel, SSE-streamed populate, OAuth error handling, credentials management moved into the browser.

**Era 3 — Consolidation and distribution (2026-01, 77 commits).** Starred filter; static dashboard + runtime config instead of HTML regeneration; systematic per-file "code review done" commits; the 2323-line dashboard.html split into HTML/CSS/JS; v1.0 label in the header (dashboard.html:73); **tkinter GUI deleted entirely** (4c29b36) to retry packaging; Homebrew formula added then immediately moved to a separate tap (c0e73ab/0030854); docs restructured into README/SETUP/GMAIL_SETUP served in-app. Final commit (598a9dd, 2026-01-26) blocks selecting today in the calendar — reflecting the pervasive "today is never final" cache logic (session_store.py:74-78, 179, 198).

What churned most: the frontend monolith (100+ commits, whole-UI reorganizations); packaging (PyInstaller built and deleted, GUI built and deleted, formula in-repo then out); docs (renamed/split/rewritten repeatedly); naming (cached→populated, seen→viewed, Populate→Get releases→Populate). TODO.rtf has exactly one open item: "package in executable with installer".

## Distribution model

**Current channel: Homebrew tap.** `brew tap keinobjekt/bcfeed && brew install bcfeed` (SETUP.md:11-12). A `Language::Python::Virtualenv` formula with every dependency pinned as a resource stanza, sourcing the keinobjekt repo at tag v1.0-beta2. Consequences: the tap and this repo can drift; the UI version string is hardcoded, not derived; the formula uses python@3.11 while `.python-version` says 3.10.19 and SETUP.md:41 says "3.10 or newer"; requirements.txt is unpinned, duplicates `requests`, and uses the `bs4` shim (ARCH-9).

**Aspirational channel: standalone .app.** The TODO's sole open item. Two packaging campaigns failed; remnants survive as a `sys._MEIPASS` check in gmail.py:42-44 that would bundle an OAuth client secret into a distributed binary — contradicting the user-owned-credentials model — while the six assets that *would* need bundle awareness have none (paths.py:30-35).

**Structural constraint:** the product is a resident local server + browser tab, so every channel must solve "keep a process alive". Hence the thrice-repeated "keep the Terminal open" instruction (SETUP.md:17, 33, 49), the 5-second health poll with a restart modal (dashboard.js:1700, server.py:231-242), and the standing desire for a real .app. Data survives install/uninstall because it lives in Application Support.

## Fundamental principles

1. **Local-first / your data stays yours.** Everything on localhost, all caches local JSON, explicitly no analytics/telemetry/remote logging (privacy.md:5-13; README.md:57-66).
2. **Privacy via user-owned credentials.** No hosted service, no verified OAuth app: each user creates their own Google Cloud OAuth client (GMAIL_SETUP.md:3-4). Privacy is achieved by pushing infrastructure onto the user.
3. **Single user, personal tool.** No accounts, one global viewed/starred set, one populate at a time (server.py:45), "for your personal use only" (GMAIL_SETUP.md:105).
4. **Never re-fetch; cache aggressively and permanently.** Per-day scrape ledger, empty-range memoization, append-only release cache (session_store.py throughout; README.md:30). Corollary: today is never final — systematically excluded from being marked scraped.
5. **Be polite to upstream.** Batched Gmail fetches with 429 handling, max-results guardrails, lazy user-initiated Bandcamp scraping with 10s timeouts and a `bcfeed/1.0` UA (gmail.py:168-169, server.py:419-423).
6. **Practicality over polish or stack purity.** Vanilla JS, no framework, no build step, hand-rolled markdown renderer (server.py:81-145), honest low-promise docs ("It probably won't work on Windows, but feel free to try", README.md:52). No tests anywhere.
7. **Progressive de-scoping toward simplicity.** tkinter GUI deleted, HTML regeneration deleted, two calendars merged into one, duplicate endpoints collapsed. The codebase repeatedly got smaller on purpose.

## Tensions

Where the implementation contradicts the principles the project holds. These are the credibility gaps to close before (or alongside) any cosmetic work.

**1. Full-mailbox OAuth scope vs the read-only promise (SEC-3, high/confirmed).** GMAIL_SETUP.md:46-56 and SETUP.md:99-109 title the step "Add Gmail read-only API scope" and have the user paste `gmail.readonly`; privacy.md implies read-only behavior. But gmail.py:89 requests `https://mail.google.com/` — full read/send/delete — with an in-code comment saying exactly that. The consent screen shown to the user contradicts the setup doc they just followed. The app only ever calls `messages().list/get` (gmail.py:130-161), so the broad scope buys nothing. This is the sharpest privacy finding and a one-string fix — plus it over-provisions a token stored as unencrypted pickle with default file perms (SEC-5).

**2. `0.0.0.0` bind + `Access-Control-Allow-Origin: *` vs local-first (SEC-1, SEC-2, SEC-4).** "Everything runs on localhost" is the core promise, but the server binds 0.0.0.0 (server.py:247/256/259), exposing an unauthenticated API — credential upload (`/load-credentials`), credential wipe, cache wipe, and the `/embed-meta` open-URL fetcher (server.py:411-444, an SSRF proxy with no allowlist) — to the entire LAN. Every response carries `Access-Control-Allow-Origin: *` with no Host/Origin validation (server.py:148-152), creating a CSRF/DNS-rebinding surface from any website. Both fixes are cheap (bind loopback; drop ACAO:* and allowlist /embed-meta); until then the privacy posture is aspirational, not actual.

**3. The onboarding wall vs the beginner persona (UX-1, UX-2, UX-5, high/confirmed).** SETUP.md explicitly courts beginners who need "Terminal" explained — then requires them to create a Google Cloud project, enable an API, configure a consent screen, paste a scope URL, publish "In Production", download a client secret, and click through Google's "unsafe app" interstitial (GMAIL_SETUP.md:9-99; realistically 20-30 minutes with known failure modes — GMAIL_SETUP's own troubleshooting section exists because of them). In-app, the funnel dead-ends in a Settings panel of four mostly-destructive buttons (`03-settings-light.png`), the credential upload silently blocks on an OAuth browser flow the UI never announces (server.py:545 → gmail.py:115-116), and the first-run empty state reads "No releases match the current filter" (`02-empty-dashboard-light.png`). The principle of user-owned credentials (privacy) directly produces the biggest usability failure; the product brief's "usable without reading docs" bar cannot be met for first-run without a guided setup flow.

Lesser but real: "never re-fetch" turns transient failures into permanent silent data loss because days are marked scraped before releases are persisted (ARCH-2/PY-1); the docs call it "a macOS desktop app" (SETUP.md:3) when it is a local web app with a CLI launcher; the header says v1.0 while no release tag exists in this repo (ARCH-9); and there is no LICENSE file despite the formula declaring MIT and past external contributors.
