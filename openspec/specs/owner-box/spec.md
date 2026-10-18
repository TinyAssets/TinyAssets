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
recorded by host setup, including for retained and quarantined trees.

#### Scenario: A fenced owner's box is still running
- **WHEN** a fenced owner requests any broker box operation
- **THEN** admission is refused through the authentication ledger check

#### Scenario: Cleanup cannot reach the box or its filesystem
- **WHEN** resolving the box fails or the owner store is unmounted
- **THEN** shared deletion phases still run and record their results
- **AND** tree completion remains pending until verified on the expected mount
