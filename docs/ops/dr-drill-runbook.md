# DR Drill Runbook

## When to run

- **Weekly, automatically** — Sunday 06:17 UTC, from the **off-region** backup store
  (target-architecture S1a.5). The scheduled run never contacts the primary host: it
  fetches the newest `tinyassets-data-*.tar.gz` from the off-region store and resolves the
  image from GHCR, so it still works when the primary is gone. Until the off-region store
  exists (`OFFREGION_BACKUP_REMOTE` + `OFFREGION_BACKUP_RCLONE_CONFIG` secrets, S1a.3), the
  weekly run is red on purpose and says so.
- **After major changes** to `deploy/compose.yml`, `deploy/hetzner-bootstrap.sh`,
  or `deploy/backup-restore.sh`.
- **After any restore event** — drill confirms the restored state is healthy before
  closing the incident.

## How to trigger

GitHub → Actions → `DR drill` → Run workflow.

Inputs:

| Input | Default | Notes |
|---|---|---|
| `backup_origin` | `offregion` | `offregion` is the drill. `primary` restores the primary's own tarball over ssh: a diagnostic that proves nothing about losing the primary. |
| `drill_droplet_size` | `s-2vcpu-2gb` | Minimum tested size for apt + Docker bootstrap; `s-1vcpu-1gb` OOMs. |
| `backup_source` | (newest) | A `tinyassets-data-*.tar.gz` name in the off-region store (or a path on the primary for `primary`), for a point-in-time test. |
| `destroy_on_failure` | `false` | Set `true` to auto-destroy on failure; default keeps the Droplet up for inspection. |
| `cleanup_droplet_id` | (empty) | Cleanup-only mode: delete this retained positive-decimal Droplet ID and skip every drill/provisioning step. |

## What the workflow does

1. Selects the backup. For `offregion` it fetches the newest (or named)
   `tinyassets-data-*.tar.gz` from the off-region store onto the runner, using a
   read-only rclone credential, and checks the name against the archive grammar
   before rclone touches it. For `primary` it picks the newest tarball on the
   primary. Either way, one validator checks the safe archive shape, contained in
   that origin's root, and records the archive and representative-member SHA-256
   values.
2. Resolves the runtime image. For `offregion` it takes the newest of the last
   20 commits with a published GHCR build, pinned to its digest; no primary
   contact. For `primary` it reads only the primary host's final
   `TINYASSETS_IMAGE` assignment. Both require the canonical immutable
   `ghcr.io/tinyassets/tinyassets-daemon@sha256:<digest>` form, and neither copies
   the primary environment or any secrets.
3. Resolves the newest public, available Debian x64 image serving `nyc3` across
   a bounded DigitalOcean distribution-catalog traversal. This first request
   verifies the token's required `image:read` scope before any mutation.
4. Registers the deploy SSH key with the DO API (idempotent by fingerprint).
5. Creates a `tinyassets-dr-drill` Droplet with the resolved Debian image and
   requested size.
6. Waits for the Droplet to get a public IP + SSH to become ready.
7. Runs `deploy/hetzner-bootstrap.sh` on the drill Droplet (Docker, user,
   systemd units, log rotation, swap).
8. Streams the exact validated backup from primary to drill, then requires the
   destination SHA-256 to match before restore.
9. Runs `deploy/backup-restore.sh` with the exact transferred `BACKUP_FILE` and
   verifies the representative member at Docker's inspected volume mountpoint.
10. Requires exactly one `TINYASSETS_IMAGE=` assignment in the fresh template
   and writes only the validated public digest into it. It starts only the
   daemon compose service, waits 30s, opens an SSH port-forward to loopback,
   and probes `http://localhost:8001/mcp` via `scripts/mcp_probe.py status`
   (no Cloudflare tunnel required).

## Pass / fail criteria

**Pass:** `mcp_probe.py status` exits 0 (MCP initialize + session + `get_status` tool call succeeds).

TinyAssets on pass:
- Confirms destruction of the drill Droplet.
- Appends and commits a timestamped `docs/ops/dr-drill-log.md` entry containing
  the Debian base image and daemon runtime image as distinct fields, plus
  archive/restored-state checksum evidence.

**Fail:** `mcp_probe.py` exits non-zero.

TinyAssets on fail:
- Opens a `dr-failed` GitHub issue with the probe output + Droplet IP.
- Leaves the drill Droplet **running** for inspection (SSH directly with the deploy key).
- Does NOT destroy unless `destroy_on_failure=true`.
- Finishes the workflow red after recording the issue and retention outcome.

## Inspecting a failed drill

```bash
# SSH to the drill Droplet (IP is in the dr-failed issue).
ssh root@<drill-ip>

# Check compose status.
docker compose --env-file /etc/tinyassets/env \
  -f /opt/tinyassets/deploy/compose.yml ps

# Tail daemon logs.
docker compose --env-file /etc/tinyassets/env \
  -f /opt/tinyassets/deploy/compose.yml logs daemon --tail 50

# Probe locally.
curl -s -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}'
```

When done, prefer the credential-scoped cleanup-only dispatch:

1. GitHub → Actions → `DR drill` → Run workflow.
2. Set `cleanup_droplet_id` to the retained ID and leave other inputs at their
   defaults. The cleanup job validates the ID, requires the exact drill name
   plus both drill tags, performs one bounded DELETE, and cannot provision a
   replacement.

An operator with an independently configured DigitalOcean CLI can instead run:

```bash
doctl compute droplet delete <droplet-id> --force
```

## Required secrets

The full drill uses the same set as `deploy-prod.yml`. Cleanup-only mode reads
only `DIGITALOCEAN_TOKEN`.

| Secret | Purpose |
|---|---|
| `DIGITALOCEAN_TOKEN` | Must include `image:read` plus SSH-key read/create and Droplet create/read/delete permissions. Missing catalog scope fails red before any mutation. |
| `DO_SSH_KEY` | Private key PEM — must be in `authorized_keys` on drill Droplet (cloud-init adds it) |
| `DO_DROPLET_HOST` | Primary Droplet IP — for streaming the backup |
| `DO_SSH_USER` | SSH user on primary (typically `root`) |
