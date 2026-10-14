# Universe Seats

> As-built (2026-09-30, change `two-dimension-usage-limits`): concurrent agent
> seats, one pool per ACCOUNT (`tinyassets/universe_seats.py`), keyed through
> the shared owner resolver `universe_owner`. The other account limit, cloud
> storage, is change `account-storage-quota`.

## Purpose

Bound how many agent calls one account runs at once. Over the count, work
waits -- visibly, with an inline upgrade link -- and is never refused or dropped.

## Requirements

### Requirement: A seat is one in-flight agent call, and the ACCOUNT's tier sets how many run at once

The platform SHALL bound how many agent calls one ACCOUNT executes concurrently
by a count of **seats** from that account's tier. The pool SHALL be one per
person, shared across all of their universes (founder, 2026-09-30); creating a
universe SHALL NOT create a pool. The account SHALL be the universe's owner as
resolved by `universe_owner.owner_of`, and the tier SHALL be
`universe_owner.tier_of` -- the same resolvers storage uses, never a second
derivation from ACL rows or bindings. A universe no account is charged for
SHALL be its own unattributed pool on the free tier: counted, never refused,
never merged into another account's. A seat SHALL be held for the whole of one
agent call, not per provider attempt. Seats SHALL NOT replace the host-wide
provider-subprocess bound (`provider_admission`), and SHALL NOT bound the size,
depth or breadth of what a user builds.

#### Scenario: Two universes of one owner share one pool
- **WHEN** an owner on the free tier (3 seats, 1 reserved) has two background agent calls running, one in each of two universes, and a third arrives in either
- **THEN** the third waits for a seat

#### Scenario: Different owners never share
- **WHEN** one account holds every seat its tier allows
- **THEN** another account's agent call acquires a seat immediately

#### Scenario: A co-admin is not an owner
- **WHEN** an account administers a universe another account owns
- **THEN** agent calls in that universe draw on the owner's pool, not the co-admin's

### Requirement: Seats are taken at the executor, and every agent call takes one

Seats SHALL be acquired where the model call happens, never where work is
enqueued: the agent node's executor (`graph_compiler`), keyed on the RUN's own
universe from its execution context; the chat turn (`converse`), as
`interactive`; and the automation and wake worker (`run_due_automation`), as
`background`, as a non-blocking admission check before it claims an attempt.
No thread another account's work is owed SHALL ever wait for a seat: runs
execute in a worker pool of their own account (keyed by the same owner
resolver), so one account's waiting runs never queue another account's run.
A run started by `run_graph`, an
automation, a wake or an inbound event SHALL therefore hold a seat for each of
its agent calls.

#### Scenario: One account's waits never hold up another account
- **WHEN** one account has far more runs waiting for its seats than the run pool has threads
- **THEN** another account's run on the same host still starts and completes promptly

#### Scenario: A real run holds a seat
- **WHEN** a run started through `runs` executes an agent node for a universe
- **THEN** that call holds one seat of the universe's owning account for its duration

### Requirement: Over the limit, work waits longest-owed-first and is never refused or dropped

An agent call that finds no seat SHALL wait and SHALL start when a seat frees,
without a deadline. It SHALL NOT be refused and SHALL NOT be dropped. The queue
SHALL be ordered by class and then by a monotone ticket; a caller that finds a
seat free while another waiter is owed one SHALL join behind it. A polling
worker SHALL keep its ticket between polls. An abandoned position SHALL lapse,
and a position whose process is proven dead SHALL be dropped, so neither stalls
live work behind it.

#### Scenario: Queue order is longest-owed first
- **WHEN** three background agent calls wait for one seat, enqueued in order A, B, C
- **THEN** they start in the order A, B, C

#### Scenario: Four agents on a free account
- **WHEN** four agent nodes across two universes of one free account start at once
- **THEN** two run, two wait, and all four complete

### Requirement: Background work can never take the last seat, so the owner's chat is never blocked by it

Chat turns SHALL be `interactive`; everything else SHALL be `background`.
Background SHALL occupy at most `seats - interactive_reserve`; interactive MAY
occupy all `seats`. An interactive waiter SHALL never be ordered behind a
background waiter.

#### Scenario: A background ping-pong cannot starve the chat
- **WHEN** background work keeps every background seat busy and more waits
- **THEN** a chat turn is served at once on the reserved seat

### Requirement: An automation or wake waits before it claims, so a wait is never an attempt or a failure

The automation worker SHALL try for a seat once per poll, before claiming an
attempt, and SHALL NOT block the consumer thread while waiting. A wait SHALL
NOT consume `MAX_ONCE_ATTEMPTS`, SHALL NOT count toward
`MAX_CONSECUTIVE_FAILURES`, and SHALL leave the automation due. The seat it
gets SHALL be given back before its run is queued: a seat held by a run still
waiting for a run-pool worker, behind workers waiting for that account's seats,
is a deadlock. The run's agent calls SHALL take their own seats at the executor,
and no run timeout, and no deadline of a blocking invoke waiting on a child,
SHALL turn a wait there into a failure.

#### Scenario: A wake outlasts a busy account
- **WHEN** a wake is due on more polls than it has attempts while its account's background seats are full
- **THEN** it records `waiting_for_seat` each time, has spent no attempt, is not retired, and runs once a seat frees

### Requirement: A blocking nested agent call inherits its parent's seat, exclusively

A call made while its caller holds a seat SHALL re-enter that seat by an
exclusive depth transition that matches account, holding process and depth;
a parallel sibling that finds the seat already lent SHALL take its own, and
SHALL retry re-entry while it waits. A seat id alone SHALL never be a
capability.

#### Scenario: Another account cannot ride a seat by naming it
- **WHEN** a caller names another account's seat as its parent
- **THEN** it takes a seat of its own account instead

### Requirement: A seat ends on every terminal path and on its holder's proven death

A seat SHALL be released on success, failure, cancellation and timeout; a
call still running after its node timed out SHALL keep its seat until it
actually ends. Each seat SHALL name its holder by `process_liveness.owner_token`
under the seat store's own root. Every read-decide-write on the store SHALL run
inside one write transaction held until it commits. A release the store refuses
SHALL be retried until it lands, never dropped, because a live holder's seat is
otherwise never reclaimed. A seat SHALL be reclaimed when its holder is
proven dead (a crash, a deploy's SIGKILL), or when its lease has lapsed and its
holder is not provably alive; a provably alive holder SHALL never be reclaimed.
The liveness cleanup SHALL keep a dead token's proof while any seat or waiter
still names it. A seat store that is a symlink or resolves outside its data
directory SHALL be refused loudly. Only the holding process SHALL release a seat.

#### Scenario: A deploy frees its seats at once
- **WHEN** the process holding seats is killed
- **THEN** the next acquisition reclaims them without waiting for their leases

#### Scenario: A cancelled waiting run gives its place back
- **WHEN** a run is cancelled while its agent node waits for a seat
- **THEN** the wait ends, its queue position is abandoned, and the provider is never called

### Requirement: The waiting state is visible to the owner with an inline upgrade link

While work waits, the owner SHALL see that it waits and how many seats are
running: a run records a `waiting_for_seat` system event (never a node `ran`);
an automation records `waiting_for_seat` as its recent reason; `get_status`
(`read_graph` status) reports the account's `seats` -- running, waiting,
`chat_waiting` for THIS universe, the message and `upgrade_url` -- to the owning
account only; and the app's one status line shows "Waiting for a free seat (N
running) -- Upgrade for more seats." with Upgrade a link INSIDE the line, never
a banner, card or modal. The link SHALL be `usage_policy.upgrade_url`
(`https://tinyassets.io/app?upgrade=1`), which the app routes to its existing
`startSubscribe()`. The top tier SHALL see the waiting line with no link.

#### Scenario: The top tier is not asked to upgrade
- **WHEN** an account on the highest tier waits for a seat
- **THEN** the waiting line carries no upgrade link and `upgrade_url` is null

#### Scenario: A co-admin never reads the owner's occupancy
- **WHEN** a non-owning admin reads the universe's status
- **THEN** no `seats` block is present
