# A file sync that must list again

Reviewed on main `f934871ad`. The joined rehearsal `151c2a83f` still uses the same
feed cursor and reset algorithm. The owner owns the ordered history and pruning
floor. The runner owns its local mirror and is the only participant that knows
whether it completed a recursive listing. Reconcile those two facts before changing
either end. The comparison below covers ordinary synthetic workspace data.

## What actually repeats

`Files.changes` in `platform/owner/src/files.ts` returns a bounded raw page and a
reset if the incoming cursor is below the pruning floor. A full page advances to its
last version. The fileChanges door then filters that page for the lease and also
requests a reset when its readable set changes. The egress door forwards the reply.
On reset, `syncIn` in `platform/sandbox/runner/sync.ts` discards those changes,
recursively lists the readable workspace and saves the response cursor after the
listing succeeds. The cursor can still be below the floor. Another whole scan follows.

The existing Worker regression used a request large enough to pass the floor at
once. With two surviving files, a pruned deletion and a limit of one, main's next
response still said reset. Independently, 3,007 synthetic history versions over
the 18 real migrations took four root listings. The real runner and mirror saw a
directory added during the first listing. A failed listing retried from the old cursor.

## Two rejected changes to the owner

* Returning the owner's observed sequence on reset reduced four scans to one.
  All 49 tests in the owner file-tree and run-authority suites passed, including
  the strengthened regression. A different history disproved this change. A
  previously materialized empty directory has no persistent row in the runner's
  mirror. A reset listing could not remove it. Main subsequently consumed its
  retained deletion at version 1,004 and removed it. The candidate advanced past
  that deletion and left the directory on disk.
* Raising a short cursor only to the floor retained that deletion. It changed the
  next page boundaries. With a floor of 1,499 and 2,500 earlier out-of-scope
  changes, a directory created after the first root listing had version 2,501.
  Main saw it on the second listing. The candidate took an empty filtered page
  at 2,499 and allowed the runner to finish without the directory. In a separate
  directory-to-file replacement, the candidate replayed the earlier directory
  record at 1,601 after its listing had installed the current regular file. The
  real runner could not apply that directory and did not advance past 1,500.
  Main completed at 2,803.

The filtered-page and four-listing comparisons used Node 22 and 24, SQLite with
the production migrations, the real owner change reader and the real runner mirror.
The fixtures controlled the ordinary write or interruption between calls. An
independent Node 22 replay compared the physical directory and regular-file results.
Neither cursor change is part of this branch.

## What survives an interruption

An existing lease that gains read access to an older path can have the same file
cursor before and after the change. The owner updates the lease's readable digest
when it answers the changes call. The runner saves its cursor only after the listing.
A listing failure left the old mirror intact, but the next request saw the already
updated lease digest, returned zero rows with reset false, and the runner completed
without the newly readable directory. An uninterrupted listing picked it up.
The existing grant-widening Worker test checks that the new path is discoverable
when reading again from zero. It does not interrupt the runner's listing.

Settle the following together before starting a CLI on a synchronized view.

1. Define the finite history boundary a sync must cover under continuous writes.
   Visible row count cannot establish that a raw page reached the boundary.
2. The owner can describe a readable set. The mirror must be able to establish
   which set it actually finished, even across an interrupted reply or listing.
   A numeric file cursor alone cannot tell two readable sets apart.
3. Establish which previously mirrored directories may be reconciled after a reset.
   Existing mirrors track regular files and deliberately preserve uncommitted local
   work. Account for existing mirrors, directory/file replacement, session paths,
   and the writes that arrive during a listing.
4. Verify both old/new owner and image pairings through stage and handback before
   enabling a changed cursor. Keep continuous writers, a pruned delete and a
   readable-set change in the same paired rehearsal.

The committed change here records the build dependencies. The production fix and
its paired release remain open.
