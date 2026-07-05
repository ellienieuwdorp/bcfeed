# bcfeed documentation

Produced by the full-project audit of 2026-07-05 (method: `audit/process.md`). Baseline commit `598a9dd`. No code was changed as part of the audit — these documents are the agreed starting point for implementation.

**Revalidation (2026-07-05, baseline `e363bf4`).** After the initial audit the repo merged the IMAP-provider PR (IMAP/email provider abstraction, gmail split into client/provider, extracted Bandcamp email parser, credential keychain storage, settings UI redesign, markdown-it-py renderer). All documents were re-verified against the merged code and re-baselined from `598a9dd` to `e363bf4`. Findings resolved by that PR are recorded in `known-issues.md`'s "Fixed upstream" section; new-code findings (`PY-16..18`, `SEC-10..11`, `UI-19`, plus `CQ-70..72` / `LOG-22..24` / `UIR-30` in the improvement plans) are integrated in place — there is no separate addendum.

## Reading order

1. **`current-state/product.md`** — what bcfeed is, who it's for, its principles, and where the implementation contradicts them.
2. **`current-state/architecture.md`** — modules, data flow, stores, concurrency, API, packaging (factual, no recommendations).
3. **`current-state/ui.md`** — the design language as it exists: three stratified generations, token inventory, theming breakages, accessibility state, the "AI smell" inventory. Screenshots in `current-state/screenshots/`.
4. **`current-state/known-issues.md`** — the canonical verified defect register (stable IDs `PY-* JS-* ARCH-* UI-* UX-* SEC-* PERF-*`). Every claim carries evidence; adversarially verified.
5. **`improvements/`** — one plan per area, each item with a stable ID, rationale, acceptance criteria, and size:
   - `ux-workflow.md` (`UXP-*`) — de-technicalization (full wording map, Status-log retirement), onboarding, one mental model for the core loop, flow and error-state fixes.
   - `architecture.md` (`ARC-*`) — typed SSE progress protocol, storage, layering, frontend decomposition, security hardening, packaging, test baseline.
   - `logic-pipeline.md` (`LOG-*`) — refresh semantics for the append-only cache, concurrent polite enrichment, identity/dedupe, scrape-ledger edge cases.
   - `code-quality.md` (`CQ-*`) — bug-fix batches, dead-code removal, test plan, lint/format, docs hygiene.
   - `ui-redesign.md` (`UIR-*`) — the chosen visual direction ("Calm Slate": system normalization, judged by a 3-lens panel) with a complete implementable token/component spec for both themes.
6. **`plans/implementation-plan.md`** — the master execution graph: 32 work packages (WP-03 split into 03a/03b) in 5 phases with hard dependencies, parallel lanes, per-WP acceptance criteria and verification, orchestration notes for agent-driven implementation, risk register, and a traceability appendix mapping every finding/item ID to its owning WP.

Agent-facing repo guidance lives in `../AGENTS.md` (imported by `../CLAUDE.md`).
