## MODIFIED Requirements

### Requirement: Authorized status explains platform resource observations
Existing authenticated status SHALL offer existing ACL admins universe-scoped resource
observations with timestamp, actual activity scope and limits, workspace
allocation/transport/storage distinctions, and explicit availability. Reads
SHALL NOT create databases, migrate schemas, reconcile usage or mutate records.
SQLite's normal coordination sidecars MAY be created by read-only connections;
reads SHALL preserve locking and visibility of committed WAL transactions.
No new handle or authority SHALL be introduced.
Activity SHALL retain observed engine-mutation counts, but its limits SHALL name
only the enforced total (900) and write-run (300) admission ceilings per rolling
3600 seconds, without the retired `engine_mutations` category ceiling.

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

#### Scenario: Engine usage is observed without a separate allowance
- **WHEN** authorized status reports engine mutations
- **THEN** activity contains their count and includes them in total, but `activity.limits` contains only `total` and `write_runs`
