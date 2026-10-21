# Recovery

Platform recovery (C3) consumes a verified `platform/tinyassets-platform-*.json`
manifest and its shared-only archive. `backup-restore.sh` selects that catalog,
verifies the archive digest and calls `platform-recovery.py`. Current lifecycle,
billing, spend, suspension and identity authority must be recovered before
admission opens. Missing authority fails closed. Owners sign in and reconnect.
Owner volumes are never an input to platform recovery.

Single-owner recovery (C2) uses the snapshot ID and digest published in the
owner-volume map. The root lifecycle restore stops that owner, installs the
verified image and commits its new generation before the next wake. It leaves
platform authority unchanged.

Cutover rollback (C4) uses deployment control's recorded backup and old image:

```bash
sudo env PYTHONPATH=/opt/tinyassets/boxhostd/harness-current/app \
  python3 -m tinyassets.cutover_control rollback \
  --state /var/lib/tinyassets-deploy/cutover \
  --data "$(docker volume inspect tinyassets-data --format '{{ .Mountpoint }}')" \
  --deploy-script /tmp/deploy_fail_safe.sh
```

Use the exact command printed by the failed deployment if its configured paths
differ. Missing or consumed eligibility refuses rollback. Ordinary traffic
permanently consumes eligibility; platform recovery never accepts the old layout.

The DR drill exercises platform recovery and a map-referenced single-owner
restore separately. It must remain red while current recovery authorities are
unavailable. Never enable a production tunnel on the drill host.
