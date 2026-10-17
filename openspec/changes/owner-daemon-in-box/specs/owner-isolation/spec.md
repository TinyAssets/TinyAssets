## ADDED Requirements

### Requirement: Owner work runs in the owner's box

Everything that reads or writes an owner's data SHALL run in that owner's
box, as the owner's daemon process: the agent loop, history, schedules,
pause, owner stores, and the app and MCP handlers for that owner. The shared
process SHALL NOT hold a path to any owner's data.

#### Scenario: The shared router reads no owner data
- **WHEN** an authenticated request for owner A arrives
- **THEN** the router forwards it to A's box socket, with A's per-box secret
- **AND** the router opens no owner file or owner database

#### Scenario: Another owner's box is unreachable
- **WHEN** owner B's authenticated request names owner A's center
- **THEN** it is forwarded only to B's box, which has no such center
- **AND** B receives not-found

### Requirement: Owner egress uses the admitted broker channel

The owner daemon SHALL send model calls, streaming replies and authenticated
external calls through the per-owner relay to the broker. The broker SHALL
derive identity from channel admission, resolve credentials and enforce its
existing model accounting, policy and connection grants. The box SHALL hold
no provider, OAuth or Stripe credential and SHALL refuse direct non-loopback
outbound connections at its socket seam.

#### Scenario: A model turn streams through the broker
- **WHEN** an owner chats using a connected HTTP model source
- **THEN** the model executor uses the existing broker OPEN stream and usage gate
- **AND** the broker injects credentials outside the box
- **AND** loss of the allocation's channel closes its broker-held liveness lease

#### Scenario: Missing channel and direct egress fail
- **WHEN** a model call has no broker channel, or an SDK or raw HTTP call attempts direct outbound access
- **THEN** it fails without a direct provider or credential-store fallback
- **AND** `api_key_http` is the only model access method; provider CLI executors and their connection actions are absent
- **AND** users reconnect through the broker HTTP connection flow at cutover

#### Scenario: An authenticated effector uses its grant
- **WHEN** the owner invokes an authenticated external call
- **THEN** its policy decision, connection grant and HTTP dispatch use the admitted box channel
- **AND** revoking the grant prevents dispatch

### Requirement: Shared reads contain no box-local shared authority

The daemon SHALL read its plan and published commons through named broker
operations. The broker SHALL select plan state using the relay-bound owner,
and SHALL read published data independently of owner-private roots.

#### Scenario: Plan and catalog reads
- **WHEN** the daemon requests its plan or a published definition
- **THEN** BOX_SHARED returns the shared billing or published catalog result
- **AND** no shared database or Stripe credential is placed in the box

### Requirement: Boxes sleep and wake without a person

A box SHALL sleep when idle, and SHALL wake on an inbound request or at its
reported next-wake time.

#### Scenario: A scheduled run in a sleeping box
- **WHEN** a sleeping box's next-wake time arrives
- **THEN** the waker wakes it and the owner's tick runs the due work


### Requirement: Credential custody and refresh have one broker writer

Only the broker SHALL persist, rotate or erase connection credential material.
The router SHALL hand deposits and OAuth token bundles to HTTP_CONNECT over
its private broker channel. The broker SHALL own its custody locks, ownership
metadata and deletion tombstones independently of daemon and owner stores.

#### Scenario: The door deposits a credential
- **WHEN** the router commits a prepared HTTP connection with a secret or OAuth bundle
- **THEN** the broker rechecks the revision before writing custody and committing its grant
- **AND** a refused deposit leaves no new grant
- **AND** the router performs no vault write

#### Scenario: An OAuth token expires during a box call
- **WHEN** an admitted model or effector call needs a refreshed OAuth token
- **THEN** the broker refreshes and durably saves the replacement before using it
- **AND** registered-client credentials are resolved inside the broker
- **AND** REFRESH and REFRESH_ACK are unknown box-channel operations

#### Scenario: Disconnect erases custody
- **WHEN** DISCONNECT erases a fenced connection with the expected incarnation
- **THEN** the broker erases its custody before removing the ledger rows
- **AND** a failed erase leaves the connection fenced and retryable
