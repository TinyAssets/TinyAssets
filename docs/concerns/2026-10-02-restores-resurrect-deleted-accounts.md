---
severity: P1
title: Restoring any backup older than an account deletion resurrects that account
filed: '2026-10-02'
summary: 'The deletion tombstone (deleted_principals, keyed by digest) lives in .tinyassets.db, the same database a restore rolls back. Restore an archive taken before a deletion and both the deleted data and the absence of its tombstone come back, so the user''s old state returns and their next login rebinds to it. This is true of the nightly archives today (up to 6 months back), of the hourly brain tier, and of any future point-in-time replica. A restore must replay deletions from a tombstone list that is not inside the restored set.'
---

# Restoring any backup older than an account deletion resurrects that account

**Filed:** 2026-10-02 (from the Codex refute of the Litestream design, S1a.2)
**Verified:** 2026-10-02, origin/main `8c68488c`. `tinyassets/account_deletion.py:631-654`
writes the tombstone into `deleted_principals` through `daemon_server._connect`, which is
`.tinyassets.db`. `deploy/backup-restore.sh` restores whole archives with no deletion replay.
`backup.sh` keeps 7 daily, 4 weekly and 6 monthly archives per tier.

## What is true

- Account deletion writes the tombstone **first**, then deletes and redacts rows across every
  root store and the user's universe directory. That ordering is correct for a live system.
- Every backup tier captures `.tinyassets.db` whole. A restore from before the deletion
  brings back:
  - the deleted rows,
  - the universe directory, from the full tier,
  - and a `deleted_principals` table **without** that principal.
- The restored system therefore treats the account as live. The next login with the same
  identity rebinds to the restored data.
- This is a general property of backups, not something Litestream introduces. Litestream (now
  deferred) would only shorten the restore points; the nightly archives already go back 6 months.

## Why it matters

The deletion promise to a user is broken by an ordinary operator action, a restore after an
incident. It is broken silently, with no step in the runbook telling the operator.

## Shape of the fix (target-architecture already plans the end state)

The end state is per-command-center data keys, crypto-shredding on deletion, and tombstones
(target-architecture D12). A shredded key cannot be restored, so old backups of that command
center become unreadable. Until then:

1. **An append-only deletion ledger outside the restored set.** It records each deletion's
   principal digest and time, and it is copied off-region on every deletion, or at least hourly
   with the brain tier, as its own object, never inside an archive that a restore replaces.
2. **Restore replays it before reopening service.** `backup-restore.sh`, and the DR drill,
   re-run the deletion for every ledger entry newer than the archive. A restore refuses to
   finish if the ledger cannot be read.
3. **The drill proves it:** it restores an archive from before a seeded test deletion and
   asserts that the principal is gone after replay.
4. **The runbook says so.** "Restoring an old backup" gets a deletion-replay step and a
   statement of how long each tier retains deleted data.

## How to resolve

Ship steps 1-3 and show a drill run that restores an archive from before a test deletion and
ends with the principal tombstoned and its rows absent. Delete this file then.
