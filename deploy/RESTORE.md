# TinyAssets state restore runbook

Use this procedure for whole-volume recovery, rollback after a bad migration,
or a fresh-host recovery drill. Nightly full archives are gzip files named
`tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz`.

`deploy/backup-restore.sh` validates and stages the data and paired box-store
archives before stopping consumers. It swaps the staged data into place and
retains the prior volume and owner trees for rollback. It never starts a service.

## Preconditions

- Docker, rclone, Python 3, GNU tar, and `flock` are installed.
- `/etc/tinyassets/env` contains the configured `BACKUP_DEST`, unless restoring
  an already-downloaded archive with `BACKUP_FILE`.
- The repository is present at `/opt/tinyassets`.
- There is enough free space beside the Docker volume for the staged restore
  and retained pre-restore data.

Do not wipe or recreate the live volume. The restore script does not mutate it
until archive validation and staging have succeeded.

## Restore from the configured remote

```bash
set -a
. /etc/tinyassets/env
set +a

# Show only eligible full-volume archives.
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh --list

# Confirm selection without mutation.
DRY_RUN=1 sudo -E bash /opt/tinyassets/deploy/backup-restore.sh

# Restore the newest full-volume archive.
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh

# Or restore one exact UTC snapshot.
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh \
  --timestamp=2026-07-23T03-00-00Z
```

## Restore an already-downloaded GitHub Release archive

`BACKUP_FILE` must be an absolute, readable, non-symlink regular-file path. It
bypasses rclone and remains caller-owned.

```bash
sudo -E env \
  BACKUP_FILE=/var/backups/tinyassets/tinyassets-data-2026-07-23T03-00-00Z.tar.gz \
  bash /opt/tinyassets/deploy/backup-restore.sh
```

Do not combine `BACKUP_FILE` with `--list` or `--timestamp`.

## What a successful restore does

The script:

1. Rejects an unreadable archive and any member that is absolute, traverses
   upward, is outside `_data`, is a symbolic/hard link, or is a special file.
2. Extracts the archive into a unique sibling stage with `_data` stripped,
   preserving dotfiles and nested files.
3. Acquires a non-blocking lock scoped to this resolved Docker volume.
4. Stops every running container that Docker reports as mounting the volume.
5. Renames the current `_data` to a unique retained sibling and moves the
   staged directory into `_data`.
6. Automatically puts the prior directory back if the second rename fails.

The final output prints the live path and retained pre-restore path. Record the
retained path before continuing.

## Start and verify separately

```bash
sudo systemctl start tinyassets-daemon
sudo systemctl status tinyassets-daemon --no-pager

python3 /opt/tinyassets/scripts/mcp_public_canary.py \
  --url http://127.0.0.1:8001/mcp --verbose
```

Only a green canary proves the restored daemon is usable. On a drill host, keep
the host available when the probe is red so the failure evidence survives.

## Remove retained data after a green canary

Set `OLD_DIR` to the exact path printed by the restore. Verify its shape before
removing it:

```bash
OLD_DIR=/var/lib/docker/volumes/tinyassets-data/.tinyassets-restore-old.REPLACE
case "${OLD_DIR}" in
  /var/lib/docker/volumes/*/.tinyassets-restore-old.*)
    sudo rm -rf -- "${OLD_DIR}"
    ;;
  *)
    echo "refusing unexpected OLD_DIR: ${OLD_DIR}" >&2
    exit 1
    ;;
esac
```

## Roll back after a red canary

Stop the daemon. Keep the failed restored directory as evidence, and move the
retained prior directory back:

```bash
sudo systemctl stop tinyassets-daemon

LIVE_DIR=/var/lib/docker/volumes/tinyassets-data/_data
OLD_DIR=/var/lib/docker/volumes/tinyassets-data/.tinyassets-restore-old.REPLACE
FAILED_DIR=/var/lib/docker/volumes/tinyassets-data/.tinyassets-restore-failed.$(date -u +%Y%m%dT%H%M%SZ)

case "${LIVE_DIR}|${OLD_DIR}|${FAILED_DIR}" in
  /var/lib/docker/volumes/*/_data\|/var/lib/docker/volumes/*/.tinyassets-restore-old.*\|/var/lib/docker/volumes/*/.tinyassets-restore-failed.*)
    sudo mv -- "${LIVE_DIR}" "${FAILED_DIR}"
    sudo mv -- "${OLD_DIR}" "${LIVE_DIR}"
    ;;
  *)
    echo "refusing unexpected restore paths" >&2
    exit 1
    ;;
esac

sudo systemctl start tinyassets-daemon
python3 /opt/tinyassets/scripts/mcp_public_canary.py \
  --url http://127.0.0.1:8001/mcp --verbose
```

## Common failures

- `archive member validation failed`: the live volume and containers have not
  been changed. Select another full archive.
- `restore already in progress`: another restore holds this volume's lock.
  Wait for it; restores of different volume directories use independent locks.
- `failed to stop all running volume consumers`: inspect
  `docker ps -a --filter volume=tinyassets-data`; no directory swap occurred.
- `failed to install staged volume`: the script attempted automatic rollback.
  Confirm `_data` contains the prior state and preserve any
  `.tinyassets-restore-old.*` directory before retrying.
- Disk full while staging: free space beside the volume or provision a larger
  disk. Do not stream-extract into the live volume; that bypasses validation
  and rollback.

The automated fresh-host proof is `.github/workflows/dr-drill.yml`. It
transfers a selected full archive, invokes this restore script, starts only the
daemon, probes through an SSH port forward, destroys a green drill host, and
keeps a red host by default.
# Box-store pair

The production box store uses the 20 GiB DigitalOcean volume
`tinyassets-box-store` at
`/dev/disk/by-id/scsi-0DO_Volume_tinyassets-box-store`. Dispatch
`box-store-volume.yml` on main before the deploy that installs the store.
The device script formats an unformatted volume as XFS once and mounts it
through `var-lib-boxhostd-owners.mount` with `pquota,noatime,nodiscard`.
It never reformats a volume with a filesystem. If no volume is attached,
the 8 GiB loop image remains the fallback. An installed loop store with
owner data is never automatically switched to the volume; restore the
matched backup pair onto the mounted volume during a maintenance window.
Do not delete or detach the volume during restore or rollback.

An installed box store adds `tinyassets-box-store-<timestamp>.tar.gz` beside
each full data archive in the rclone destination. `backup.sh` freezes XFS
while streaming this member to rclone, then thaws it. The box member is not
staged on the root filesystem or shipped to GitHub releases. Restore requires
the exact timestamp pair and
extracts the box member to a temporary directory on the XFS store. Numeric
owners, ACLs, xattrs and safe in-tree links are preserved. The broker's
`.broker/box-registry.json` export in the data archive must contain `boxes`
rows with `uid`, `gid`, `project_id` and `bhard_bytes` for every owner tree;
restore re-applies each XFS project and `bhard`, then runs `xfs_quota report`.
An empty P2a box store needs no registry rows. A local restore can provide the
matching archive through `BOX_BACKUP_FILE` and a registry export through
`BOX_REGISTRY_FILE`. `BOX_STORE_DIR` permits a restore drill on a mounted
temporary XFS store.

The restore script stops boxhostd and retains old owner trees in
`.tinyassets-box-restore-old.*` on the store. After checking both restored
data and boxes, start `boxhostd.service`; remove the retained old trees only
after the post-restore verification. To roll back before removing them, stop
boxhostd, move the restored numeric owner trees aside, move the retained old
trees back, restore the retained old data volume, then start services and
verify the public canary. Keep both archive members and the old directories
until verification completes.
