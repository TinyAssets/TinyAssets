## Why

Founder direction (2026-10-04): minimal, replaceable plumbing and an editable starter, including a replaceable main agent. Reviewed movable starter text totals roughly 9-13K characters, about 2.5-3.5K tokens: a ceiling of about 20-25% of the reported 13-15K resident tokens per round, not 70%. D6 separately owns 30,096 characters of served tool descriptions (roughly 8.5-9K tokens). These estimates require per-adapter reproduction before claims of savings.

## What Changes

- Move resident starter behavior into editable `starter/hooks.md`, loaded alongside untouched custom `AGENTS.md`, and five on-demand skills; announce these additions visibly. Retain factual clock and no-tools context in plumbing.
- Map all resident fragments, remove per-turn AGENTS.md reseeding/default substitution, and preserve cross-user and untrusted-data plumbing.
- **BREAKING:** switch every center to the new renderer using D10's single starter-seed-lifecycle mechanism. Stock files upgrade automatically; customized files stay intact with a visible offer. No owner review gates cutover and no compatibility renderer remains.
- Solely own the renderer cutover wiring and all-center/dormant-center proof; depend on seed-lifecycle's transaction mechanism.
- Prove resident savings and full-task cost against D6-only per adapter, plus memory write/recall non-regression per supported model family on both stock and preserved-custom-AGENTS fixtures.

## Capabilities

### New Capabilities

- `starter-agent-files`: editable starter content, complete section mapping, resident-cost and capability acceptance.

### Modified Capabilities

None. Learning extraction and both learning-removal deltas belong exclusively to parent universe-agent-harness D7, after memory IDs, history and Undo. Seed receipts, automatic upgrades and file Undo belong exclusively to starter-seed-lifecycle (D10).

## Impact

Design only; no product code. Future work touches prompt assembly in universe_intelligence.py, onboarding_note.py, _HARNESS_HEAD in universe_tools.py, starter content and converse-cost tests. D6 owns tool schemas/discovery and the platform-versioned handbook; seeds link to those references, never copy the handbook.

One intent: move resident starter policy into owner-editable files while preserving behavior and reducing cost. Parent ownership is reconciled in this revision, not deferred to sync. D7 removal is independent of renderer selection and contributes no savings claimed by this slice. D10's seed mechanism is a prerequisite reused here, not reimplemented here.

Owner: Codex. Branch: spec/starter-agent-out-of-plumbing. This is the requested planning revision; no implementation or PR is opened. Future implementation lanes remain separate and merge serially.
