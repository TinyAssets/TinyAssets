# live-mcp-connector-surface (delta)

## ADDED Requirements

### Requirement: read_graph reports the two usage numbers, and converse reports a waiting turn

`read_graph` SHALL report, for the universe it reads, the tier's seat count,
how many seats are running, how many callers are waiting, and the universe's
and how many callers are waiting. `converse` SHALL, when its turn
cannot obtain a seat within the bounded wait, reply with the waiting state
naming the number of seats running and carrying the inline upgrade link, and
SHALL keep the turn's queue position so its real reply arrives when a seat
frees. Neither surface SHALL report a per-hour or per-day usage notice, a
capacity-returns time, or a rate-limit reason, because none exists. A universe
on the highest tier SHALL receive no upgrade link. Seat figures SHALL be readable
only by a caller already authorized to read that universe, and a waiter's queue
position SHALL be reported as a universe-local position, never as the global
ticket value, which would leak other universes' enqueue activity.

#### Scenario: read_graph shows seats
- **WHEN** an authorized owner reads a universe with seats held and work waiting
- **THEN** the response reports seats running, seats total and waiters

#### Scenario: A waiting turn answers instead of hanging
- **WHEN** a chat turn cannot obtain a seat within the bounded wait
- **THEN** `converse` replies with the number of seats running and an inline upgrade link, and the turn's real reply follows when a seat frees

#### Scenario: The removed meters are gone from every surface
- **WHEN** any connector surface reports usage
- **THEN** no per-hour write or total count, no per-day run count, no outbound dispatch or byte window, and no capacity-returns time appears

#### Scenario: Usage is private to the universe
- **WHEN** an unauthenticated or unauthorized caller reads a universe
- **THEN** no seat occupancy figure is disclosed

## ADDED Requirements

### Requirement: Authorized status explains platform resource observations
Existing authenticated status SHALL offer existing ACL admins universe-scoped resource
observations with timestamp, actual activity scope and limits, workspace
allocation/transport/storage distinctions, and explicit availability. Reads
SHALL NOT create databases, migrate schemas, reconcile usage or mutate records.
SQLite's normal coordination sidecars MAY be created by read-only connections;
reads SHALL preserve locking and visibility of committed WAL transactions.
No new handle or authority SHALL be introduced.

#### Scenario: Owner asks about usage
- **WHEN** the app's pinned agent reads status with its owner's existing admin authority for the universe
- **THEN** it receives observed usage and actual policy scope without needing operator database access

#### Scenario: Meter is missing or unreadable
- **WHEN** a trustworthy observation cannot be obtained
- **THEN** the field is unknown or unavailable, not a fabricated zero, and independent status still works

#### Scenario: Canary or unrelated user reads status
- **WHEN** a caller is not authorized for private universe usage
- **THEN** no private counts, paths, run identifiers or holder identities are disclosed

#### Scenario: The oldest charge is about to expire
- **WHEN** status reports a rolling-window expiration
- **THEN** it gives a UTC next-charge-expiration instant and does not guarantee enough capacity for an unspecified future request
