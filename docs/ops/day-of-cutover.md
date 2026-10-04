# Day-of-Cutover Runbook

**Your step list (host) is short.** Lead's step list is long, but lead runs it via shell automation on your laptop — you paste tokens back, lead does the rest.

Total host time: ~25-35 min, mostly waiting for Hetzner to provision + watching a browser.

---

## Section 1 — YOUR steps (host)

Six MUST items, two OPTIONAL. Do MUST in order; OPTIONAL any time.

### 1. DigitalOcean account + Droplet — MUST (~5-7 min)

**Pivot note (2026-04-20):** this step used to say Hetzner. Host hit a broken Hetzner US individual-signup form mid-cutover; switched to DigitalOcean — GitHub-OAuth signup is 1-click for the typical host vs. captcha + email verification + card form on Hetzner. Same Droplet-as-Debian-12-VM target, same `hetzner-bootstrap.sh` runs on the box unchanged, same everything downstream.

- Go to `https://cloud.digitalocean.com/registrations/new`. Sign up with **"Continue with GitHub"** (1-click if you're already logged into GitHub). Skip the email-signup fork entirely.
- Add payment method when prompted (required before Droplet creation).
- **Settings → Security → SSH Keys → Add SSH Key.** Paste the key from §4 below.
- **Droplets → Create Droplet:**
  - Region: closest to your users (NYC / SFO / AMS / FRA — all fine).
  - Image: Distributions → **Debian 12**.
  - Size: Basic → Regular SSD → **$6/mo** (1 vCPU, 1 GB RAM). Bump to $12/mo if you want headroom for paid-market day-one.
  - Authentication: SSH Key → select the one you just added. Do NOT enable password auth.
  - Hostname: `tinyassets-daemon-prod-01`.
  - Firewall: attach or create — Inbound SSH (22) from your admin IP only, ICMP, everything else closed. Outbound: all.
  - **Create Droplet.**
- Wait for status → green (~60 s). Copy the public IPv4 from the Droplet detail page.

**Paste back to me:**
```
DROPLET_IP=203.0.113.42
```

(Named `DROPLET_IP` not `HETZNER_BOX_IP` so the variable tracks reality. Lead uses it the same way downstream — it's just the remote box's public IP.)

### 2. Supabase project — MUST (~5 min)

- `https://supabase.com/dashboard/sign-up`. GitHub login.
- **New project:** name `workflow-prod`, region closest to your users, Postgres latest, generate + save the DB password.
- Wait for project provision (~2 min).
- **Project Settings → Database → Connection string → Connection pooling** (port 6543) → copy URI.
- **Project Settings → API → service_role key** → reveal + copy.

**Paste back to me:**
```
SUPABASE_DB_URL=postgres://postgres.xxxxxxxxxxxxx:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOi...
```

### 3. GitHub OAuth App — MUST (~3 min)

- `https://github.com/settings/applications/new`.
- Application name: `TinyAssets`.
- Homepage URL: `https://tinyassets.io`.
- Authorization callback URL: `https://tinyassets.io/authorize/github/callback`.
- **Register application.** Copy the Client ID.
- Click **Generate a new client secret.** Copy it **now** — you can't retrieve it later.

**Paste back to me:**
```
```

### 4. Cloudflare API token + tunnel token — MUST (~5 min)

**API token first:**

- `https://dash.cloudflare.com/profile/api-tokens` → **Create Token** → **Custom token.**
- **Token name:** `workflow-automation`.
- **Permissions:**
  - `Zone` → `DNS` → `Edit` → on zone `tinyassets.io`.
  - `Account` → `Workers Routes` → `Edit`.
- **Token TTL:** 1 year.
- **Continue → Create Token.** Copy once.

**Then the tunnel token:**

- `https://one.dash.cloudflare.com` → your account → **Networks → Tunnels.**
- If `tinyassets-daemon-prod` tunnel does not exist: **Create a tunnel → Cloudflared → name `tinyassets-daemon-prod` → Save.** Click the tunnel → **Connectors → Install connector** → copy the **Token** field (long JWT-looking string). Skip the "install on this machine" page; just copy.
- **Public Hostname tab → Add a public hostname:** hostname = `mcp.tinyassets.io` (the Access-gated tunnel origin, not the user-facing connector URL), service = `http://localhost:8001`, save.

**Paste back to me:**
```
CLOUDFLARE_API_TOKEN=<long token>
CLOUDFLARE_ZONE_ID=<from dash.cloudflare.com → tinyassets.io → Overview → right sidebar>
CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...very-long...
```

### 5. Hetzner Storage Box — MUST (~3 min)

- `https://www.hetzner.com/storage/storage-box` → order BX11 (~€4/mo, 100 GB). Use same Hetzner account.
- After provision (~1 min): Storage Box dashboard → your box → **Samba/SMB & SSH** tab.
- Note the hostname, username, and generate a password (Hetzner uses a dedicated password separate from your account password).

**Paste back to me:**
```
STORAGE_BOX_HOST=u123456.your-storagebox.de
STORAGE_BOX_USER=u123456
STORAGE_BOX_PASSWORD=<the generated password>
```

### 6. Better Stack source token — OPTIONAL but recommended (~2 min)

- `https://betterstack.com/users/sign-up`. Free tier.
- **Sources → Connect source → Vector.** Platform: Linux. Name: `tinyassets-daemon`.
- Copy the source token.

**Paste back to me:**
```
BETTERSTACK_SOURCE_TOKEN=<token>
```

**Skip consequence:** logs stay on the Hetzner box (via `journalctl`) and don't ship anywhere. If the box dies we lose the log history. You can add this later without re-doing Section 2.

### 7. (Optional) Hetzner `hcloud` API token — OPTIONAL (~2 min)

Only needed if you want me to create future Hetzner resources (additional boxes, volumes, snapshots) without your involvement.

- Hetzner Cloud Console → your project → **Security → API Tokens → Generate API Token.**
- Permissions: **Read & Write.** Copy once.

**Paste back to me:**
```
HETZNER_API_TOKEN=<token>
```

**Skip consequence:** scaling or replacing the box requires you to do the Hetzner console steps again. Current plan doesn't need this.

---

## Section 2 — Lead automation (what I do)

Each block names the command I run + expected output + next trigger. I run these via the Bash tool on your laptop; outbound SSH uses the key from §4.

### 2.1 On first DROPLET_IP arrival (from §1.1)

```bash
ssh -i ~/.ssh/workflow_deploy -o StrictHostKeyChecking=accept-new \
    root@<DROPLET_IP> 'echo ready'
# Expected: "ready"
```

If it errors: retry every 15 s for up to 2 min. If still failing, you (host) check Hetzner Console → server status.

Then bootstrap:

```bash
ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> \
    'curl -fsSL https://raw.githubusercontent.com/TinyAssets/TinyAssets/main/deploy/hetzner-bootstrap.sh | sudo bash'
# Expected trailing line: "[bootstrap] bootstrap complete."
```

Next trigger: your §1.2-§1.5 pastes.

### 2.2 On each credential paste — compose and push env file

Once all MUST items arrive I compose `/etc/tinyassets/env`:

```bash
ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> \
    'sudo tee /etc/tinyassets/env > /dev/null' <<'EOF'
TINYASSETS_IMAGE=ghcr.io/tinyassets/tinyassets-daemon:latest
CLOUDFLARE_TUNNEL_TOKEN=<your paste>
TINYASSETS_MCP_CANARY_URL=https://tinyassets.io/mcp
SUPABASE_DB_URL=<your paste>
SUPABASE_SERVICE_ROLE_KEY=<your paste>
BETTERSTACK_SOURCE_TOKEN=<your paste or empty>
BACKUP_DEST=storagebox:tinyassets-backups
EOF

ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> \
    'sudo chown root:tinyassets /etc/tinyassets/env && sudo chmod 640 /etc/tinyassets/env'
# Expected: no output.
```

### 2.3 Storage Box rclone config

After §1.5 paste:

```bash
ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> bash -s <<'SSHEOF'
sudo install -d -m 0700 -o root -g root /root/.config/rclone
OBSCURED=$(echo -n '<STORAGE_BOX_PASSWORD>' | rclone obscure -)
sudo tee /root/.config/rclone/rclone.conf > /dev/null <<EOF
[storagebox]
type = sftp
host = <STORAGE_BOX_HOST>
user = <STORAGE_BOX_USER>
pass = $OBSCURED
port = 23
EOF
sudo chown root:root /root/.config/rclone/rclone.conf
sudo chmod 600 /root/.config/rclone/rclone.conf
SSHEOF
# Expected: no output.
```

### 2.4 GitHub repo secrets + variables

```bash
gh secret set CLOUDFLARE_API_TOKEN --body "<your paste>"
gh secret set CLOUDFLARE_ZONE_ID --body "<your paste>"
gh secret set SUPABASE_SERVICE_ROLE_KEY --body "<your paste>"
gh secret set DEPLOY_SSH_HOST --body "<DROPLET_IP>"
gh secret set DEPLOY_SSH_USER --body "root"
gh secret set DEPLOY_SSH_PRIVATE_KEY < ~/.ssh/workflow_deploy
# If §1.7 pasted:
gh secret set HETZNER_API_TOKEN --body "<your paste>"

gh variable set CLOUDFLARE_ZONE_NAME --body "tinyassets.io"
gh variable set SECRETS_EXPIRY_METADATA_JSON \
    --body "$(cat deploy/secrets-expiry-metadata.seed.json)"
# Expected: "✓ Set secret/variable ..." per command.
```

### 2.5 Start the daemon + enable timers

```bash
ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> bash -s <<'SSHEOF'
sudo systemctl start tinyassets-daemon
sudo systemctl status tinyassets-daemon --no-pager | head -20
sudo systemctl enable --now tinyassets-watchdog.timer
sudo systemctl enable --now tinyassets-backup.timer
SSHEOF
# Expected: "active (running)" + timer lists with next fire time.
```

First backup (sanity):

```bash
ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> \
    'sudo systemctl start tinyassets-backup.service && \
     sudo journalctl -u tinyassets-backup.service --since "2 minutes ago" --no-pager | tail -20'
# Expected: rsync completed, snapshot path logged.
```

### 2.6 Verify canary green

```bash
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --verbose
# Expected: prints "[canary] OK" + exit 0.
```

If red, I diagnose per `deploy/HETZNER-DEPLOY.md` and report back before proceeding.

### 2.7 Verify GHA cloud canary running

```bash
gh workflow view uptime-canary.yml
gh run list --workflow=uptime-canary.yml --limit=3
# Expected: most recent run green, < 10 min ago.

gh issue list --label=p0-outage --state=open
# Expected: empty.
```

### 2.8 Stop home cloudflared + daemon — one host action

I ask you once:

> "All canaries green. Stop the home tray now: right-click TinyAssets tray icon → Exit. Confirm when done."

### 2.9 Post-cutover 10-min watch

```bash
for i in {1..5}; do
    date
    python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
    sleep 120
done
# Expected: 5 consecutive [canary] OK results.
```

If all green: I report cutover succeeded. You're free to hibernate the home machine for the 48h trial at your discretion.

---

## Section 3 — Paste format cheat sheet

Paste these blocks as-is into chat when the corresponding §1 step completes. Lead parses them verbatim. Empty values are fine for OPTIONAL items you skip.

```
DROPLET_IP=
```

```
SUPABASE_DB_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

```
```

```
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ZONE_ID=
CLOUDFLARE_TUNNEL_TOKEN=
```

```
STORAGE_BOX_HOST=
STORAGE_BOX_USER=
STORAGE_BOX_PASSWORD=
```

```
BETTERSTACK_SOURCE_TOKEN=
```

```
HETZNER_API_TOKEN=
```

Rules of the road:
- One block per paste. Safer than one mega-paste; if a value is wrong I can re-request just that one.
- Don't paste secrets into a PR or commit — only into chat.
- If you accidentally regenerate a secret on the dashboard, re-paste the new one; I'll rewrite the env file + GitHub secret.

---

## Section 4 — SSH handshake mechanics

**The question:** how does lead get shell access to the Hetzner box without you hand-running every command?

**Flow:**

1. **Lead generates an SSH key on your laptop** (one-time, via Bash tool):

   ```bash
   ssh-keygen -t ed25519 -C "workflow-deploy" -f ~/.ssh/workflow_deploy -N ""
   cat ~/.ssh/workflow_deploy.pub
   ```
   Expected: prints the public key (starts with `ssh-ed25519 AAAA...`).

2. **You paste that public key** into Hetzner during §1.1 server-creation flow (**Security → SSH Keys → Add SSH Key** → paste).

3. **Lead SSHs to the Hetzner box** via the Bash tool on your laptop. The private key at `~/.ssh/workflow_deploy` lives on your disk — every SSH command lead runs originates from your laptop, using your key:
   ```bash
   ssh -i ~/.ssh/workflow_deploy root@<DROPLET_IP> <command>
   ```

4. **No human is in the middle after §1.1.** Lead runs commands via Bash; output returns in chat; next command auto-fires. Your laptop is the jump host but you don't see the shell — lead does.

**Why this is safe:**
- Private key stays on your disk. Never pasted, never sent to any service.
- Every command lead runs is visible to you in chat (tool-call records in Claude Code UI). You can deny any specific command via the permission prompt.
- If you want zero lead-shell: skip §2 and run the commands yourself from `deploy/HETZNER-DEPLOY.md`. The day-of-cutover runbook is additive on top of that base.

**Caveat worth saying out loud:** lead doesn't have its own credentials. "Lead automation" means "lead authors commands + shell on your machine runs them." You can abort anytime. This is the intended safety property, not a workaround.

---

## Section 5 — Rollback

If anything goes red between §2.5 and §2.8 (home machine still up), rollback is additive-clean:

- Lead runs: `ssh ... 'sudo systemctl stop tinyassets-daemon'` on Hetzner.
- Home cloudflared is still running. The Worker keeps serving canonical `https://tinyassets.io/mcp` while `mcp.tinyassets.io` resolves back to the home tunnel origin.
- Lead re-verifies canary on home tunnel + opens a GitHub issue titled `Cutover rollback: <date>` with the red output. Troubleshoot + retry later.

If red occurs *after* §2.8 (home already stopped): lead asks you to restart the home cloudflared via tray. You need to be at your machine 10 min post-§2.8 in case this happens. Pick a cutover window where you're available.

---

## Section 6 — What runs automatically after cutover

Nothing host-facing. All of these run without your involvement going forward:

- **Uptime canary** (GHA every 5 min) — opens `p0-outage` GitHub issue on 2 consecutive reds; auto-closes on 3 greens.
- **Image builds** (GHA on every main push) — publishes to GHCR.
- **Deploy pipeline** (GHA post-build) — SSHs to Hetzner, `docker compose pull && up -d`, post-deploy canary, auto-rollback on red.
- **Secrets expiry** (GHA monthly) — opens issue 30 days pre-expiry.
- **Nightly backup** (systemd timer) — 03:00 UTC to Storage Box with retention rotation.
- **Watchdog** (systemd timer) — 30 s probes; restart on hang.
- **Logs** (Vector sidecar) — stream to Better Stack.

Host-required actions going forward:
- **Annual:** rotate secrets when `SECRET EXPIRY` issues fire (lead runs `gh secret set` once you paste new values).
- **Annual:** pre-pay Hetzner 12 months at next renewal window.
- **At Row-D + 30 days:** switch Supabase to annual prepay.
- **As needed:** approve + merge PRs. Deploy pipeline takes it from there.

---

## References

- `deploy/HETZNER-DEPLOY.md` — detailed Hetzner deploy (if you want to do it yourself instead).
- `docs/ops/host-independence-runbook.md` — ongoing maintenance (bills, emergency DNS, secrets rotation).
- `docs/exec-plans/active/2026-04-20-selfhost-uptime-migration.md` — full 14-row exec plan.
