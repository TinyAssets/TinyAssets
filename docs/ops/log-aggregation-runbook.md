---
title: Log Aggregation Runbook
date: 2026-04-20
row: K (self-host uptime migration)
---

# Log Aggregation Runbook

TinyAssets uses a layered logging strategy on the self-hosted Droplet:

| Layer | Tool | What it does |
|-------|------|--------------|
| Collection | Vector sidecar (`deploy/vector.yaml`) | Receives daemon, tunnel, and slack-agent stdout from Docker's async Fluent driver on host-loopback port 24224; has no Docker socket; re-emits everything on its own stdout |
| **Durable, on-box** | **the host journal** | The `logs` container uses the `journald` driver (`deploy/compose.yml`), so Vector's re-emitted stream is journal data and **survives container recreates**. Retention: `deploy/journald-tinyassets.conf`, a drop-in the host-uptime installer owns |
| Real-time (optional) | Better Stack | Vector also ships live when `BETTERSTACK_SOURCE_TOKEN` is set |
| Offsite archiving | `deploy/ship-logs.sh` + systemd timer | Pulls last 24 h of container logs, archives as `.tar.gz`, uploads to `LOG_DEST` (Hetzner Storage Box or DO Spaces), prunes archives older than 30 days |

**Why the journal is the durable layer (2026-09-26).** It used to be the `logs`
container's own json-file, which lives under `/var/lib/docker/containers/<id>/`
and is deleted with the container. Every deploy recreates the daemon, and
`deploy/deploy_fail_safe.sh` force-recreates `tinyassets-logs` whenever a Vector
input changes. On 2026-09-26 a live latency investigation needed the per-attempt
`latency_ms` lines from turns six minutes earlier and they no longer existed
anywhere (`docs/concerns/2026-09-26-daemon-logs-not-shipped.md`).

Two claims that were in this repo and were **wrong**, corrected here: `compose.yml`
and `tinyassets-env.template` both said logs were "captured by journald" via
compose stdout. `docker compose up -d` detaches, so a container's stdout goes to
its logging driver, never to the systemd unit that ran compose.

> **`LOG_DEST` is still unset in production, so the offsite row below still ships
> nothing.** That is tracked separately and is not fixed by this change — what is
> fixed is that the evidence now survives on the box long enough to be collected.

---

## Setup

### 1. Better Stack (optional, recommended)

1. Create a free account at `logs.betterstack.com`.
2. Create a new **Source** → **HTTP** type.
3. Copy the ingest token.
4. Add to `/etc/tinyassets/env`:

```
BETTERSTACK_SOURCE_TOKEN=<your-token>
```

5. Reload the logs sidecar:

```bash
docker compose -f /opt/tinyassets/deploy/compose.yml restart logs
```

Logs from the daemon, tunnel, and workers will appear in Better Stack within seconds.

### 2. Offsite archive via ship-logs.sh

`ship-logs.sh` uses rclone. Each archive includes `fleet-manifest.tsv` with the exact container ID, state, and log filename for the daemon, tunnel, and four workers. Logs are read by immutable ID and the name-to-ID mapping is rechecked, so a concurrent Compose recreation fails the run instead of silently mixing generations. Stopped containers are included; a missing container or unreadable log also fails before upload. The script and timer are installed through the content-addressed host-uptime release after every successful production deploy. Configure the same remote as Row J backups (see `docs/ops/backup-restore-runbook.md`).

Add to `/etc/tinyassets/env`:

```
# rclone URL for log archives — can share the same remote as backups
LOG_DEST=sftp:storagebox/tinyassets-logs
# or for DO Spaces:
# LOG_DEST=s3:tinyassets-logs/logs
```

Install the systemd units (ship-logs + disk-watch together):

```bash
cp /opt/tinyassets/deploy/tinyassets-ship-logs.service  /etc/systemd/system/
cp /opt/tinyassets/deploy/tinyassets-ship-logs.timer    /etc/systemd/system/
cp /opt/tinyassets/deploy/tinyassets-disk-watch.service /etc/systemd/system/
cp /opt/tinyassets/deploy/tinyassets-disk-watch.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tinyassets-ship-logs.timer tinyassets-disk-watch.timer
```

Verify the timers are scheduled:

```bash
systemctl list-timers tinyassets-ship-logs.timer tinyassets-disk-watch.timer
```

**Disk-watch** (`scripts/disk_watch.py`) fires hourly at minute27 (also shortly
after boot) and opens a `disk-pressure` GH Issue when the verified image-store
filesystem reaches `DISK_WARN_PCT` (default
80%). Requires `GITHUB_TOKEN` in `/etc/tinyassets/env` with `issues: write` scope.
Optional env vars: `DISK_WATCH_PATH`, `DISK_WARN_PCT`, `GITHUB_REPOSITORY`.

Both automatic cleanup services use bounded daemon-image retention, not system,
builder, journal, volume or container cleanup. The compatibility command
`python3 scripts/disk_autoprune.py` defaults to dry-run; both `--apply` and exact
`TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY=1` are required for exact non-force removals
after all protection/recovery checks. Timer installation alone cannot enable it.
Run the helper directly for acceptance: the chained service also runs ordinary
transcript rotation, which this flag does not affect. Containerd image
storage needs an operator-verified `TINYASSETS_IMAGE_RETENTION_STORAGE_PATH`.
See the [retention rollout checklist](../../openspec/changes/archive/2026-09-19-daemon-image-retention/operator-acceptance.md)
before installing or activating the updated cleanup units. Unknown mapping or
protected-image evidence is a refusal, not permission for broad prune.

---

## Daily Operations

### Query today's logs (live on Droplet)

```bash
# Vector's local forwarded stream (daemon, tunnel, and workers)
docker logs tinyassets-logs --since 1h
docker logs tinyassets-logs -f
```

### Query via journald — this is the one that survives a deploy

```bash
# Every forwarded line from every container, across past container recreates.
journalctl CONTAINER_NAME=tinyassets-logs --since "1 hour ago"

# Microsecond timestamps — needed for per-attempt latency evidence.
journalctl CONTAINER_NAME=tinyassets-logs --output=short-iso-precise \
  --since "2026-09-26 18:10" --until "2026-09-26 18:20"

# One originating container: the JSON payload carries Vector's `tag`/`role`.
journalctl CONTAINER_NAME=tinyassets-logs -o cat | grep '"role":"daemon"'

# The systemd unit that runs compose — the supervisor's own output, NOT the
# containers' (`docker compose up -d` detaches).
journalctl -u tinyassets-daemon --since "1 hour ago"
```

`docker logs tinyassets-logs` also still works, because journald is a readable
driver — but it is scoped to the current container, so prefer `journalctl` when
the question spans a deploy.

### Journal retention (no host action — the installer owns it)

`deploy/journald-tinyassets.conf` is installed to
`/etc/systemd/journald.conf.d/tinyassets.conf` by
`deploy/install-host-uptime-services.sh`, which runs after every successful
production deploy and restarts `systemd-journald` only when the bytes changed.
`Storage=persistent`, `SystemMaxUse=12G`, `MaxRetentionSec=30day`,
`SystemKeepFree=2G`. Rate limiting stays on at a high ceiling
(`RateLimitBurst=100000` per 30s). A dropped message during an incident is
the evidence this exists to keep, and journald records the loss with its own
suppressed-message line. The age cap is a maximum, not a promise: 12 GiB at a
sustained 100 KiB/s is about 35 hours, and whichever bound runs out first wins.
Three days at that rate is about 25 GiB. On the 49 GiB production disk (40%
used on 2026-10-01) that would pass the 80% `disk_watch` alarm, so the cap
stays at 12G.

Change it there, not on the box, or the next install reverts it.

```bash
journalctl --disk-usage
cat /etc/systemd/journald.conf.d/tinyassets.conf
```

### Query from Better Stack

1. Log in at `logs.betterstack.com`.
2. Filter by source or by metadata field `.service = "tinyassets"`.
3. Use `.role = "daemon"` or `.role = "cloudflared"` to scope to one container.
4. Date-range picker selects the archive window.

Better Stack free tier retains 3 GB / month — sufficient for a single daemon running at normal load.

---

## Fetch a session trail from GitHub Actions

`Fetch production logs` (`.github/workflows/fetch-prod-logs.yml`) reads the
journal over the deploy key and uploads one artifact, `production-log-timeline`,
holding `production-log-timeline.enc`: the ordered, redacted JSONL trail sealed
to `mobile/review-evidence-recipient.pub`. This repo, its artifacts and its job
logs are public, so the plaintext never leaves the runner, and the job log and
step summary print aggregates only (line count, truncation, error types by
commit). Name a `session_id`, `account_id` or `trace_id` for a window of up to
7 days; a window with no filter must be within 1 hour.

Decrypt on the founder's machine, where the private key lives at
`~/.tinyassets/review-evidence.key`. It is never in CI and never in the repo.

```bash
gh run download <run-id> -n production-log-timeline
python mobile/scripts/review_evidence.py decrypt production-log-timeline.enc ./timeline
# ./timeline/prod-logs.jsonl is the trail; ./timeline must not exist or be empty
```

---

## Pull a Date Range from Offsite Archives

Archives are named `tinyassets-logs-YYYY-MM-DDTHH-MM-SS.tar.gz` and stored at `LOG_DEST`.

### List available archives

```bash
rclone lsf --format "tp" "${LOG_DEST}/"
```

### Download and inspect a specific archive

```bash
# Download
rclone copyto "${LOG_DEST}/tinyassets-logs-2026-04-20T02-00-00.tar.gz" /tmp/

# Extract
mkdir /tmp/log-restore
tar -xzf /tmp/tinyassets-logs-2026-04-20T02-00-00.tar.gz -C /tmp/log-restore

# Inspect
ls /tmp/log-restore/
grep "ERROR" /tmp/log-restore/tinyassets-daemon.log
```

### Pull a date range (multiple archives)

```bash
# List archives between two dates
rclone lsf --format "tp" "${LOG_DEST}/" | grep "2026-04-1[5-9]"

# Download all of them
rclone copy --include "tinyassets-logs-2026-04-1*.tar.gz" "${LOG_DEST}/" /tmp/log-range/
```

---

## Manual Offsite Push

To trigger an ad-hoc archive (e.g. before a deploy):

```bash
LOG_DEST="${LOG_DEST}" LOG_SINCE=4h bash /opt/tinyassets-host-uptime/current/deploy/ship-logs.sh
```

Dry-run to confirm env without touching anything:

```bash
DRY_RUN=1 LOG_DEST="${LOG_DEST}" bash /opt/tinyassets-host-uptime/current/deploy/ship-logs.sh
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| No logs in Better Stack | Token not set or wrong | Check `BETTERSTACK_SOURCE_TOKEN` in `/etc/tinyassets/env`; restart `logs` container |
| Vector container not running | Depends-on daemon unhealthy | Check `docker logs tinyassets-logs`; confirm daemon healthcheck passes. While it is down the fluentd driver buffers in memory and then **drops** — the journal gets nothing, so fix this first |
| `journalctl CONTAINER_NAME=tinyassets-logs` is empty | The `logs` container is not on the journald driver (compose drift) | `docker inspect -f '{{.HostConfig.LogConfig.Type}}' tinyassets-logs` must print `journald`; if not, the deployed compose.yml is behind `deploy/compose.yml` |
| Journal history shorter than 30 days | Drop-in missing, or `SystemMaxUse` hit | `cat /etc/systemd/journald.conf.d/tinyassets.conf`; `journalctl --disk-usage`. Absent drop-in means the installer has not run since the change landed |
| Journal empties on reboot | `Storage` is not persistent | `/var/log/journal` must exist; the drop-in sets `Storage=persistent` and journald creates it on restart |
| ship-logs.sh exits 1 | `LOG_DEST` missing, a required container missing, or its logs unreadable | Set `LOG_DEST`; then inspect `docker ps -a` and `docker logs <container>` for every required fleet member |
| rclone upload fails | Remote misconfigured | Run `rclone lsd "${LOG_DEST}/"` to test connectivity |
| Archives not being pruned | Clock skew or naming mismatch | Check archive names match `tinyassets-logs-YYYY-MM-DDTHH-MM-SS.tar.gz` pattern |
| `docker logs` shows nothing | Container hasn't started | `docker ps -a` to check container state |

---

## Retention Policy

| Storage | Retention | Where |
|---------|-----------|-------|
| Docker fluentd driver buffer | In-memory; **drops** while Vector is down (`docs/concerns/2026-09-26-fluentd-driver-drops-while-vector-is-down.md`) | Not durable, never evidence |
| **host journal** | **12 GiB maximum, 30 days maximum, whichever runs out first** (`deploy/journald-tinyassets.conf`). About 35 hours at a sustained 100 KiB/s | Droplet local disk, survives container recreates |
| Better Stack | 3 GB/month (free tier) | Better Stack cloud |
| Offsite archive | 30 days | `LOG_DEST` (Hetzner/DO Spaces) |

To adjust offsite retention, set `LOG_RETAIN_DAYS` in `/etc/tinyassets/env`.
