## Why
An external agent can authenticate to MCP or the API without appearing in the command center. Owners should see only real authenticated connections, without a separate introduction or seeded brand placeholders.

## What Changes
- Add an owner-only authenticated external-connection projection to the existing agents read and AppUI roster/presence.
- Observe successful authentication without storing credentials or granting new authority; unused API keys do not appear.
- Keep installed agents addressable and external connections distinct, with last-authenticated evidence and no invented online or delivery readiness.
- Remove revoked connections from the available roster and preserve genuine conversation history.

## Capabilities
### New Capabilities
- `authenticated-agent-roster`: automatic owner-visible MCP/API connection evidence.
### Modified Capabilities
None.

## Impact
Authentication metadata, API key use timestamp, owner agents read, AppUI bridge/shell, outside-authority database observation table and account erasure inventory. Private Roundtable presentation belongs to the event lane. Local implementation only; publication requires separate approval.
