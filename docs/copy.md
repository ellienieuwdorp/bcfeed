# bcfeed copy & vocabulary reference

Canonical wording rules for every user-visible string in bcfeed — UI labels,
buttons, tooltips, toasts, banners, status lines, empty states, the SSE
`message` field (which the Details log shows the user), and the docs
(README / SETUP / IMAP_SETUP / GMAIL_SETUP). Established by WP-26 from the
UXP-1 wording map.

**Tone target:** a calm, reliable local utility a semi-technical music fan can
use without reading docs. Sentence case everywhere; no ALL-CAPS microcopy.

## What counts as user-visible

A string is user-visible if a person using the app or reading the docs can see
it: rendered DOM text, `title`/`aria-label` attributes, `placeholder`s, toast /
banner / status text, the SSE `message` line (shown in the Details disclosure),
and any error body returned to the browser.

**Exempt (keep internal names):** code identifiers — variable / function names,
CSS class names, element `id`s, `data-*` attributes, JSON payload keys, SSE
protocol `phase` enum values (`query|download|parse|persist|cache|enrich`),
route paths, `console.*` developer logs, and `app.logger` server-log lines.
These never reach the user and may keep their technical names.

## Banned → preferred

| Banned term | Use instead |
|---|---|
| populate / populating / "Populate release list" | **Get releases**, check(ed) for releases, "Checking for releases…" (running button) |
| preload / preloading | load the player(s) / release details (background loading) |
| cache / cached / CACHED | saved, ready, loaded — or say nothing; the badge text is **Saved** |
| scrape / scraped | check(ed) |
| parse / parsing | read / reading (emails) |
| query / querying | search / searching (your mail) |
| token | connection / sign-in |
| credentials | Google **access file** (the JSON), Gmail **connection** / **sign-in** (auth state) |
| provider (the abstraction / a settings label) | connection, or the concrete **Google** / **IMAP** |
| configuration | settings ("Save mail settings", not "Save IMAP Configuration") |
| embed | player |
| proxy | (never user-visible — say "bcfeed") |
| secure storage / keychain backend | your Mac's Keychain |
| "days" as scrape units | dates / dates checked |

## Rulings

- **`IMAP` is allowed.** It is the term the user's own mail service uses in its
  settings and docs, so hiding it would hurt recognition. "Mail server (IMAP)"
  is the preferred framing.
- **"email provider" / "mail provider" / "most providers" are allowed** *only*
  in the real-world sense of "the email service you use" (Gmail, iCloud,
  Fastmail…). This is the user's own word for their mailbox. What stays banned is
  `provider` as the internal abstraction or a settings label — that became
  **"How bcfeed reads your email"**. When in doubt, prefer "email service".
- **`provider` / `configuration` / `credentials`** remain banned in favor of
  **connection** / **settings** / **access file**.

## Control-name map (UI ↔ docs must match)

| Old name | Current name |
|---|---|
| Populate release list | Get releases |
| (fully-checked, disabled) Release list populated | Check again |
| Preload release data | (removed; players load in the background) |
| Email Configuration (settings section) | Email connection |
| Provider (settings label) | How bcfeed reads your email |
| Gmail API (OAuth) | Google sign-in (Gmail only) |
| IMAP | Mail server (IMAP — most providers) |
| Save IMAP Configuration | Save mail settings |
| Folder To Scan / Manual Folder Name | Folder to scan / Manual folder name |
| Load credentials file / Clear credentials | Connect Gmail… / Disconnect Gmail |
| Clear cache & reset database | Delete downloaded data… |

## Dates

Dates shown to users are always **inclusive** calendar dates that match the
calendar selection exactly — never the end-exclusive query bounds used
internally. Prefer "these dates" over echoing a raw range when the range is
already visible on the calendar.

## Disabled controls

Every disabled control's `title` states, in plain language, *why* it is disabled
and *what would enable it* (e.g. the Save-mail-settings button: "Load your
folders and choose one, then you can save your mail settings.").

## Enforcement

`tests/test_wording.py` is a grep-guard over the user-visible surfaces listed
above. It fails if a banned term reappears in a curated user-facing string, with
a documented allowlist for the real-world "email provider" sense and `IMAP`.
