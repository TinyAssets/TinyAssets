## Why

The 2026-10-06 pi audit found parallel authoring paths for tools, hooks,
connections, MCP and UI. The bubble agent and its owner need one revisioned
extension, with one lifecycle and permissions that cannot grow through authoring.

## What Changes

- Define schema v2 `extension.json` for tools, hooks, commands, cards/UI,
  connection requirements and remote/stdio MCP declarations.
- Install immutable content, activate an exact owner/center/agent revision,
  discover and dispatch through `ta`, and revoke all contributions together.
- Reuse command-center packages for sharing and existing UI, connection and
  workflow stores as backends. Import never transfers activation or credentials.
- Supersede extension activation/hooks/cards work in
  `command-center-harness-control` (#4503/#4508), all `saved-agent-connectors`
  (#4511), MCP attachment lifecycle in `connect-anything-ladder` (#4496), and
  installed-directory resolution in `command-center-agent-templates` (#4515).
  Settings, generic transports and unrelated package UI remain their own work.
- Preserve today's jail for execution; consume U1's package-revision boundary
  before credential-scoped package execution or stdio server admission.

## Capabilities

### New Capabilities
- `one-extension-unit`: exact revision lifecycle and unified contributions.

### Modified Capabilities
None. Existing stores remain implementation backends.

## Impact

Owner: Codex, lane K1, branch `feat/one-extension-unit`, worktree `wf-K1`,
one draft PR to main. Affects extension validation/state, ta, package export,
on-demand handbook and focused tests. No resident prompt expansion, new model
tool handles, platform LLM or privileged launcher changes.
