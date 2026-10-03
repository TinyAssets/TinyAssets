## Context

A command center is a folder under the data root that the command center's own
processes can write — a workflow provider jail binds it read-write and permits
`symlink`. The daemon keeps platform state in that same folder: hidden
databases, the credential vault, locks. Two properties follow, and both have
now been measured rather than argued:

1. **Whoever creates a file first owns what it says.** `.effector_consents.db`
   is adopted if it already exists (`CREATE TABLE IF NOT EXISTS`,
   `storage/effector_consents.py:60`), its rows are read as consent (`:263`),
   and `effectors/authenticated_external_call.py:741` trusts the result.
2. **A name can be redirected.** Linux oracle, 2026-10-03: with a link planted
   at a per-command-center database name, the daemon read another command
   center's rows and committed a row into its database.

`providers/provider_jail.py`'s `hidden_root_masks` is the current mitigation for
(2): it masks every hidden root entry for the duration of a jailed launch and
refuses a launch outright if one is already a link. Its docstring names this
exact threat. But it covers jailed provider launches, and the daemon's own opens
happen outside any jail at any time. It does nothing for (1): a masked file is
still adopted when the mask comes off.

Two in-place fixes have failed. `?nofollow=1` is ignored by SQLite. Per-database
provenance records (#4330) collected three P1 defects — a post-epoch forgery
window, inaccurate connection-mode parsing, and an unrecoverable state after an
interrupted create — because "tell a forged file from a real one" is a harder
problem than it looks when the adversary writes the same directory.

## Goals / Non-goals

- **Goal:** platform state that decides authority is not writable by the party
  it decides about. Specifically: a command center cannot create, replace or
  link the file its own consent is read from.
- **Goal:** one-way, resumable migration with a refusal rather than a silent
  mixture.
- **Goal:** the enumeration is enforced, so a store added later cannot quietly
  be placed inside a command center.
- **Non-goal:** moving what a command center's own code must read. The
  per-launch credential snapshots under `.runtime/provider-launch-credentials/`
  stay where the jail can bind them.
- **Non-goal:** the sticky-directory hardening. It is useless until the uid
  split lands and is a follow-up afterwards.
- **Non-goal:** retiring `connect_guarded` (#4370). It stays as a second line
  for anything still opened by name during and after the move.

## Decisions

### D1. The destination already exists

`<data>/.universe-sidecars/<command center>/`, from
`providers/provider_jail.py:347-350`: "Daemon-owned files that belong to one
universe but must not live inside it … No jail binds that directory, so nothing
a universe runs can replace them." It holds the egress proxy socket today
(`universe_egress.py:389,459`) and is already described in
`storage_accounting.py:560`'s root-entry registry.

Reusing it rather than inventing a path means: no new root entry to register,
no new jail rule to get wrong, and the "no jail binds this" property is already
reviewed and tested. The alternative — a new `<data>/.platform/<cc>/` — buys
nothing and adds a second concept.

### D2. The consent database moves first, alone

Smallest change that closes the live authority hole:
`storage/effector_consents.py:45`'s `consents_db_path` returns the sidecar path
instead of the in-folder one. Everything else about that module is unchanged.

It goes first because it is the only store where the in-folder placement
produces a *forgeable authority answer* rather than a redirectable read. The
rest are a cleanup; this one is the reason.

### D3. The migration refuses what it cannot account for

For each command center, in one locked pass:

1. If the sidecar database exists and the in-folder one does not, nothing to do.
2. If the in-folder one exists and the sidecar does not, create an **empty**
   sidecar database and rename the in-folder file aside to
   `.effector_consents.db.premigration`. No row is copied — see the decision
   below. The old file is renamed rather than deleted so an operator can still
   read what was there.
3. If both exist, refuse the command center loudly and leave both in place.
   That state is either an interrupted run (resumable by hand) or something
   worse, and guessing is how a forged file gets blessed.

**DECIDED 2026-10-03 (lead; the founder may override): carry nothing.** Step 2
creates an **empty** sidecar database and renames the in-folder file aside. No
existing row is copied.

The reasoning, recorded because it is the kind of decision that gets revisited:
there is no provenance record for consent rows written before this change —
that is exactly what #4330 tried and failed to build — so the migration cannot
prove an existing row was granted by the owner. **A row carried forward is
indistinguishable from the forgery this change exists to prevent.** Copying
them would mean the first act of the fix is to bless the thing it is fixing.
This is also the standing preference while the platform is early: a clean
cutover rather than a compatibility shim.

I had recommended the opposite (copy rows, disclose them to the owner, let them
revoke) on the grounds that the exposure window was already the status quo. The
decision went the other way, and it is the stronger reading: "already exposed"
is not a reason to carry an exposure across a migration whose entire purpose is
to end it.

**The cost is real and is paid down rather than accepted.** Every consent must
be granted again, so the re-grant has to be cheap or it becomes an outage:

- nothing is pre-asked and nothing is batched — the first use of each effect
  raises the **normal** consent ask, which is already a one-click
  Waiting-on-you item;
- its wording says this is a one-time re-confirmation after a security move,
  not a new or unexpected request, so an owner who sees it understands why and
  does not read it as a malfunction;
- the ask carries the same sink and destination it always did, so an owner who
  does not recognise one has learnt something worth knowing.

That turns "every integration breaks at once" into "each integration asks once,
at the moment it is used, with a reason attached". It is the same mechanism the
system already uses, which is why it is cheap.

### D4. The enumeration is a test, not a list in this document

A test walks the platform's own path helpers and asserts that none of them
resolves inside a command-center folder, with an explicit allowlist for the
things that must stay (the `.runtime` snapshots the jail binds). A store added
later either stays out or fails the test — the same derivation discipline that
caught `artifacts/` in the package allowlist, where a hand-written list of names
would have stayed green.

This is also why this proposal does not claim a complete inventory. Two
per-command-center stores are confirmed — the consent database and `.runs.db`
(`api/resource_usage.py`, `api/storage_observations.py:135`). The rest comes
from the test, which is build work.

### D5. Everything that reaches these paths follows them

Each of these currently names a path inside the command center and must be
changed in the same phase as the store it reads:

| Reader | Why it matters if missed |
|---|---|
| account deletion / `scoped_reset.py` | a sidecar left behind is retained user data after a delete |
| `storage_accounting.py` | bytes stop being charged, or are charged twice |
| `deploy/backup.sh`'s set | the state stops being backed up, silently |
| `storage_layout.py` | the layout marker must refuse an image that predates the move |

Account deletion is the one with a compliance edge: a sidecar that survives a
delete is the `a-delete-starts-with-every-reader` problem, so the deletion sweep
is derived from the same enumeration as D4 rather than listed separately.

## Risks / Trade-offs

- **The migration is the dangerous part**, not the path change. It touches every
  command center once, it is one-way, and D3's "both exist" refusal means a
  partially migrated volume needs a human. Mitigated by running under the
  exclusive layout lock before any role starts, by the resumable shape, and by
  the marker refusing a pre-move image.
- **Every consent must be granted again.** The accepted cost of D3, and the
  largest user-visible effect of this change. Mitigated by the re-ask being the
  normal one-click ask at first use with a reason attached, not a migration
  wizard and not a batch. The failure mode to watch for is an owner who is not
  present: an automation whose first post-migration run needs a consent nobody
  is there to click waits, which is the correct behaviour but should be visible
  rather than silent.
- **A second place to look.** Per-command-center state is now in two places
  during the move and in one afterwards. The enumeration test is what stops it
  being two places forever.
- **`connect_guarded` stays**, so there is still a guard whose docstring says it
  is partial. That is correct while any store is still opened by name.

## Verification

- A command center that creates `.effector_consents.db` in its own folder before
  the daemon does gains nothing: the consent gate reads the sidecar, and the
  in-folder file is never adopted. This is the test that would have caught the
  original hole.
- The enumeration test (D4) fails when a path helper is pointed back inside a
  command center.
- Migration run twice changes nothing; killed mid-way, it resumes; with both
  files present, it refuses and names the command center.
- Account deletion leaves no sidecar — asserted by walking
  `.universe-sidecars/` after a delete.
- The Linux oracle proves a planted link at the old in-folder name has no effect
  on the consent answer after the move.
- `deployed_sha.py --assert-contains` plus a read-only prod check that the
  sidecar directory holds the consent database and the command-center folders do
  not.
