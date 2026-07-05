# bcfeed documentation

Produced by the full-project audit of 2026-07-05 (method: `audit/process.md`). Baseline commit `598a9dd`. No code was changed as part of the audit — these documents are the agreed starting point for implementation.

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
6. **`plans/implementation-plan.md`** — the master execution graph: 31 work packages in 5 phases with hard dependencies, parallel lanes, per-WP acceptance criteria and verification, orchestration notes for agent-driven implementation, risk register, and a traceability appendix mapping every finding/item ID to its owning WP.

Agent-facing repo guidance lives in `../AGENTS.md` (imported by `../CLAUDE.md`).
