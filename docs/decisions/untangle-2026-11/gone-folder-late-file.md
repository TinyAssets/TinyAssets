# gone-folder-late-file as built (2026-11-22)

Owed by `sync-resume-took.md` (#5801, decision 5): `sync.ts` `goneFolder` walked a folder the
owner's files removed, removed each file it read but those holding unsent changes, and then,
where it kept none, removed the folder whole (`remove`). A file a run made in the folder after
the walk read past it went with it, never read and never sent: lost without a word (Fact 8).

## Research (Fact 14), read 2026-11-22 on main `55a517eb8`

**POSIX `rmdir`** removes a directory only while it holds no entry but `.` and `..`; one that
holds any fails with EEXIST or ENOTEMPTY (Linux: ENOTEMPTY), and a path that is not a
directory, a link among them, fails with ENOTDIR (the link is never followed). Linux takes the
directory's lock for the check and the removal, and a directory removed takes no new entry
(ENOENT), so a run's write in it either lands before and keeps it, or fails at the run, loudly.

**POSIX `readdir`**: an entry made or removed after the open may or may not be returned;
every other entry is returned once. glibc reads a batch at a time, tmpfs (Linux 6.6 on) gives a
new entry a later offset, so a walk still listing returns it, and ext4 (this host's `/tmp`)
lists by name hash, so whether it does depends on the name. No walk order rules a late file
out: only a removal that refuses a folder holding something does.

**What the next sync-out does with a late file** (`owner/src/files.ts` `#syncOp`): it has no
row, so it is a write with no base. Where the owner's files hold nothing there, it is
committed, and `#commit` makes each missing folder above it (`#parents`, a recorded `mkdir`,
kind `dir`), so the feed names the folder again, then the file at the version the mirror
holds (never fetched back). Where the owner's files made a file of the folder (a kind
change), the write is under a file (`#blocked`): a conflict, its copy beside that file
(`docs.ta-conflict-<epoch>/late.md`), `current` null, so the sandbox takes it (the file goes),
then the scan takes the marked folder. Where it stands at a path with a row (a file the run
removed and made again), it is a write against a base that moved: a conflict copy. Never a
refusal.

**What the owner's side ends with**, under #5798's rule: the owner's removal holds for every
file it named that held no unsent changes; one that did comes back as a conflict copy,
reported; a file it never saw comes back as a new file, in its folder made again, the same
outcome as a file a run makes just after sync-in. One outcome a file, no loop.

**The order the owner's removal is fed** (`#remove`, `#below`): the folder's own entries,
then those deeper (by parent), then the folder. So a subfolder is named before what it holds,
and its `goneFolder` walks the folders under it: a folder left inside a walk is the usual case,
not only the top.

**What the old final removal also took**: what the walk never offers (a link, a name not
UTF-8, an entry a sync-ignore line keeps out), and, where a file was kept, it left them
(`sync-in-unsent-edit.md`'s "a gone folder keeping a file leaves what sync-out never sends").

## Decided

1. **The one walker meets every entry** (`fsafe.ts` `walkTree`, its `Met`): a file, a folder
   once everything under it was met (`left`), what it passes over (`passed`: kept out, a link,
   anything else), and a name not UTF-8 (`unnamed`, its path as bytes, told as before).
   `walkFiles` is its files, so every other walk is as it was.
2. **A gone folder goes an entry at a time, each folder by `rmdir`** (`removeEmpty`): each
   entry goes once the walk met it, its rows with it: a file unless it holds unsent changes,
   what sync-out never sends whole; a folder when the walk left it, and the top last, each
   only while it holds nothing. A late file keeps every folder above it and is sent as any new
   file. `goneFolder` says whether the top went; a kind change marks the path where it did not,
   as before.
3. **What sync-out never sends goes whatever else stays** (`sync-in-unsent-edit.md` decision
   5), so a folder kept for an unsent file no longer keeps a link, a name not UTF-8 or an
   ignored tree beside it.
4. **No format change.** The mirror's rows are written as before. A runner one deploy back
   removes a gone folder whole again: the late file's loss returns, nothing else changes, so a
   rollback is safe.
5. **The tests** (`sync.test.ts`, "a folder gone from the owner's files"): a run's write as
   the walk closes the folder's listing (the test's own `opendirSync`, its handle's close told
   to the test, a harness hook only), at the top, a folder the removal names, and one only the
   walk meets; the same with the owner making a file of it; and a tree whose kept file keeps
   its folders while a link, a name not UTF-8, an ignored tree and an emptied folder go. The
   stand-in owner now makes a write's missing folders (`#parents`) and feeds changes in
   version order, as the owner's object does.

## Left

- An empty folder a run makes in a gone folder after the walk passed keeps it too (`rmdir`
  refuses); sync-out sends no folder, so it stays in the sandbox alone, as any empty folder a
  run makes. No byte is lost.
- The rows dropped with each removal fail no test when left out: the owner's removal names
  every entry, whose own change drops its row. They keep the mirror true to the disk whatever
  order the feed takes.
- A run's write between the walk's look at a file and its removal is removed with it: the
  window `theirs` has too (`sync-in-unsent-edit.md` decision 2), the narrowest a filesystem
  without a compare-and-remove allows.
