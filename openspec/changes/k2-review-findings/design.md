## Context

K2 separates four model handles from signed backend authority, but ta's extension
adapter currently checks owner identity without checking that authority.

## Goals / Non-Goals

Close the three K2 findings. Keep the existing owner, consent, binding and effect
checks. No new web capability, storage schema, or deployment in this builder lane.

## Decisions

The dispatcher receives the verified backend grant. A single dispatch guard
requires a mutation grant for any capability not explicitly classified read-only.
Mutation grants are write, edit, bash, write_graph, write_brain, run_graph,
connect_compute or source_channel; they do not grant unlisted platform capabilities
or connections. Unknown extension contributions default to requiring mutation
authority, then retain their existing execution and connection checks.

Remove loop-owned history/activity routing. Their authorized equivalents already
exist as ta read_graph targets conversation and runs. Claude disables its native
inventory for served chat whether or not the engine grant exists; non-granted
Codex already constructs an empty definition.

## Risks / Trade-offs

Read-only extension contributions conservatively require mutation authority until
they have a reviewed read-only classification. Direct test fixtures must declare
their authority explicitly. Workflow nodes outside served chat keep their policy.

## Migration Plan

No migration. Rebuild the plugin, open a PR, and hand off review and deployment.
