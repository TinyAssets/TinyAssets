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

### Requirement: One forward-only migration, re-runnable after a crash
The box cutover SHALL convert the production volume once, with every writer stopped, after a
verified full backup whose digest is checked. It SHALL refuse before any write when any of
these holds: the volume lacks the `owner-split` marker, no backup id is given, the digest
fails, or project quotas are unavailable on the data volume. It SHALL attach each owner's tree
to that owner's box in place under the same UID, and SHALL move no owner byte. It SHALL copy
rule and grant rows into the owner policy store, verified by count and digest. Each step SHALL
converge to a computed target and change only fields that differ. The `owner-box` layout
marker SHALL be written last. Rollback SHALL be restoring the previous image with its pinned
runtime bundle, plus the backup once the old rule files are deleted. There SHALL be no reverse
migration.

#### Scenario: A crash is fixed by running it again
- **WHEN** the cutover is killed at any step and the same command is run again
- **THEN** the final state equals an uninterrupted run
- **AND** a further run changes nothing and `--check` reports zero diffs

#### Scenario: No owner bytes move
- **WHEN** the cutover completes
- **THEN** every owner file has the same path, inode content hash and UID as before

## REMOVED Requirements

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
