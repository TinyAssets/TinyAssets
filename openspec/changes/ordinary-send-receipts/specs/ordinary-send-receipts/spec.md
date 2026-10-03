## ADDED Requirements

### Requirement: Non-executable preparation and one initial start
The service SHALL issue a fresh server receipt ID bound to owner/home/agent and
immutable caller payload. Preparation SHALL NOT execute. Only a current-boot
PREPARED-to-STARTED transition SHALL permit the winning invocation to dispatch.

#### Scenario: Concurrent identical sends
- WHEN identical scoped receipt IDs arrive concurrently
- THEN at most one invocation receives permission and the others observe

#### Scenario: Changed payload or missing receipt
- WHEN a supplied receipt has conflicting payload or no exact stored row
- THEN it SHALL NOT dispatch, create a receipt, or enter consumer negotiation

#### Scenario: Lost preparation response
- WHEN preparation committed but its response was lost
- THEN retained custody remains held and SHALL NOT cause automatic new preparation

### Requirement: Preserved input and exact terminal projection
The service SHALL retain exact queued/steered input content, scope and identity
before claim or model exposure, including stale-open cleanup. Unknown delivery
SHALL NOT be requeued. Only exact terminal/history acknowledgement permits cleanup.

#### Scenario: Crash after take or settle
- WHEN model exposure or settlement may have occurred before durable terminal
- THEN original custody and attempt state remain held and SHALL NOT execute again

#### Scenario: Crash after history commit
- WHEN projection acknowledgement was lost
- THEN repair SHALL add no second pair and SHALL execute no provider/effect work

### Requirement: Lookup-only erased identities and ordinary privacy lifecycle
Caller-supplied receipt IDs SHALL always be lookup-only. Missing schemas SHALL
fail closed. Restored preparations from retired boots SHALL NOT dispatch.
Receipt/custody/projection content SHALL follow existing owner/session deletion;
replay protection SHALL NOT extend authorized content retention.

#### Scenario: Reset or account erasure
- WHEN receipt/content rows are erased
- THEN an old ID remains unknown and SHALL NOT become a fresh send

#### Scenario: Other owner's retained data
- WHEN one owner's rows are erased across home stores
- THEN another owner's content and receipts SHALL remain unchanged

### Requirement: Read-only scoped recovery
Recovery SHALL read exact receipts without dispatch, projection writes, migrations
or consumer selection. Missing status remains unknown. Legacy text matching SHALL
NOT settle a request. An ordinary ID SHALL be resolved before consumer routing.

#### Scenario: Changed thread during read
- WHEN login epoch, owner, home or agent changes before the read returns
- THEN its result SHALL NOT affect the new thread

#### Scenario: Consumer selected after ordinary send
- WHEN an ordinary receipt is presented after consumer selection changes
- THEN its exact stored ordinary identity is observed, never converted/rekeyed


### Requirement: Original issuing process across engine delivery
Engine delivery SHALL preserve existing serving-owner/scope checks and exact
session/live_id/root binding. It SHALL require positive observation of the original inherited self-pidfd AND
active serving-epoch channel, bound to the exact stored epoch by the trusted
launcher/bootstrap boundary. owner_state, lock contention/errors, numeric PID
reopening and engine BOOT SHALL NOT stand in for positive issuer identity. This observation SHALL NOT grant a start, takeover or terminal write.

#### Scenario: Separate engine process and dead issuer
- WHEN a separately running engine observes an open admitted root's issuer alive
- THEN exact bound inputs may be attempted once under existing scope checks
- WHEN that issuer is dead/unknown or its input frontier is closed
- THEN further delivery SHALL hold, preserving attempted/untouched inputs
- AND no observer SHALL gain another dispatch permission

### Requirement: Immutable event timestamps and exact projection links
An original client send timestamp SHALL remain immutable through prepare, send,
queue/steer transitions, recovery and history projection. Server preparation,
admission, terminal and first projection times SHALL remain distinct. Missing
original times SHALL remain unknown; terminal or rendering time SHALL NOT be
presented as original send time. Timestamp values SHALL NOT confer authority,
settle a receipt, match repeated text or authorize replay. Founder/failure pairing
SHALL use exact relationships, not timestamp equality.

#### Scenario: Completed failure followed by navigation
- WHEN an exact receipt projects a failure and the page reloads its history
- THEN the founder retains the original known send time and the failure its terminal time
- AND navigation SHALL NOT imply a different backend duration
