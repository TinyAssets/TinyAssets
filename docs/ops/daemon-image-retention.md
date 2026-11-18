# Daemon image retention: rollout procedure

Rollout was accepted September19,2026UTC; see [the rollout evidence](https://github.com/TinyAssets/TinyAssets/blob/f934871ad44b9a198070d55de517be85d8e107f6/openspec/changes/archive/2026-09-19-daemon-image-retention/live-acceptance.md). The
following procedure remains the operator contract, not a claim that every
resource-capacity gap is closed.

No production cleanup or service activation was performed while preparing this
patch. Both cleanup units change together; transcript rotation remains unchanged.
The compatibility command is dry-run by default. Removal needs BOTH `--apply`
and exact `TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY=1`; absent/0 stays read-only,
malformed values refuse before work. `DRY_RUN=1` can only reduce that authority.
Installed timers pass `--apply`, but **timer installation alone does not opt in**.
The coordinating release owner must verify the installed helper's direct dry-run
before setting the retention-specific opt-in. Existing alarm/rotation behavior
is not disabled by the new flag; the whole service is not a no-effect probe.

## Configuration and prerequisites

- Pressure is `100 * (1 - available / total)`. Zero available means100%; invalid
  or zero total is unknown. Defaults:85% trigger,75% stop; optional
  `DISK_AUTOPRUNE_PCT` and `DISK_AUTOPRUNE_LOW_PCT` must satisfy0<low<high<100.
- Classic overlay2 uses the inspected DockerRootDir. Containerd stores require
  `TINYASSETS_IMAGE_RETENTION_STORAGE_PATH` to name the operator-verified image
  content filesystem; absence refuses, including dry-run. Verify live Docker
  driver and actual storage mount before setting it. The path grants no deletion
  authority: only exact allowlisted Docker image refs can be removed.
- `DISK_WATCH_PATH` remains an explicit alarm-only override. Without it, the
  alarm uses the same image-store path resolution as retention. Unknown alarm
  mapping reports UNKNOWN/status1 and still permits transcript rotation; the
  retention command independently refuses any deletion.
- Current configuration is reread from `/etc/tinyassets/env` under the host
  mutation lock. No eval, shell sourcing, arbitrary target path, or secret log.
- `tinyassets-data` must be the active daemon's `/data` mount. Its root receipt
  must exist and contain an explicit rollback_target (empty is valid). Release
  path overrides, unknown protected images, any fence state, unavailable locks,
  or unverified registry recovery refuse.
- GHCR verification uses an anonymous pull-scoped token for the fixed public
  daemon repository. No Docker login, account key, registry push or paid service.
  Failure preserves local cache. Manifest/config hashes and host platform are
  checked; every required layer is probed before any deletion.

## Lead-owned acceptance

1. Exact-head independent review and required CI first. Root verifies the new
   activation key in `/etc/tinyassets/env` is absent or exactly0 before installing.
   Publish the complete checksummed runtime closure. The installer stops timers,
   waits active services, publishes files, then restarts normal timers; retention
   stays read-only. No race to disable it afterward and no global DRY_RUN needed.
2. Run the installed helper directly with no `--apply`, not
   `systemctl start tinyassets-disk-watch.service`. The latter includes real
   transcript rotation (its separate --dry-run CLI is the only dry-run control
   it reads). Confirm current/all stopped and
   running container references, configured image, explicit receipt rollback,
   two most recent older rollback candidates and all images at least as new as
   the current image protected.
   Unknown store mapping is a blocker, not a reason to guess a path or skip it.
3. Only after that proof does root use the existing environment installer to
   set `TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY` to exact1. It affects retention
   only. New service invocations load the value via EnvironmentFile; no timer
   restart is needed to arm the next tick. A direct `--apply` pass must likewise
   receive the opt-in explicitly; opt-in alone never enables a direct dry-run.
   The existing `deploy/install-tinyassets-env.sh set <KEY>` accepts the value
   on stdin and preserves root:tinyassets/0640 via atomic replacement. Use its
   verified release copy under the existing fence-then-host-mutation locks;
   do not source or print the secret-bearing environment file. The key helper
   itself does not take the shared locks. These are root-owned operations, not
   actions performed in this preparation.
   The helper takes fence then mutation lock, makes at most four non-force
   immutable daemon removals, caps the locked phase at60s/whole pass120s, and
   rechecks live inventory/configuration before each removal. Per-removal events
   remain visible even if a later check fails. Pressure residual is not success.
4. Verify data volumes/containers unchanged, rollback images available, protected
   public canary green, and actual filesystem pressure measured. Registry-verified
   cache can be pulled again by exact digest; remote permanence is not promised.
5. Next healthy tick must avoid needless removal. Rollback sets the retention
   opt-in to exact0, leaves alarm/rotation enabled, and waits for any already
   running bounded retention pass to settle before declaring cleanup disarmed.
   Do not roll back to broad-prune code. Installer failure may restore its prior
   bundle, so root verifies the actual installed closure before any activation;
   an older broad-prune bundle does not implement this new flag. Sync/archive
   only after accepted live proof.

This slice does not fix browser per-job memory isolation, arbitrary user data
retention, build cache, journals, or the separate emergency-triage workflow.
