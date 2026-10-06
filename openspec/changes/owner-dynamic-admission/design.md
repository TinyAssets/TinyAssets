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
  center. The center stays on a `missing` list in `volume.json` and is
  re-checked at every restart until a `retire` row or a host action clears it.
  If the tree is restored, the next restart binds it, because its row and its
  label still match.

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
- **Canonical root shape.** `role_owner_migration._permissions`, root kind:
  `1001:<machine>`, mode `live & 0o2750`, access ACL `user::rwx
  user:<machine>:r-x user:1002:--x group::--- mask::r-x other::---`, and no
  default ACL. Legacy roots come from a plain `mkdir`, so a migrated root is
  normally mode `0750` with no S_ISGID. Bootstrap accepts a root whose GID is the machine and whose UID is 1001 or
  the machine. D218's two-pass deletion requires UID 1001.
- **D216.** Three places refuse a changed binding set:
  - `role_volume_migration.migrate` refuses
    `journal["principals"] != facts["principals"]`;
  - `role_metadata_migration.migrate` requires
    `journal["configuration"]["bindings"] == bindings`;
  - `role_owner_migration.migrate` does the same, and also refuses an empty
    binding set.

  `role_volume_inventory.reserved` separately refuses when a journaled center's
  principal changed. That last check is kept.
- **Root removal outside deletion.** Two app paths `rmtree` a center root after
  creating it: the rollback in `api/universe.py` (around line 5763) and the
  incomplete-home retry in `api/first_contact.py` (around line 136).

Why the root label needs a hand-off. Only the startup window holds `CAP_CHOWN`.
At runtime, the daemon (UID 1001, no capabilities, no supplementary groups) can
create a 1001-owned directory but cannot chgrp it to `<machine>`. A cell can
create only `<machine>:<machine>` inodes, which would make the root
owner-writable and break D4/D65's protected siblings. The mapper's namespace
does not map 1001. The kernel offers one capability-free way to get a foreign
GID onto a directory you own: create it under a setgid parent that carries that
GID. Directories inherit the GID and the setgid bit at creation. If the parent
has a default ACL, they also inherit the access ACL, without the creator naming
any UID. A non-member's chmod (D17) or access-ACL write (the kernel's
`posix_acl_update_mode`) clears S_ISGID. DA3 uses exactly that: one final
chmod brings the root to the migrated shape.

## Options

**Option 1. Broker admission log, mapper verifies (recommended).**
The broker appends `admit` and `retire` rows. The mapper reads them over its
own read-only broker channel. The root is labelled by the setgid hand-off.
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

### DA2. The mapper's read-only broker channel

The D70 window creates one more unnamed SOCK_SEQPACKET pair before the
broker fork. The broker keeps its end open across exec, and the mapper inherits
the other end. The pair carries exactly three read-only ops:

- `OWNER_MACHINE {principal}` returns the existing reservation or "absent".
  It never allocates.
- `CENTER_STATE {center}` returns `unadmitted`, `admitted` or `retired`.
- `ADMISSION_ROW {generation}` returns the row or "absent".

PID1 sends the mapper's PID to the broker over the existing D70 proof pair, in
the same message as the proof hash. Until it arrives, the broker refuses every
op on this pair. Afterwards, every packet must carry SCM_CREDENTIALS for that
PID, pinned by a pidfd, as host 300000:300000. A retired daemon that sends a
wrong PID cannot pass the credential check. Any other op or field refuses and closes the
channel. There is no allocation, write or path. The mapper
never accepts a numeric identity from the daemon (D68 unchanged).

### DA3. Capability-free root labelling (setgid hand-off)

1. The daemon creates `/data/.role-admission/<token>/` (S), owned 1001:1001
   with no group or other access. It gives S the access ACL
   `user:<machine>:rwx` (mask `rwx`) and a default ACL equal to the canonical
   root access ACL (see Context). The daemon owns S, so both writes need no
   capability. `<machine>` comes from the daemon's own `OWNER_IDENTITY`
   lookup. It only shapes S; the mapper never relies on it.
2. Over the D68 channel, the daemon asks the mapper for the fixed
   `center-root` class, passing `{principal, command_center}` and a pinned
   descriptor of S. The mapper checks two things first:
   - it resolves `machine` with `OWNER_MACHINE` (DA2), never from the daemon;
   - it refuses unless `CENTER_STATE` is `unadmitted` and the center is
     unbound, so a retired name never gets a root.

   The cell then runs capability-free under `<machine>` with cell-deny and a
   fixed deadline. It does exactly one thing: `mkdirat(S, "g", 0777)`, then
   `fchmod(g, 02777)`. The owner is in its own group, so S_ISGID survives. The
   cell gets no caller path, executable, environment, relay or credential.
3. The daemon checks with no-follow descriptors that `g` is a single
   `<machine>:<machine>` directory with mode 02777. It runs
   `mkdirat(g, "root", 0750)`. The root inherits:
   - GID `<machine>` and S_ISGID;
   - the canonical access ACL, from `g`'s default ACL, with umask skipped;
   - a copy of that default ACL.

   The daemon removes the default ACL and runs `fchmod(root, 0750)`. The
   requested mode has no S_ISGID, so the bit clears. The kernel keeps the named ACL
   entries and sets the mask to `r-x`. The result is the shape forward
   migration gives a legacy `0755` root: `1001:<machine>`, mode `0750`, the
   canonical access ACL, and no default ACL. The daemon reads back the full
   tuple (UID, GID, mode, access ACL, default ACL), using the same comparison
   as `_permissions`, and refuses on any difference.
4. The daemon runs `renameat2(g/root, /data/<center>, RENAME_NOREPLACE)`.
   Moving a directory needs write access to the directory itself, and the
   daemon owns it. The daemon then removes `g` and S, which it may do because
   it owns S. Last, it creates every entry forward migration creates in a
   center, each with an explicit owner and mode read back afterwards. Today
   that is only `previews`, 1001:1001 0700. With S_ISGID cleared, these
   daemon-created siblings keep GID 1001.

Every existing reader, cell and D218 deletion treats the result like a migrated
root. Task 1 measures each kernel fact this relies on, on a Docker ext4 named
volume and on tmpfs:

- setgid inheritance into a daemon `mkdir` under an owner-owned 02777
  directory;
- the exact inherited access ACL;
- the final chmod clearing S_ISGID while keeping the named entries;
- the cross-parent rename and the removals working without capability.

If any of them fails, that is an acceptance stop, not a workaround.

### DA4. Admission order, failure and crash recovery

Center creation on the selected path runs these steps in order:

1. Reserve the identity (`OWNER_IDENTITY allocate=true`).
2. Label the root (DA3).
3. Publish it by rename.
4. Append the `admit` row (DA1).
5. Bind in the mapper.

The center becomes usable only after step 5. Before that, its creation fails
loudly, and while the bounded client is installed there is no fallback to a
legacy `mkdir`.

**Failure after publish.** On the selected path, no code removes a published
root except D218 deletion, which retires it (DA6). Admission is idempotent for
the same principal and center: a repeated create resumes at the first
unfinished step, and a center that is already bound is a success, not a
refusal.

A failed create after step 3 is never abandoned. The create reports the
failure, keeps the root and the grant, and resumes in place on the next attempt
with the same id. That attempt finishes steps 4-5 first, then reruns seeding.
This one rule covers both `rmtree` sites, which are one call path:
`first_contact.ensure_founder_home` reaches the `api/universe.py` rollback
through `_universe_impl`. Removing a center a user no longer wants is ordinary
deletion (DA6), which needs a bound center. That is why abandoning mid-admission
is not offered: D218 cannot run before the bind, and `retire` cannot precede
`admit`. Task 6 changes both sites.

**Crash recovery.** What a restart finds depends on where the crash stopped
the sequence. Startup removes `.role-admission/` with writers stopped, before
the inventory and before any phase's dry run.

- **Before step 3.** S is garbage. The daemon also removes its own staging on
  retry.
- **Between steps 3 and 4.** The root is a published orphan. The daemon's retry
  appends the row idempotently. At restart, the coordinator appends it instead,
  but only when two things hold: the root's tuple matches the durable
  reservation of the principal its authority database names
  (`role_volume_inventory.principals`), and `CENTER_STATE` is `unadmitted`.
- **Between steps 4 and 5.** The daemon retries the bind. A restart binds the
  center from the table.

Startup log writes, for adoption and for DA7 seeding, go through a broker child
that has already retired, as `role_volume_migration._allocate` does. Root never
opens the broker-private database.

**The bind (step 5).** The daemon sends
`admit {principal, command_center, generation}`. It attaches an O_PATH
descriptor of the published root, which it has just checked is host UID 1001
with the canonical tuple. The mapper then:

- fetches the row and requires `event=admit` with the same principal and
  center;
- requires the row's generation to be above its bootstrap generation, the
  center to be unbound, and the machine not to be bound to another principal;
- opens `data_root/<center>` with no-follow, requires the same device and inode
  as the attached descriptor, and requires a directory with inner GID
  `machine-300000`, the overflow UID and mode 0750.

The mapper cannot see host UID 1001. It takes that fact from the daemon's
descriptor-bound assertion, which is the same trust D85 places in the daemon.
No extra round trip is needed. Requests are serialized by the mapper's existing
single serve loop.

### DA5. Mapper table lifecycle

At bootstrap the mapper receives `(bindings, generation)`. `generation` is the
log high-water mark that the startup inventory reconciled. `bindings` includes
every center that has a tree and either is in the expected set E (DA7) or has a
pending deletion intent, so D218's resume can rerun pass one. At runtime the
table only grows by DA4's `admit` and shrinks by DA6's `retire`. Both are
authenticated daemon requests verified against the log. The table lives only
in mapper memory, and the log is the durable truth. Concurrency limits
(MAX_OWNER_CELLS, MAX_CELLS) and the D85 fences are unchanged.

### DA6. Deletion retires the binding

`role_owner_tree_deletion` (D218) keeps its order. Once the daemon pass has
removed the tree, and also on its resume path where the tree is already gone
(including a center on DA7's `missing` list, which today falls through to the
legacy traversal), the following happens before `finish` and before the
intent is cleared:

1. The daemon appends `retire` (DA1). A repeat returns the existing row.
2. The daemon sends mapper `retire {principal, command_center, generation}`.
   - If the center is bound, the mapper verifies the row, requires the owner's
     D85 fence and zero running cells for that center, then drops the binding.
   - If the center is unbound, which is the case after a restart, the request
     succeeds as a no-op once the row is verified.

Each step is idempotent on resume. A center with a pending deletion intent is
always explained at restart, whether or not its tree is present or a `retire`
row exists (DA7). The daemon's resume writes the `retire` row; startup never
infers one.

### DA7. The admission-generation contract at restart

This supersedes D216's last sentence and closes D218's open item.

**Journal fields.** `volume.json` (stable or migrating) records `generation`
next to `principals`. It also records `missing`, a center-to-principal map
that is non-empty only under F1(b). A center on `missing` is retired like any
deleted center: DA6's tree-absent route writes its `retire` row, which removes
it from `missing` at the next restart.

**Expected set.** The coordinator computes the expected center map E. It
starts from the journal's `principals` plus `missing`, then applies the log
rows after the journal's `generation` in order: `admit` adds the center and
`retire` removes it.

**Stable forward journal.** The inventory must equal E, with three exceptions:
DA4's orphan adoption, centers with a pending deletion intent (DA6), and the F1
case. In detail:

- A discovered tree that is not in E, not an adoptable orphan and not under
  deletion refuses.
- A center in E with no tree and no pending intent is the F1 case. Under (a)
  startup refuses. Under (b) the center goes to `missing`, stays unbound and
  raises the alarm.
- A changed principal for a center refuses, as today.

**No journal, or a stable reverse journal.** The coordinator seeds `admit` rows
for every inventoried center that lacks one. That covers the first volume and
centers the legacy image created after a reverse. It then continues as a fresh
forward. No `retire` is ever inferred.

**Interrupted journal.** Exact configuration matching stays (D216 unchanged),
because writers were stopped throughout.

**Phase journals.** The same delta applies to all three refusal sites in
Context, and only after a completed phase. That is the volume journal, the
metadata phase's `metadata.json` and the owner phase's `journal.json`, so
D216's work-name reconciliation now extends to the binding set. D214/D217
generation and provenance rules are unchanged. Runtime-created inodes in an
admitted center have no journal row, so D214 treats them as new runtime
content.

**Empty volume.** If E is empty after retirements, the owner and metadata
phases skip their inode work instead of refusing an empty binding set. They
still rewrite their journals' binding configuration to the empty set before
`volume.json` advances, so no phase journal lags the volume generation.

**Reverse.** No new rule is needed. A runtime-admitted root has no journal
row, so it reverses to legacy `1001:1001` with its ACL removed, as D214
already does. Retired centers have no tree.

**Completion.** `volume.json` stores the new high-water `generation` and the
current `missing` list. The mapper bootstraps with that generation.

### DA8. Switch and venue

All of this is inert unless the D69/D70 bounded client is installed. Today
only the unwired role-split bootstrap installs it. Legacy center creation and
legacy startup are unchanged. Proofs use `scripts/linux_oracle.py`, root
oracle and production-image modes. A skip is not a pass.

## Risks / Trade-offs

- **Kernel behaviour.** DA3 relies on setgid and default-ACL inheritance
  semantics, so task 1 measures them before anything is built.
- **Root removal in app code.** The two existing `rmtree` sites would strand
  an `admit` row, so task 6 must change them before the switch can turn on.
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
