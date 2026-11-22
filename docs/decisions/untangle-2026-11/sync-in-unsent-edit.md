# sync-in-unsent-edit as built (2026-11-22)

Owed by `sync-stage-race.md` (#5795): a sync-in replaced a workspace file holding changes the
sandbox had not sent (one refused at the owner's full storage, or a second run's edit) when
the owner's files changed it meanwhile, so the sandbox's edit was lost without a word
(Fact 8). The same log left a session already marked changed (`UNREAD`) at staging: dropped,
not sent, so two writers of one transcript ended in a drop or a conflict copy by their order.

## Research (Fact 14), read 2026-11-22 on main `6042dc11a`

**How sync-in decides to write** (`sync.ts` `apply`): only by the version. A path the feed
names at a version other than its mirror row's is fetched over the local file (`fetchInto`,
renamed into place), or removed (`vanished`: the file, or a folder's whole tree). Nothing
compared the local file to the row's identity (size, change time, hash), the check sync-out
makes before it offers a file (`offer`, `heldBack`).

**What the owner's commit does with a stale base** (`owner/src/files.ts` `#syncOp`): a write
against a version that moved on, or with no base where the path exists, keeps the owner's
version, saves the sandbox's bytes under the first free `<name>.ta-conflict-<epoch>[-n]`
(`#copyPath`), and answers `conflict` with `current`, the owner's entry (null when gone). A
remove against a moved base is a conflict with no copy. The run reports each conflict
(`ta.sync_conflicts`). A folder removed through the files door records a change for every
entry under it before the folder's own (`#remove`), so the feed names each file first.

**The loop the builder named.** A file left in place keeps its row, so its sync-out goes
against the base it was read at and conflicts: the copy is made once. But the feed's cursor
has moved past the owner's version, so nothing ever fetches it: every later sync-out sends
the sandbox's bytes again and makes another copy. So the conflict itself has to end with
the sandbox taking the owner's version.

**How #5795 ended a session's race in one copy**: the mark (`UNREAD`) kept the row's version,
so the staged copy conflicted; the mark stayed, so the next staging dropped the store's copy
and a resume fetched the owner's. The drop is the session's way of taking the owner's
version. It was safe only while the store's copy was the one sent: the mark blanked the hash,
so staging could not tell a copy synced as it is from one holding a newer turn (a run
resumed meanwhile, or a turn refused at a full storage), and dropped both. A resume's fetch
replaced the store's copy on the mark alone too, and the fetched bytes were written over a
copy a run kept meanwhile.

**What sync-out sends** is the walk's rule (`syncOut`'s `skip`, the per-file read-only
check): a regular file with a UTF-8 name, not the sessions' tree, no `.ta-` temporary name,
not a client's read-only tree, no custom line's path, no default line's path the owner does
not keep. A change the walk never offers is never sent, so a file holding one is not an
unsent edit: a held-back file's changes are said each sync (`held_back`), a default line's
are rebuilt.

**The mirror's format.** Keeping a session's hash under the mark needs a column (`unread`),
and a no-base row (`version` nullable): format 2. The rule ships a format change in two
deploys so a disk living across one stays readable; no disk can today (no owner has a
sandbox before the cutover's routes, `session-store-pressure.md`, the same day), the same
fact that let protocol 5 ship in one deploy. A runner meeting a disk it does not read refuses
it by name (`formatStep`), so a rollback meets only that, and takes no forward entry (as
#5794). (The review found the premise stale and this deploy reading format 1 too, below.)

## Decided

1. **One rule for every synced path: a copy holding changes sync-out sends is never
   replaced, removed or dropped by the owner's newer version.** It is left with its row, so
   sync-out sends it against the base it was read at; the owner's commit keeps both and
   reports it; then this sandbox takes the owner's version. One conflict copy, no loop; a
   copy changed again since it was sent is a change of its own, sent again (its own copy).
2. **Workspace files** (`theirs`): sync-in leaves a file that differs from its row (or has
   none) where sync-out would send it (`Selection.sends`, the walk's own `skip`, so the two
   never disagree). A conflict's outcome takes the owner's state at once (fetched, a folder
   made, or removed), only while the file is still the copy sent. Fetched bytes go into
   place only while the file is the one looked at before the fetch (`same`: inode, size,
   change time), so a run's write meanwhile is left too. The window left is the look to the
   rename, the narrowest a filesystem without a compare-and-rename allows. A crash between a
   conflict and its take sends the copy again: a second copy, never a loss.
3. **A folder gone** from the owner's files goes but for the files under it holding unsent
   changes, each kept with its row (a conflict there, then removed); with none, it goes
   whole, as before.
4. **Sessions** keep their hash under the mark (`Known.unread`, format 2). Staging drops a
   marked copy only while it is the one synced and still the one hashed; one holding changes
   is sent, a conflict copy at the owner's files. A session's conflict leaves its row on its
   base with the sent copy's hash, marked, so the next staging lets that copy go and a
   resume fetches the owner's. A resume never fetches over a store copy holding changes,
   nor names it gone, and puts what it fetched only where the store still holds the copy it
   hashed (`storeResume`, `stillHashed`, which the let-go shares). A path the feed names with
   no row is marked with no base and no bytes, so a copy of it is a conflict wherever the
   owner's files hold the path (it took the feed's version before: a copy would have landed
   over theirs).
5. **A path sync-out never sends takes the owner's version**, as before: a held-back file's
   changes are the owner's line's, said each sync; a default line's are rebuilt; a client's
   read-only tree is never written by its sandbox.
6. **Format 2, its contract deploy alone** (`STATE_FORMAT` 2, `READS` [1, 2], format 1's
   mirror rebuilt on open, its `""` hash read as the mark); `state-format.ts`'s rule says
   when the expand deploy may be skipped (review, below).
7. **The sync-ignore's defaults are written by sync-out alone** (`keepDefaults`): sync-in reads
   the list only when it meets a file that may hold changes, and must not make a local copy
   of it before the owner's arrives.
8. **The tests** run each race in the sync agent's jobs' order against the stand-in owner,
   which now answers a conflict as the owner does (`current`, the first free copy name): a
   file refused at a full storage, a run's write during the fetch, a write during the
   conflict's commit, a new file the owner makes too, a file and a folder the owner removes,
   a default line's file, and the sessions' fetch and staging (`runner.test.ts`).

## Found and left

- **A path that changes kind** (the owner makes a file where the sandbox holds a folder):
  sync-in's rename onto the folder fails loudly (`is a directory`), and so does every sync-in
  after it, until the folder goes. It predates this change and is not this race.
- **A gone folder keeping a file** leaves what sync-out never sends under it (an ignored
  tree, a link): only while a file under it holds unsent changes.

## Review (2026-11-22)

- **The one-deploy premise is stale.** The stage serves the routes on `stage.tinyassets.io`
  (its gate closed, no owner admitted yet), and two stage accounts are about to be let in, so
  a sandbox on format 1 may exist before this deploys. What each side does, read in
  `control.ts` (`#speaking`, `#adopt`) and `sandbox.test.ts` ("starts on the runner it has"):
  a runner's `adopt` refusing the disk is an adoption failed, so the Worker keeps the
  sandbox's runner, logged (`sandbox_runner_adopt_failed`) at every start. So reading format
  2 alone stranded no sandbox, but kept a format 1 one on the runner before, without this
  fix, until killed. This deploy now reads format 1 too (decision 6), so such a sandbox
  adopts it. A rollback of one deploy still meets format 2 with a runner that does not read
  it: it keeps the sandbox on this runner, never stranded, so it takes no forward entry.
- **Left, owed:** a resume's fetch moves the row to the owner's version before the root
  puts the bytes in the store (`fetchSessions`, then `storeResume`). Where the store's copy
  changed meanwhile (another run's `keepHome` of the same file), the fetched bytes are let
  go but the row stays on the owner's version, so that copy, made on the older base, is sent
  later over the owner's without a conflict (the owner's version kept in its history). It
  needs a store copy written during one fetch of the same file, which the owner's one live
  run per conversation all but rules out, and before this change the same race lost the
  run's copy instead.

## Owed

- [ ] `untangle/sync-resume-took`: a resume's fetched session moves the mirror's row to the
  owner's version only once the store took its bytes (a second sync job after the import,
  applied only to a row unchanged since the fetch), so a copy kept meanwhile is sent against
  its own base, a conflict copy.
- [ ] `untangle/sync-kind-change`: a path whose kind changes in the owner's files while the
  sandbox holds the other kind is taken by the same rule (the sandbox's unsent files sent
  beside, then the owner's kind), never a sync-in that fails every time.
