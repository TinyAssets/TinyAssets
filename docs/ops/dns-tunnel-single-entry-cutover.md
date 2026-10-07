---
title: DNS + tunnel single-entry cutover runbook
date: 2026-04-20
updated: 2026-04-21
author: navigator
directive: host-directive-2026-04-20 — retire mcp.tinyassets.io/mcp; canonical = tinyassets.io/mcp only
status: ACTIVE — Option 1 (CF Access service token) ratified 2026-04-20
---

# DNS + Tunnel Single-Entry Cutover Runbook

Retire `mcp.tinyassets.io` as a publicly *reachable* surface while preserving it as
internal tunnel-routing plumbing. After this runbook completes, `tinyassets.io/mcp`
is the sole public entry point; direct connections to `mcp.tinyassets.io` are blocked
by Cloudflare Access (HTTP 403).

**Why.** Extra public URL = extra attack surface. The 2026-04-19 P0 (`api.tinyassets.io`
appearing then disappearing during a tunnel reshuffle) demonstrated that secondary DNS
records create ambiguity and silent-fail risks.
Design decision: one canonical public entry point (`AGENTS.md` fact 11).

**Architecture constraint.** `deploy/cloudflare-worker/worker.js:31` uses
`mcp.tinyassets.io` as the tunnel origin for Worker subrequests — deleting the CNAME
would break the Worker. The CNAME stays; public access is blocked via Cloudflare Access.
Options analysis: `docs/design-notes/2026-04-20-single-entry-execution-options.md` (deleted 2026-10-06; in git history).

---

## Pre-cutover: establish baseline

Run this first. If it fails, do NOT proceed — fix the canonical URL first.

```bash
python scripts/mcp_public_canary.py
```

Expected: exit 0, green output with `serverInfo` in body. Any failure = canonical URL
broken; stop and diagnose before proceeding.

Save the output — this is your green baseline before the cutover.

---

## CRITICAL: Deploy the Worker before executing the cutover

The cutover enables Cloudflare Access on `mcp.tinyassets.io`, meaning the Worker must
present CF Access service-token headers on every subrequest. If the **deployed** Worker
does not contain the Access header injection, it will 403 immediately when the gate
goes live — silently breaking production.

**Verify the deployed Worker is current before proceeding:**

```bash
# Option A: CI automation (preferred)
# Any push to main touching deploy/cloudflare-worker/** triggers
# .github/workflows/deploy-worker.yml automatically.
# Check the latest run is green before running this runbook.

# Option B: Manual deploy
cd deploy/cloudflare-worker
wrangler deploy
```

The deployed Worker must contain both `env.CF_ACCESS_CLIENT_ID` and
`env.CF_ACCESS_CLIENT_SECRET` reads in `proxyToTunnel()`. The pre-commit invariant
in `scripts/pre_commit_invariant_worker.py` guards against these being removed in
future commits; the CI workflow (`deploy-worker.yml`) ensures a push to main always deploys
before the gate can be activated.

---

## Step 1: Establish CF Access gate (API path — primary)

Two auth options — use whichever token you have available:

**Option A — Scoped API token (recommended):**
```bash
export CLOUDFLARE_API_TOKEN=<token>    # Account:Access:Service Tokens:Edit
                                       # Account:Access:Apps and Policies:Edit
                                       # Account:Workers Scripts:Edit
                                       # Zone:Zone:Read
export CLOUDFLARE_ZONE_ID=<zone-id>    # tinyassets.io zone ID
```

**Option B — Global API key:**
```bash
export CLOUDFLARE_EMAIL=<your-cf-email>
export CLOUDFLARE_GLOBAL_KEY=<global-api-key>   # from dash.cloudflare.com → profile → API Keys
export CLOUDFLARE_ZONE_ID=<zone-id>
```

The existing `workflow` token in `.env` (cfut_arv...) has exactly the Option A
scopes — use that value as `CLOUDFLARE_API_TOKEN` if it's available.

```bash
# Dry-run first — confirm what will be created:
python scripts/cf_access_cutover.py --worker tinyassets-mcp-proxy

# Apply (creates service token + Access app + policy + Worker secrets):
python scripts/cf_access_cutover.py --worker tinyassets-mcp-proxy --apply
```

The script is idempotent: re-running with `--apply` on an already-configured
environment is safe — it finds existing resources by name/domain and reuses them
rather than creating duplicates.

**What the script does:**
1. Creates (or reuses) service token `tinyassets-mcp-worker`
2. Creates (or reuses) Access application for `mcp.tinyassets.io`
3. Creates (or reuses) policy `worker-only` (Service Auth = service token)
4. Sets `CF_ACCESS_CLIENT_ID` + `CF_ACCESS_CLIENT_SECRET` as Worker secrets on
   `tinyassets-mcp-proxy` (skipped if reusing an existing token whose secret is
   not recoverable — see script output)
5. Runs the built-in three-check verification

After the script completes with `--apply`, skip to **Post-cutover verification**.

---

## Step 1 (fallback): Dashboard path

Use this if the API token is unavailable or if the script errors.

**1a. Create a Service Token**

Cloudflare dashboard → Zero Trust → Access → Service Auth → **Service Tokens** → **Create Service Token**.

- Name: `tinyassets-mcp-worker`
- Token duration: Non-expiring (or set a long rotation schedule)
- Click **Generate Token**

Copy both values immediately — the `Client Secret` is shown only once:
- `CF-Access-Client-Id`: looks like `<uuid>.access`
- `CF-Access-Client-Secret`: long hex string

**1b. Create an Access Application**

Zero Trust → Access → Applications → **Add an Application** → **Self-hosted**.

- **Application name:** `tinyassets-mcp-worker-gate`
- **Application domain:**
  - Subdomain: `mcp`
  - Domain: `tinyassets.io`
  - Path: (leave blank — protects the whole subdomain)
- **Session Duration:** 24 hours
- Click **Next**

On the policy screen:
- **Policy name:** `worker-only`
- **Action:** Service Auth
- **Include rule:** Service Token = `tinyassets-mcp-worker` (select from dropdown)
- Click **Next**, then **Save**

After saving, Cloudflare Access is now active on `mcp.tinyassets.io`. Direct browser/curl
requests without the service token headers get a Cloudflare Access login redirect or 403.

**1c. Add the service token as Worker secrets**

The Worker must present these headers to every subrequest it makes to `mcp.tinyassets.io`.

Via Wrangler CLI (recommended):

```bash
cd deploy/cloudflare-worker
wrangler secret put CF_ACCESS_CLIENT_ID
# paste the Client Id value when prompted

wrangler secret put CF_ACCESS_CLIENT_SECRET
# paste the Client Secret value when prompted
```

Via Cloudflare dashboard:
Workers & Pages → `tinyassets-mcp-proxy` → Settings → Variables and Secrets →
**Add variable** → Type: **Secret** → Name: `CF_ACCESS_CLIENT_ID` → value → Save.
Repeat for `CF_ACCESS_CLIENT_SECRET`.

---

## Step 2: (No cloudflared config change required)

The `mcp.tinyassets.io` ingress entry in cloudflared config is **kept**. The tunnel
still routes that hostname — that's correct. Access gates who can reach it via DNS,
not whether the tunnel serves it. No cloudflared restart needed for this cutover.

---

## Step 3: (No DNS change required)

The `mcp.tinyassets.io` CNAME is **kept**. It resolves publicly (expected), but no
unauthorized client can get a valid response — Cloudflare Access intercepts before
the request reaches the tunnel. No DNS edits needed.

---

## Post-cutover verification

The `cf_access_cutover.py --apply` script runs a built-in three-check automatically.
For manual verification or re-checking after the fact:

### Check 1 — canonical URL still green

```bash
python scripts/mcp_public_canary.py
```

Expected: same green output as pre-cutover baseline. Any failure = stop; investigate
before marking cutover complete.

### Check 2 — `mcp.tinyassets.io` direct access is blocked

```bash
curl -si https://mcp.tinyassets.io/mcp -X POST \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"access-probe","version":"1.0"}}}' \
  | head -5
```

Expected: HTTP **401** or **403** from Cloudflare Access.
NOT acceptable: HTTP 200 with a valid MCP `result.serverInfo` response — means the
Access application is not protecting the subdomain.

### Check 3 — Worker subrequest still reaches tunnel (end-to-end)

Check 1 covers this indirectly. For explicit confirmation, tail Worker logs while
running the canary:

```bash
# In one terminal:
cd deploy/cloudflare-worker && wrangler tail

# In another:
python scripts/mcp_public_canary.py
```

Expected: a request log entry for `POST /mcp` with status 200 and no `CF-Access`
authentication error. Any `401` in the Worker tail means the Worker's secrets are
not set — re-run Step 1 and redeploy.

---

**All three green = cutover complete.**

---

## Observability after cutover

**Cloudflare Worker logs:**
Dashboard → `tinyassets.io` zone → Workers Routes → click the Worker → Logs or Analytics.

**Cloudflared tunnel logs:**
`journalctl -u cloudflared -f` (Linux systemd) or Windows Event Viewer → Application → cloudflared.

**Layer-1 canary (`scripts/uptime_canary.py`):**
Probes `tinyassets.io/mcp` every 5 minutes via GHA (Row H). Single-URL canary is correct
for single-URL architecture. Confirm `TINYASSETS_MCP_CANARY_URL=https://tinyassets.io/mcp`.

---

## Rollback path

Since no DNS or cloudflared config changes were made, rollback is simply removing
the Access application. Use the API script (primary) or dashboard (fallback):

### Rollback — API path (primary)

```bash
# Dry-run first:
python scripts/cf_access_rollback.py

# Apply (deletes the Access app; policies cascade automatically):
python scripts/cf_access_rollback.py --apply

# Also delete the service token (forces re-creation on next cutover):
python scripts/cf_access_rollback.py --apply --rotate-token
```

The script runs an inverted verification after `--apply`: confirms canonical is still
green and internal is now reachable (ungated), proving the rollback worked.

### Rollback — Dashboard path (fallback)

1. Zero Trust → Access → Applications → `tinyassets-mcp-worker-gate` → **Delete**.
2. `mcp.tinyassets.io` immediately becomes publicly reachable again (no propagation delay).
3. The Worker continues to work either way — the Access headers it sends are ignored
   if no Access application is protecting the subdomain.

**Note:** Rollback is for emergency diagnosis only. The single-entry decision
argues against a second public surface at steady state. After recovery, re-enable
the Access application and investigate root cause.

---

## Canary env var confirmation

After cutover, confirm the canary is pointed at the right URL.

In `/etc/tinyassets/env` (or wherever the daemon env is managed):

```
TINYASSETS_MCP_CANARY_URL=https://tinyassets.io/mcp
```

If it still reads `https://mcp.tinyassets.io/mcp`, update it. The Layer-1 canary
probing an Access-blocked URL will hard-fail on every probe otherwise.
