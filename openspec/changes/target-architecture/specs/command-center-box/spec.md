## ADDED Requirements

### Requirement: Every command center runs in exactly one sealed box

The platform SHALL give each command center exactly one box with its own kernel
boundary (a microVM guest kernel on the primary driver, or the gVisor
user-space kernel on the fallback driver), its own disk bound, and no network
interface. The box SHALL hold the command center's user content. Every tool
that touches command-center content or runs code, and every CLI run on the
command center's behalf, SHALL execute inside that box and nowhere else.
Owner-door read tools over platform history SHALL execute in the control plane,
read-only and bound to the account. A box SHALL never hold another account's files or
processes. Its host-side processes SHALL run under a host uid that no other
account's box uses. The driver SHALL be box-host configuration, and no code
path SHALL branch on it or on the account's tier.

#### Scenario: Two users' tools run in two boxes
- **WHEN** user A's agent and user B's agent each run `bash` at the same time
- **THEN** each command runs in its own command center's box, under different host uids and kernel boundaries, and neither can list the other's files

#### Scenario: Agents of one command center share its box
- **WHEN** two agents of one command center run tools concurrently
- **THEN** both run inside that command center's one box

### Requirement: The platform reaches box contents only through BoxProvider, and every operation is authenticated to its owner

The daemon SHALL read, write, list, stat, export and execute against a command
center's contents only through `BoxProvider`, using box paths resolved inside
the box. The daemon process SHALL have no filesystem permission on box images,
checkpoints or their directories. The box host SHALL authenticate every
operation with: the cell's credential; the handle's account and command
center; the operation id; and the box's current placement epoch. It SHALL
refuse an operation whose account does not own the command center, whose epoch
is stale, or whose handle names another command center. A ratchet test SHALL
fail the build on any daemon-side open of command-center content outside
`BoxProvider`. Everything read from a box SHALL be treated as untrusted input.

#### Scenario: A planted link cannot reach another user
- **WHEN** an agent creates `founder.md` as a symlink to another command center's path and the daemon then reads `founder.md` for persona grounding
- **THEN** the read resolves inside the box's own filesystem, and no other command center's bytes are returned

#### Scenario: A valid handle on the wrong turn is refused
- **WHEN** a turn of account A presents a valid handle for account B's command center
- **THEN** the box host refuses the operation, and nothing executes

### Requirement: Box operations are idempotent by operation id and never re-run after a lost reply

Every mutating box operation SHALL carry an operation id. The box SHALL record
each operation's outcome by that id for a retention window. A retry with the
same id SHALL return the recorded outcome without running the operation again.
An operation in flight across a host crash SHALL report its outcome as unknown,
and the caller SHALL hold rather than re-issue it.

#### Scenario: A lost reply is not a second run
- **WHEN** an exec's reply is lost in transport and the caller retries with the same operation id
- **THEN** the caller receives the first run's outcome, and the command ran once

### Requirement: Each box has a hard disk bound; the account quota is logical; host space is reserved

Each box's filesystem SHALL have a hard size bound. A box that reaches it SHALL
see `ENOSPC` inside the box, and no other box, account or the host SHALL be
affected. New box storage SHALL be fresh, never-reused space that reads as
zeros. The box host SHALL keep a durable reservation ledger covering every
box's bound plus its checkpoint and runtime space. It SHALL refuse to grow a
bound past the host's free-space floor. Grows SHALL be idempotent by operation
id and reconciled on startup. An account's storage usage SHALL count logical bytes as
the account storage quota defines them: file sizes, hard links counted once,
runtime and scratch excluded. It covers its boxes' content plus its
user-attributable platform bytes, never the bounds or filesystem block usage.
A Firecracker box's bound SHALL be grown only while its image is unmounted. At the
quota, new user-driven writes SHALL be refused visibly with the inline Upgrade
link, and bounds SHALL stop growing.

#### Scenario: One account cannot exhaust the host
- **WHEN** an agent writes until its box is full
- **THEN** its writes fail with `ENOSPC` inside its box, and every other box and the control plane keep writing

#### Scenario: Empty boxes do not consume the quota
- **WHEN** an account with a 2 GiB quota creates three nearly empty command centers
- **THEN** its usage is the bytes those boxes actually hold, and it can still write

### Requirement: Boxes are awake only while acting, keep no timers, and restore only from a matching checkpoint

A box SHALL be woken only by the control plane. It SHALL checkpoint and stop
after an idle period of at most 60 seconds with no operation in flight. A box
SHALL run no scheduler the platform relies on. A box SHALL restore from a
memory checkpoint only when that checkpoint's manifest matches the disk's
current state. A box whose disk changed after its last checkpoint SHALL
cold-boot from disk instead. At host capacity, a wake SHALL wait in a
first-come queue that is fair across accounts, SHALL NOT be refused, and its
waiting state SHALL be visible to the user. Reading a command center's cached
content SHALL NOT wake its box when the cache matches the box's committed
generation.

#### Scenario: A host crash forces a cold boot
- **WHEN** the box host crashes while a box is awake and later restarts
- **THEN** that box cold-boots from its disk rather than restoring an older memory checkpoint

#### Scenario: A warm cache does not wake a box
- **WHEN** a turn needs persona grounding whose cached generation equals the suspended box's committed generation
- **THEN** the turn uses the cache, and the box stays suspended

### Requirement: Box egress goes only through the cell's egress floor

A box SHALL have no network interface. Its only egress SHALL be one socket to
the cell, where the egress floor SHALL apply: globally routable destinations
only; no metadata, private or loopback addresses; SMTP ports refused; and a
per-box connection cap.

#### Scenario: Metadata is unreachable
- **WHEN** a process in a box requests `http://169.254.169.254/`
- **THEN** the request is refused, and no direct path exists

### Requirement: Destroying a deleted command center destroys its box, and deleted accounts cannot be restored

Account deletion SHALL destroy the box of every command center that the
deletion set deletes, removing its image, its checkpoints and its backup set.
Command centers that survive the person SHALL keep their boxes under the
opaque-fingerprint owner. Each command center's backups SHALL be encrypted
under its own data key, wrapped by its owning account's key. Before the
deleted account's key is destroyed, every surviving command center's data key
SHALL be re-wrapped under its new custody, and a backup under that custody
SHALL be verified. The deleted command centers' data keys SHALL be destroyed. Every restore SHALL consult a
tombstone list, and SHALL NOT bring back a deleted account. Deletion SHALL
complete only when every destroy receipt and the key destruction are recorded.

#### Scenario: A surviving command center stays recoverable
- **WHEN** an account is deleted while one of its non-home command centers survives, and that command center's primary disk is later lost
- **THEN** the command center restores from a backup readable under its new custody

#### Scenario: An old backup does not resurrect a deleted account
- **WHEN** a restore runs from a backup taken before an account was deleted
- **THEN** that account's data is not restored, and its backup data is unreadable without the destroyed key
