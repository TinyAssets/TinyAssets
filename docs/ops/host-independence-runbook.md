# Host-Independence Runbook

Row N owner surface for bills, emergency DNS, and secrets expiry. This
runbook exists so a repo co-admin can keep the public surface alive without
the host being online or logging into vendor dashboards.

## 1. Billing Autonomy

| Vendor | Current role | Autonomous action | Proof to record |
|---|---|---|---|
| GoDaddy | Registrar for `tinyassets.io` | At next renewal window, pre-pay domain registration through 2031. | Renewal date in succession vault and this table. |
| Cloudflare | DNS, TLS, Worker route, tunnel hostname | Free tier is sufficient near-term. Re-evaluate Worker paid tier only if traffic approaches free quotas. | Billing plan and Worker request trend. |
| Hetzner | Row D self-host target | Pay 12 months in advance immediately after Row D is green. Review annually. | Paid-through date and invoice PDF in vault. |
| Supabase | Managed Postgres/project services | Switch to annual prepay at Row-D + 30 days if traffic pattern is stable. | Annual renewal date and invoice in vault. |
| Backup card | Payment continuity | Add a secondary card controlled by a co-admin with vault access. | Last four digits and vendor coverage in vault, never in repo. |

Rules:

- Do not put card numbers, recovery codes, API tokens, or invoice PDFs in git.
- If a billing action changes production reachability, run the public canary:
  `python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --verbose` (canonical apex — `mcp.tinyassets.io` is an Access-gated internal tunnel origin per host directive 2026-04-20, not a public URL).
- If a vendor dashboard has no prepay option, record the exact limitation and
  the next review date in `STATUS.md` as a concern.

## 2. Emergency DNS

Primary `/mcp` independence is the Row E Cloudflare Worker. This Row N DNS
tool covers residual cases: retiring `mcp.tinyassets.io` DNS records, future subdomains, MX,
TXT/SPF/DKIM, and emergency Worker route removal/restoration when the lead
explicitly asks for it.

Required GitHub secrets and variables:

| Name | Kind | Purpose |
|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Secret | Cloudflare token with `Zone DNS Edit`; add `Workers Routes Edit` only if Worker route actions are needed. |
| `CLOUDFLARE_ZONE_ID` | Secret | Zone ID for `tinyassets.io`; avoids extra zone lookup failure paths. |
| `CLOUDFLARE_ZONE_NAME` | Repository variable | Defaults to `tinyassets.io` if unset. |

Dry-run a CNAME flip from GitHub Actions:

```powershell
gh workflow run emergency-dns.yml `
  -f action=upsert-record `
  -f record_type=CNAME `
  -f name=mcp.tinyassets.io `
  -f content=fallback.example.net `
  -f proxied=true `
  -f apply=false
```

Apply the same change:

```powershell
gh workflow run emergency-dns.yml `
  -f action=upsert-record `
  -f record_type=CNAME `
  -f name=mcp.tinyassets.io `
  -f content=fallback.example.net `
  -f proxied=true `
  -f apply=true `
  -f canary_url=https://tinyassets.io/mcp
```

The workflow runs `scripts/mcp_public_canary.py` after any applied change.
If the canary fails, revert immediately with the previous record target and
leave a `STATUS.md` concern.

Local dry-run, if GitHub Actions is down but a co-admin has the token:

```powershell
$env:CLOUDFLARE_API_TOKEN = "<from vault>"
$env:CLOUDFLARE_ZONE_ID = "<from vault>"
python scripts/emergency_dns_flip.py `
  --action upsert-record `
  --record-type CNAME `
  --name mcp.tinyassets.io `
  --content fallback.example.net `
  --proxied true
```

Local apply:

```powershell
python scripts/emergency_dns_flip.py `
  --action upsert-record `
  --record-type CNAME `
  --name mcp.tinyassets.io `
  --content fallback.example.net `
  --proxied true `
  --apply
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --verbose
```

Supported script actions:

| Action | Use |
|---|---|
| `upsert-record` | Create a missing DNS record or patch a single exact match. |
| `delete-record` | Delete a single exact DNS record match; no-op if absent. |
| `upsert-worker-route` | Create or update an exact Worker route pattern. |
| `delete-worker-route` | Delete an exact Worker route pattern. |

Safety behavior:

- The script is dry-run by default; `--apply` is required for mutation.
- Duplicate exact matches fail closed instead of guessing which record to edit.
- Tokens are read from environment variables and are never printed.
- TTL must be `1` for Cloudflare automatic TTL or a value from 60 to 86400.

## 3. Secrets Expiry

Monthly workflow: `.github/workflows/secrets-expiry-check.yml`.

The workflow opens a GitHub issue titled `SECRET EXPIRY: <name> in <N> days`
when a known secret expires inside the threshold. It uses `issues: write` on
`GITHUB_TOKEN` and dedupes against existing open issues with the same prefix.

Machine-readable metadata is stored as a repository variable named
`SECRETS_EXPIRY_METADATA_JSON`. This contains names and expiry dates only,
never secret values.

Example metadata:

```json
[
  {
    "name": "HETZNER_API_TOKEN",
    "provider": "hetzner",
    "expires_on": "2027-04-20",
    "runbook": "docs/ops/host-independence-runbook.md#hetzner-api-token"
  },
  {
    "name": "SUPABASE_SERVICE_ROLE_KEY",
    "provider": "supabase",
    "expires_on": "2027-04-20",
    "runbook": "docs/ops/host-independence-runbook.md#supabase-service-role-key"
  }
]
```

Manual dry-run with a known-expiring test item:

```powershell
gh workflow run secrets-expiry-check.yml `
  -f threshold_days=30 `
  -f dry_run=true `
  -f metadata_json='[{"name":"TEST_SECRET","provider":"test","expires_on":"2026-04-25","runbook":"docs/ops/host-independence-runbook.md"}]'
```

Manual issue-open test:

```powershell
gh workflow run secrets-expiry-check.yml `
  -f threshold_days=30 `
  -f dry_run=false `
  -f metadata_json='[{"name":"TEST_SECRET","provider":"test","expires_on":"2026-04-25","runbook":"docs/ops/host-independence-runbook.md"}]'
```

Known secret metadata table:

| Name | Provider | Expiry source | Rotation runbook anchor |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare | API verify endpoint when token exposes `expires_on`; otherwise metadata JSON. | [#cloudflare-api-token](#cloudflare-api-token) |
| `CLOUDFLARE_ZONE_ID` | Cloudflare | Non-secret identifier; no expiry. | [#cloudflare-zone-id](#cloudflare-zone-id) |
| `DIGITALOCEAN_TOKEN` | DigitalOcean | Metadata JSON. | [#digitalocean-token](#digitalocean-token) |
| `DO_SSH_KEY` | DigitalOcean | Non-expiring ed25519 keypair. | [#do-ssh-key](#do-ssh-key) |
| `DO_DROPLET_HOST` | DigitalOcean | Non-secret IP identifier. | [#do-droplet-host](#do-droplet-host) |
| `DO_SSH_USER` | DigitalOcean | Non-secret username. | [#do-ssh-user](#do-ssh-user) |
| `PUSHOVER_USER_KEY` | Pushover | Non-expiring account identifier. | [#pushover-user-key](#pushover-user-key) |
| `PUSHOVER_APP_TOKEN` | Pushover | Non-expiring application token. | [#pushover-app-token](#pushover-app-token) |

### Per-secret rotation runbooks

Anchors below match the `runbook:` field in
`.github/workflows/secrets-expiry-check.yml`'s `DEFAULT_METADATA`. Any
rename here must update the metadata too, or expiry issues will link to
a 404. Regression test in
`tests/test_host_independence_runbook.py` enforces this.

#### cloudflare-api-token

**What:** Bearer token used by `deploy/cloudflare-worker/worker.js` to
inject `CF-Access-Client-Id` / `CF-Access-Client-Secret` headers and by
the emergency-DNS workflow to edit zone records.

**Rotate:**
1. Cloudflare → My Profile → API Tokens → "workflow-zone-rw" → Roll.
2. `gh secret set CLOUDFLARE_API_TOKEN --body "<new token>"`.
3. Update local vault (1Password "CLOUDFLARE_API_TOKEN" item) per
   `2026-04-22-secrets-vault-integration.md (archived in git)`.
4. Verify: trigger `.github/workflows/emergency-dns.yml` dry-run OR wait
   for next `deploy-prod.yml` run.
5. Revoke the prior token in the same Cloudflare UI.

#### cloudflare-zone-id

**What:** Non-secret identifier for `tinyassets.io`. Included in the
expiry-check metadata only so the workflow knows about it; tagged
`non_expiring: true`.

**Rotate:** Only if the zone is recreated in Cloudflare. Fetch the new
zone ID from Cloudflare dashboard → Overview → right sidebar. Update
`gh secret set CLOUDFLARE_ZONE_ID --body "<new id>"` and
`scripts/secrets_keys.txt` (if local ops need it).

#### digitalocean-token

**What:** DO API token used by `.github/workflows/dr-drill.yml` to
create + destroy drill droplets, and by `bootstrap_add_second_ssh_key.py`
to register backup SSH keys with the account.

**Rotate:**
1. DO Console → API → Tokens → "workflow-ops" → Regenerate (scope:
   read + write; expires in 1 year).
2. `gh secret set DIGITALOCEAN_TOKEN --body "<new token>"`.
3. Update 1Password ("DIGITALOCEAN_TOKEN").
4. Verify: `.github/workflows/dr-drill.yml` dry-dispatch, or
   `curl -H "Authorization: Bearer $TOKEN" https://api.digitalocean.com/v2/account`.
5. Delete the prior token via DO Console.
6. Update `expires_on` in `SECRETS_EXPIRY_METADATA_JSON` to reflect the
   new 1-year window.

#### do-ssh-key

**What:** Private ed25519 key for droplet SSH access. Primary at
`~/.ssh/workflow_deploy_ed25519`; backup at
`~/.ssh/workflow_deploy_backup_ed25519` (see §5b).

**Rotate primary (normal):**
1. `ssh-keygen -t ed25519 -f ~/.ssh/workflow_deploy_ed25519_new -N ""`.
2. SSH in with old primary + append new pubkey to
   `/root/.ssh/authorized_keys`.
3. Update `gh secret set DO_SSH_KEY --body "$(cat ~/.ssh/workflow_deploy_ed25519_new)"`.
4. Replace local file: `mv ~/.ssh/workflow_deploy_ed25519{_new,}`.
5. Test `.github/workflows/deploy-prod.yml` → dry SSH step.
6. SSH in, remove the old pubkey line from `authorized_keys`.
7. Re-run `bootstrap_add_second_ssh_key.py` to mint a fresh backup.

**Primary lost or compromised (break-glass):**
See §5b "Using the backup key when the primary is gone" — retrieve
backup from 1Password, SSH in, rotate primary as above.

#### do-droplet-host

**What:** Non-secret IPv4 of the production droplet. Stored in GH
Actions so every uptime-canary / deploy-prod / dr-drill reference
stays consistent.

**Rotate:** Only when the droplet is actually recreated (DR recovery,
resize requiring rebuild). After the new droplet has a public IP:
`gh secret set DO_DROPLET_HOST --body "<new IPv4>"`. Update DNS
(`tinyassets-mcp-proxy` Worker's origin, if bypassing the tunnel —
normally unchanged because cloudflared connects outbound).

#### do-ssh-user

**What:** Non-secret username for droplet SSH. Typically `root` for
bootstrap, `deploy` for routine ops.

**Rotate:** Change via `gh secret set DO_SSH_USER --body "<new user>"`.
Ensure the user exists in `/etc/passwd` on the droplet + has the
expected entry in `authorized_keys`.

#### openai-api-key

**Retired 2026-09-24.** The platform has no LLM (AGENTS.md Hard Rule 15): no
model API key, subscription bundle or login belongs on the droplet or in
GitHub Actions. `llm_endpoint_bound=unset` on the production host is the
expected state, not an outage -- a universe runs on the provider its owner
connects. Delete the `OPENAI_API_KEY` repository secret and revoke the key.

#### pushover-user-key

**What:** Pushover account identifier. Non-secret in the strict sense
but required for P0 paging; rotating means all paged incidents after
the rotation go to a different device.

**Rotate:** Only if the host changes phones or revokes the Pushover
account. Get the new user key from pushover.net → Your User Key.
`gh secret set PUSHOVER_USER_KEY --body "<new key>"`.

#### pushover-app-token

**What:** Pushover application token identifying "TinyAssets" as the
source app.

**Rotate:**
1. pushover.net → Your Applications → TinyAssets → Edit → Generate new
   token (or create replacement app if revoking the app entirely).
2. `gh secret set PUSHOVER_APP_TOKEN --body "<new token>"`.
3. Fire `.github/workflows/pushover-test.yml` to confirm delivery with
   the new token before revoking the old.

## 4. Acceptance Checks

Row N is complete when these checks pass:

1. `scripts/emergency_dns_flip.py` unit tests pass without network.
2. `emergency-dns.yml` dry-run completes from GitHub Actions with a test payload.
3. An applied DNS change is followed by a green public MCP canary, then reverted.
4. `secrets-expiry-check.yml` dry-run reports a known-expiring test secret.
5. `secrets-expiry-check.yml` non-dry-run opens one test issue and dedupes on rerun.
6. Billing prepay dates are recorded in the vault and reflected in section 1.

## 5. Watchdog Restart Signals

`scripts/watchdog.py` fires every 30 s via `tinyassets-watchdog.timer`. When 3 consecutive probes fail, it:

1. Issues `sudo systemctl restart tinyassets-daemon.service`.
2. Appends a `WATCHDOG_RESTART` line to `.agents/uptime_alarms.log`.
3. Opens a GitHub issue (label `watchdog`) if `GH_TOKEN` is set.

### Reading watchdog signals

```bash
# On the Droplet — live watchdog logs
journalctl -u tinyassets-watchdog -f

# Alarm log (also readable locally after sync)
tail -f /opt/tinyassets/.agents/uptime_alarms.log | grep WATCHDOG_RESTART

# GitHub issues opened by the watchdog
gh issue list --label watchdog --repo TinyAssets/TinyAssets
```

### Suppressing restarts during maintenance

```bash
# Stop the timer without disabling it permanently
systemctl stop tinyassets-watchdog.timer

# Resume when done
systemctl start tinyassets-watchdog.timer
```

### DRY_RUN mode

```bash
# Probe without restarting or emitting alarms — safe for smoke-testing the watchdog itself
DRY_RUN=1 python /opt/tinyassets/scripts/watchdog.py
# or
python /opt/tinyassets/scripts/watchdog.py --dry-run
```

---

## 5b. Backup SSH key (bus factor = 2)

The Droplet accepts a backup ed25519 key in addition to the primary
`~/.ssh/workflow_deploy_ed25519`. If the primary is lost, corrupted, or
revoked, the backup gets you in without a DO Console rescue.

### Provisioning the backup key (one-time, host action)

`scripts/bootstrap_add_second_ssh_key.py` generates + distributes the key.
Default mode writes to `~/.ssh/workflow_deploy_backup_ed25519`; host then
pipes the private key into 1Password (Task #7 vault-first) and shreds the
local copy.

```bash
# 1. Set DO_TOKEN so the pubkey also registers with the DO account
#    (required to reference the key at droplet-creation time, e.g. DR drill).
export DIGITALOCEAN_TOKEN="$(op read 'op://tinyassets/DIGITALOCEAN_TOKEN/password')"

# 2. Generate + distribute:
python scripts/bootstrap_add_second_ssh_key.py \
    --host "$DO_DROPLET_HOST" \
    --primary-key ~/.ssh/workflow_deploy_ed25519 \
    --do-token "$DIGITALOCEAN_TOKEN"

# 3. Store the private key in 1Password:
op document create ~/.ssh/workflow_deploy_backup_ed25519 \
    --title "DO Droplet backup SSH key (private)" --vault workflow

# 4. Store the pubkey offsite too (so you can re-add it if the droplet
#    is ever rebuilt without vault access — belt + suspenders).
op document create ~/.ssh/workflow_deploy_backup_ed25519.pub \
    --title "DO Droplet backup SSH key (public)" --vault workflow

# 5. Shred the local private copy:
shred -u ~/.ssh/workflow_deploy_backup_ed25519

# 6. Verify both keys work:
ssh -i ~/.ssh/workflow_deploy_ed25519 root@"$DO_DROPLET_HOST" 'echo primary OK'
# Retrieve backup private key from vault, then:
ssh -i /tmp/backup_key root@"$DO_DROPLET_HOST" 'echo backup OK'
rm -f /tmp/backup_key
```

Alternatively, `--print-only` emits the PEM to stdout for direct vault
piping without ever touching disk:

```bash
python scripts/bootstrap_add_second_ssh_key.py \
    --host "$DO_DROPLET_HOST" --primary-key ~/.ssh/workflow_deploy_ed25519 \
    --do-token "$DIGITALOCEAN_TOKEN" --print-only \
    | op document create --title "DO Droplet backup SSH key (private)" \
          --vault workflow -
```

### Using the backup key when the primary is gone

1. Retrieve the private key from 1Password:
   ```bash
   op read "op://tinyassets/DO Droplet backup SSH key (private)/document" \
       > /tmp/backup_key
   chmod 600 /tmp/backup_key
   ```
2. SSH in with it:
   ```bash
   ssh -i /tmp/backup_key root@"$DO_DROPLET_HOST"
   ```
3. Rotate: generate a new primary via `ssh-keygen`, append to
   `/root/.ssh/authorized_keys`, remove the old primary's pubkey, update
   `DO_SSH_KEY` in GH Actions secrets, re-run
   `bootstrap_add_second_ssh_key.py` to mint a fresh backup.
4. Shred the retrieved backup copy:
   ```bash
   shred -u /tmp/backup_key
   ```

The backup key is a break-glass credential. Don't use it for routine
operations — the whole point is that it only sees daylight when the
primary is compromised or lost, so rotations can focus there.

---

## 6. P0 Auto-Triage

When `uptime-canary.yml` opens a `p0-outage` issue, `.github/workflows/p0-outage-triage.yml`
fires automatically:

1. SSHs into the Droplet using `DO_SSH_KEY` / `DO_DROPLET_HOST` / `DO_SSH_USER` secrets.
2. Captures pre-restart diagnostics (`docker ps`, compose status, journalctl tail, `df -h`, `systemctl status`, cloudflared logs).
3. Runs `scripts/triage_classify.py` over the diag to identify the outage class.
4. Fires the matching repair branch (see table below).
5. Runs `docker compose up -d --force-recreate daemon` as belt-and-suspenders (skipped for `watchdog_hotloop` and `tunnel_token` classes — both have their own paths).
6. Waits 30s, re-probes `https://tinyassets.io/mcp`.
7. **Green**: comments auto-recovered + closes the issue.
8. **Still red**: comments diag output + adds `needs-human` label. GHA run marked failed.

Required secrets (same set as `deploy-prod.yml`): `DO_SSH_KEY`, `DO_DROPLET_HOST`, `DO_SSH_USER`.

If secrets are missing, the workflow comments and exits 1 — the `p0-outage` issue stays open
for manual response.

### Auto-triage classes

Each class is detected by a single regex against the diag bundle. First-match-wins in priority order. Full implementation + tests: `scripts/triage_classify.py` + `tests/test_triage_classify.py`.

| Class | Anchor regex match | Repair | Manual-only? |
|---|---|---|---|
| `env_unreadable` | `ENV-UNREADABLE` token emitted from systemd ExecStartPre / entrypoint / sed sites | `chown root:workflow && chmod 640 /etc/tinyassets/env` | No |
| `tunnel_token` | `UnauthorizedError` / `authentication failed` / `Invalid tunnel secret` in cloudflared logs | **None** — detection opens `tunnel-token-rotation` issue, pages priority=2 | **Yes** |
| `oom` | Kernel `Out of memory:` / `oom-killer` / container `OOMKilled` | `docker compose restart daemon` (memory cap **not** auto-bumped) | No |
| `disk_full` | `df -h` shows ≥90% on `/`, `/data`, or `/var/lib/docker` | `docker system prune -af` + `journalctl --vacuum-time=3d` | No |
| `image_pull_failure` | `manifest not found` / `pull access denied` / `ImagePullBackOff` | Fall back to `TINYASSETS_IMAGE=ghcr.io/tinyassets/tinyassets-daemon:latest` (loses SHA pin until next deploy) | No |
| `watchdog_hotloop` | systemd `start-limit-hit` / `Start request repeated too quickly` | `systemctl stop → sleep 60 → reset-failed → start` | No |
| `unknown` | Fall-through when no class matches | Generic compose restart (legacy behavior) | No |

**Memory-cap ratchet for repeated OOMs (host action, not auto):**
If `oom` class recurs 2+ times in a week, bump the memory cap once in `deploy/compose.yml` under the `daemon` service:

```yaml
services:
  daemon:
    # ...
    deploy:
      resources:
        limits:
          memory: 2G  # up from default 1G; bump again only after another recurrence
```

Don't ratchet faster — each OOM is signal about a real memory leak worth preserving in GH issue history before papering over.

**Image-pull fallback recovery:**
When auto-triage falls the daemon off the SHA pin to `:latest`, the next `deploy-prod.yml` run re-pins to a new short-SHA. If you want to pin manually in the interim:

```bash
ssh ... "sudo sed -i 's|^TINYASSETS_IMAGE=.*|TINYASSETS_IMAGE=ghcr.io/tinyassets/tinyassets-daemon:<sha>|' /etc/tinyassets/env && \
         sudo chown root:workflow /etc/tinyassets/env && sudo chmod 640 /etc/tinyassets/env && \
         sudo systemctl restart tinyassets-daemon"
```

**Tunnel-token rotation (manual):**
See `#cloudflare-api-token` / `#pushover-app-token` for the general rotation pattern. Tunnel-token specifics:
1. Cloudflare Zero Trust → Networks → Tunnels → `tinyassets-daemon-prod` → rotate token → copy new value.
2. `gh secret set CLOUDFLARE_TUNNEL_TOKEN --body "<new token>"`.
3. Trigger `.github/workflows/deploy-prod.yml` or edit `/etc/tinyassets/env` directly on the droplet (maintains ENV-UNREADABLE invariant — chown + chmod after sed).
4. Close the auto-opened `tunnel-token-rotation` issue with a resolution comment.

---

## 7. LLM Binding Canary (retired)

Retired 2026-09-24 with the platform's own model logins (Hard Rule 15). It
asserted that the production daemon reported a bound model, which is now the
opposite of the invariant. `scripts/verify_llm_binding.py` remains only as a
self-host check (`scripts/selfhost_smoke.py`).

---

## 8. References

- Cloudflare DNS Records API: <https://developers.cloudflare.com/api/resources/dns/>
  lists create, patch, and delete endpoints under `/zones/{zone_id}/dns_records`.
- Cloudflare Workers Routes API:
  <https://developers.cloudflare.com/api/resources/workers/subresources/routes/methods/list/>
  documents route list/create/update/delete under `/zones/{zone_id}/workers/routes`.
- Cloudflare API token verify endpoint:
  <https://developers.cloudflare.com/fundamentals/api/get-started/create-token/>
  documents `/user/tokens/verify`.
- GitHub Actions `GITHUB_TOKEN` docs:
  <https://docs.github.com/en/actions/tutorials/authenticate-with-github_token>
  show `issues: write` for automated issue creation.
