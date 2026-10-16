## ADDED Requirements

### Requirement: Isolation between accounts is the box

Each command center SHALL have exactly two homes: the owner's box for owner
content, and the platform sidecar `<data>/.universe-sidecars/<center>/` for
platform state. Platform code SHALL NOT perform filesystem I/O under
`<data>/<center>/`, and that directory SHALL NOT exist after the C6 move.

#### Scenario: Platform state is read without any box
- **WHEN** the scheduler checks whether a center is paused and no box is running
- **THEN** it reads `.pause` from that center's sidecar
- **AND** a missing sidecar for an admitted center reads as paused

#### Scenario: No host center path survives
- **WHEN** platform code builds a filesystem path from a center's host directory
- **THEN** the guard test fails, unless that code is the migration or account deletion

### Requirement: Owner reads bind as the requester

An owner-facing read SHALL use the request's own admitted session binding, or
SHALL bind the box as the requesting account. It SHALL NOT bind as any other
account and SHALL NOT read a host copy. Cross-owner isolation SHALL come from
broker box admission alone.

#### Scenario: A non-owner cannot read another owner's box
- **WHEN** account B requests a read of account A's center
- **THEN** broker box admission refuses the bind
- **AND** the read returns its not-found or unavailable shape with a reason, without retrying as A

### Requirement: The C6 move relocates platform state

The one-pass move SHALL classify every entry of every admitted host center
before the daemon stops. It SHALL refuse on an unknown name, and SHALL move
each platform name into the sidecar with digest and integrity verification.
It SHALL then remove the host center. The paired pre-move backup SHALL remain
the restore point.

#### Scenario: An unknown host entry stops the move
- **WHEN** the preflight finds a host center entry on neither list
- **THEN** the move refuses before the daemon stops and nothing is written
