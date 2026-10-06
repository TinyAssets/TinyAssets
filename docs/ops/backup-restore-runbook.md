---
title: Backup and restore runbook
date: 2026-07-23
row: J — self-host migration
---

# TinyAssets daemon — backup and restore runbook

State backup for the DO Droplet's `/data` volume. Row J per
`docs/exec-plans/active/2026-04-20-selfhost-uptime-migration.md`.

---

## Architecture

- **Script:** `deploy/backup.sh` — two archives per run (see "Two-tier design"), uploaded to any
  rclone-compatible remote.
- **Restore:** `deploy/backup-restore.sh` — validates and stages a full snapshot,
  stops every running container mounting the selected volume, then swaps directories.
  It does not start services.
- **Schedule:** `deploy/tinyassets-backup.timer` (systemd) — fires nightly at **03:00 UTC**.
- **Offsite options:** DO Spaces (`s3://`), Hetzner Storage Box (`sftp://`), AWS S3, etc. — any
  rclone remote works. `BACKUP_DEST` is the single config variable.

### Two-tier design (2026-06-10)

A live volume cannot be tarred consistently: the daemon writes during the
~7-minute archive, GNU tar exits 1 ("file changed as we read it"), and the
pre-2026-06-10 script treated that as fatal — every nightly run from
2026-05-28 to 2026-06-10 failed this way, silently starving offsite history
(the only successes were nights quiet enough to win the race). The redesign:

| Tier | Archive | Contents | Consistency | Failure policy |
|------|---------|----------|-------------|----------------|
| **Brain** | `tinyassets-brain-<ts>.tar.gz` (MBs) | `wiki/`, `daemon_wikis/`, top-level `*.json` ledgers, top-level `*.db`, relocated `.broker/outbound.db` | Strict — staged to a temp dir; SQLite copied via python3 `sqlite3.backup()` API | Any failure is fatal (exit 2/3) |
| **Full** | `tinyassets-data-<ts>.tar.gz` (GBs) | whole volume incl. rebuildable per-universe `lancedb/` indexes + universe canon/output | Best-effort — tarred live; tar rc=1 tolerated, rc≥2 fatal | Upload failure fatal (exit 3) |

The brain tier also includes `.broker/outbound.db` after role relocation,
using the SQLite backup API and retaining its relative path, numeric owner/group
and private file/directory modes. Legacy root `outbound.db` remains supported.
New brain archives omit the private staging-root header, so extraction does not
replace the live volume root's ownership/mode. Full role/ACL restore and old-image
rollback still require the per-role-uid-split acceptance proofs before activation.

The brain tier is the irreplaceable knowledge state and must always land.
The full tier may contain torn copies of files that were mid-write; LanceDB
indexes are rebuildable from canon, and run state is resumable. Retention is
applied **per tier prefix** by `scripts/backup_prune.py`; unrecognized
filenames at the destination are never pruned.

### Production history and current contract

Until 2026-06-10 the droplet had `BACKUP_DEST=/var/backups/workflow` — a
local directory on the same disk as the data — and GitHub releases were the
only true offsite copy. On 2026-06-10, DO Spaces was provisioned as the primary:

- `BACKUP_DEST=spaces:workflow-backups-jonnyton-sfo3/workflow-backups`
- A dedicated Spaces access key.
- rclone config at `/root/.config/rclone/rclone.conf` (the systemd unit runs
  as root and does not override `HOME`).
- Verified end-to-end: `Result=success`, both tiers listed in the bucket.

An exact-SHA production exercise on 2026-07-24 found that subsequent
application deploys had deleted `BACKUP_DEST`, and the replacement host lacked
root's rclone file. Releases after the preservation repair keep
`BACKUP_DEST`; the exact-source host-service workflow treats a working
destination as a no-op, transactionally creates a bucket-scoped `readwrite`
key when both configuration halves are absent, and fails closed on partial or
invalid existing configuration. Newly created credentials receive a bounded
95-second worst-case data-plane propagation window before transactional
rollback. That bound includes 65 seconds of backoff plus five probes hard-capped
at five seconds with one second of kill grace each.

The Space is an external resource created before the product rename. Its
provider identity remains `workflow-backups-jonnyton-sfo3`; Spaces buckets
cannot be renamed. A mechanical repository rename temporarily documented the
nonexistent `tinyassets-backups-jonnyton-sfo3` name. Read-only provider probes
on 2026-07-23 returned HTTP 403 for the private pre-rename bucket and HTTP 404
for the nonexistent renamed bucket. Do not rename external resource
identifiers during product terminology migrations.

Exact-merge installer run `30070438676` on 2026-07-23 proved the corrected
bucket accepts scoped key creation. Its immediate object-list probe returned
HTTP 403, after which the workflow removed the temporary host configuration and
deleted the new key. This is treated as provider credential propagation unless
the bounded retry window also expires; it is not authority to use a full-access
key.

Exact-merge run `30071110351` then exposed an ordering bug: S3 has no empty
directories, but the pre-probe `rclone mkdir` still made a data-plane request
and received HTTP 403 before the bounded retry loop. Rollback again removed the
host configuration and new key. The installer no longer calls `mkdir`; its
bounded non-mutating list probe is the only data-plane gate.

Exact-merge installer run `30071496671` completed successfully on 2026-07-23:
the scoped key passed the bounded data-plane gate and the five uptime timers
were converged from merge `37698cadd7c3ec7072120fe466e85436aec80386`.

To collect fresh backup evidence without making every deploy run a backup,
dispatch `install-host-services.yml` at an exact merge ref with
`run_backup=true`. The default is false. The exercise requires a successful
oneshot result, new brain/full archive names at the Spaces destination, exactly
two GitHub release upload markers, and `backup complete.` All journal markers
must carry the new service run's exact systemd invocation ID; time-window
queries are insufficient because they can mix adjacent runs. The exercise never
prints the environment file or rclone credentials.

Final exact-merge proof on 2026-07-24 used merge
`a18751dc3b8544d048a745304b1823dfdd9fbb11`:

- Backup run `30075565479` reused the verified configuration, converged five
  timers, produced fresh `2026-07-24T07-28-29Z` brain/full archives at both
  tiers, and emitted no invocation warning/error.
- The private release repository converged to 30 recognized backup releases
  plus one permanent audit release.
- Both downloaded assets matched GitHub's SHA-256 digests, passed tar/path
  validation, and the full archive extracted outside production. All 14
  SQLite databases passed read-only `PRAGMA integrity_check`; `ledger.json`
  parsed.
- Deploy run `30076034679` passed health and public canaries. Its automatic
  host-service run `30076156783` then reported the off-host configuration
  already verified and reconverged all five timers.

The intended offsite topology remains: **DO Spaces (primary) + GitHub releases
(secondary)**.
Teardown/rollback: delete the Spaces key via DO API, repoint `BACKUP_DEST`,
remove the bucket. Cost: Spaces subscription ~$5/mo on the existing DO
account. Local retention stays tight (`BACKUP_RETAIN_DAILY=3 / WEEKLY=2 /
MONTHLY=2`) although the local dir is no longer the dest.

**Retention schedule:**

| Window | Count | Default env var |
|--------|-------|-----------------|
| Daily  | 7     | `BACKUP_RETAIN_DAILY=7` |
| Weekly | 4     | `BACKUP_RETAIN_WEEKLY=4` |
| Monthly| 6     | `BACKUP_RETAIN_MONTHLY=6` |

The newest archive within each bucket is kept. Oldest bucket that falls outside all windows is
deleted at the end of every run.

---

## Setup

### 1. Configure rclone remote

Install rclone on the Droplet and configure a named remote for your offsite target.

For the canonical production host, dispatching `Install host services`
performs this setup automatically only when both `BACKUP_DEST` and root's
rclone configuration are absent. Operators must inspect and deliberately
rotate partial or failing existing configuration; the workflow does not
overwrite it.

**DO Spaces (recommended — same provider, cheapest):**

```bash
apt-get install -y rclone

# Create a DO Spaces bucket (once) via DO console or API.
# Then configure rclone:
sudo rclone config create spaces s3 \
  provider DigitalOcean \
  endpoint nyc3.digitaloceanspaces.com \
  access_key_id "$DO_SPACES_KEY" \
  secret_access_key "$DO_SPACES_SECRET"
```

**Hetzner Storage Box (SFTP):**

```bash
sudo rclone config create storagebox sftp \
  host u123456.your-storagebox.de \
  user u123456 \
  pass "$(rclone obscure "$STORAGEBOX_PASS")"
```

### 2. Set `BACKUP_DEST` in `/etc/tinyassets/env`

```bash
# DO Spaces:
echo 'BACKUP_DEST=spaces:my-bucket-name/tinyassets-backups' >> /etc/tinyassets/env

# Hetzner Storage Box:
echo 'BACKUP_DEST=storagebox:tinyassets-backups' >> /etc/tinyassets/env
```

Any rclone remote URL is accepted: `s3://bucket/path`, `sftp://host/path`,
`spaces:bucket/path`, etc.

### 3. Install and enable the systemd units

```bash
cp /opt/tinyassets/deploy/backup.service /etc/systemd/system/
cp /opt/tinyassets/deploy/backup.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now backup.timer
systemctl status backup.timer
```

Verify the first timer run:

```bash
systemctl list-timers backup.timer
```

---

## Trigger a manual backup

```bash
# As root on the Droplet:
sudo systemctl start backup.service

# Watch progress:
journalctl -f -u tinyassets-backup

# Or run the script directly (useful for testing with DRY_RUN):
source /etc/tinyassets/env
sudo -E bash /opt/tinyassets/deploy/backup.sh

# Dry-run (no mutations):
DRY_RUN=1 bash /opt/tinyassets/deploy/backup.sh
```

---

## List available snapshots

```bash
source /etc/tinyassets/env
bash /opt/tinyassets/deploy/backup-restore.sh --list
```

Example output:

```
[restore 2026-04-21T10:00:00Z] available archives at s3://my-bucket/tinyassets-backups:
  tinyassets-data-2026-04-21T02-00-00Z.tar.gz
  tinyassets-data-2026-04-20T02-00-00Z.tar.gz
  tinyassets-data-2026-04-19T02-00-00Z.tar.gz
```

---

## Restore the brain tier only

The brain archive's contents are relative to the volume root (`./wiki`,
`./daemon_wikis`, `./ledger.json`, `./*.db`). To restore just the knowledge
state over an existing volume:

```bash
systemctl stop tinyassets-daemon   # or: docker compose -f /opt/tinyassets/deploy/compose.yml stop daemon
tar -xzf /tmp/tinyassets-brain-<ts>.tar.gz -C /var/lib/docker/volumes/tinyassets-data/_data
systemctl start tinyassets-daemon
```

---

## Restore on the same Droplet

```bash
# Dry-run first — shows which archive would be restored:
DRY_RUN=1 sudo -E bash /opt/tinyassets/deploy/backup-restore.sh

# Restore latest:
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh

# Restore a specific snapshot:
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh --timestamp=2026-04-20T02-00-00Z
```

The script first rejects corrupt archives, paths outside `_data`, links, and
special files; it then extracts into a unique sibling directory. Only after
staging succeeds does it stop every running container mounting `tinyassets-data` and
swap the staged directory into place. It deliberately does **not** start any
service. The pre-restore directory is retained at the path printed by the
script.

Start only the intended service, prove it healthy, and then remove the retained
directory:

```bash
sudo systemctl start tinyassets-daemon
python3 /opt/tinyassets/scripts/mcp_public_canary.py \
  --url http://127.0.0.1:8001/mcp

```

Only after the canary is green, use the guarded `OLD_DIR` cleanup procedure in
`deploy/RESTORE.md` with the exact retained path printed by the restore.

If the canary is red, stop the service and swap the retained directory back
before further diagnosis. Never delete the retained directory before the
post-restore canary passes.

---

## Restore on a new Droplet (disaster recovery)

Use this when the original Droplet is gone or unrecoverable.

1. Provision a fresh Droplet and run `deploy/hetzner-bootstrap.sh` (idempotent; DigitalOcean-compatible).
   Bootstrap automatically configures:
   - Docker log-rotation (`/etc/docker/daemon.json` — 10 MB max, 3 files)
   - 2 GB swap file (`/swapfile`) + `/etc/fstab` persistence + `vm.swappiness=10`
2. Copy `/etc/tinyassets/env` from the vault (or re-populate from the succession runbook).
3. Pull the daemon image:
   ```bash
   docker pull ghcr.io/tinyassets/tinyassets-daemon:latest
   ```
4. Run the restore:
   ```bash
   source /etc/tinyassets/env
   sudo -E bash /opt/tinyassets/deploy/backup-restore.sh
   ```
5. Start only the daemon:
   ```bash
   docker compose -f /opt/tinyassets/deploy/compose.yml up -d daemon
   ```
6. Verify:
   ```bash
   python scripts/mcp_public_canary.py --url http://127.0.0.1:8001/mcp
   ```
7. After the canary is green, remove the exact retained pre-restore directory
   printed by `backup-restore.sh`. If it is red, keep that directory and the
   failed host as recovery evidence.

---

## Verify backup health

Check the last backup result:

```bash
journalctl -u tinyassets-backup --since "24 hours ago" | grep -E "backup complete|ERROR"
```

Or tail the log file directly:

```bash
tail -20 /var/log/tinyassets-backup.log
```

Expected healthy output ends with `backup complete.`

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `BACKUP_DEST is not set` | Env file missing the variable | Add `BACKUP_DEST=...` to `/etc/tinyassets/env` |
| `restore already in progress` | Another restore holds this volume's lock | Wait for it to finish; restores of other volume directories remain independent |
| `archive member validation failed` | Corrupt or unsafe archive (wrong root, traversal, link, or special file) | Leave the live volume untouched and select another full archive |
| `rclone upload failed` | Network/auth error | Check root's rclone config: `sudo rclone lsd $BACKUP_DEST` |
| `failed to install staged volume` | The second same-parent rename failed | The script attempts automatic rollback; confirm the old live data is back before retrying |
| Timer never fires | Unit not enabled | `systemctl enable --now backup.timer` |

---

## Offsite backup — GitHub release assets

When `GH_TOKEN` is set in `/etc/tinyassets/env`, `backup.sh` also ships the
tarball to a private GitHub repo (`Jonnyton/tinyassets-backups`) as a release
asset via `scripts/backup_ship_gh.py`.  This is a second copy independent of
the rclone primary destination.

**Restore from a GitHub release asset:**

```bash
# List available releases (requires GH_TOKEN or gh CLI auth).
gh release list --repo Jonnyton/tinyassets-backups

# Download a specific release asset.
gh release download <tag> --repo Jonnyton/tinyassets-backups --dir /tmp

# Restore from an absolute caller-owned path; this bypasses rclone.
sudo -E env BACKUP_FILE=/tmp/tinyassets-data-<timestamp>.tar.gz \
  bash /opt/tinyassets/deploy/backup-restore.sh
```

Or via raw API (no gh CLI):

```bash
curl -sL -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/Jonnyton/tinyassets-backups/releases/latest" \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print(r['assets'][0]['browser_download_url'])"
# Then curl -L <url> > /tmp/backup.tar.gz
```

**Retention:** 30 recognized backup releases are kept by default
(`BACKUP_GH_RETAIN`). Retention waits boundedly until GitHub's list endpoint
contains the just-created release; an already-deleted victim in a stale view
forces another bounded reconciliation pass. Each API request has a 15-second
transport timeout, and one shared wall-clock budget across listing, release
deletion, best-effort tag cleanup, and retry sleeps caps the complete
reconciliation at two minutes. The oldest recognized backup releases are then
pruned after each successful upload. Unrecognized parked/audit releases are
permanent and do not count toward this limit, so the repository's total
release count can be higher.

**Setup:** create `Jonnyton/tinyassets-backups` as a private repo once (or let
`backup_ship_gh.py` create it automatically on first run).  Add `GH_TOKEN` to
`/etc/tinyassets/env` with `repo` scope.
