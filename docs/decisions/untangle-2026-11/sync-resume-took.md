# sync-resume-took as built (2026-11-22)

Owed by `sync-in-unsent-edit.md` (#5798), two items, and a third the #5799 review found in
the same code:

1. A resume's fetch (`fetchSessions`) moved a session's mirror row to the owner's version
   before the root runner put the bytes in the store (`storeResume`). Where the store's copy
   changed meanwhile (another run's `keepHome` of the same file), the import let the fetched
   bytes go, the row stayed on the owner's version, and that copy, made on the older base,
   was later committed over the owner's version with no conflict (theirs kept in history only).
2. A path whose kind changes in the owner's files (a file where the sandbox holds a folder, or
   the reverse) failed sync-in loudly at every run until the folder went.
3. `goneFolder` walked a gone folder in sorted mode, so each folder's whole listing sat in the
   sync agent's heap (about 300 bytes a name): 1.5 million names fill it.

## Research (Fact 14), read 2026-11-22 on main `9409f8949`

**Who writes a session's row, and when** (`sync.ts`): the feed's mark (`unread`, keeping the
base and hash), a sync-out's outcome (a commit's version, or a conflict's base with the copy's
hash, marked), the offer's identity refresh, and the fetch. Each job runs under the sync
agent's lock; between two jobs any run's job may take it. The store is root's alone: the sync
agent cannot read or write it, so only the root knows what the store took.

**Why the row moved at the fetch.** So the feed's version check (`apply`) skipped a later
change naming the version just fetched, and a mark for a newer one stuck. A row that waits for
the import loses that: the feed's mark on an already-marked row changes nothing, so a later
step cannot tell "the feed named a newer version" from "nothing happened". What can tell is the
feed's cursor: it moves only when a sync-in read changes. A moved cursor may be another path's
change, so it may only cost a fetch again, never a copy.

**What the owner's commit does with a kind change** (`owner/src/files.ts` `#syncOp`,
`#copyPath`): a write onto a folder is a conflict, the copy beside it, `current` the folder; a
write under a file (`#blocked`) is a conflict whose copy goes beside that file with the rest of
the path below it (`docs.ta-conflict-1/kept.md`), `current` null, since the path itself holds
nothing. The files door removes a folder by its entries first, then the folder; it makes a
folder's missing parents (`#parents`, recorded `mkdir`, kind `dir`), so the feed names a folder
before what it holds, and a removal of the sandbox's file before the owner's folder there.

**What sync-in did with one** (`theirs`): an owner's file over a local folder renamed onto the
folder (`is a directory`, `safeStream`); an owner's folder over a local file failed in
`safeDirs` (`not a directory`), and every path under it in `lstat`/`rmSync` (`ENOTDIR`). The
cursor never moved past the page, so every sync-in failed the same way. Nothing was lost; the
sandbox stopped taking the owner's files at all.

**Whether a gone folder's walk needs sorted order.** It removes each file as the walk hands it
over. POSIX `readdir` leaves unspecified only whether an entry made or removed since the open
is returned; every other entry is returned once. The walk removes only entries it has already
read, so the order buys nothing and the unsorted walk (one entry at a time, #5799) is safe.

## Decided

1. **A resume's row follows what the store took.** `fetchSessions` moves no row: it reports
   each fetch with the row's base and hash it was fetched against, and the feed's cursor. The
   root imports only those fetches (no walk of the staged directory, so a crash's leftovers
   never go in unreported), each only where the store still holds the copy it hashed, and
   hands what it took back to the sync agent as a second job, `took` (`tookSessions`), only
   when something was taken. Each taken row moves to the owner's state as fetched (a gone one
   dropped) only while it is still the row fetched against; it stays marked where it was marked
   and the cursor moved since. A fetch not taken leaves the row on the base the store's copy was
   made on, so that copy is sent against it: one conflict copy.
2. **A crash between the fetch and `took`** leaves the row on its old base: a store copy taken
   is sent against it, a conflict copy of bytes equal to the owner's; a copy not taken is
   fetched again. A second copy at worst, never a loss (as decision 2 of #5798).
3. **A path that changes kind goes to the owner's kind, by the one rule.** In `theirs`: a file
   of the sandbox's own where the owner's files hold a folder is replaced by one (its row
   dropped: a folder has none), where it holds no unsent changes; a folder where they hold a
   file goes as a gone folder does, and the file is fetched once it went.
4. **A file the sandbox cannot take yet is marked** (`mark`, the mirror's `unread`, as a
   session's): the owner's file where a folder still holds unsent files, and the owner's files
   under a folder where a file of the sandbox's holding unsent changes stands (`onFile`). Those
   unsent files go out against their bases; the owner's commit keeps them beside its own; the
   sync-out's conflict take replaces the file by the folder, or empties the folder. The
   sync-out's scan of the mirror takes each marked path (`theirs`, the mark dropped first, so a
   path the owner no longer holds as a file leaves none), and the walk's batch is sent before
   the scan, so a mark is taken by the same sync-out that sent what stood in its way. Still in
   the way (refused, or changed again), it is marked again. A sandbox removing its own unsent
   file there is covered by the same scan.
5. **A gone folder is walked an entry at a time** (no `sorted`); the sorted walk stays for the
   root's stores and homes (`sortedWalk`), whose order is read. The walk removes each file only
   once read; the folder's own removal after it (`remove`) takes, unread, a file a run made in
   it after its folder was read, as the sorted walk's did (owed below).
6. **No format change.** The schema and the columns' values are format 2's; a workspace row may
   now be marked. A runner one deploy back reads such a row as a file gone: a removal against
   the row's base, always older than the owner's version (a row is marked only where the owner's
   version is not the row's, and versions are the space's one sequence) or none, so
   a conflict at the owner's files, then its own `theirs`, which throws where the path still
   changes kind (a folder created on a file, a file renamed onto a folder; neither removes a
   byte). That is the old loud failure at that path, no loss; `took` is a job between one
   runner's own processes, in no protocol.
7. **The tests**: the race in the sync agent's jobs' order (a fetch not taken, a fetch taken, a
   row moved by a sync-out before the take, the cursor moved before it, a gone session with a
   copy kept meanwhile, `sync.test.ts`), the real interleaving through the root runner (the
   doors' read of the session writes the store's copy, `runner.test.ts`), both kind changes
   with unsent files (one deeper than a folder) and without, and a gone folder of 22,000 long
   names holding no more than one of 2,000 (`large`). The stand-in owner answers a write onto a
   folder or under a file as `#syncOp` does.

8. **Found in review: each run fetches into its own directory** (`in/<run>`, as `out/<run>`).
   The fetch wrote into one directory an engine's (`in/<engine>`), which each import removed
   whole: a run's import let go another run's fetch waiting for its own import (that run then
   resumed on the store's older copy, or none), or put in another fetch's bytes of the same
   path against its own report. Now an import reads and removes only its own run's.

## Left

- `goneFolder`'s final removal (decision 5) takes a file made in the folder after the walk read
  that folder: a run's new file there goes unsent. It was so in the sorted walk too; a fix
  removes each emptied folder (`rmdir`, refused while it holds a late file) and what the walk
  skips by name, which is its own design (Fact 14).

- [x] fix/gone-folder-late-file: `sync.ts` `goneFolder` removes the folder whole after its walk,
  so a file a run makes in it after the walk read that folder goes unread and unsent; remove
  what the walk read and skipped, then each emptied folder (`rmdir`), keeping one still holding
  a file, and hold it in `sync.test.ts`. Done in `untangle/gone-folder-late-file`
  (`gone-folder-late-file.md`).
