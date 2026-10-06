## Why

The guided OpenRouter connection succeeds but the new user's first greeting fails
with `engine_tools_unavailable`: a historical vetted-universe allowlist prevents
ordinary serving universes from receiving engine tools. Any authorized provider
must be able to operate the user's own universe without operator enrollment.

## What Changes

- Replace vetted membership with current serving-binding, admin-ACL and
  non-deleted-principal checks at route publication, route reading and tool entry.
- Preserve per-universe processes, bearer secrets, graph pins, consent, sandbox,
  effect admission and canonical handler permissions for every provider.
- Remove the obsolete engine allowlist configuration; retain the separate source
  approval control and deployment kill switch.
- Share a bounded pre-protocol startup wait across HTTP foreground/background
  turns, without retrying MCP discovery, tools or conversation effects.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `universe-personification-and-relay`: owner-bound general engine-tool admission
  for foreground conversations and authorized background turns.

## Impact

Engine HTTP route supervisor/reader, engine handlers, environment workflow and
authority regression tests. No credential, storage schema, provider choice,
public MCP handle or user workflow changes.

Owner: Codex. Branch: `codex/http-engine-tools`. One PR, pending.
Necessary uptime dependency of guided OpenRouter first-reply acceptance; other
builders proceed independently under the owner's coordinated-push directive.
The second test account stays on hold until this and in-app connection removal
are ready. Acceptance requires a deployed, rendered free-user
reply, read/write tool use and cross-user refusal, not merely an OAuth return.
