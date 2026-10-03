## Why

**A command center can forge its own owner's consent, today.** The effector
consent database lives inside the command-center folder
(`storage/effector_consents.py:45`, `Path(universe_dir) / ".effector_consents.db"`),
its schema is `CREATE TABLE IF NOT EXISTS` so a file that already exists is
adopted rather than refused (`:60`), the consent lookup reads rows from it
(`:263`), and the external-call gate trusts that answer
(`effectors/authenticated_external_call.py:741`). A command center's own
processes can write that folder. So whichever party creates the file first
decides what the owner has consented to.

That is the whole argument. Everything below follows from it.

The same folder placement produces a second, measured defect. A per-command-center
database is opened by NAME, so a link planted at the name redirects the daemon:
measured in the Linux oracle 2026-10-03, the daemon read another command
center's rows **and committed a row into its database**
(`docs/concerns/2026-10-03-a-universe-database-name-steers-the-daemon.md`). PR
#4370 added `universe_files.connect_guarded`, which refuses a link at the name
and detects an identity change around the connect — but its own docstring is
explicit that this is detection in a narrow window, that an ABA swap defeats it,
and that it says nothing at all about *who created the file*. It does not touch
the consent hole.

Two attempts have now failed to fix this in place:

- PR #4330 proposed a `?nofollow=1` URI parameter. It does nothing: SQLite
  ignores unrecognised URI parameters silently, including invented ones.
- #4330's other mechanism was per-database provenance records (device and inode,
  recorded outside the folder, compared on open). Its review found three P1
  defects in that machinery — a post-epoch forgery window, inaccurate mode
  parsing, and an unrecoverable interrupted state — and it was not carried
  forward.

Both failed for the same reason: they try to tell a forged file from a real one
*after* putting it somewhere the command center can write. The fix is to stop
putting it there.

## What Changes

- **Platform state moves out of the command-center folder**, into the existing
  `<data>/.universe-sidecars/<command center>/`. That directory already exists
  for exactly this reason and says so: "daemon-owned files that belong to one
  universe but must not live inside it … No jail binds that directory, so
  nothing a universe runs can replace them"
  (`providers/provider_jail.py:347-350`). Today it holds one thing, the egress
  proxy socket. This extends a reviewed pattern rather than inventing a
  destination.
- **The consent database moves first**, on its own, because it is the live
  authority hole and it is small: one filename, one path helper, one migration.
- **Then the rest, enumerated rather than assumed.** `.runs.db` at a command
  center root is confirmed (`api/resource_usage.py`,
  `api/storage_observations.py:135`). The complete list is a task, not a claim
  in this proposal — and the enumeration itself gets a test, so a future store
  placed inside a command center fails rather than joining the problem.
- **No consent survives the move.** Decided 2026-10-03: the migration carries
  **nothing** forward. There is no provenance record for rows written before
  this change, so a carried row would be indistinguishable from the forgery
  being fixed. The sidecar database is created empty and the prior file is
  renamed aside, not read. The re-grant is the ordinary one-click consent ask
  at the first use of each effect, worded as a one-time re-confirmation after a
  security move -- no migration wizard, no batch.
- **Deletion, accounting and backup follow the data.** Account deletion,
  `storage_accounting` and the backup set all currently reach these paths
  inside the command center; each must reach the new location, and the
  account-deletion sweep must not leave a sidecar behind.

## Impact

- **Storage shape and a migration**, which is why this is a proposal and not a
  branch: the layout changes, the move is one-way, and a half-migrated volume
  must be a refusal rather than a silent mixture.
- **Authority.** The consent gate's answer stops depending on a file the asking
  party can create. That is the point.
- **Code:** `storage/effector_consents.py` (the path helper), then each store the
  enumeration finds; `scoped_reset.py` and the account-deletion sweep;
  `storage_accounting.py` (the root-entry registry already describes
  `.universe-sidecars`); `deploy/backup.sh`'s set; `storage_layout.py` gains the
  migration and its marker.
- **Not affected:** the per-launch credential snapshots under
  `.runtime/provider-launch-credentials/`, which a provider child must read
  through its jail. Those stay, and stay jail-bound.
- **Specs:** new capability `platform-state-location`. `credential-vault`'s
  as-built storage requirement is amended when the vault itself moves, which is
  a later phase of this change, not the first.
- **Follow-up this enables, not required by it:** once the per-role uid split
  (`openspec/changes/per-role-uid-split`) lands, a sticky bit on the parent
  stops a child uid renaming a daemon-owned entry at all. Useless today —
  command-center processes share the daemon's uid, so `+t` buys nothing — and a
  cheap second line afterwards.
