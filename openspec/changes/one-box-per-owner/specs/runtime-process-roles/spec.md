## MODIFIED Requirements

### Requirement: Distinct kernel identities per role and per owner
The production container SHALL run the daemon as uid 1001 with no capabilities and the broker
as uid 1002. Every owner-scoped process SHALL run inside that owner's box, which the box host
runs under the owner's dedicated host UID and GID. Each principal SHALL receive a permanent
UID/GID from 300001..399999, reserved append-only in the broker's `owner-identities.db`, and
never reused. Only the box host SHALL start a process under an owner identity. There SHALL be
no configuration under which these roles share a uid.

#### Scenario: Roles are distinct after startup
- **WHEN** the service has started and served one owner turn
- **THEN** `ps` shows the daemon at 1001, the broker at 1002 and the owner's box at its
  dedicated UID
- **AND** the daemon and broker read back an empty capability set

#### Scenario: No switch restores a shared uid
- **WHEN** any environment variable or configuration that formerly selected the legacy path
  is set
- **THEN** startup and every box operation behave identically to it being unset

## ADDED Requirements

### Requirement: One owner-box migration with snapshot restore after interruption
The box cutover SHALL convert the production volume once, with every writer stopped, after a
verified full backup whose digest is checked.
It SHALL copy admitted owner trees to the XFS box store under their permanent UID/GID,
verify checksums, and write the `owner-box` layout marker last. A failure after the first
write SHALL require restoring the verified snapshot with its paired runtime bundle.
There SHALL be no automatic retry, reverse migration or previous-image fallback after commit.

#### Scenario: A precondition fails
- **WHEN** the volume lacks the `owner-split` marker, no backup id is given, the digest fails, or project quotas are unavailable on the box store
- **THEN** the migration refuses before any write

#### Scenario: An interrupted copy requires snapshot restore
- **WHEN** the cutover is killed at any step and the same command is run again
- **THEN** the migrating marker refuses startup and refuses reapplying the migration
- **AND** the operator restores the verified paired snapshot before a fresh attempt

#### Scenario: The verified copy preserves owner bytes
- **WHEN** the cutover completes
- **THEN** every copied file has the same content checksum under its owner's permanent UID
- **AND** the old trees remain until the post-cutover backup and restore test pass

#### Scenario: A cross-owner inode stops the migration before any write
- **WHEN** the preflight finds an inode named in two owner trees
- **THEN** the migration exits non-zero, naming both paths, and the volume is unchanged

## REMOVED Requirements

### Requirement: One forward-only migration, re-runnable after a crash
**Reason**: The owner-split conversion is complete. The owner-box copy is one all-or-nothing maintenance-window move (ADR-022).
**Migration**: Replaced by "One owner-box migration with snapshot restore after interruption"; a partial copy restores the verified snapshot rather than resuming.

### Requirement: Startup is a capability-retiring bootstrap that never migrates
**Reason**: The daemon container starts unprivileged; box creation privilege belongs to the box host (one-box-per-owner P6).
**Migration**: `owner-box` "Each owner has exactly one box that runs the work"; the launcher and `ta_op` bootstrap modes are deleted.

### Requirement: Every owner-scoped child runs in an owner cell
**Reason**: Per-path cell classes are replaced by one box per owner, admitted once.
**Migration**: `owner-box` "Each owner has exactly one box that runs the work" and "Admission happens once at box entry".

### Requirement: Cell refusals remain observable without disclosing owner data
**Reason**: The mapper and its cells are deleted.
**Migration**: Box operation failures report through `BoxProvider` errors with operation ids; no owner bytes in diagnostics (kept as a box contract test).

### Requirement: Cutover acceptance runs production-data chat
**Reason**: The owner-split cutover is complete; acceptance moves to the box.
**Migration**: `scripts/box_acceptance.py` and the G1 and policy checks in the one-box-per-owner design §12.

### Requirement: Centers are admitted at runtime from the broker log
**Reason**: A center is a directory inside the owner's box; admission is per owner, once per session.
**Migration**: The broker log keeps one box row per owner; `owner-box` "Admission happens once at box entry".

### Requirement: Owner deletion is two-pass without capabilities
**Reason**: Account deletion destroys the owner's box.
**Migration**: `BoxProvider.destroy` under the existing owner-wide deletion fence.

### Requirement: Quiet provider turns use independent liveness
**Reason**: Provider cells and the mapper liveness channel are deleted; sessions run in the box.
**Migration**: Session liveness is the box host's process status for the session's loop.
