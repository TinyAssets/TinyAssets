# SUCCESSION.md — Keeping TinyAssets Alive Without Us

**Status:** First-pass runbook (v0.1). Treat every line as load-bearing for the "system is always up without us" forever rule (see `project_host_independent_succession.md` in session memory). Amendments go via PR; 2 admin-pool members must approve changes.

**Last updated:** 2026-04-19.

---

## 0. What this document is (and isn't)

**Is:** the single operational runbook a person with no prior context can read and use to keep the platform running when the original host (Jonathan Farnsworth) is unavailable — including permanently.

**Is not:** a secrets file. Every credential below is referenced by **location + access method**, never by value. Rotating a secret means updating *this file's reference*, not the secret itself.

**Who should read this first:** any named co-maintainer, any ops volunteer the admin pool has onboarded, and the person the host has verbally designated as succession lead. The file assumes working familiarity with git, Docker, Postgres, and basic cloud-console literacy.

**Failure mode this prevents:** host dies tomorrow → domain expires in 11 months → platform goes dark. Or: host disappears for a month → a critical cert renewal fails → tier-2 installers start failing SmartScreen → user onboarding breaks. Both scenarios are the class of problem this runbook exists to stop.

---

## 1. Bus factor inventory (the single-person-dependency list)

Every row is a thing that breaks if only one person can do it. Target: **every row has ≥2 named humans** who can perform the action before launch. "Named" = listed in §2 below + has their access verified at least once per quarter.

| Asset / capability | Current sole holder | Bus factor | Priority to fix |
|---|---|---|---|
| `tinyassets.io` domain (GoDaddy) | Host | 1 | **P0 pre-launch** |
| GoDaddy account billing | Host | 1 | **P0 pre-launch** |
| Supabase project + billing | Host | 1 | **P0 pre-launch** |
| Cloudflare account | Host | 1 | **P0 pre-launch** |
| Fly.io account + billing | Host | 1 | **P0 pre-launch** |
| GitHub org owner (`TinyAssets/` + `TinyAssets-catalog/`) | Host | 1 | **P0 pre-launch** |
| `tinyassets-catalog-bot[bot]` GitHub App owner | Host | 1 | **P0 pre-launch** |
| Windows EV code-signing cert | Host (not yet procured) | 0 (N/A) | P1 (procurement is itself the gate) |
| macOS Apple Developer ID | Host (not yet procured) | 0 (N/A) | P1 |
| Treasury wallet private key (Base testnet for now) | Host | 1 | **P0** — migrate to 2-of-3 multisig before any mainnet value |
| TinyAssets token contract (ERC-20) admin role | Host | 1 | **P0 pre-mainnet** |
| Moderation `host_admin` mod_role rows in Postgres | Host only until recruitment | 1 | **P0 pre-launch** (recruit ≥1 co-admin) |
| DNS registrar override (if GoDaddy itself breaks) | — | 0 | P2 — document fallback registrar |
| This SUCCESSION.md document | Anyone with repo write | ≥2 already | — |

**Rule:** no line on this table ships to launch at bus factor 1. If any row isn't 2+ when host pushes the "launch" button, the launch delays.

---

## 2. Admin pool + named successors

### 2.1 Admin pool (current)

| Handle | Role | Contact channel | Verified access last |
|---|---|---|---|
| @jonathanfarnsworth | host, primary operator, treasury signer #1 | email on GH profile | 2026-04-19 |
| _TBD pre-launch_ | co-maintainer, treasury signer #2, host_admin #2 | | |
| _TBD pre-launch or post-launch_ | tertiary maintainer, optional treasury signer #3 | | |

**Recruitment criteria** for co-maintainers (first co-admin must be seated pre-launch per `project_host_independent_succession.md` §38):
- Merged ≥3 non-trivial PRs into `TinyAssets/` OR ≥30 days of active T2 daemon-host operation with zero moderation strikes.
- Explicit written acceptance of the SUCCESSION.md responsibilities (including the "I will pay bills if host is unreachable for >30 days" clause; optional if a separate operations fund exists).
- Two-factor auth enabled on GitHub + any service with shared access.
- Time-zone diversity preferred: admin pool should not all be in the same ±3-hour band.

### 2.2 Succession lead

A specifically named person who becomes primary operator if host is unreachable for >30 days AND at least one other admin-pool member concurs.

| Succession lead | _TBD_ |
| Alternate succession lead | _TBD_ |

**Activation path:**
1. An admin-pool member believes host is unreachable >30d (checked via: no git commits, no email response, no public activity).
2. They post a signed message to `TinyAssets/` repo issue labeled `succession`.
3. If the succession lead (or alternate) concurs within 7 days, they become primary operator.
4. Primary operator inherits the host's access (via §3 secret-vault access) but NOT the host's personal identity. Communications are signed as "TinyAssets succession operator" not as the host.
5. If host returns, they resume primary — no permission-reclaim drama. Succession was interim.

### 2.3 Bills-paying party

**Critical:** the platform runs on paid infrastructure. "Someone pays the bills" is the one precondition for the forever-rule.

| Bill | Annual cost (est.) | Pays today | Fallback |
|---|---|---|---|
| `tinyassets.io` domain renewal | $18/yr | Host (GoDaddy auto-renew on host's card) | **TBD** — see §4 |
| Supabase Pro | $300/yr | Host | TBD |
| Fly.io Machines (2× min=2 regions) | $120-300/yr | Host | TBD |
| Windows EV code-signing cert | $300/yr | Host (not yet procured) | TBD |
| macOS Developer ID | $99/yr | Host | TBD |
| Cloudflare | $0 (Free tier) | — | — |
| **Total estimated** | **~$850/yr pre-revenue** | | |

**Target state:** 1% paid-market treasury fee covers ongoing bills (per `project_monetization_crypto_1pct.md`). Until mainnet + revenue, we need a documented pre-revenue payer:
- **Primary:** host's personal card (current).
- **Fallback 1:** a foundation / LLC with its own bank account, funded by initial contributions from admin-pool + community donations.
- **Fallback 2:** admin-pool donates personally from a pre-funded pool (ugly; treat as last resort).
- **Fallback 3:** platform degrades gracefully — reads stay up on static-host infra (GoDaddy prepaid + Cloudflare Free tier can coast for months after the dynamic services die), writes stop.

**Action: document the primary payer's method for auto-paying if they become unreachable.** E.g., "host's card auto-renews for 2 years; if card expires and host isn't reachable, admin-pool has authority to switch billing to the foundation LLC."

---

## 3. Secret locations (references, not values)

**Policy:** every **production** secret lives in a shared managed vault. No production secret lives only in one person's personal keychain. Period.

### 3.0 Testnet-phase vs real-currency-phase posture

The policy above applies to the **real-currency phase**. We split discipline by phase because the cost of a mistake differs by ~6 orders of magnitude.

- **Testnet phase (current, 2026-04-19 onwards):** secrets live at host-local paths outside the project folder. No vault required. No multisig required. Treasury is a single-key wallet. Multiple test-currency rotations are EXPECTED (we'll burn + regenerate wallets as the prototype evolves). Blast radius of a compromise: zero real money. See §3.2 row "Treasury wallet seed phrase" for the specific default path.

- **Real-currency phase (pre-launch gate, per `project_host_independent_succession.md`):** flip ALL of the following before any mainnet funds flow:
  - Treasury wallet migrates to 2-of-3 multisig (Safe).
  - All secrets move to a shared managed vault (1Password, Bitwarden, or self-hosted Vaultwarden — §9 Q1).
  - Code-signing certs procured + in vault.
  - Hardware-signing for any mainnet transactions.
  - Admin-pool ≥2 seated + each has verified vault access.
  - Secrets rotated from testnet-phase values.

**The flip is a launch-gate item on the `docs/ops/launch-readiness-checklist.md` §B list.** Until that checklist is green, we do NOT move real money.

### 3.1 Vault of record

**Primary vault:** `1Password` team vault named `TinyAssets-Prod`, OR Supabase Vault for Supabase-native secrets. Lead-over-time.

Members with access (launch-seed):
- Host (full admin)
- Co-maintainer #1 (full admin, added on pool seating)
- Succession lead (read + time-limited admin on emergency)

Rotation policy:
- All vault secrets rotated every 180 days or on any admin-pool departure.
- Vault membership audited every 90 days.

### 3.2 Specific secrets and their vault paths

| Secret | Vault path | Used for | Rotation |
|---|---|---|---|
| Supabase service-role key | `TinyAssets-Prod/supabase/service-role` | Edge Functions + batcher scripts | 180d |
| Supabase anon (public) key | `TinyAssets-Prod/supabase/anon` | Public clients, gateway | 180d |
| Supabase JWT secret | `TinyAssets-Prod/supabase/jwt-secret` | Gateway JWT verify + SvelteKit SSR | 180d; requires coordinated deploy |
| Cloudflare API token (scoped: purge-cache + zones:edit for `tinyassets.io`) | `TinyAssets-Prod/cloudflare/api-token` | GH Action cache purge + DNS updates | 180d |
| Fly.io access token | `TinyAssets-Prod/fly/access-token` | CI deploys | 180d |
| `tinyassets-catalog-bot[bot]` GitHub App private key (PEM) | `TinyAssets-Prod/github-apps/catalog-bot.pem` | Postgres→catalog export sync | 180d |
| GoDaddy account login | `TinyAssets-Prod/godaddy/account` | Domain renewal, static file SFTP | 180d |
| Treasury wallet seed phrase — `tinyassets-testnet-treasury-v0` (Base Sepolia, chain_id=84532) | **Default path: `~/.tinyassets-secrets/base-sepolia-treasury-v0.txt`** (host-local, OUTSIDE the project folder per §3.0 testnet-phase posture). Override via `TINYASSETS_TREASURY_KEY_PATH` env var. Public address `0x3023f144fdaEC0F3E5F75125D55B9BAe3AEEdEb1` recorded here. **Testnet phase: multiple rotations expected; no vault required.** Real-currency phase flips to 2-of-3 multisig (Safe) + vault per §3.0 — until then, moving real money is launch-checklist-blocked. | 1% fee settlement destination | Rotations expected during testnet iteration. Mainnet migration is a separate flow (multisig + vault, not a rotation of this key). |
| Windows EV code-signing cert + password | `TinyAssets-Prod/signing/windows-ev` | Tray .exe signing | On procurement + on expiry |
| Apple Developer ID certs | `TinyAssets-Prod/signing/apple-dev-id` | Tray .dmg signing + notarization | On procurement + on expiry |
| Sentry project DSN | `TinyAssets-Prod/sentry/dsn` | Crash reports | Rarely |
| Plausible self-host admin password | `TinyAssets-Prod/plausible/admin` | Analytics dashboard | 90d |

### 3.3 What's explicitly NOT in the vault

- **Personal GitHub account credentials.** Each admin-pool member uses their own GitHub identity. Repo access is granted via GitHub org membership, not by sharing accounts.
- **Personal payment cards.** The platform card (see §2.3) is owned by the bills-paying party; if that's a personal card, it's THEIR keychain, not the shared vault.
- **Host's personal email.** Communications about the platform go through a shared `ops@tinyassets.io` alias (to be provisioned).

---

## 4. Domain + billing failover

### 4.1 Domain expiry prevention

- `tinyassets.io` auto-renews on host's GoDaddy account. Expiry: documented in vault at `TinyAssets-Prod/godaddy/account`.
- **5-year pre-pay recommended before launch.** GoDaddy supports multi-year registration; this buys time even if billing breaks.
- **Fallback registrar:** if GoDaddy itself becomes unreliable (account suspension, company shutdown), the domain can be transferred. Transfer path documented in §4.2.
- **Monitoring:** a weekly cron pings `tinyassets.io` and checks WHOIS expiry date; if <60 days, alerts `ops@tinyassets.io`. Spec for this monitor: it runs outside the primary stack (GitHub Actions with `curl` + `whois` parse), so it still fires if the primary stack is down.

### 4.2 Domain transfer procedure

If the domain needs to move registrars:
1. Log into GoDaddy at account `TinyAssets-Prod/godaddy/account`.
2. Unlock the domain (GoDaddy dashboard → domain settings → unlock).
3. Request EPP (transfer auth) code. Email delivery to account owner.
4. At the new registrar: initiate transfer with EPP code + domain name.
5. Approve the transfer email from GoDaddy (72-hour default window).
6. Update DNS records at the new registrar to match §5 DNS table (Cloudflare CNAME remains the primary record).

### 4.3 Billing auto-failover (not yet implemented)

Future post-launch feature: a smart-contract treasury bill-payer that pulls from accrued 1% fees to auto-pay infrastructure invoices. Not MVP. Current state: human must pay the bills; this file tells them what to pay.

---

## 5. Infrastructure inventory (what to keep running)

### 5.1 DNS + TLS (Cloudflare)

| Record | Points at | Cache rule | Note |
|---|---|---|---|
| `tinyassets.io` (apex) | GitHub Pages (via CNAME-flatten) | 5-min edge TTL + 24h stale-while-revalidate on `/catalog/*` | Primary static host. |
| `tinyassets.io/mcp*` (apex + path) | Cloudflare Worker `tinyassets-mcp-proxy` → `mcp.tinyassets.io` origin | Bypass `/mcp*` + `/authorize*` | **Canonical user-facing MCP URL.** Installed Claude.ai connectors point here. Worker is a pure proxy; preserves streaming + session headers. |
| `mcp.tinyassets.io` | Cloudflare Tunnel → localhost:8001 | Bypass | Direct-tunnel origin — target of the Worker above + canary parity probes. Not the user-facing URL. |
| `host-<slug>.tinyassets.io` | cloudflared per-host tunnels | Bypass | Daemon-host outbound-only pattern. |
| TLS mode | Full (strict) on all subdomains | — | Non-negotiable. Origin CA cert (15-yr validity). |

**If Cloudflare breaks:** DNS still resolves via GoDaddy's authoritative NS. Worst-case drop Cloudflare-proxy and point records directly at origin; lose cache + DDoS absorption but reads keep working.

### 5.2 Supabase project

- Project ID + URL: in vault at `TinyAssets-Prod/supabase/project-info`.
- Migrations live in `TinyAssets/supabase/migrations/` (PR-reviewed; no manual DDL in production).
- Backups: Supabase Pro auto-backs-up daily for 7 days. **Additional weekly pg_dump to S3-compatible storage** (Cloudflare R2 or Backblaze B2) — runbook for restore in §6.
- Connection limits: Pro plan supports 60 direct connections + pooled via pgbouncer.
- Realtime connection envelope: 2,500 concurrent soft-included, $10/mo per 1k extra.

### 5.3 Fly.io Machines

- App names: `tinyassets-gateway` (MCP gateway per #27), `tinyassets-web` (SvelteKit dynamic routes per #35). Deploy configs in `TinyAssets/fly/`.
- Autoscale: `min=2, max=8` per region. Regions: `ord` primary, `fra` secondary.
- Cost: ~$10-15/mo/region at `min=2`.
- If Fly.io becomes unavailable: **fallback to Hetzner CX11 boxes** (~€6/mo each) running the same Docker images. Migration runbook documented in §6.2.

### 5.4 GitHub infrastructure

- Org: TBD name (currently host's personal account; **must migrate to a named org pre-launch** for bus-factor ≥ 2).
- Repos: `TinyAssets/` (platform) + `TinyAssets-catalog/` (content export).
- GitHub App: `tinyassets-catalog-bot` — scoped to `TinyAssets-catalog/` only.
- Org admin count: ≥2 before launch.
- Branch protection: `main` on both repos requires PR + at least 1 approval from admin-pool member.

### 5.5 Package signing

- **Windows:** EV code-signing cert from Sectigo/DigiCert. Signing happens on CI via the private key retrieved from vault. Cert expiry: 1 year; renewal 60 days pre-expiry.
- **macOS:** Apple Developer ID. Signing + notarization in CI. Annual renewal.
- **Linux:** GPG key for `.deb` signing. Key fingerprint in vault; private key per admin, public key published at `tinyassets.io/linux-pubkey.asc`.

---

## 6. Runbooks — "if X breaks, do Y"

### 6.1 If the gateway is down

**Symptoms:** `tinyassets.io/mcp` returns 5xx (or any non-2xx JSON-RPC response), Claude.ai MCP connector fails handshake, `scripts/mcp_public_canary.py` exits non-zero. Diagnostic split: probe `mcp.tinyassets.io/mcp` directly (bypasses the Worker) — if that's green and apex is red, the Worker or its route is the broken link; if both are red, the tunnel or daemon is down.

1. Check Fly.io status page. If Fly outage, wait + monitor.
2. Check `fly machine list --app tinyassets-gateway` — are Machines healthy?
3. Check Supabase status — gateway depends on Postgres + Realtime.
4. If config-bug suspected: `fly releases --app tinyassets-gateway` + rollback last known good (`fly deploy --image <prior-image-id>`).
5. If still down after 30 min: post banner on `tinyassets.io` via static-host emergency template (`TinyAssets/scripts/emergency-banner.sh`).

### 6.2 If Fly.io itself is unavailable

**Symptoms:** Fly.io status page confirms regional or global outage.

1. Spin up Hetzner CX11 box (~5 min via Hetzner Cloud console using vault-stored API token).
2. Run `TinyAssets/scripts/migrate-to-hetzner.sh` — Docker-compose with both gateway + web images.
3. Update Cloudflare DNS: `mcp.tinyassets.io` A record → Hetzner box IP.
4. TLS propagates via Cloudflare Origin CA (no new cert needed).
5. Return to Fly when their outage resolves; Cloudflare DNS switch reverts.

### 6.3 If Postgres data corruption suspected

1. **Stop writes first.** Put control plane in read-only mode via `UPDATE app_config SET writes_enabled=false;` (admin-pool RPC).
2. Identify the corruption scope. Supabase dashboard → SQL Editor for diagnostic queries.
3. Restore path 1 (preferred): Supabase point-in-time recovery to 5-min-before-corruption. Available on Pro. ~30 min RTO.
4. Restore path 2 (fallback): `pg_restore` from the weekly S3-hosted backup. Up to 7-day data loss. Last resort.
5. After restore: re-enable writes, post public post-mortem within 48h.

### 6.4 If host is unreachable >30 days

Activate §2.2 succession procedure:
1. Admin-pool member opens `succession` issue in `TinyAssets/` repo.
2. Succession lead concurs within 7 days.
3. Succession lead gains full admin access via vault.
4. Weekly status reports posted publicly until host returns OR succession becomes permanent.
5. After 180 days with no host contact, succession becomes permanent. Repo ownership, domain, Supabase billing all transfer to succession lead's identity.

### 6.5 If treasury funds compromised

(Mainnet concern; pre-mainnet this is a no-op.)
1. All admin-pool members + succession lead posted to.
2. If multisig: initiate withdraw to cold wallet with remaining signers.
3. Public disclosure within 24h. Platform continues to function — paid-market routes to new treasury address via `treasury_config` table update.

### 6.6 If a core dependency (e.g. Supabase, Fly) shuts down the business

Extreme but possible. Migration paths documented in `docs/specs/full-platform-migration-options.md` (TBD spec). Core principle: every dependency's data is exportable, and every function is reimplementable on Hetzner + self-hosted Postgres + self-hosted Realtime. Migration cost: ~5-10 dev-days. Not automated.

---

## 7. Communication + coordination

- **`ops@tinyassets.io`** — shared alias. All ops email routes here; admin-pool members have access. To be provisioned pre-launch.
- **`security@tinyassets.io`** — shared alias for vulnerability reports. Pre-launch: routes to host + primary co-maintainer.
- **GitHub org discussions** — primary venue for admin-pool coordination. Public transparency on decisions where possible.
- **Shared chat (TBD)** — Signal group or similar for time-sensitive coordination. Not a replacement for GitHub discussions; for urgent real-time only.

---

## 8. Launch-readiness checklist (§22 gate per forever rule)

Every box below MUST be checked before any public launch announcement:

- [ ] Admin pool has ≥2 named members (host + ≥1 co-maintainer).
- [ ] Co-maintainer has verified access to vault at `TinyAssets-Prod`.
- [ ] Succession lead + alternate named.
- [ ] Bills-paying fallback documented (who pays if host unreachable).
- [ ] Domain `tinyassets.io` pre-paid for ≥2 years.
- [ ] Domain expiry monitor cron running (outside the primary stack).
- [ ] GitHub org created + `TinyAssets/` + `TinyAssets-catalog/` transferred + ≥2 admins.
- [ ] Windows EV + macOS Developer ID certs procured + in vault.
- [ ] Treasury wallet migrated from single-key to 2-of-3 multisig (if any real money flows).
- [ ] Weekly S3 pg_dump + restore-test done within last 30 days.
- [ ] Quarterly access-verification drill: every admin-pool member logs into every service and confirms access.
- [ ] `ops@` + `security@` email aliases routed.
- [ ] SUCCESSION.md revision is current (last-updated date within 30 days).

---

## 9. Open items (needs host decision)

| # | Question |
|---|---|
| Q1 | Which vault product — 1Password team vault vs. a self-hosted Vaultwarden? 1Password is simpler; Vaultwarden stays self-hosted on Hetzner. Recommend 1Password at launch; migrate post-scale if needed. |
| Q2 | Email alias provider — Google Workspace (~$6/user/mo) vs. ForwardEmail.net (free) vs. ProtonMail (privacy-forward)? Recommend ForwardEmail.net at launch. |
| Q3 | Foundation / LLC setup timing — pre-launch or at first $10k treasury accrual? LLC setup costs ~$500-1000; provides billing + liability shield. Recommend within 6 months post-launch, sooner if revenue accelerates. |
| Q4 | Co-maintainer recruitment strategy — public call for volunteers, direct-invite from trusted contributors, or both? Recommend direct-invite for the launch-seed co-maintainer; public call once the first is seated. |
| Q5 | Succession lead vs. co-maintainer — can these be the same person? Yes at launch (minimal admin pool); separate once pool grows to ≥3. |
| Q6 | Multi-sig wallet product — Safe (formerly Gnosis Safe) is the standard for EVM-based chains like Base. Confirm Safe is acceptable vs. other options (Argent, etc.). |
| Q7 | Backup encryption key — pg_dump to S3 should be client-side-encrypted with a key stored in the vault. Which encryption scheme? Recommend `age` (simple + modern). |
| Q8 | Public transparency cadence — publish a "state of TinyAssets" blog post monthly, quarterly, or on-demand? Recommend monthly during launch year, then quarterly. |

---

## 10. Appendix: references

- **Memory:** `project_host_independent_succession.md` (2026-04-18) — source of the forever-rule text.
- **Memory:** `project_monetization_crypto_1pct.md` — treasury structure.
- **Memory:** `project_godaddy_hosting.md` — current hosting + domain.
- **Design note:** `docs/design-notes/2026-04-18-persistent-uptime-architecture.md` — infrastructure shape.
- **Design note:** `docs/design-notes/2026-04-18-full-platform-architecture.md` — system-wide inventory.
- **Spec:** `2026-04-18-moderation-mvp.md (archived in git)` §1.7 — admin pool ≥2 moderation policy.
- **Spec:** `2026-04-18-paid-market-crypto-settlement.md (archived in git)` §6.5 — `treasury_config` table.
- **Upstream GitHub Apps docs:** https://docs.github.com/en/apps — for bot account management.
- **Cloudflare runbook:** internal (TBD).
- **Supabase restore docs:** https://supabase.com/docs/guides/platform/backups

---

*This file is load-bearing for the forever rule. Read it, verify your access, and update it whenever reality shifts.*
