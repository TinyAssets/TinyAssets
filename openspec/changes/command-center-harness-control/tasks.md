Implementation backlog only. D7 owns editor/history, D8/D9 own package/main selection, and starter-agent-out-of-plumbing owns renderer replacement; extend those paths rather than duplicating them.

## 1. Runtime settings

- [ ] 1.1 Implement v1 settings validation, sparse/default and legacy-model behavior with revision snapshots; verify malformed/duplicate/unknown fields fail visibly and package bindings remain local.
- [ ] 1.2 Wire one resolver into foreground/background turns, tool/skill selection, starter hooks and model/loop controls; verify next-turn changes, current revocation and no platform LLM fallback.

## 2. Extension mechanism

- [ ] 2.1 Extend existing manifests to v2 hooks with v1 tool-only support, pinned code revisions and jail execution; verify deterministic order and owner/center confinement.
- [ ] 2.2 Wire all six lifecycle events, tool replacement and owner-supplied loop calls through ta; verify failure/cancellation, no recursive hook dispatch and no uncertain-effect retry.
- [ ] 2.3 Add extension commands/cards with owner-content provenance and existing protected request references; verify no counterfeit decisions or receipts.

## 3. UI parity and acceptance

- [ ] 3.0 Add permission-bound subscribe/unsubscribe, snapshot/cursor replay and live activity/tool/approval/task/process events; verify dedupe, resync after retention/backpressure, account switching and revocation.

- [ ] 3.1 Expose shared ta search/describe/call through the existing bridge and installation permission binding; verify connections, memory, harness/history, rules and model/extension capabilities.
- [ ] 3.2 Implement revision-checked edits and exact-revision permission/frame invalidation, including recipient updates and explicit author/ceiling auto-update grants; prove cross-user mutations, unchanged-scope hostile updates, guessed IDs, link escapes, copied bundles, account switching, stale responses and revocation cannot cross bindings.
- [ ] 3.3 Coordinate D7/D8/D9 and starter acceptance: prove owner-controlled main replacement, hook disablement and model-independent recovery without duplicate installation or editor paths.
- [ ] 3.4 Keep test names; run affected/heavy tests on Windows and Linux 3.11 oracle, touched-Python ruff, plugin mirror for tinyassets edits and hygiene gate with 0 removed / 0 tampering.
- [ ] 3.5 Assert deployed implementation SHA, public canary and a real-user custom-UI settings/memory/tool/main-agent pass; sync only this capability and reconcile the parent delegation links.
