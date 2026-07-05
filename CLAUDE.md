# CLAUDE.md

@AGENTS.md

The file above is the canonical agent guidance for this repo (project overview, layout, run instructions, data-store schemas, API surface, conventions, gotchas). Keep the two files in sync by editing `AGENTS.md` only.

Claude-specific notes:
- Run the app with `.venv/bin/python bcfeed.py --no-browser`, then hit `http://localhost:5050/dashboard`. Frontend edits need only a browser reload; backend edits need a server restart.
- For UI verification, screenshot with Playwright against mock JSON stores (see "Mock data for UI work" and "Verifying UI changes" in AGENTS.md).
- Improvement plans and audit findings live in `./docs/` — check `docs/plans/implementation-plan.md` before starting redesign or refactor work so you build on the agreed direction.
