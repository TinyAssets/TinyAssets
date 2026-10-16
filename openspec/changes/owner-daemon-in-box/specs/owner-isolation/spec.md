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

### Requirement: Boxes sleep and wake without a person

A box SHALL sleep when idle, and SHALL wake on an inbound request or at its
reported next-wake time.

#### Scenario: A scheduled run in a sleeping box
- **WHEN** a sleeping box's next-wake time arrives
- **THEN** the waker wakes it and the owner's tick runs the due work
