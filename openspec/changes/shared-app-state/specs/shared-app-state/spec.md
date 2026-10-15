## ADDED Requirements

### Requirement: An app is stored once and charged to its owner
The platform SHALL store one app document for an app, under the app owner.
A second insert SHALL be refused. The document's bytes SHALL be charged to
the app owner and SHALL NOT be charged to an invitee. The owner SHALL be
able to replace that document by presenting its etag. A mismatched etag
SHALL raise a retryable conflict and leave the stored document unchanged.
An invitee SHALL NOT replace the document.

#### Scenario: Invitees add no copy
- **WHEN** several accounts redeem invites into one app
- **THEN** one document row exists and only the owner's definition bytes increase

#### Scenario: A member replaces the document
- **WHEN** an invitee submits a replacement document
- **THEN** the platform refuses and the stored document is unchanged

### Requirement: A record's bytes follow the account that owns it
A creator-owned record SHALL be billed to the account that created it, and
the stored key SHALL be that account's. An app-owned record SHALL be billed
to the app owner. A client-supplied account id inside the body SHALL NOT
choose the owner.

#### Scenario: A player writes their own record
- **WHEN** an invitee writes a creator-owned record whose body names the app owner
- **THEN** the stored owner is the invitee and the bytes are charged to the invitee

### Requirement: Concurrent writes of one record do not silently overwrite
A write, transition, or transfer SHALL present the etag it read. A mismatch
SHALL raise a retryable conflict and SHALL NOT change the record.

#### Scenario: Two writers use the same etag
- **WHEN** two writes present the same etag for one record
- **THEN** one write commits and the other raises a retryable conflict

### Requirement: A transfer can cross apps without copying either app
A transfer SHALL move one declared integer field across the named records in
one transaction, or change nothing. When a move names another app, the actor
SHALL be a member of that app and that app SHALL declare the same transfer.
The record SHALL remain in the app that stored it. Neither app document
SHALL be copied. A conserving transfer whose deltas do not sum to zero
SHALL be refused.

#### Scenario: An item is debited in one app and credited in another
- **WHEN** a member of both apps transfers a conserving field from a record they own in the first app to a record in the second
- **THEN** the two quantities change by opposite amounts, each record's owner is unchanged, and each app still has one document

### Requirement: Declared motion does not take a seat
A motion step SHALL add a record's velocity to its position for the elapsed
ticks, and SHALL NOT start a run or take a seat. A field the document marks
as server-owned SHALL keep its stored value when a client writes the record
again.

#### Scenario: A client writes over the position
- **WHEN** a clock step has moved a server-owned position and the owner then writes a different position and a new velocity
- **THEN** the stored position is the clock's value and the stored velocity is the new one

### Requirement: A scheduled run is charged to the app owner
A document MAY declare a repeating run whose actor is the app. The host
SHALL enqueue that run when its interval has elapsed and no such run is
queued, waiting, or running, and only while that owner's in-flight runs are
below the owner's background-seat limit. A run that would pass the limit
SHALL stay due and SHALL be reported as capped. The run SHALL take a seat
from the app owner's pool and SHALL record a provider bound on the app
owner's own home. The pump SHALL NOT call a model. Only the app owner SHALL
be able to read which runs are billed, and that reading SHALL name the
owner as the account that pays with the owner's seat and provider.

#### Scenario: Nobody is connected
- **WHEN** the host pumps due runs and no client has enqueued one
- **THEN** one run is charged to the app owner on that owner's provider, and an invitee's seat count does not change

#### Scenario: The owner is already at the background-seat limit
- **WHEN** the document declares more scheduled runs than the owner's background seats and the host pumps them
- **THEN** at most that many runs are in flight, the rest are capped, and a member cannot read the billing

### Requirement: Transient records are not committed on every tick
A record kind MAY declare `durability` `transient`. The default is
`durable`. An update of an existing transient record SHALL be applied in
memory for that app, fanned out on the stream, and SHALL NOT commit on
that tick. A checkpoint SHALL store the latest body and one log row for
that record in one transaction, and SHALL bill the stored growth plus
that log row. Creation of a transient record SHALL be a durable insert.
A durable write, transition, or transfer SHALL still conflict when the
etag does not match, and SHALL NOT lose an update. A checkpoint SHALL
run before a transition or transfer so that compare-and-swap sees the
memory etag. Unrelated apps SHALL NOT share the transient lock.

#### Scenario: A burst of position updates
- **WHEN** a transient record is created and then updated many times before one checkpoint
- **THEN** the stored body is the latest one, the log has the create plus one checkpoint row, and a second writer using the stale etag conflicts

#### Scenario: A durable record is written twice at one etag
- **WHEN** two durable writes present the same etag
- **THEN** one write commits and the other raises a retryable conflict

### Requirement: Live updates are pushed to the signed-in shell
A member SHALL be able to read public deltas after a cursor, limited to
named kinds and keys. The cursor SHALL move past every row examined. A
waiter SHALL be woken by a committed write. `POST /app/api/live` and
`GET /app/api/live/stream` SHALL take the actor from the signed-in session.
The sandboxed screen SHALL NOT open a socket. The signed-in shell SHALL
hold the stream and push matching deltas into the screen.

#### Scenario: A screen subscribes
- **WHEN** a screen names kinds and keys
- **THEN** the shell requests that interest as the signed-in account, and the screen's connect-src does not include http or ws

### Requirement: Secret fields stay with the record owner
The public log and a read by anyone except the record owner SHALL omit
secret fields. The app owner SHALL NOT receive another account's secret
fields. A viewer grant SHALL see the public projection only.

#### Scenario: A spectator reads a seat
- **WHEN** a spectator syncs an app whose records have a secret field
- **THEN** the secret value is absent from the spectator's view and present in the record owner's private read
