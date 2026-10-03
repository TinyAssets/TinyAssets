---
severity: P1
title: Off-host backups sit in the same region (sfo3) as the only droplet
filed: '2026-10-02'
summary: BACKUP_DEST is a DigitalOcean Space in sfo3, the droplet's own region; the only other copy (GitHub releases) skips the full tier, so a regional loss leaves no full restore
---

# Off-host backups sit in the same region (sfo3) as the only droplet

**Filed:** 2026-10-02
**Verified:** 2026-10-02 on production, read-only.
**Severity:** P1. A regional DigitalOcean incident takes out the primary and its
full-tier backup together.

## Source (verbatim)

From the staged-architecture review (2026-10-02):

> The backups have two copies: DigitalOcean Spaces in the same region (sfo3) as
> the droplet; a private GitHub releases repo, where the full tier fails because
> the tarball (4.0 GB) is over GitHub's 2 GiB asset limit.

## Evidence

- Droplet region: `curl http://169.254.169.254/metadata/v1.json` returns
  `region: sfo3`.
- The `BACKUP_DEST` remote name is `spaces`, and `rclone listremotes --long`
  shows `spaces: s3`. The backup journal uploads to
  `spaces:workflow-backups-jonnyton-sfo3/workflow-backups/` (for example on
  2026-09-30T01:05Z).
- `.github/workflows/install-host-services.yml` hard-codes
  `destination="spaces:workflow-backups-jonnyton-sfo3/workflow-backups"` and
  `endpoint="sfo3.digitaloceanspaces.com"`.
- The second copy (GitHub releases) holds only the brain tier. See
  `2026-10-02-github-full-tier-backup-exceeds-2gib.md`.

## What would resolve it

Point `BACKUP_DEST` at a bucket outside sfo3: another DO region, Cloudflare R2
or Backblaze B2, all at about $5/mo or less. Prove it with one restore from that
copy, ideally through the scheduled DR drill
(`2026-10-02-dr-drill-is-dispatch-only.md`).

Continuous SQLite replication, for example Litestream v0.5 to that bucket,
would also cut RPO from about 24 h to about 1 s. Pin SQLite first; see
`2026-10-02-sqlite-predates-wal-reset-fix.md`.
