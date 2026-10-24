Reviewing the PR diff now. Next I'm reading the broker routing, the deploy script's transaction and restore paths, and the env installer to check the failure scenarios you listed.

Still reviewing. Now tracing where `harness-current` is switched (deploy script and owner-box roll) relative to the snapshot and the dirty marker.

Checking which compose service actually receives the outbound-HTTP flag, since `compose.yml` also sets it.

Still going: verifying how a readiness timeout propagates in `roll_owner_boxes.py`, and checking the test fixtures for mocked-away behavior.

Now checking `bundle_state` for the absent-bundle case, then the second half of the workflow rollback step.

Code reading is mostly done. Running the pure-Python tests from the PR (skipping the shell-transaction test, which touches real `/etc` paths) to confirm they pass as written.

I found two High-severity gaps, so I disagree: the PR doesn't yet meet "nothing reports success over a mixed tree". The slot queue and the request-wake priority are sound. The remaining problems are in the restore transaction, in timeouts that now include queue time, and in what the readiness signal can actually see.

**What held up**
- **No forgery:** a box can't set wake priority. Only uid 1002 reaches `broker_lifecycle`. A `_request_driven` key from any other caller fails the exact-key check. Boxes can't reach `daemon.sock`.
- **No slot leaks:** the `claim()` counters stay correct on timeout (the `finally` decrements and notifies).
- **No fence deadlock is introduced:** the claim never touches `Fence`, and each wait is capped at 45s. Before the PR the broker's own queue had no cap.
- **No thread is held forever:** each wait is limited by 45s plus the socket timeout.
- **No secret leak:** snapshot directories are 0700 and `cp -a` keeps file modes. The new flag isn't a secret.
- **Restore order is right:** box host → compose → broker → daemon holds on both `--restore-bundle` and `recover_from_failed_install`.
- **Tests:** the 7 pure-Python tests pass. I didn't run the shell transaction test (see finding 9).

## Findings

1. **High — `deploy/deploy_fail_safe.sh:2065`, `:2069`, `:2356`: three failure exits skip the restore.** They come after `install_bundle`, which now installs the new box host and restarts it, and after the dirty marker is cleared. Those exits (`exit 1` / `harness_invalid`) never call `recover_from_failed_install`.
   - They leave the new box host and new compose running with the old broker and daemon. That is the incident-2 pairing.
   - There is no dirty marker, and the message says "prod untouched", which is false.
   - The workflow won't roll back, because `IMAGE_ROLLBACK_ALLOWED` needs `rc == 0`.
   - `harness_invalid` is a realistic trigger: a 2.4 GB rootfs export filling the disk has happened before.

2. **High — `deploy/deploy_fail_safe.sh:1527`: `domains/` isn't covered by the snapshot.** `host_managed_name` omits it, but `scripts/host_service_bundle.py:83` ships `domains.*` modules. `boxhostd_install_files.py` lists 22 `domains/` files that get installed into `/opt/tinyassets/boxhostd/domains`. The snapshot and restore skip them, so a restore reports `deployed` over old `tinyassets/` plus new `domains/`. The managed names should come from the top-level entries in `.host-service-files.json`, not a hand-written list.

3. **Medium — `deploy_fail_safe.sh:1985` and `:2638`: an interrupted restore leaves no dirty marker.** Both restore paths write the marker only when the restore returns a failure. `restore_host_bundle` stops boxhostd and deletes the host trees with `rm -rf` before copying them back (lines 1590–1596). If the run is killed or ssh drops in that window:
   - boxhostd is left stopped on a half-deleted tree, with no marker.
   - The next deploy snapshots that tree as its rollback target.
   - `broker.env` is replaced with `rm -f` then `cp`, and its compose `env_file` is `required: false`, so the broker can start with no secrets.
   - Fix: run `mark_dirty "$snap"` before `restore_bundle_from`.

4. **Medium — env "restore" is weaker than `DEPLOY.md` says.**
   - `deploy-prod.yml:537` (`install-tinyassets-env.sh set …`) re-renders `broker.env`, `boxhostd.env` and `broker-rclone.conf` with the new renderer before `deploy_fail_safe.sh` takes its snapshot. The snapshot therefore holds the new release's output.
   - `daemon.env` is never snapshotted.
   - Restoring files derived from `/etc/tinyassets/env` without restoring that source breaks the rule that they are always rendered from it.

5. **Medium — queue time now eats the broker's fixed client deadline.**
   - `daemon_control.py:20` gives wake/sleep/status/stop 65s in total. That used to cover the host's 60s `startup_timeout` (`owner_daemons.py:36`) with no queue in front.
   - Now a host wait of up to 45s (`host_service.py:97`) plus startup can exceed 65s. The broker then reports failure (a 503 to the user) while the box comes up.
   - Background calls now fail after 45s while competing copies run for minutes. For example, the drain's `status`/`sleep` calls in `deploy()` can come back `incomplete` and fail owner-box acceptance; before, the broker queue simply waited.

6. **Medium — removing the broker-side cap moves the queue into the broker's shared thread pool.** `routing.py:272` starts `tick_owner` for every pending owner. Each call blocks an `asyncio.to_thread` worker (`admission.py:35`, `min(32, cpu+4)` workers) for up to 45s while waiting, or for minutes while copying.
   - Request-driven calls queue first-in-first-out behind these before they reach the host's priority, so the priority can be inverted.
   - `publish` and `BrokerRegistration.maintain` also go through `thread_work`, so re-registration after a boxhostd restart can be delayed.
   - Before the PR, `host_call` capped this at 3.

7. **Medium — the readiness signal can both pass too early and fail on healthy work** (`host_service.py:42`, `:191`).
   - "Idle" is a snapshot of host-side counters at one moment. It can't see work the broker has recorded but not yet sent (`owner_volumes.pending`, sent on a 30s tick). So `ready()` after the drain can pass in the gap before the post-drain copies start, which isn't what `DEPLOY.md` claims.
   - It also requires the whole fleet to be idle. One legitimately long background operation (volume ops are allowed 4h) pushes readiness past 540s. That fails `host_ready`, which sets `IMAGE_ROLLBACK_ALLOWED=1` (`deploy-prod.yml:1093`) and rolls back a healthy deploy.
   - Smaller point: `wait_boxhostd_ready.py:21` doesn't catch `EOFError` (raised if boxhostd restarts mid-probe), so the script crashes and triggers a rollback instead of retrying.

8. **Medium — the box image selection isn't in the snapshot.** `/var/lib/boxhostd/state/images.json` (written by `select_image`, read at `images.py:19-21`) chooses the box rootfs at `owner_daemons.py:108-111`; `harness-current` does not. A manual `--restore-bundle` after owner-box acceptance reverts `harness-current` but leaves boxes on the new rootfs with old host code. The workflow's own rollback path isn't affected.

9. **Low — tests skip or bypass the behavior they claim to cover.**
   - `test_restore_reverts_box_host_bundle_and_harness_before_converge` (`tests/test_deploy_bundle_transaction.py:1142`) doesn't override `BOXHOSTD_UNIT_FILE`, `BROKER_ENV_FILE` and the related paths. The script therefore reads, and runs `rm -f` on, the real `/etc/systemd/system/boxhostd.service` and `/etc/tinyassets/*`.
   - On CI those files are absent, so restoring the unit and env files and restarting boxhostd before converge are never exercised. There is no failure or interruption test.
   - The slot tests call `broker_lifecycle` directly and never go through `serve()`. Reverting the change at `host_service.py:287`, which removed the refusal at connection time that caused the incident, would still pass every test.

10. **Low — smaller issues.**
    - `BOXHOSTD_BUNDLE_REQUIRED` only applies when `BUNDLE_DIR` exists. If the stage directory is missing, the deploy runs image-only: a new broker over the old box host (`deploy_fail_safe.sh:1020`).
    - The comment at `install-boxhostd-preflight.sh:36` ("reaches this installer BEFORE deploy_fail_safe.sh") is now stale.
    - The `migration_refused` path starts the daemon (line 2240) before restoring the host, which is the reverse order, though only briefly.

## Would the 2026-10-23 incident have been prevented?

1. **Canary 503: mostly, but not guaranteed.** "Connection limit exceeded" can no longer happen for broker calls, and the reserved slot plus priority will likely carry the canary's wake. Findings 5–7 mean it can still fail: deadline overruns, a full thread pool, or a readiness check that passes in the gap before post-drain work starts.
2. **Rollback left a mixed box host: yes for this incident's path.** `tinyassets/`, `deploy/`, `scripts/`, `harness-current` and the units are restored and boxhostd is restarted before the broker converges. It still leaves `domains/` mixed (finding 2) and doesn't cover the paths in findings 1, 3 and 8.
3. **Missing broker flag: only if the flag is in `/etc/tinyassets/env`.** The render now carries it and the test proves that. However, eb582d82d08's compose already set it on the broker (default `1`), so the failure most likely appeared after the rollback restored the pre-eb582d82d08 compose, which lacks that override. If the live flag existed only as a hand edit in `broker.env`, the step at `deploy-prod.yml:537` still drops it before the snapshot, and it would still be lost (finding 4). I couldn't check the live env from here.

VERDICT: DISAGREE_EVIDENCE
