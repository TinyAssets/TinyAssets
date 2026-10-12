# Cloudflare tunnel — provider-agnostic runbook

Self-host migration Row C per
`openspec/specs/uptime-and-alarms/spec.md`.

The TinyAssets MCP daemon binds `0.0.0.0:8001` locally. To expose it
through the canonical public endpoint (`https://tinyassets.io/mcp`)
without opening a port through the host's NAT, run a Cloudflare tunnel
that connects outbound from the container to Cloudflare's edge and
proxies Worker subrequests back to the daemon.

This directory holds the provider-agnostic tunnel artifacts:

- `cloudflared.yml` — config-file template (shape b below).
- `../scripts/run-tunnel.sh` — entrypoint script that dispatches between
  token-based (shape a) and config-based (shape b) tunnel auth.

Row D (deploy) layers provider-specific wiring (Docker Compose,
systemd unit, fly.toml) on top of these primitives. Row C stays
provider-agnostic.

---

## Two auth shapes

Cloudflare tunnels support two authentication patterns. Pick one per
tunnel — mixing them conflicts on ingress ownership.

### Shape A — Token-based (dashboard-managed ingress)

**Use when:** you want to manage ingress rules via the Cloudflare Zero
Trust dashboard UI. The 2026-04-19 live tunnel used shape A: a
dashboard-managed Public Hostname rule routed the tunnel origin
`mcp.tinyassets.io/*` → `http://localhost:8001`. After the single-entry
cutover, that origin is Access-gated and is reached publicly only
through the Worker at `https://tinyassets.io/mcp`.

**Setup:**

1. Dashboard → Networks → Tunnels → Create tunnel.
2. Pick a name (e.g. `tinyassets-daemon-prod`). Dashboard issues a token;
   copy it. Never commit the token to the repo.
3. Under the new tunnel's Public Hostnames tab, add:
   - Hostname: your tunnel-origin hostname (for example, `mcp.tinyassets.io`;
     not the user-facing connector URL)
   - Path: `*`  (catch-all under the hostname; MCP lives at `/mcp`)
   - Service: `http://localhost:8001`
4. Verify DNS: `dig +short <hostname>` — Cloudflare auto-adds the CNAME.

**Run:**

```bash
export TUNNEL_TOKEN="..."  # dashboard-issued token
./scripts/run-tunnel.sh
```

Origin port defaults to 8001; override with `ORIGIN_PORT=9000` if the
daemon binds elsewhere. `HOSTNAME` and ingress routing come from the
dashboard, not the env.

**Known gotcha** (from the `godaddy-ops` skill, hard-won 2026-04-19):

> Moving a Tunnel public-hostname from apex → subdomain deletes the
> apex CNAME. Cloudflare auto-managed the record; when the Tunnel's
> "Published application route" changes hostname, the DNS is updated
> atomically. Plan the replacement apex record BEFORE making the
> tunnel change or DNS goes dark.

This is exactly the 2026-04-19 P0's first failure mode. When migrating
a tunnel between hostnames, stage the DNS cutover carefully.

### Shape B — Config-file-based (ingress rules in `cloudflared.yml`)

**Use when:** you want ingress rules in version control for
reproducible setup on fresh hosts, or when ops wants the routing rules
reviewable at commit time.

**Setup:**

1. Run `cloudflared tunnel login` once per machine; saves
   `~/.cloudflared/cert.pem`.
2. `cloudflared tunnel create tinyassets-daemon-prod` → emits a tunnel
   UUID + a credentials JSON at `~/.cloudflared/<UUID>.json`.
3. Route DNS:
   ```bash
   cloudflared tunnel route dns <UUID> mcp.tinyassets.io
   ```

**Run:**

```bash
export TUNNEL_ID="<UUID-from-step-2>"
export TUNNEL_CREDENTIALS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"
export TINYASSETS_PUBLIC_HOSTNAME="mcp.tinyassets.io"    # tunnel origin, not public connector URL
export ORIGIN_PORT=8001                                # optional; default 8001

./scripts/run-tunnel.sh
```

**Why `TINYASSETS_PUBLIC_HOSTNAME` instead of plain `HOSTNAME`?** Bash
defines `HOSTNAME` implicitly as the machine's hostname (e.g.
`DESKTOP-KCPMGP3`). If you export nothing, the script would silently
try to route traffic to a tunnel for your laptop's local name — which
would either fail (no such Cloudflare DNS record) or worse, match some
other zone by accident. `TINYASSETS_PUBLIC_HOSTNAME` is explicit; the
script falls back to `HOSTNAME` only if you clearly overrode bash's
default.

`run-tunnel.sh` renders `deploy/cloudflared.yml` with envsubst and
invokes cloudflared with `--config <rendered.yml> run <TUNNEL_ID>`.

---

## Requirements

- **cloudflared binary** on PATH (or `CLOUDFLARED_BIN=/path/to/cloudflared`).
  Install: https://github.com/cloudflare/cloudflared/releases or
  `brew install cloudflared` / distro package manager.
- **envsubst** (shape B only). `gettext-base` on Debian/Ubuntu;
  `gettext` on macOS (`brew install gettext`).
- **A Cloudflare account** + the target domain's DNS managed by Cloudflare.
- **Outbound internet access** on the host running the tunnel (cloudflared
  connects to Cloudflare edge via port 7844).

---

## Script contract

`scripts/run-tunnel.sh`:

- **Foreground process.** Suitable for systemd `ExecStart=`, Docker
  `ENTRYPOINT`, or interactive ops. Does NOT daemonize; supervisors
  want the PID.
- **Secrets never logged.** Token is passed via `--token` on the CLI;
  credentials JSON path is logged but not contents.
- **Auth selection logic:** if `TUNNEL_TOKEN` is set, shape A wins
  (matches production); else if shape-B env is complete, shape B;
  else exits 1 with which env var is missing.
- **Exit codes:** 0 clean shutdown on SIGTERM, 1 config validation,
  2 binary not found.

---

## Testing locally

Shape A — hand the script a known-good token in a throwaway shell:

```bash
TUNNEL_TOKEN="eyJhIjoi..." scripts/run-tunnel.sh
```

Expect log output `Registered connIndex=0 connection=…` within 2-5s,
then the configured tunnel origin can reach the local daemon.

Shape B — after `cloudflared tunnel create` completes:

```bash
TUNNEL_ID=b59f3cd9-… \
TUNNEL_CREDENTIALS_FILE=~/.cloudflared/b59f3cd9-….json \
HOSTNAME=test.example.com \
  scripts/run-tunnel.sh
```

For production, verify the canonical public endpoint from another host:

```bash
python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp --verbose
```

For a throwaway non-Access-gated hostname, you can probe that hostname
directly:

```bash
python scripts/mcp_public_canary.py --url https://<hostname>/mcp --verbose
```

Exit 0 = the route under test is serving the daemon correctly.

---

## Tearing down

**Shape A:** Cloudflare dashboard → Networks → Tunnels → (tunnel) →
Delete. DNS is cleaned up automatically. Revoke the token after
deletion.

**Shape B:**

```bash
cloudflared tunnel delete <UUID>       # removes dashboard entry
cloudflared tunnel route dns <UUID> <hostname> --overwrite-dns   # or via dashboard
```

---

## What this doesn't cover (Row D scope)

- **Provider-specific wiring** — Docker Compose / systemd unit /
  fly.toml. Row D adds those on top of this script.
- **Multi-region or failover** — single-tunnel shape here. Multi-host
  redundancy (Hetzner primary + Fly secondary) is a separate design
  question that §2 of the exec plan defers to post-near-term.
- **Secrets management** — where `TUNNEL_TOKEN` lives at rest. Row D
  picks (systemd `EnvironmentFile` with `chmod 600`; Fly secrets via
  `fly secrets set`; etc.).
- **Health-check + alert integration** — the uptime canary already
  handles external probing (`scripts/mcp_public_canary.py` + tray
  scheduler). A tunnel-specific health check could be added but isn't
  Row C scope.
