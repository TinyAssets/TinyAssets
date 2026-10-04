# Final round-3 response to round-2 ADAPT — 2026-10-04

Review basis: `0697add7cf` on `spec/starter-agent-out-of-plumbing`. All five
DISAGREE findings are accepted and resolved in planning artifacts. This is the
final requested review round; no sub-agents, product implementation, runtime
trials or deployment were performed. Prior round-1 history follows below and
is superseded where the final resolutions refine it.

| Finding | Final resolution |
|---|---|
| N1 — DISAGREE_CONCERN | AGREE. Move the mapped resident guidance to separate editable `starter/hooks.md`. D10 installs this new path and five skills alongside untouched custom AGENTS.md; generic loading reads actual hooks before owner instructions, without depending on a skill-load decision. The note explains moved guidance and edit/delete/Undo controls and identifies any unavailable collision. Owner instructions win, hook deletion/Undo is preserved, and replacement agents load their own files. Require N>=10 paired write/recall trials in every model-family x stock/customized-AGENTS fixture cell, repeated in parent D7 with extraction off. |
| N2 — DISAGREE_CONCERN | AGREE. Require an explicit legacy-empty notice saying the instructions file is empty and previously ran defaults. Linked/unreadable cases name their observed condition and former fallback. Explain no runtime substitution, actual hooks/skills installed or unavailable, and owner controls; retain the diagnostic for dormant owners even if no file changed. |
| N3 — DISAGREE_EVIDENCE | AGREE. Retain "This turn has no tools available." in plumbing, derived from effective allowed_tools=() and tool_choice="none", regardless of custom/blank/replacement instructions or absent hooks. Move only response-priority advice. Require tool-less/tool-enabled checks and count the retained line in costs. |
| N4 — DISAGREE_CONCERN | AGREE. Starter task 2.3 alone owns consumer/renderer wiring and the all-center/dormant-center cutover proof. Seed-lifecycle task 3.1 supplies and contract-tests the prerequisite transaction API; its integration dependency and the parent's dependency point to that single owner. |
| N5 — DISAGREE_CONCERN | AGREE. Fix schema v1 in `<data>/.universe-sidecars/<canonical-center-key>/starter-seeds.sqlite3`. Specify binding, receipt/path keys, persistent owner choices/tombstones, scoped journal and exact byte blobs, transaction phases/idempotency, notice outbox and Undo references. All keys/queries and private-byte references bind authenticated owner and center, including recovery/Undo; no cross-owner blob deduplication. Define recoverable file/SQLite commit ordering and cross-owner/same-owner-other-center isolation acceptance. |

Final validation:

- Initial requested `git pull --ff-only origin spec/starter-agent-out-of-plumbing`: already up to date.
- `openspec validate starter-agent-out-of-plumbing --strict`: passed.
- `openspec validate starter-seed-lifecycle --strict`: passed.
- `openspec validate universe-agent-harness --strict`: passed.
- Task counts remain 10 starter, 9 seed lifecycle, 12 parent; implementation checkboxes unchanged.
- `git diff --check`: passed.
- `python -m ruff check .`: 59 existing findings in unchanged Python files; no Python/product files changed.
- Runtime reliability, storage/isolation and live proofs remain implementation acceptance work, not claimed results of this spec revision.

## Prior round-1 response

Review basis: `origin/spec/starter-agent-out-of-plumbing` at `5dfe7655ef`,
merge-base `26980f01db`. This revision preserves the interrupted worktree edits
and changes planning artifacts only. No implementation, deployment or measured
runtime savings are claimed. Founder direction supersedes the earlier
acceptance-gated migration: clean cutover, autonomous safe defaults and owner
control over command-center behavior.

| Finding | Resolution in this revision |
|---|---|
| F1 — DISAGREE_EVIDENCE | Accept. The section map, starter requirement and task 2.1 explicitly remove `read_operating_instructions` per-turn creation and every `DEFAULT_OPERATING_INSTRUCTIONS` fallback. Empty/deleted/linked/unreadable files never regain stock instructions. D10 alone provisions or upgrades seeds. |
| F2 — DISAGREE_EVIDENCE | Accept. Map `_GROUNDING_IS_CURRENT`, the ordinary-message suffix, the open-question curiosity line and `_addressed_agent_section`. Move workflow advice to editable seeds/skills; retain source metadata, selected-agent identity, instruction provenance, honesty and code disclosure filtering. Remove generated missing-instruction fallback. Count all retained fragments in the cost proof. |
| F3 — DISAGREE_CONCERN | Accept. D6 owns the seven on-demand handbook chapters as platform-versioned read-only API reference with executable examples. Seeds link through `ta describe`/skill references and never copy chapters. Owner skills/instructions take precedence over workflow guidance; users can replace their skills/docs/main agent within existing authority. Update parent D6 and appendix now. |
| F4 — AGREE | Preserve the intended deletion, but assign both removal deltas exclusively to parent D7. Delete extraction, its call chain and nag only after IDs/history/Undo and memory proof. The parent requirement preserves unresolved spans verbatim with source IDs, pending status and crash-safe replay. |
| F5 — AGREE | Preserve all mapped capabilities: memory, asking/GitHub, continuity, input-method advice, onboarding, clock, harness guidance and lazy summary/inventory reads. The map now distinguishes minimal resident hooks from movable recipes. |
| F6 — DISAGREE_CONCERN | Accept. Keep an editable resident memory trigger and refreshed factual founder-only timezone/local-time line; only detailed recipes move on demand. Require N>=10 paired natural fact-teaching trials per supported model family, immediate verified-write and later cross-surface recall rates each no worse than extraction-on baseline. Repeat with extraction off in D7. Add clock rollover/change/unknown-timezone and memory negative/failure cases. |
| F7 — AGREE | Preserve D6 reachability as a technical prerequisite. Keep working handles until replacement recipes work, coordinating their removal with the single global cutover. Owner acceptance no longer selects a runtime. |
| F8 — DISAGREE_CONCERN | Accept with founder-directed clean cutover. Automatically add proven never-installed paths and upgrade untouched/seed-identical files. Preserve customized, deleted or unverifiable paths with visible version offers. All centers, including dormant/non-reviewing owners, use the new renderer at one release boundary; record actual UTC cutoff and SHA. No grace period or legacy mode. One-click conditional file Undo stays on the new runtime. |
| F9 — DISAGREE_CONCERN | Accept. D10's sole `starter-seed-lifecycle` mechanism commits the same per-path installed-hash receipt for new provisioning and upgrades. Tombstones distinguish deleted from never-installed files. Legacy absence of previously seeded paths is conservatively preserved. |
| F10 — AGREE | Preserve conditional/hash-bound custom adoption, exclusive create, no-link traversal, byte-for-byte customization preservation, turn-boundary visibility and idempotent recovery. Extend conditional writes to automatic stock upgrades. File Undo preserves later edits, and its recorded choice overrides predecessor-hash matching on future upgrades. |
| F11 — AGREE | Preserve identity/honesty, untrusted envelopes, `disclosure_permits`/`permitted_grounding_files`, founder-only private sections and code consent/rule enforcement. Editing a user's own files cannot weaken another user's boundary. |
| F12 — DISAGREE_CONCERN | Accept. Explicitly retain nonce-delimited untrusted / NOT consent history with sanitized roles/names and the client-reported, informational, never-authority-or-consent input-method label. Parent renderer/helper removal is conditional on preserving those protections. |
| F13 — DISAGREE_CONCERN | Accept. Core plus four schemas <1,000 tokens is a D6 prerequisite. This slice separately targets starter/index/hooks <=1,000 tokens and >=50% non-tool resident reduction. Every fixture's full-task tokens must be <= D6-only per supported adapter, including skill loads, all model calls and matched extraction state; report HTTP/Codex/Claude separately and record calls. |
| F14 — DISAGREE_EVIDENCE | Accept. Correct proposal/design expectations to 9–13K movable characters / 2.5–3.5K tokens, about a 20–25% ceiling of the reported per-round total. D6 owns 30,096 tool-description characters / ~8.5–9K tokens. The non-resident 67,723-character handbook contributes no resident savings. Figures remain estimates until measured per adapter. |
| F15 — DISAGREE_CONCERN | Accept. `starter-agent-out-of-plumbing` owns content/rendering/cost proof (10 tasks); `starter-seed-lifecycle` owns D10 provisioning/upgrades/receipts/Undo (9 tasks); existing `universe-agent-harness` D7 owns learning retirement/backlog with its safety prerequisites (parent stays at 12 tasks). Parent design, deltas and task ownership are reconciled now. Future implementation uses separate worktrees/PRs and serialized merges. |

## Validation

- `git pull --ff-only origin spec/starter-agent-out-of-plumbing`: already up to date.
- `openspec validate starter-agent-out-of-plumbing --strict`: passed.
- `openspec validate starter-seed-lifecycle --strict`: passed.
- `openspec validate universe-agent-harness --strict`: passed.
- `python scripts/openspec_flow.py check-change <name> --provider codex --json`:
  allowed for both starter changes; audit reports unrelated existing queue warnings.
- Task counts: 10 starter, 9 seed lifecycle, 12 existing parent.
- `git diff --check`: passed.
- `python -m ruff check .`: 59 pre-existing findings in unchanged Python files;
  no product/Python files changed. The bare `ruff` executable was not on PATH.

Implementation task checkboxes remain unchecked; no runtime tests apply to this
planning-only revision. Future live and performance proofs remain acceptance
requirements, not evidence from this documentation edit.
