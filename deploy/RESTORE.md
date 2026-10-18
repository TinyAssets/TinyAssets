# Paired restore

Restore stops every consumer and never restarts services. Keep both retained
pre-restore volumes until validation succeeds. Never swap those volumes back
manually after traffic; doing so bypasses credential and operation fences.

## Maintenance-window rollback

Normal restore is only for the pre-traffic cutover window. It restores the exact
verified shared/owner pair, including the original layout. The live router
`traffic_opened_at` marker, registered shared stores, owner trees and owner
journals must be readable. Missing live state refuses. A missing traffic store
is accepted only when the live layout proves a pre-traffic cutover.

```bash
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh --timestamp=YYYY-MM-DDTHH-MM-SSZ
```

Do not use that command for an operational rollback after traffic opens.

## Disaster recovery, including a fresh droplet

Provision empty replacement shared and quota-enabled XFS owner mounts at the
usual locations. Retain the newest verified pair: data tar, owner-store tar and
pair manifest. Set BACKUP_FILE and BOX_BACKUP_FILE when using local archives.
A disaster restore refuses an older local or remote pair.

Supply the existing WorkOS API credential for read-only identity reconciliation
and the Stripe adapter configuration when available. An uncertain WorkOS lookup
refuses publication; absence of billing configuration is listed in the report.
No application service or box may start during preparation.

```bash
set +e
sudo -E bash /opt/tinyassets/deploy/backup-restore.sh \
  --disaster-restore --acknowledge-unrecoverable-facts
rc=$?
set -e
test "$rc" -eq 6
```

Status **6** means completed disaster recovery, with acknowledged loss from backup
time to restore time. Status **5** means publication succeeded but erasure is
pending; boxhostd resumes the durable cleanup list before admitting any box.
Preparation failures return **4** with both original volumes intact. Inspect the durable
`.disaster-restore.json` and logged report for credential reset, operation fences,
erased owners, billing reconciliation and the daily-spend hold. All owners must
sign in and reconnect external credentials. Pre-restore bearer credentials and
operations are refused. Consents, grants, content and other listed classes revert
to backup time. Erasure alone is carried from readable tombstones and WorkOS;
preparation records erasures without touching live trees. Only after publication
are listed trees erased or moved into root-private quarantine. Deleted accounts
cannot sign in, refresh, or authenticate with API keys or hook tokens.

Start only an image compatible with the restored layout. Run the acceptance
canaries while ordinary traffic remains closed, then use the router's local
root-authenticated acceptance RPC (run `scripts/open_router_traffic.py` as host root).
The router commits `traffic_opened_at` before admitting user traffic. Verify the
deployed SHA and app path before deleting retained directories.

The scheduled `.github/workflows/dr-drill.yml` uses the acknowledged disaster
path and asserts status 6 plus credential-reset and operation-fence evidence.
A drill must not open a production tunnel or enable production workflows.
