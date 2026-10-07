# App outbound adapter — async action-result delivery deltas

## ADDED Requirements

### Requirement: An async action's terminal result is delivered once as a governed follow-up

The daemon SHALL, when a heavy action (a background run) is enqueued from an
authenticated app conversation, record a content-free outbox entry linking the run
to its originating conversation, carrying no credential and no pre-authorized reply
body. The daemon SHALL, when that run reaches a terminal status, deliver a truthful
result-or-failure follow-up to the originating conversation through the governed
outbound adapter, with authority re-resolved fresh at delivery time, and SHALL
deliver it at most once per terminal revision.

#### Scenario: A completed action delivers a result follow-up once

- **WHEN** an app-originated action run reaches a `completed` terminal status
- **THEN** exactly one result follow-up is delivered to the originating
  conversation through the outbound adapter, keyed by the run and its terminal
  revision
- **AND** a subsequent delivery tick does not post a duplicate

#### Scenario: A still-running action is not delivered

- **WHEN** the action run has not yet reached a terminal status
- **THEN** no follow-up is delivered and the outbox entry stays pending

#### Scenario: A failed action is reported honestly, never as success

- **WHEN** the action run reaches a `failed` terminal status
- **THEN** the follow-up states the job failed (at a safe phase) and never claims
  success, and leaks no internal detail

#### Scenario: Delivery holds fail-closed when authority is unavailable

- **WHEN** current app authority for the conversation cannot be re-resolved at
  delivery time
- **THEN** the follow-up is held (not posted, not dropped) rather than delivered
  without authorization
