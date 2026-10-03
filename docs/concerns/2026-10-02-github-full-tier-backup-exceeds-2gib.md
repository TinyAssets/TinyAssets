---
severity: P2
title: The GitHub copy of the full-tier backup fails at GitHub's 2 GiB asset limit
filed: '2026-10-02'
summary: the nightly full tarball is ~4.0 GB and GitHub rejects it with 422; only the brain tier reaches the second copy, about 1.0 GB of it retained _removed_* data
---

# The GitHub copy of the full-tier backup fails at GitHub's 2 GiB asset limit

**Filed:** 2026-10-02
**Verified:** 2026-10-02 from `journalctl -u tinyassets-backup.service` on
production (read-only).
**Severity:** P2 on its own. Combined with
`2026-10-02-backups-share-the-droplets-region.md`, it means no full backup
exists outside sfo3.

## Source (verbatim)

From the backup journal, 2026-09-30:

> gh-ship: ERROR: GitHub asset upload ... → 422: {"message":"Validation
> Failed", ... "field":"size","message":"size must be less than 2147483648"}
> gh-ship: [backup-ship] tarball: /tmp/tinyassets-data-2026-09-30T01-05-06Z.tar.gz (4035405694 bytes)

## Evidence

- The full tarball is 4,035,405,694 bytes. The brain tier (about 3.4 MB) uploads
  to `Jonnyton/tinyassets-backups` (private) every night.
- On the data volume, `_removed_legacy_20260829` (831 MB),
  `_removed_universes_20260927` (167 MB) and `_removed_universes_20260828`
  (4.3 MB) make up about 1.0 GB of the 1.8 GB live volume.

## What would resolve it

Do one of these:
- stop shipping the full tier to GitHub and rely on a cross-region object store;
- split the full tier into chunks under 2 GiB.

Separately, with founder approval and inventory first (Hard Rule 13), the
retained `_removed_*` data could be reclaimed to shrink the archive.
