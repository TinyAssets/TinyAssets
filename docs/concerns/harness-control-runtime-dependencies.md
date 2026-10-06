---
severity: P2
title: Harness control continuation lacks required runtime integrations
filed: '2026-10-05'
summary: K1 implements roster resolution, automatic extension hooks and UI projections; stdio package admission and the separate editable starter loader retain their named owners.
---

Consolidation: [`one-extension-unit`](../../openspec/changes/one-extension-unit/proposal.md)
now owns extension activation, hooks/cards and installed-directory resolution.
Do not continue parallel authoring paths in the source changes. U1 owns package
process admission; the starter lane retains its editable starter hook loader.

Observed on `ae7790a388` after #4503 merged. That slice completed only
`command-center-harness-control` task 1.1. Its design and review already explain
the task 1.2 dependency; this is the continuation handoff, not a request to
reimplement those owners' paths.

| Required integration | Owner and concrete handoff |
| --- | --- |
| Authenticated installed roster directory | Implemented in K1 / #4519 with completed recipient-pin resolution and isolation tests. |
| Editable starter hook loader | starter-agent-out-of-plumbing task 1.3: supply the loader consumed by the settings toggle; retain that lane's extraction/main-replacement ownership. |
| Raw-key stdio custody | connect-anything-ladder task 1.4: separate process/user/filesystem sandbox, exact-revision owner opt-in, broker-scanned stdout and stderr, cancellation and revocation. Harness task 2.1 must prove activated hooks cannot inspect it. |

L5 retains the executable recipient-update extension: current
`command_center_update_policy.POLICY` is presentation-only and
`presentation_decisions()` refuses code changes. Implement exact-revision
activation and explicit author/ceiling grants through that infrastructure;
never reinterpret an existing presentation policy as executable permission.

The L5b boundary patch rejects unsupported manifests instead of executing v2
tools as v1. It does not establish activation, pin code, add lifecycle dispatch,
or finish task 2.1. All subsequent requested boxes remain unchecked. Resume in
order with the actual integrations, then perform Linux hostile-hook/stdio and
recipient-update proofs, the remaining UI/orchestration work, review and live
acceptance. Delete this concern once the integrations and their proofs exist.


K1 continuation: automatic extension hooks and app_ui projection are implemented
and reviewed in #4519, with local connection bindings, remote MCP and git on the
current broker. The older runtime-unavailable paragraph above describes the L5b
baseline. It no longer describes K1's extension contributions. This concern stays
open only for U1 stdio/package-cell proof and the separately owned starter loader.
