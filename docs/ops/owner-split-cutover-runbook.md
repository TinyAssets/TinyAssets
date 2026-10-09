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
ROLLBACK=/var/backups/tinyassets/cutover-runtime
```

## 1. Announce

Tell the testers the window and the expected downtime.

## 2. Stop

1. Hold the merge queue and all manual merges except PR #4568. Disable
   `deploy-prod.yml` and every other host-mutating workflow (including
   `p0-outage-triage.yml`, `restart-daemon.yml`, `install-host-services.yml`,
   `apply-daemon-env.yml`, and recovery workflows). Record which were enabled.
   Cancel queued runs and wait for running host mutations to finish **before**
   stopping anything. Disabling a workflow does not cancel its existing runs.
   Keep this exclusive operator hold until Verify or Rollback completes.
   Leave `build-image.yml` enabled; builds do not mutate the host.
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
4. Pin the previous image and its **live runtime bundle**, before a deploy can
   replace either. The image contains its own entrypoint/launcher; save it too.
   These local archives contain configuration secrets: keep them root-only.
   ```sh
   umask 077
   mkdir -m 0700 "$ROLLBACK"
   docker inspect -f '{{.Image}}' tinyassets-daemon > "$ROLLBACK/image-id"
   PREVIOUS_IMAGE=$(cat "$ROLLBACK/image-id")
   docker image save "$PREVIOUS_IMAGE" > "$ROLLBACK/image.tar"
   tar -czpf "$ROLLBACK/runtime.tar.gz" --numeric-owner --acls --xattrs -C / \
       opt/tinyassets/compose.yml opt/tinyassets/deploy \
       etc/systemd/system/tinyassets-daemon.service etc/tinyassets \
       usr/local/sbin/tinyassets-env
   (cd "$ROLLBACK" && sha256sum image.tar runtime.tar.gz image-id > SHA256SUMS)
   ```
   Do not replace this pinned bundle with a later deploy's `bundle-previous`
   pointer. `opt/tinyassets/deploy` includes the host launch/deploy scripts;
   the saved image pins the container launcher and Compose pins its authority.

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

It refuses unless every container is stopped and the archive's exact member
inventory equals the restorable volume inventory. Only stopped relay sockets
`.universe-sidecars/<center>/egress-<pid>.sock` and
`engine-<pid>-<12 lowercase hex>.sock` are disposable, matching the migration.
Any other socket or omitted member fails. Record the id, sha256 and size.

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

1. With deploy still disabled and the exclusive merge hold active, merge only
   PR #4568. Record the resulting full main SHA as `CUTOVER_SHA`. Wait for its
   `build-image.yml` run to finish successfully while deploy remains disabled.
   Confirm no other build or host-mutating run is queued or running, and that
   main still equals `CUTOVER_SHA`. The earlier branch image used for migration
   is not the deploy tag: use the completed merge build's 12-character SHA tag.
2. Enable and explicitly dispatch that one deploy, in this order:
   ```sh
   test "$(gh api repos/TinyAssets/TinyAssets/commits/main --jq .sha)" = "$CUTOVER_SHA"
   gh workflow enable deploy-prod.yml
   gh workflow run deploy-prod.yml --ref main -f image_tag="$(printf %.12s "$CUTOVER_SHA")"
   gh run list --workflow deploy-prod.yml --event workflow_dispatch --limit 5
   ```
   Identify the new run by event, head SHA and creation time. Wait until that
   exact run is `in_progress`, then disable `deploy-prod.yml` again (this does
   not cancel the active run). Watch its run ID to completion with
   `gh run watch <run-id> --exit-status`. All other mutations and merges remain
   held, so neither another build completion nor a manual dispatch may race it.
   If an unexpected run appears, cancel it before allowing host mutation.
3. Do not trust `deploy_fail_safe.sh`'s automatic image revert here. The
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
6. Restore only the workflow/timer states recorded in step 2, release the merge
   hold, then announce the end.

## 7. Rollback

Roll back if any Verify step fails, or on a regression later.

1. Stop: steps 2.1 to 2.3.
2. Set the migrated volume aside if the disk allows it:
   `docker volume create tinyassets-data-migrated` and copy into it. Otherwise
   go straight to the restore.
3. Restore the snapshot. Check its sha256 first. This archive is ours and
   verified, so it is extracted with its links, numeric owners and ACLs:
   ```sh
   (cd /var/backups/tinyassets && sha256sum -c <id>.sha256)
   VOL=$(docker volume inspect -f '{{ .Mountpoint }}' tinyassets-data)
   find "$VOL" -mindepth 1 -delete
   tar -xzpf /var/backups/tinyassets/<id> -C "$VOL" \
       --numeric-owner --acls --xattrs --same-owner
   ```
   `deploy/backup-restore.sh` restores the nightly full tier. It refuses link
   members, so it cannot restore a volume that holds workspace symlinks or the
   multi-link `.runtime/` inodes. Use the tar line above for this snapshot.
4. Restore the pinned runtime and image **together**, with workflows and timers
   still disabled. Never dispatch a previous image using post-cutover main's
   Compose/launcher bundle. Retagging an image does not deploy it.
   ```sh
   (cd "$ROLLBACK" && sha256sum -c SHA256SUMS)
   docker image load -i "$ROLLBACK/image.tar"
   tar -xzpf "$ROLLBACK/runtime.tar.gz" -C / --numeric-owner --acls --xattrs --same-owner
   systemctl daemon-reload
   PREVIOUS_IMAGE=$(cat "$ROLLBACK/image-id")
   test "$(docker image inspect -f '{{.Id}}' "$PREVIOUS_IMAGE")" = "$PREVIOUS_IMAGE"
   TINYASSETS_IMAGE="$PREVIOUS_IMAGE" docker compose --env-file /etc/tinyassets/env \
       -f /opt/tinyassets/compose.yml up -d --force-recreate --timeout 20 daemon cloudflared logs
   test "$(docker inspect -f '{{.Image}}' tinyassets-daemon)" = "$PREVIOUS_IMAGE"
   SAVED_REF=$(sed -n 's/^TINYASSETS_IMAGE=//p' /etc/tinyassets/env)
   docker image inspect "$SAVED_REF" >/dev/null 2>&1 || docker pull "$SAVED_REF"
   test "$(docker image inspect -f '{{.Id}}' "$SAVED_REF")" = "$PREVIOUS_IMAGE"
   ```
   The restored environment file also retains the previous immutable image ref
   for subsequent systemd starts. Verify it resolves to the saved image ID.
5. Revert PR #4568 on main while deploy is disabled. Wait for that build to
   finish before enabling deploy; never release the merge hold with cutover
   code at main on the restored old volume.
6. Assert the previous deployed SHA and run the public canary and a founder
   app turn. Restore the recorded workflow/timer states only after these pass.

Once users have written after the cutover, rollback loses those writes. Decide
at Verify.
