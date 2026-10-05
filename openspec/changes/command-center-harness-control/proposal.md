## Why

Audit L11–L13 found packaged settings that runtime ignores, tool-only extensions, and a custom-UI bridge that cannot operate the owner's harness. Founder direction (2026-10-04) makes the owner king of the command center, including the main agent, with cross-user isolation as the only platform invariant.

## What Changes

- Read versioned `settings.yaml` at runtime for model selection, exposed tools, skills, extensions, starter hooks and loop behavior.
- Extend the existing extension mechanism with lifecycle hooks, tool replacement, commands and inline cards; no second plugin runtime.
- Add owner-permitted `ta` capability discovery/dispatch to the existing custom-UI bridge, including connections, memory and harness files.
- Keep authentication and execution scoping enforceable while owner behavior and approval policy remain editable; no platform LLM or immutable starter/main-agent policy.

## Capabilities

### New Capabilities

- `command-center-harness-control`: settings snapshots, loop extension protocol and owner-permitted UI/ta parity.

### Modified Capabilities

None. This supplies the missing D7 runtime contract and extends D6/`composable-ui-experiences` through their existing mechanisms. It does not replace agent-model-selection, package installation or inline approval requirements.

## Impact

Planning owner: Codex. Branch: `spec/muse-pi-gap-proposals`; baseline `origin/main` at `9e96ff9595`. Draft only, no product code or PR. Future implementation is one separate lane/PR.

Expected integration: `universe_intelligence.py`, `universe_tools.py`, `ta_cli.py`, `ta_capabilities.py`, `ui_frame.py`, `app_ui.js`, harness history and package settings. Coordinate UI changes with the app lane; this branch does not touch `tinyassets/onboarding/app.html`.

Dependencies: `starter-agent-out-of-plumbing` owns instruction extraction/main replacement proof; D7 owns memory/history/editor and learning retirement; D8/D9 plus `command-center-agent-templates` own roster/package installation, quarantine and main selection; `composable-ui-experiences` owns renderer isolation; `inline-connect-and-approve` owns interactive decisions. These responsibilities stay there.

## Orchestration blocking gaps (2026-10-04)

Source: [comparative analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md), C1–C24 and T1–T10. This change owns hooks/runtime settings and adds ta-parity event subscriptions for live screens. `resident-agent-processes` owns persistent boxes, resident gateways and heartbeats; `agent-team-channel-templates` owns spawn/A2A/task-board composition and publishable channel templates. `connect-anything-ladder` owns MCP/secret entry/OAuth; `browser-login-custody` owns D5-gated login custody; `saved-agent-connectors` owns tested extensions. This keeps each change at or below twelve tasks.

Follow-up ownership: `command-center-packages` owns publishing/versioned releases, `command-center-recipient-updates` owns opt-in versioned updates to installed copies with local edits preserved, and `creator-revenue-share` plus existing attribution own remix lineage/credit and usage revenue. Require changelogs, conflict/Undo behavior, source and remixer credit and local credential rebinding in those lanes; no second updater, marketplace or ledger here. Their completion is required where T1–T10 calls for published second-account copies, and T7 revenue/T8 cross-user subscriptions remain unmet until their owning lanes pass.

## Follow-up review and verification (2026-10-04)

One cross-family Claude review through peer-agents returned ADAPT; the review covered all eight changed proposal directories. DISAGREE_EVIDENCE (round 2): the earlier unknown-effect refusal and immutable once-only spend restriction contradict owner control; replace them with trusted owner classification, editable unknown-effect defaults and optional budget-capped spend grants. AGREE: once receipts live in ordinary activity history after panel removal. AGREE with the budget-timing clarification: an approval sheet holds no funds, reservation occurs with the approved dispatch intent, and insufficient cap means visibly blocked with no payment credential/card issued. These corrections are in the requirements, designs and existing task boxes; no extra review round.

Proposal verification: all eight changes pass strict OpenSpec validation (`--type change` disambiguates the active inline change from its as-built spec); 271 focused OpenSpec-flow, hygiene-gate and concerns-index tests pass; concerns index and diff whitespace checks pass. All changes have at most 12 task boxes. Both research payloads are byte-identical to the supplied files after removing the new date/purpose header. Repository-wide Ruff reports 58 findings in unchanged Python files; this follow-up contains only Markdown and OpenSpec YAML. Test hygiene reports 0 removed / 0 tampering and no product code. No new concerns, product implementation, PR, deployment, live pass or as-built spec sync is claimed.
