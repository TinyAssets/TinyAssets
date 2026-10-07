# Historical Documentation Archive

This folder holds project artifacts that captured architecture intent, build
plans, or implementation summaries from earlier project phases. Each document
already has a `**HISTORICAL — superseded.**` banner at the top.

These are kept for git/decision history value. **Do not edit, do not extend,
do not cite as live.** Current architecture lives in `PLAN.md`.

## Why these moved here (2026-05-06)

Per [[cowork-project-folder-sweep-cleanup-proposal-2026-05-06]]:

The project root accumulated ~10 superseded artifacts over the past 2-3 months
of architecture iteration. They were already self-labeled as historical, but
their root-level placement created visual clutter for new sessions and made it
harder to spot the canonical orientation files (`AGENTS.md`, `CLAUDE.md`,
`PLAN.md`, `STATUS.md`, `README.md`).

Moving them to `docs/historical/` preserves the git-history value, signals
"do not cite as live" via path more strongly than via banner alone, and frees
the project root for the live orientation artifacts.

## Contents

- `BUILD_PREP.md` — captured arch intent as of 2026-03-31
- `RESTRUCTURE_PLAN.md` — captured arch intent as of 2026-04-05
- `ARCHITECTURE_PLAN.md` — companion to BUILD_PREP, pre-PLAN.md era
- `PHASE_3_5_6_IMPLEMENTATION.md` — Phase 3.5/3.6 implementation summary, 2026-04-06
- `IMPLEMENTATION_SUMMARY_PHASE_3.md` — Phase 3 summary
- `AGENTIC_SEARCH_RESEARCH.md` — research doc, predates current architecture
- `IMPORT_COMPATIBILITY.md` — import migration tracking
- `PHASE_3_FILES.txt` — Phase 3 file index
- `VAULT_GUIDE.md` — early vault guide
- `INDEX.md` — early root index
- `REFLECTION.md`, `OUTREACH_CONTENT_ENGINE.md`, `WORKFLOW_DESIGN_HANDOFF_FOR_POLSIA.md` — root notes moved here 2026-10-06

## Reading order if you need historical context

1. `PLAN.md` (root) — current architecture
2. `RESTRUCTURE_PLAN.md` (here) — pre-PLAN.md design rationale
3. `BUILD_PREP.md` (here) — implementation guidance from 2026-03-31
4. `IMPLEMENTATION_SUMMARY_PHASE_3.md` + `PHASE_3_5_6_IMPLEMENTATION.md` (here) — what got built in Phase 3 era
