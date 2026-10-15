## ADDED Requirements

### Requirement: Each owner has exactly one box that runs the work
Each owner account SHALL have exactly one box: an isolated sandbox with its
own kernel boundary, behind `BoxProvider`, run by a box host service outside
the daemon container. The agent loop, its tools, child sessions, resident
processes, the browser and native CLIs packaged as external agents SHALL run
inside the owner's box. Every command center of the owner SHALL be a
directory inside that box. Owner-scoped work SHALL NOT run in any other sandbox or cell kind.

#### Scenario: A turn and its tools run in the owner's box
- **WHEN** an owner's session runs a turn that calls `bash`, `git` and the browser
- **THEN** each of these runs as a process inside that owner's box
- **AND** no `role_*` cell or per-path jail is started
- **AND** credentials, owner policy, the control plane, the turn journal and platform stores remain outside every box

#### Scenario: Owners never share a box
- **WHEN** owner A's box tries to read, write or signal anything belonging to owner B
- **THEN** the kernel boundary refuses and no byte of B's data is read or changed

### Requirement: Nothing inside the box is trusted and one channel leads out
The box SHALL have no network interface and no credential, except a
file-OAuth CLI token leased into its owner's box for one run. Its only
channel out SHALL be one socket to the box host. The box's identity SHALL be
taken from that channel, never from a request payload. Every request that
leaves the box SHALL cross that channel to the broker's gate. The platform SHALL
reach box content only through `BoxProvider`, as untrusted input.

#### Scenario: Every outbound action crosses the gate
- **WHEN** a box makes model, connector or `ta` calls, network connections, spend, child-session starts, messages or notifications
- **THEN** the action crosses the one channel and the broker gate

#### Scenario: A box names another owner in a payload
- **WHEN** a request from owner A's box carries owner B's account or connection id
- **THEN** the gate resolves the principal from the channel as A and refuses B's resources

#### Scenario: Direct network access
- **WHEN** a process in the box opens a socket to an external address without the gate
- **THEN** the connection fails because the box has no interface

### Requirement: Admission happens once at box entry
`session.start` SHALL perform the only admission for a session: owner, seat,
box and allocation. It SHALL bind one box handle carrying the owner, the
placement epoch and the session. Tool calls, browser actions, CLI runs and
child sessions SHALL use that handle without a further admission. A child
session SHALL inherit its parent's handle and allocation. The box host SHALL
authenticate every operation by owner, epoch, session and operation id.

#### Scenario: A child session starts
- **WHEN** a session runs `ta agent run` to start a child
- **THEN** the child runs in the same box under the parent's allocation with no second admission

#### Scenario: A handle from another owner
- **WHEN** an operation presents a handle minted for another owner or a stale epoch
- **THEN** the box host refuses it before any effect

### Requirement: The box keeps working with no device and no host online
A box SHALL stay awake while any session, resident process or owner view is
live, and SHALL suspend only after it has been idle. Closing every client
SHALL NOT cancel a session. A daemon deploy SHALL NOT stop a box or its
running loops. A box host restart SHALL checkpoint and then restore every
awake box. Only the control plane SHALL wake a
box. No owner or founder machine SHALL be on any path.

#### Scenario: Checkpoint or restore fails
- **WHEN** a checkpoint or restore fails
- **THEN** the box cold-boots and its sessions resume from the turn journal
- **AND** operations whose outcome is unknown are held, never replayed

#### Scenario: Two-hour task with every device off
- **WHEN** the owner starts a multi-step task, closes every app and turns the phone off
- **THEN** the agent finishes the task in its box within two hours with no host machine online
- **AND** the result is stored and delivered when a device returns

#### Scenario: Deploy during a task
- **WHEN** the daemon is redeployed while a session runs in a box
- **THEN** the session continues and completes without an interrupted turn

### Requirement: Box limits are the two account limits plus a host floor
A box's account limits SHALL be exactly seats (concurrent top-level sessions)
and storage (the box disk's hard bound, equal to the account's storage quota).
Every box SHALL also carry the same host-protection floor: a memory ceiling,
a CPU weight, a process-count limit and an egress connection cap. Exceeding
the floor SHALL affect only processes in that box. At host capacity, a wake
SHALL wait in a FIFO queue that is fair across owners, and SHALL NOT be refused.

#### Scenario: A runaway process
- **WHEN** a process in owner A's box exhausts its memory ceiling
- **THEN** that process is killed inside A's box and no other box is affected

### Requirement: The box exposes snapshot, view and egress hooks
`BoxProvider` SHALL offer `snapshot` and `restore` of the owner tree, taken
under quiesce. Off-region backup SHALL use the same snapshot path. It SHALL
offer `view`, a browser and desktop stream with input, which only the owner
door can open, with the owner's session. All box network traffic SHALL pass
one per-box egress gate at the broker, which applies the platform floor,
then the owner's egress policy, then connection grants.

#### Scenario: Undo a task
- **WHEN** the owner asks to undo a session's work
- **THEN** the box is restored to the snapshot taken at that session's start

#### Scenario: A non-owner asks for the view
- **WHEN** a model-door, MCP or other-account caller requests a box view
- **THEN** no view operation exists for it and nothing is streamed


#### Scenario: A disconnected channel reconnects to its session
- **WHEN** a daemon channel closes while an admitted session has running or queued work
- **THEN** only that channel's handle is detached and the work continues
- **AND** a new channel bound to the same admitted session can recover its operation status
- **AND** detaching the old channel cannot revoke the new channel's handle

#### Scenario: Restoring files cannot turn an operation into a replay
- **WHEN** a quiesced paired owner-box snapshot is restored
- **THEN** its root-private receipt journal is restored with the data member
- **AND** epochs and generations advance beyond both the live and archived copies
- **AND** every operation ID seen in either copy is retained as unknown, never replayed
- **AND** differing identity, admission or deletion frontiers refuse the swap until reviewed forward recovery is available

### Requirement: A shared record commits only at its own path etag
The owner-box host journal SHALL keep a monotonic etag for each command-center
path. A write SHALL carry `expect_etag` equal to that etag. A mismatch SHALL
raise `RecordConflict` before any effect and SHALL NOT bump the etag or the
owner generation. A matching write SHALL bump that path's etag and the owner
generation in one journal transaction before the guest runs, and the write
result SHALL return the new etag from the host, never from the guest. A write
to a different path SHALL NOT invalidate this etag. Owner-generation
compare-and-swap SHALL keep its meaning. Arbitrary execution, host recovery
and journal restore SHALL fence every path etag, including paths with no row,
so an unpublished or unknown effect cannot be overwritten by a stale etag.
The guest SHALL NOT observe `expect_etag`.

#### Scenario: Two writers share one record
- **WHEN** two writers read the same path etag and both write with it
- **THEN** the first write publishes and returns the next etag
- **AND** the second write raises `RecordConflict` without a guest call
- **AND** a write of a different path with its own etag still publishes

#### Scenario: A lost write is not silently replaced
- **WHEN** a write bumps the etag and the guest response is lost
- **THEN** a new write that presents the old etag raises `RecordConflict`
- **AND** the original operation stays unknown and is not replayed
