# Design: dynamic owner admission and the admission-generation contract

Decision records here use the `DA` prefix so they do not collide with
`per-role-uid-split`'s D-numbers. Task 12 renumbers them into that design when
it lands on main. Code references are to `feat/per-role-uid-split-migration` at
`58395c4d99`.

## Founder decision (the only one)

**F1. A center the log admits is missing at restart, and no deletion explains
it.** This can only come from data loss outside the platform, such as a manual
removal or a partial restore. Choose one:

- **(a) Refuse the whole startup.** This is D216's behaviour today. Every user
  is down until a host action records the loss.
- **(b) Start everyone else (recommended).** That center stays unbound. Its
  requests refuse loudly, an alarm fires, and a `docs/concerns/` file names the
  center. A tree restored later is bound at the next restart, because its row
  and its label still match.

Until the founder answers, (a) applies. It is fail-closed, and activation is
gated anyway, so the open question blocks only task 10. Tracked in
`docs/host-actions.md`. Everything else below is a lead or mechanical
decision inside the agreed per-role-uid-split work: D70 already requires that
"dynamic identity allocation uses the live broker IPC; dynamic center admission
remains separate and must not accept caller-selected numeric identities or
paths".

## Context

What exists:

- **D61/D62.** `owner-identities.db` is broker-private and append-only. Its
  reservations run from 300001 to 399999. Allocation goes through OWNER-channel
  identity IPC (`OWNER_IDENTITY {principal, allocate}`), and lookup alone
  never allocates.
- **D68/D69.** The daemon talks to the mapper over a private seqpacket pair.
  Every packet carries SCM_CREDENTIALS for the exact daemon PID. Numeric
  identities and paths are not request fields.
- **D70.** One privileged window forks the broker and then the mapper, and
  retires PID1 to the daemon. The mapper gets `bindings` resolved by
  `role_startup.bindings()` from the stopped-volume inventory, and keeps them
  for its whole life.
- **D83.** A fixed, capability-free cell runs under the owner's identity for
  trusted file preparation. D85 adds the pattern where the daemon answers
  whether a descriptor's host UID is 1001, which a mapper or cell cannot see,
  because 1001 shows as the overflow UID inside their namespaces.
- **Canonical root shape.** `role_owner_migration._label`, root kind:
  `1001:<machine>`, mode `02750`, access ACL `user::rwx user:<machine>:r-x
  user:1002:--x group::--- mask::r-x other::---`, and no default ACL.
  Bootstrap accepts a root whose GID is the machine and whose UID is 1001 or
  the machine. D218's two-pass deletion requires UID 1001.
- **D216.** `role_volume_migration.migrate` refuses
  `journal["principals"] != facts["principals"]`. `role_volume_inventory.reserved`
  separately refuses when a journaled center's principal changed. That second
  check is kept.

Why the root label needs a hand-off. Only the startup window holds `CAP_CHOWN`.
At runtime, the daemon (UID 1001, no capabilities, no supplementary groups) can
create a 1001-owned directory but cannot chgrp it to `<machine>`. A cell can
create only `<machine>:<machine>` inodes, which would make the root
owner-writable and break D4/D65's protected siblings. The mapper's namespace
does not map 1001. The kernel offers one capability-free way to get a foreign
GID onto a directory you own: create it under a setgid parent that carries that
GID. Directories inherit the GID and the setgid bit at creation. If the parent
has a default ACL, they also inherit the access ACL, without the creator naming
any UID. D17 (chmod and ACL writes clear S_ISGID for a non-member) is why the
daemon must never chmod the new root or write its access ACL afterwards.

## Options

**Option 1. Broker admission log, mapper verifies (recommended).**
The broker appends `admit` and `retire` rows. The mapper reads them over its
own lookup-only broker channel. The root is labelled by the setgid hand-off.
The restart contract is the log delta since the journal's generation.

- For: no numeric identity crosses the daemon channel (D68 holds). The broker
  stays the single identity authority (D61). Deletion leaves positive evidence,
  so a shrink is explained rather than guessed. The mapper and the journal
  share one generation.
- Against: a new IPC edge (mapper to broker, read-only), a new fixed cell
  class, and one new table. Center creation becomes a five-step protocol with
  crash points (DA4).

**Option 2. Daemon-asserted numbers, label-verified; evidence-based restart.**
The daemon sends `(principal, center, machine)`. The mapper checks the root's
GID and its own table for consistency. Restart accepts additions whose roots
carry a canonical label, and removals that have a daemon-written deletion
tombstone.

- For: no broker change and no mapper channel. About half the code.
- Against: it reverses D68's rule that numbers are not request fields. A daemon
  bug could then label and bind a center to any reserved identity whose
  principal has no current center (a deleted or center-less account). D61
  forbids reusing those identities while their files may exist. Shrink
  evidence would be the daemon's own file. The restart contract would also
  rest on filesystem labels, which reverse migration rewrites.

**Option 3. Restart-only admission.**
The table stays frozen. A center created after startup refuses every cell
request until the next restart, and restart admits it like a first volume.

- For: no runtime mapper or broker change.
- Against: a new user's first turns fail, which is the onboarding path. It
  makes restarts a product requirement. D216 still needs this same contract
  for deletion. Rejected.

Choose Option 1. Option 2 saves a channel and costs the boundary the whole
change exists to keep.

## Decisions

### DA1. The broker admission log

`owner-identities.db` gains `center_admissions(generation INTEGER PRIMARY KEY
AUTOINCREMENT, event TEXT NOT NULL CHECK (event IN ('admit','retire')),
principal TEXT NOT NULL, center TEXT NOT NULL, machine INTEGER NOT NULL)`. It is
written in the same serialized transaction with `synchronous=FULL`, under the
same private parent and descriptor checks as D61. Rows are never updated or
deleted. Invariants, checked inside the writing transaction:

- `admit` requires an existing reservation for the principal, and `machine` is
  that reservation. The broker fills in `machine`; the caller never supplies
  it.
- A center has at most one `admit` row, ever. `retire` requires a prior `admit`
  for the same principal and center, and at most one `retire` per center. A
  retired center is never admitted again.
- An identical repeated `admit` returns the existing row (idempotent retry).
  A conflicting one refuses.

OWNER channel op `CENTER_ADMISSION {event, principal, center}` returns
`{generation, machine}`. Center names use the bootstrap's existing character
set. Allocating a new user stays `OWNER_IDENTITY allocate=true`. A missing map
refuses, and runtime never initializes it (D61).

### DA2. The mapper's lookup-only broker channel

The D70 window creates one more unnamed SOCK_SEQPACKET pair before the mapper
forks: the broker end is inherited by the broker, the other by the mapper. It
carries exactly two read-only ops: `OWNER_MACHINE {principal}`, which returns
the existing reservation or "absent" and never allocates, and
`ADMISSION_ROW {generation}`, which returns the row or "absent". The broker
requires SCM_CREDENTIALS from the exact mapper PID, pinned
by a pidfd, as host 300000:300000. Any other op or field refuses and closes the
channel. There is no allocation, write or path. The mapper
never accepts a numeric identity from the daemon (D68 unchanged).

### DA3. Capability-free root labelling (setgid hand-off)

1. The daemon creates `/data/.role-admission/<token>/` (S), owned 1001:1001
   with no group or other access. It gives S the access ACL
   `user:<machine>:rwx` (mask `rwx`) and a default ACL equal to the canonical
   root access ACL (see Context). The daemon owns S, so both writes need no
   capability. `<machine>` comes from the daemon's own `OWNER_IDENTITY`
   lookup; it only shapes S, and the mapper never relies on it.
2. Over the D68 channel, the daemon asks the mapper for the fixed
   `center-root` class, passing `{principal, command_center}` and a
   pinned descriptor of S. The mapper resolves `machine` with
   `OWNER_MACHINE` (DA2), never from the daemon, and refuses if the center is
   already bound. A retired name fails later, at the broker (DA1). The cell
   runs capability-free under `<machine>` with cell-deny and a fixed deadline. It does exactly one thing:
   `mkdirat(S, "g", 0777)`, then `fchmod(g, 02777)`. The owner is in its own
   group, so S_ISGID survives. The cell gets no caller path, executable,
   environment, relay or credential.
3. The daemon checks with no-follow descriptors that `g` is a single
   directory `<machine>:<machine>` with mode 02777. It then runs
   `mkdirat(g, "root", 0750)` and never chmods the result. The root inherits
   GID `<machine>`, S_ISGID and the canonical access ACL from `g`'s default
   ACL. It also inherits a default ACL, which the daemon removes. The daemon
   then reads back the full `_label` tuple (UID, GID, mode, access ACL, default
   ACL) and refuses on any difference.
4. The daemon runs `renameat2(g/root, /data/<center>, RENAME_NOREPLACE)`, then
   removes `g` and S. It owns the root and S, so it has the write access both
   need. Finally it creates the same daemon-owned entries the forward migration
   creates in a center, for example `previews` as 1001:1001 0700.

The result cannot be told apart from a migrated canonical root, so every
existing reader, cell and D218 deletion applies unchanged. Task 1 measures the
four kernel facts this relies on, on a Docker ext4 named volume and on tmpfs:

- setgid inheritance into a daemon `mkdir` under an owner-owned 02777
  directory;
- the exact inherited access ACL;
- S_ISGID surviving removal of the default ACL;
- the rename and the removals working without capability.

If any of them fails, that is an acceptance stop, not a workaround.

### DA4. Admission order and crash recovery

Center creation on the selected path runs these steps in order:

1. Reserve the identity (`OWNER_IDENTITY allocate=true`).
2. Label the root (DA3).
3. Publish it by rename.
4. Append the `admit` row (DA1).
5. Bind in the mapper (DA5).

The center becomes usable only after step 5. Before that, its creation fails
loudly, and while the bounded client is installed there is no fallback to a
legacy `mkdir`.

Recovery depends on where a crash stops the sequence:

- **Before step 3.** S is garbage. The daemon removes it on retry, and startup
  removes `.role-admission/` with writers stopped.
- **Between steps 3 and 4.** The root is a published orphan. The daemon's retry
  appends the row idempotently. At restart, the coordinator appends it instead,
  but only when the root's `_label` tuple matches the durable reservation of
  the principal its authority database names (`role_volume_inventory.principals`).
  Startup is the same authority that seeds first-volume rows.
- **Between steps 4 and 5.** The daemon retries the bind. A restart binds the
  center from the table.

In step 5 the mapper handles an `admit {principal, command_center, generation}`
request:

- It fetches the row and requires `event=admit` with the same principal and
  center.
- It requires the row's generation to be above its bootstrap generation, the
  center to be unbound, and the machine to be unbound to any other principal.
- It opens `data_root/<center>` with no-follow and requires a directory with
  inner GID `machine-300000`, the overflow UID and mode 02750.
- The daemon answers the D85 host-UID question for that descriptor. The answer
  must be 1001.

Requests are serialized by the mapper's existing single serve loop.

### DA5. Mapper table lifecycle

At bootstrap the mapper receives `(bindings, generation)`, where `generation`
is the log high-water mark the startup inventory reconciled. At runtime its
table only grows by DA4's `admit` and shrinks by DA6's `retire`. Both are
authenticated daemon requests verified against the log. The table lives only
in mapper memory, and the log is the durable truth. Concurrency limits
(MAX_OWNER_CELLS, MAX_CELLS) and the D85 fences are unchanged.

### DA6. Deletion retires the binding

`role_owner_tree_deletion` (D218) keeps its order. Once the daemon pass has
removed the tree, the following happens before `finish` and before the
intent is cleared:

1. The daemon appends `retire` (DA1).
2. The daemon sends mapper `retire {principal, command_center, generation}`.
   The mapper verifies the row, requires the owner's D85 deletion fence, and
   requires zero running cells for that center. It then drops the binding.

Resume reruns whatever has not completed, and each step is idempotent. If the
intent is still present at restart, D218's forward resume handles it. A
`retire` row with no tree is the explained shrink.

### DA7. The admission-generation contract at restart

This supersedes D216's last sentence and closes D218's open item.

- `volume.json` (stable or migrating) records `generation`, next to
  `principals`.
- The coordinator computes the expected center map E. It starts from the
  journal's `principals`, then applies the log rows after the journal's
  `generation` in order: `admit` adds the center, `retire` removes it.
- With a stable forward journal, the inventory must equal E, after DA4's
  orphan adoption. In detail:
  - A discovered tree that is neither in E nor an adoptable orphan refuses.
  - A center in E with no tree is the F1 case.
  - A changed principal for a center refuses, as today.
- With no journal, or a stable reverse journal, the coordinator seeds `admit`
  rows for every inventoried center that lacks one. That covers the first
  volume and centers the legacy image created after a reverse. It then
  continues as a fresh forward. No `retire` is ever inferred.
- An interrupted journal keeps exact configuration matching (D216 unchanged),
  because writers were stopped throughout.
- The owner phase's `journal.json` configuration bindings reconcile by the same
  delta, and only after a completed phase. This extends D216's work-name
  reconciliation to the binding set. D214/D217 generation and provenance rules
  are unchanged. Runtime-created inodes in an admitted center have no journal
  row, so D214 treats them as new runtime content.
- Reverse needs no new rule. A runtime-admitted root has no journal row, so it
  reverses to legacy `1001:1001` with its ACL removed, as D214 already does.
  Retired centers have no tree.
- On completion, `volume.json` stores the new high-water `generation`. The
  mapper bootstraps with it.

### DA8. Switch and venue

All of this is inert unless the D69/D70 bounded client is installed. Today
only the unwired role-split bootstrap installs it. Legacy center creation and
legacy startup are unchanged. Proofs use `scripts/linux_oracle.py`, root
oracle and production-image modes. A skip is not a pass.

## Risks / Trade-offs

- **Kernel behaviour.** DA3 relies on setgid and default-ACL inheritance
  semantics, so task 1 measures them before anything is built.
- **A world-writable `g`.** It exists only inside daemon-private S, which only
  the daemon and that one owner can traverse. It is removed in the same
  admission.
- **A new broker parser.** DA2 is exact-field, bounded and read-only, and its
  peer is authenticated per packet.
- **The daemon still names the principal for a center.** That is the same
  authority startup already takes from `founder_home` and `universe_acl`; this
  change does not widen it. The mapper adds the guarantee that the number
  always comes from the broker.
- **Log growth.** At most two rows per center lifetime.
- **F1 default (a).** One unexplained loss stops the whole platform until a
  host action records it.

## Verification

Task 9's root-oracle matrix and task 11's production-image probe are the
acceptance. Both must show zero foreign bytes and zero retained capabilities.
`openspec validate owner-dynamic-admission --strict` covers this spec.
