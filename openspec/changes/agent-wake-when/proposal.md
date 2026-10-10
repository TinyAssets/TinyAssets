## Why

An agent blocked on an external condition currently promises to retry but needs another user message. Let it save a bounded follow-up and resume automatically when the condition clears.

## What Changes

- Add owner-scoped `ta` wake registration, listing and cancellation; no model tool or provider-specific behavior.
- Reuse the protected request continuation worker and owner turn admission. Support time, persisted run/request state, connections, deployed release membership and bounded sandboxed probes.
- Show pending wakes and cancellation in the app; teach the editable starter skill to register a wake before promising a retry.

## Capabilities

### New Capabilities
- `agent-wake-when`: Durable bounded follow-ups for any owner agent.

### Modified Capabilities
None.

## Impact

Protected request storage, ta capability bridge, continuation worker, release-state publication, app controls and starter skill. Owner: Codex; branch: feat/agent-wake-when; one PR. Existing automation/event lanes supply reusable machinery and are not replaced.
