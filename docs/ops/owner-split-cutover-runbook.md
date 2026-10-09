# Owner-split cutover runbook

One maintenance window moves production from one daemon uid to per-owner
isolation (`openspec/changes/per-role-uid-split/design.md`). It is forward
only. Rollback means restoring the snapshot taken in step 3.

Budget about 60 minutes. The founder schedules the window.

Rehearsal (2026-10-08) on a restored copy of the 2026-10-08 03:00 nightly
backup (68,763 names, 6 centers):

| Step | Result |
|---|---|
| `--check` before the apply | 66,007 diffs, preflight passed, 0 cross-owner inodes |
| The apply | 65,989 entries changed in 3 m 32 s (Docker Desktop disk) |
| `--check` after the apply | 0 diffs |
| Converged rerun | 3.7 s, nothing changed |

The bootstrap then accepted all six owner bindings. Plan about 5 minutes for
step 4.

All host commands run on the production box as root:

```sh
cd /opt/tinyassets
IMAGE=<the PR2 image tag>   # built from the cutover branch; it carries ta-migrate.py
```

## 1. Announce

Tell the testers the window and the expected downtime.

## 2. Stop

1. Hold auto-deploy, so nothing else merges or deploys:
   `gh workflow disable deploy-prod.yml`. Hold the merge queue too.
2. Stop every timer that could restart or read the daemon:
   ```sh
   systemctl stop tinyassets-backup.timer tinyassets-autoheal.timer \
       tinyassets-watchdog.timer daemon-watchdog.timer tinyassets-prune.timer
   ```
3. Stop every container on the volume, then check that none is left:
   ```sh
   docker compose -f compose.yml stop
   docker ps --filter volume=tinyassets-data   # must list nothing
   ```

## 3. Snapshot (the rollback point)

```sh
set -a; . /etc/tinyassets/env; set +a
BACKUP_MODE=snapshot deploy/backup.sh
```

This takes a full-volume tar with every writer stopped
(`--numeric-owner --acls --xattrs`). It keeps a local copy under
`/var/backups/tinyassets/`, uploads it to the Storage Box and prints:

```
SNAPSHOT id=tinyassets-snapshot-<ts>.tar.gz sha256=<...> bytes=<n> names=<n>
```

It refuses unless every container is stopped and the archive's name count
equals a fresh census of the volume. Record the id, the sha256 and the size.

## 4. Migrate

Run the one-shot container from the cutover image. It gets only the data
volume, no network, and the three capabilities the service never holds:

```sh
migrate() {
  docker run --rm --user 0 --cap-drop ALL \
    --cap-add CHOWN --cap-add FOWNER --cap-add DAC_OVERRIDE \
    --security-opt no-new-privileges=true --network none \
    -v tinyassets-data:/data --entrypoint /opt/venv/bin/python "$IMAGE" \
    -I -B /usr/local/libexec/ta-migrate.py "$@"
}
migrate --check                                   # lists the work; exit 1
migrate --snapshot <id> --snapshot-bytes <bytes>  # the apply
migrate --check                                   # must print nothing; exit 0
```

- The apply refuses before its first write if any precondition fails:
  - a process still holds `/data/.layout.lock`;
  - the volume is not ext4 or has no POSIX ACLs;
  - free space is not above the snapshot's size;
  - the marker is not a stable layout 2;
  - an inode is reachable from two owners or two platform areas;
  - an entry is neither uid 1001 nor already at its target;
  - a special file is found (sockets other than stale relay sockets, or FIFOs);
  - egress state exists in both places;
  - the accounting tables diverge;
  - an owner tree has no authority row;
  - a retired center still has a tree.

  Fix the cause, or stop and roll back.
- If the apply crashes, run the same command again. Every step sets its target
  or skips, so a rerun converges, and a converged rerun changes nothing
  (`scripts/role_migrate_probe.py` proves this at every kill point).
- `migrate --manifest > manifest.tsv` prints the census
  (path, type, uid, gid, mode, ACLs, sha256). Keep it with the snapshot record.
- **"zero diffs" holds only before the daemon has run.** Once it has, `--check`
  reports a handful of platform-state diffs forever, and they are not a failed
  migration: the broker creates `.broker/state/*` with its own gid 1002 where
  the label target is 1101, the daemon creates its private state under umask
  0o007 (`0770`/`0660`) where the target is `0700`/`0600`, and the next deploy
  reinstalls `release-state.json` as `root:root 0644` where the target is
  `1001:1001 0600`. Every one of those lives under a parent no other identity
  can open, so nothing is exposed; run `--check` only with writers stopped, as
  above, and read a post-start run as drift, not as a diff to fix.
- Before the window, rehearse the whole thing on a throwaway volume:
  `python scripts/role_image_oracle.py --image "$IMAGE"`. It builds a
  production-shaped volume, runs these three commands, boots the image in this
  file's posture and drives every owner-cell class.
- The last write adds `"split": "owner-split"` to `/data/.layout.json`. Without
  it the new image refuses to serve (exit 78), and the old image must not be
  started on a migrated volume.

## 5. Start

1. Merge PR2. `deploy-prod` deploys it, and PID1 starts with the marker present.
2. Do not trust `deploy_fail_safe.sh`'s automatic image revert here. The
   previous image on a migrated volume is not a supported state. If it
   reverted, go to step 7 (Rollback).

## 6. Verify

1. `python scripts/deployed_sha.py --assert-contains <PR2 sha>`
2. `python scripts/mcp_public_canary.py --assert-handles`
3. Check the processes from the host, with no exec into the container:
   `docker top tinyassets-daemon -eo pid,uid,gid,cmd`, then
   `grep -E 'Uid|Cap' /proc/<host pid>/status` for each process:
   - uid 1001 for PID1;
   - uid 1002 for the broker;
   - a host uid of 300000 for the mapper;
   - owner uids for the cells;
   - zero capabilities everywhere except the mapper's in-namespace
     SETUID/SETGID.
4. One founder app turn through the app agent, including a provider turn. That
   turn exercises provider-exec.
5. Run the cross-owner refusal probe in production:
   - tester B's cell tries to read the founder's tree and vault;
   - B's daemon-side read names the founder's file;
   - both must refuse.
6. Re-enable `deploy-prod.yml` and the timers stopped in step 2, then announce
   the end.

## 7. Rollback

Roll back if any Verify step fails, or on a regression later.

1. Stop: steps 2.1 to 2.3.
2. Set the migrated volume aside if the disk allows it:
   `docker volume create tinyassets-data-migrated` and copy into it. Otherwise
   go straight to the restore.
3. Restore the snapshot. Check its sha256 first. This archive is ours and
   verified, so it is extracted with its links, numeric owners and ACLs:
   ```sh
   sha256sum -c /var/backups/tinyassets/<id>.sha256
   VOL=$(docker volume inspect -f '{{ .Mountpoint }}' tinyassets-data)
   find "$VOL" -mindepth 1 -delete
   tar -xzpf /var/backups/tinyassets/<id> -C "$VOL" \
       --numeric-owner --acls --xattrs --same-owner
   ```
   `deploy/backup-restore.sh` restores the nightly full tier. It refuses link
   members, so it cannot restore a volume that holds workspace symlinks or the
   multi-link `.runtime/` inodes. Use the tar line above for this snapshot.
4. Redeploy the previous image with `deploy-prod` `workflow_dispatch
   image_tag=<previous>` or with `recovery-retag-image.yml`.
5. Revert PR2 on main, so the next push does not redeploy it.
6. Run `deployed_sha` and the canary.

Once users have written after the cutover, rollback loses those writes. Decide
at Verify.
