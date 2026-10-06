# Dynamic owner admission and the admission-generation contract

**Status: spec only, no code.** One founder decision is open (F1 in
`design.md`); its default is the current fail-closed behaviour, so nothing
waits on it except the last implementation task.

## Why

Per-role-uid-split (`per-role-uid-split`, branches `feat/per-role-uid-split` and
`feat/per-role-uid-split-migration`) gives every owner a dedicated UID and GID
(founder D60) and runs every owner engine in a cell started by a bounded mapper
(D62/D68–D70). Two gaps block activation:

1. **The mapper's identity table is fixed at startup.**
   `deploy/role_owner_launcher.py` `OwnerLauncher.bindings` is built once from
   `deploy/role_startup.py:bindings()`, which reads the stopped volume. A
   command center created after startup, including a new user's first home,
   has no binding, so every cell request for it raises. Its root also cannot be
   labelled `1001:<owner>` at runtime: no running process holds `CAP_CHOWN`, the
   daemon is not in the owner's group, and the mapper's namespace does not map
   UID 1001.
2. **D216 refuses a changed principal set at restart.**
   `deploy/role_volume_migration.py` raises "full migration authority changed"
   when the volume journal's principals differ from the inventory. D218 records
   that a completed deletion shrinks that set. So the first restart after any
   signup, new center or account deletion refuses to start.

## What Changes

- **Broker admission log.** The broker-private `owner-identities.db` gains an
  append-only, generation-numbered `center_admissions` log (`admit` and
  `retire` rows), kept under D61's discipline. It is the only record of which
  centers are admitted.
- **The mapper reads the log, never a daemon number.** The D70 window gives the
  mapper an inherited, read-only channel to the broker. A runtime `admit`
  request names a principal, a center and a log generation. The mapper fetches
  that row itself, checks the root's label, and only then adds the binding.
- **Labelling without capabilities (setgid hand-off).** A fixed
  `center-root` owner cell (the D83 pattern) creates a setgid directory under
  its own identity inside a daemon-private staging directory. The daemon
  creates the new root inside it, so the root inherits the owner GID and the
  canonical access ACL. One chmod by the daemon then clears the inherited
  setgid bit, leaving exactly the migrated root shape, and the daemon renames
  the root into place. No process gains or keeps a capability. On the selected
  path, app code never removes a published root; only deletion does, which
  retires it.
- **Deletion retires the binding.** D218's whole-center deletion appends a
  `retire` row and drops the mapper binding before `finish`. A retired center
  name is never admitted again.
- **Admission-generation contract at restart.** The volume journal records the
  log generation it last reconciled. At restart the coordinator accepts exactly
  the principal-set change the log explains since that generation. It also
  accepts centers that are mid-deletion and adopts a root that was published
  before its log row was written. It still refuses any change the log does not
  explain. The same rule covers all three phase journals (volume, metadata,
  owner). This replaces D216's "new principals remain a loud refusal" and
  closes D218's open item.

## Capabilities

### New Capabilities

- `owner-cell-admission`: runtime admission and retirement of command-center
  owner bindings for the bounded mapper, capability-free root labelling, and the
  restart contract over the admission log.

### Modified Capabilities

None on main. When `per-role-uid-split` lands its `runtime-process-roles`
delta (landing slice L0), task 12 folds these requirements into it.

## Impact

- Code (later tasks, switch OFF throughout): `tinyassets/broker/owner_identities.py`
  and the broker server, `deploy/role_owner_launcher.py`,
  `deploy/role_volume_migration.py`, `deploy/role_volume_inventory.py`,
  `deploy/role_owner_migration.py`, `tinyassets/role_owner_tree_deletion.py`, and the
  daemon's center-creation sites. `deploy/` is release-critical, so the launcher
  and migration tasks carry the `infra-change` label.
- Nothing is active until the D69/D70 bounded client is installed, which only
  the unwired role-split bootstrap does. Legacy startup is unchanged.
- No new privileged component, capability, setuid binary or host service.
- Non-goals: transferring an existing center to another principal (still
  refused); a subtree deletion cell for pool removal and `scoped_reset` (lane
  B); first-volume identity-map initialization itself (lane A, which this
  contract seeds); activation.
