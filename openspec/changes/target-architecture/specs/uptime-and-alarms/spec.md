## ADDED Requirements

### Requirement: Per-command-center owners behind replaceable frontends; deploys fail no requests

Each command center SHALL have at most one execution owner at a time, under
that command center's own lease, fenced by generation. That owner covers the
command center's agent loop and its turn journal writer and reconciliation.
The scheduler, triggers, the outbox pump, metering aggregation and the storage
allocator SHALL run under one platform lease.

Every owner-side mutation SHALL re-check its command center's fence inside its
own transaction. Box and broker effects SHALL carry `(command_center,
generation)`, and SHALL be refused below that command center's fenced
generation. Every turn row SHALL record the command center and the generation
that created it. Reconciliation SHALL run only after the command center's lease
is acquired, and SHALL settle only that command center's rows of older
generations.

Per-account seats and host admission SHALL live in shared stores, not in an
owner process's memory. Frontends SHALL route each turn start and cancel
through a map from command center to its current owner and lease generation.
A handover of one command center SHALL NOT make any other command center's
requests wait. A command center's lease SHALL be released only in the
transaction that observes it idle. A busy command center SHALL keep its current
owner until its in-flight turn ends; no automatic time bound SHALL interrupt
it. Only an explicit operator force SHALL stop an owner with running turns, and
those turns SHALL reconcile into a visible held state, never replayed. An alarm
SHALL fire when an old owner process outlives a threshold (default two hours).
For each command center, at most two owner generations SHALL coexist: a further
deploy SHALL wait for that command center, leaving it on its current owner,
instead of starting a third.
The user SHALL see that an update is pending for their command center until
its key moves. Frontends SHALL hold no turn ownership, and SHALL be replaced
blue-green. While the owner hands over, frontends SHALL queue requests rather
than fail them. A handover SHALL move each command center at its idle instant
and SHALL affect no other command center. A turn still running at the
bound SHALL reconcile into a visible held state, and SHALL NOT be replayed. A
frontend-only deploy SHALL interrupt no turn. Schema-changing cutovers
SHALL be declared maintenance windows under the cutover exclusion protocol.

#### Scenario: A frontend deploy during a chat turn
- **WHEN** only the frontends are deployed while a user's turn is streaming
- **THEN** the old frontend keeps the stream until it ends, the turn completes, and no request fails

#### Scenario: A long turn keeps its owner during a deploy
- **WHEN** an owner deploy starts while command center A has an hour-long turn running
- **THEN** A stays on the old owner until that turn finishes, every idle command center moves at once, and no turn is interrupted unless an operator forces it

#### Scenario: A standby successor does not misjudge live turns
- **WHEN** a successor owner starts in standby, the old owner then creates a turn, and the old owner dies
- **THEN** the successor, after acquiring the lease at a higher generation, settles that turn as interrupted, because its generation is older

#### Scenario: One user's long turn does not delay another user
- **WHEN** an owner deploy is in progress while command center A has a long turn running, and a request for command center B arrives
- **THEN** B's request is served without waiting for A

#### Scenario: A third owner generation is refused
- **WHEN** a deploy starts while command center A is still busy on the previous-but-one owner generation
- **THEN** A waits on its current owner rather than starting a third generation, the lingering-owner alarm has fired after its threshold, and A's owner sees the update-pending status

#### Scenario: A stalled old owner cannot write
- **WHEN** an old owner resumes after the new owner acquired the lease at a higher generation
- **THEN** its next mutation is refused

### Requirement: A fenced warm standby in a second region

A standby cell and box host SHALL run in a second region or provider. They
SHALL restore platform state continuously, keep their tunnel connectors
stopped, and restore box disks from off-region backups on first wake.
Promotion SHALL first fence every primary execution host, meaning the cell host
and any separate box host, by powering them off and blocking auto-restart
through a credential held only by CI. Only then SHALL it start the standby. If
fencing cannot be confirmed, promotion SHALL stop and page a human. Failback
SHALL be manual. The stated recovery point SHALL be about one second for
platform state, and the last box backup for box files.

#### Scenario: Fencing fails
- **WHEN** promotion cannot confirm that a primary host is powered off
- **THEN** the standby is not started, and a page is sent

### Requirement: The restore drill runs weekly from off-region copies, including boxes

The DR drill SHALL run weekly on a schedule. It SHALL restore into a fresh host
from off-region copies only, using a fresh template environment with the pinned
image, and SHALL NOT copy the primary host's environment or secrets. It SHALL
assert that the public canary goes green, and that a sample of boxes restores
with matching content checksums. A failed drill SHALL page.

#### Scenario: The drill restores from off-region
- **WHEN** the weekly drill runs
- **THEN** it restores platform state and sampled boxes from off-region storage into a fresh host without the primary's secrets, and the canary goes green
