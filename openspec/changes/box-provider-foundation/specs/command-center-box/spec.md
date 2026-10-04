## ADDED Requirements

### Requirement: The local box driver is for tests and development only

The local `BoxProvider` driver SHALL refuse to start unless the caller explicitly acknowledges
that it has no kernel boundary. No production box-host configuration SHALL select it.

For every operation, it SHALL still enforce:
- handle ownership;
- placement epochs;
- operation-id idempotency, with an operation's outcome reported as unknown after a restart;
- link-free path resolution beneath the box directory.

It SHALL pass the same driver-agnostic contract suite as every isolating driver.

#### Scenario: Unacknowledged use is refused
- **WHEN** code constructs the local driver without acknowledging that it has no kernel boundary
- **THEN** construction fails with a box error, and no box directory is used

#### Scenario: A planted link is not followed even by the local driver
- **WHEN** a command run in a local box creates a link to another box's file, and the daemon reads that path through the driver
- **THEN** the read is refused as crossing a link, and no other box's bytes are returned
