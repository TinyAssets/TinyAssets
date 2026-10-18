# Owner Box

## Purpose

Define the owner-box erasure boundary and its durable recovery contract.

## Requirements

### Requirement: Erasure fails closed independently of box availability
Authentication and every broker box admission SHALL consult the single root-owned
erasure ledger. Only explicit cutover or fresh-install initialization may create
its schema and sealed initialization header. Reads SHALL be read-only; absent,
empty, corrupt or unreadable authority SHALL refuse authentication with a loud log.
Billing, custody, router credentials and identity deletion SHALL each run and
persist completion independently of box cleanup. Failed box cleanup SHALL retry.
Erasure and absent-tree completion SHALL require the mounted XFS device and UUID
recorded by host setup, including for retained and quarantined trees. The visible
mount MUST expose the whole filesystem (mountinfo root `/`) and be the topmost
mount at that path. Immediately after fencing, deletion SHALL retry a separate
`broker_channels` ledger phase closing all of the principal's broker channels.
Every established broker stream, including public tunnels, SHALL recheck erasure
at most every second and close on a fence or unavailable ledger. Cleanup admission
SHALL be permanently deletion-only, including after a local 409 refusal.
Cleanup targets SHALL contain only store-relative paths and UIDs, resolved against
each process's configured store. Startup SHALL record cleanup failures, keep those
owners fenced, and open control listeners for the remaining owners.

#### Scenario: A fenced owner's box is still running
- **WHEN** a fenced owner requests any broker box operation
- **THEN** admission is refused through the authentication ledger check

#### Scenario: Cleanup cannot reach the box or its filesystem
- **WHEN** resolving the box fails or the owner store is unmounted
- **THEN** shared deletion phases still run and record their results
- **AND** tree completion remains pending until verified on the expected mount
