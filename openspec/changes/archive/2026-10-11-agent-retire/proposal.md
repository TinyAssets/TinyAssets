## Why

Owners and their agents cannot remove leftover agents from a command center (#4521). A reversible binding operation should hide and stop an agent while preserving its history and files.

## What Changes

- Add owner-scoped, revision-fenced `retire` and `restore` operations to `write_graph target=agent_binding`, also reachable through ta.
- Exclude retired agents from addressing and the existing switcher; stop their active work with an explicit reason.
- Refuse retirement of main; retain retired bindings for account deletion and restoration.

## Capabilities

### New Capabilities
- `agent-retire`: reversible lifecycle for command-center agent bindings.

### Modified Capabilities
None.

## Impact

Binding storage and graph adapter, addressed-agent admission, active turns and automation, existing command-center roster, on-demand handbook, tests. No feature editor or always-sent prompt additions.
