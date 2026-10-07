## Why

In a remote box an installed extension's tools, commands and hooks reported
`runtime_unavailable` and returned `extension_runtime_unavailable`: the box never
received the extension's files, so the engine never marked them mounted. Local ta
runs them. One agent shape everywhere means the same capabilities in a remote box.

## What Changes

- The trusted turn bridge fetches the owner's active, enabled revision blobs on
  the same signed engine session, verifies each blob's SHA-256 against its
  revision, and sends only package bytes to the box for one bash launch.
- The box worker stages them read-only in a launch-private directory; `ta`
  resolves `/ta/extensions/...` launches there. No host path, binding, grant or
  credential crosses.
- The host wraps every box message in an envelope naming the revisions it
  delivered for that execution; the engine dispatcher still re-checks owner,
  activity, generation, ceiling and settings before returning a launch.
- Delivery failure refuses the bash call before anything starts.
- Turn lifecycle hooks open the turn's own tool route, so a thin-loop turn runs
  them in its box.

## Capabilities

### New Capabilities
- `remote-box-extension-runtime`: installed extensions run in a remote box.

### Modified Capabilities

None. Public MCP inventory, provider inventories and local jail are unchanged.

## Impact

Owner: Claude. Branch: feat/remote-box-extension-runtime. One PR to main.
Affected: `agent_loop/box_ta*.py`, `box_tools.py`, `tool_session.py`,
`extension_capabilities.py`, `extension_hooks.py`, `ta_cli.py`, Linux proofs.
