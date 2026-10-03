## ADDED Requirements

### Requirement: Exact ordinary request identity
The service SHALL bind each ordinary request key to authenticated owner, current
home, addressed agent and canonical caller payload before consuming input or work.
Identical repeats SHALL observe the existing receipt; changed payload SHALL reject.

#### Scenario: Duplicate concurrent send
- WHEN two identical scoped keys arrive concurrently
- THEN exactly one initial invocation may execute and both refer to one receipt

#### Scenario: Uncertain crash
- WHEN acceptance or effects may have occurred without a committed terminal
- THEN recovery SHALL hold uncertainty and SHALL NOT execute the request again

### Requirement: Preserved input and exact terminal projection
The service SHALL preserve the identity/content of claimed queued and steered
inputs across every custody, terminal and history crash boundary. Terminal history
SHALL be idempotent by receipt identity and exact row IDs, never by text/time.

#### Scenario: Crash after history commit
- WHEN history committed but its receipt projection acknowledgement did not
- THEN projection repair SHALL add no second pair and SHALL execute no work

### Requirement: Read-only scoped recovery
The owner app SHALL read exact receipts without dispatch, projection writes or
schema migration, fenced to owner/home/agent/key. Missing status SHALL remain
unknown. Legacy unkeyed messages SHALL NOT be settled from matching history text.

#### Scenario: Changed thread during read
- WHEN owner, home, agent or login epoch changes before a read returns
- THEN the old result SHALL NOT affect the new thread
