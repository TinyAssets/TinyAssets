# user-owned-automations (delta)

## ADDED Requirements

### Requirement: An engine event wakes the owner's subscribed automation

An automation SHALL be creatable with trigger kind `event`, naming an
`event_type` the engine emits (`run_completed` or `pending_request_answered`)
and an optional equality `event_filter` over that event's payload fields. A
`run_completed` subscription SHALL name `branch_def_id` in its filter. An event
row SHALL never be due on a clock. Each emitted event that matches an active
subscription SHALL store a one-shot wake for the subscribed branch, with the
payload under `inputs.event`, and the automation pump SHALL fire it with every
run-time check an automation already has. An event SHALL be stamped with the
principal that caused it, recorded when the run was created, and SHALL wake
only subscriptions that principal owns in that principal's own home universe.
An event with no principal SHALL wake nothing. A cancelled run SHALL announce
nothing.

#### Scenario: A run finishing wakes the branch that follows it
- **GIVEN** an owner's `run_completed` subscription filtered on branch A
- **WHEN** a run of A reaches a terminal status, or a deploy interrupts it
- **THEN** one wake of the subscribed branch is stored with `inputs.event` naming the run and its outcome, and the pump runs it once

#### Scenario: An answer from any surface wakes a subscribed branch
- **WHEN** the owner answers or dismisses a pending request, through the app or the connector's `answer_request`
- **THEN** one wake is stored with the request id, kind and status

#### Scenario: Another user's activity wakes nothing
- **WHEN** a different user's run finishes in the owner's universe, a visitor-driven run of the universe finishes, someone else cancels the owner's run, or someone else answers a request
- **THEN** no wake is stored for the owner's subscription

#### Scenario: The owner sees when a subscription last fired and what it produced
- **WHEN** a subscription fires, or its wake is refused
- **THEN** the subscription's own row records when (`last_due_at`) and the outcome (`last_reason` `woke:<wake id>` or the refusal), without a `revision` bump, and the owner's read of it carries `last_wake` with that wake's run, drawn only from a wake in the same universe

#### Scenario: A subscription that could not fire is refused
- **WHEN** an owner subscribes to an event the engine does not emit, filters on a field the event lacks, or omits `branch_def_id` from a `run_completed` filter
- **THEN** nothing is stored and the refusal names the reason

### Requirement: A scheduler subscription names only an emitted event

`subscribe_branch` SHALL refuse any event type the engine does not emit, and
SHALL name automation events as the replacement. It SHALL NOT store a row that
cannot fire.

#### Scenario: A retired event type is refused
- **WHEN** a caller subscribes to `canon_change`, `branch_run_completed`, `canon_upload` or `pr_open`
- **THEN** the action returns `event_type_not_subscribable` and stores nothing

## ADDED Requirements

### Requirement: A node can wake one of its owner's branches, now or not before a time

A node with `enqueue_branch_run` in `tools_allowed` SHALL be able to store a
one-shot automation, trigger kind `once`, in its run's own universe. The row is
owned by the run's owner and runs one of that owner's authored branches, private
ones included. The row SHALL carry an optional `not_before`, given either as an
ISO instant or as `delay_seconds`. It SHALL be stored durably and fired by the
automation pump once `not_before` has passed. The pump SHALL apply every
run-time check an automation already has. A row whose run started SHALL retire
itself. The capability SHALL be on by default. It SHALL be limited by usage (the
per-universe pending-automation count and run admission), never by spawn depth
or fan-out.

#### Scenario: A delayed wake fires once, after its time
- **WHEN** a node enqueues its owner's branch with `delay_seconds: 600`
- **THEN** no run starts before 600 seconds have passed, one run starts after that, and the row is retired

#### Scenario: A branch can reschedule itself indefinitely
- **WHEN** every run of a branch enqueues that same branch
- **THEN** each hop is accepted, subject only to the per-universe limits

#### Scenario: Another user's universe, identity or branch is refused
- **WHEN** a node names another universe, runs without its universe's owner bound, or targets a branch its owner did not author
- **THEN** nothing is stored and the node receives the refusal

#### Scenario: A wake survives a deploy
- **GIVEN** a stored wake whose run was killed with its process
- **WHEN** a later process holds the universe lease
- **THEN** the wake is retried under a new key, up to a bounded number of attempts

## ADDED Requirements

### Requirement: The owner's new message wakes an owner_message subscription once per burst

The engine SHALL emit `owner_message` once the owner's message is stored in their own conversation thread, stamped with the verified sender. It SHALL wake only that principal's own subscriptions in their own home universe, and a `universe:<id>` principal or a visitor SHALL wake nothing. While a wake stored by the subscription has not started, a further message SHALL store no second wake. The event SHALL carry no filter keys.

#### Scenario: A burst is one wake
- **GIVEN** an owner's `owner_message` subscription
- **WHEN** the owner sends three messages before the wake starts
- **THEN** exactly one wake is stored and the pump runs it once; a message after it started stores a new wake

#### Scenario: The universe's own reply wakes nothing
- **WHEN** the universe answers, or another user talks in the owner's universe
- **THEN** no wake is stored

## ADDED Requirements

### Requirement: A served agent can set its own next wake

Automation create SHALL accept `not_before` (an ISO-8601 instant) or `delay_seconds` (a number >= 0) as a one-shot trigger, exclusive with every other trigger, and the pump SHALL fire it once at or after that instant.

#### Scenario: A wake set an hour out fires at its time
- **WHEN** the agent creates an automation with `delay_seconds: 3600` through `write_graph`
- **THEN** it is not due before the hour and is due after it

#### Scenario: Two triggers are refused
- **WHEN** create names `delay_seconds` with `interval_seconds`, or `delay_seconds` with `not_before`
- **THEN** nothing is stored and the refusal names the reason

## ADDED Requirements

### Requirement: An automation read shows when it fires next

Every automation projection SHALL include `next_due_at`. It is the instant the
pump will next owe a run, derived from the same trigger rules the pump uses to
decide that a run is due. It SHALL be empty for a paused or retired automation.
An instant at or before the read time SHALL mean the run is owed and starts on
the pump's next poll.

#### Scenario: Interval automation
- **WHEN** an active interval automation is read before its first period elapses
- **THEN** `next_due_at` is its anchor plus one interval, and the pump finds it due at exactly that instant

#### Scenario: Cron automation
- **WHEN** an active cron automation is read
- **THEN** `next_due_at` is the next minute the pump's cron match accepts

#### Scenario: Paused or retired automation
- **WHEN** the automation is paused or retired
- **THEN** `next_due_at` is empty
