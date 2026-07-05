# Revalidation — July 2026 (`598a9dd` → `e363bf4`)

Record of how the July-2026 audit (`audit/process.md`, baseline `598a9dd`) was re-verified after the repo fast-forwarded to `e363bf4`, which merged the IMAP-provider PR. The original audit changed no code; this revalidation changed no code either — it re-checked every finding against the merged tree and re-baselined the documents. No separate addendum was produced: corrections are folded into the existing docs in place.

## Commit range

- **From:** `598a9dd` (original audit baseline)
- **To:** `e363bf4` (merge of `feature/imap-email-provider`, PR #1)
- **Diff stat:** +2724 / −464 across 21 files.
- **What the merge did:** IMAP/email-provider abstraction (`email_provider.py`, `imap_client.py`, `imap_provider.py`, `provider_factory.py`); `gmail.py` split into `gmail_client.py` + `gmail_provider.py`; `bandcamp_email_parser.py` extracted; keychain credential storage (`credential_store.py`, `keyring`) replacing the `token.pickle`; settings-UI redesign (dashboard.html/js/css grew substantially, provider controller appended to `dashboard.js`); markdown-it-py + `templates/docs.html` replacing the hand-rolled markdown renderer; an empty-email-body crash fix; and stricter IMAP parsing.

Inspect with:

```bash
git -C . log 598a9dd..e363bf4 --oneline
git -C . diff 598a9dd..e363bf4 -- <file>
```

## Method

No independent live-app / screenshot harness re-run beyond the post-merge captures noted below; this was a targeted document revalidation, not a fresh full audit.

1. **Change survey.** Read `git log 598a9dd..e363bf4` and per-file diffs to enumerate exactly what the merge touched, so every finding could be classified as unaffected, fixed, or changed.
2. **Per-finding re-check.** Every finding in `current-state/known-issues.md` was re-checked against the current files at `e363bf4` and its `file:line` evidence refreshed. Findings the merge resolved were moved to a new **"Fixed upstream (e363bf4 IMAP merge)"** section (IDs reserved as history, original `598a9dd` evidence preserved). Findings the merge altered but did not resolve carry an inline **"Changed at e363bf4"** note describing what moved; still-valid findings got refreshed line numbers (dashboard.js refs past line 60 shifted +3, plus the appended provider controller at 1706-2093).
3. **New-code audit.** The merged provider / credential-store / settings code was audited as new surface (the email-provider interface and its Gmail/IMAP implementations, `provider_factory`, `credential_store`, the two new HTTP routes `/provider-config` and `/imap/discover`, the redesigned Settings panel, and the markdown-it-py docs path). New findings were assigned fresh IDs and appended to their sections.
4. **Spot-checks of prior high-severity items.** Confirmed SEC-3 (full `https://mail.google.com/` Gmail scope) still present at `gmail_client.py:217`; SEC-1 (`0.0.0.0` bind) still present at `server.py:186` and the `__main__` `app.run` at `server.py:822`; the hand-rolled markdown renderer confirmed replaced by markdown-it-py; the `token.pickle` confirmed replaced by keychain storage, leaving only a one-time legacy migration path (new SEC-10).

## Outcome summary

### Fixed upstream by the merge
Recorded in the "Fixed upstream (e363bf4 IMAP merge)" section of `known-issues.md` (IDs reserved as history): the hand-rolled markdown renderer / raw-HTML injection concern (SEC-8), the `token.pickle` plaintext-token storage (SEC-5, now keychain), the empty-email-body populate crash, the `/clear-credentials` no-op, and the duplicate `requests` in `requirements.txt`. Several other findings were partially resolved and stay open with "Changed at e363bf4" notes (e.g. PY-2 downgraded high→medium, UX-1/UX-18 eased by the redesigned Settings panel, JS-13/UX-18 theme toggle now exposed).

### Changed / partial (still open, re-scoped)
Numerous findings narrowed but survived — e.g. PY-1 (mark-before-persist ordering kept), PY-6/util.py date-crash (narrowed by a new `allow_none` param that the crash sites do not use), CQ-1 dead code re-scoped, ARCH-4/JS-10 SSE terminal-event protocol defect intact, and the security cluster SEC-1/SEC-3/SEC-4 all still present with a widened mutating surface.

### New findings from the merged code
- **Known-issues register:** `PY-16`, `PY-17`, `PY-18` (provider-blind scrape ledger; mis-selected IMAP folder / weak SEARCH silently records empty days; unparseable IMAP `Date` headers yield `date:None` releases that crash dedupe); `SEC-10`, `SEC-11` (legacy `token.pickle` `pickle.load` migration sink; `/provider-config` + `/imap/discover` extend the unauthenticated mutating / outbound-connection surface); `UI-19` (the appended settings/provider styles are a third styling generation with fresh token violations).
- **Improvement plans:** `CQ-70..72` (code-quality), `LOG-22..24` (logic-pipeline), `UIR-30` (ui-redesign) — the plan-side counterparts of the above.

## Documents re-baselined to `e363bf4`

- `README.md` — added the revalidation note.
- `current-state/known-issues.md` — revalidation header, "Fixed upstream" section, per-finding "Changed at e363bf4" notes, refreshed evidence, new PY-16..18 / SEC-10..11 / UI-19, and updated "explicitly cleared" entries.
- `current-state/product.md`, `current-state/architecture.md`, `current-state/ui.md` — re-verified and updated for the provider abstraction, credential store, and redesigned Settings surface.
- `improvements/code-quality.md`, `improvements/logic-pipeline.md`, `improvements/ui-redesign.md` — integrated CQ-70..72 / LOG-22..24 / UIR-30 in place.
- `plans/implementation-plan.md` — traceability updated for the new IDs (WP-03 split into 03a/03b; 32 work packages).
- `current-state/screenshots/` — post-merge captures added (`20-postmerge-*`, `22b-postmerge-settings-panel-{light,dark}`, `23-postmerge-provider-{gmail,imap}`); shots numbered ≤19 predate the settings redesign.

Evidence `file:line` references throughout the re-baselined docs are to the repo at `e363bf4`; the "Fixed upstream" section preserves the original `598a9dd` citations.
