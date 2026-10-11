# Launch-Readiness Checklist

**Status:** v1. Living document — PR changes per `SUCCESSION.md` §1.7 admin-pool rule.
**Last reviewed:** 2026-04-28 metadata-only; unchecked boxes still require host verification before launch.
**Owner:** admin pool (joint).

Single authoritative gate. **Every box below MUST be checked before any public launch announcement.** No soft "we'll fix it after launch" items; this list is the launch-blocking set. Nice-to-haves live elsewhere.

---

## A. Infrastructure

- [ ] **Domain `tinyassets.io` pre-paid ≥2 years** (per `SUCCESSION.md` §4.1).
- [ ] **Domain expiry monitor cron live + verified** (fires >60 days before expiry to `ops@tinyassets.io`).
- [ ] **GoDaddy account login + GoDaddy billing card documented in vault** (ref `SUCCESSION.md §3.2 TinyAssets-Prod/godaddy/account`).
- [ ] **Cloudflare DNS + Worker configured:** `tinyassets.io` → GH Pages (landing), `tinyassets.io/mcp*` routes through the `tinyassets-mcp-proxy` Worker to the tunnel origin at `mcp.tinyassets.io` → Fly (or whatever provider hosts the daemon), `host-<slug>` reserved pattern for per-host tunnels.
- [ ] **Cloudflare Origin CA certs installed** (Full-strict TLS on all subdomains, 15-yr validity).
- [ ] **Fly.io Machines deployed** at `min=2, max=8`, regions `ord` primary + `fra` secondary.
- [ ] **Supabase Pro project provisioned** with daily backups enabled.
- [ ] **Weekly pg_dump → R2 running + verified-restorable** within last 30 days (per §Data below).
- [ ] **Plan-B self-host migration playbook rehearsed** at least once (per `docs/specs/2026-04-19-plan-b-selfhost-migration-playbook.md` §3.4).

## B. Identity + access

- [ ] **Admin pool has ≥2 named members** seated (host + ≥1 co-maintainer per `SUCCESSION.md §2.1`).
- [ ] **Co-maintainer has verified access to `TinyAssets-Prod` vault**.
- [ ] **Succession lead + alternate named** (`SUCCESSION.md §2.2`).
- [ ] **Bills-paying party + fallback documented** (`SUCCESSION.md §2.3`).
- [ ] **GitHub org created** + `TinyAssets/` + `TinyAssets-catalog/` transferred + ≥2 org admins.
- [ ] **GitHub OAuth app registered** + client ID/secret in vault.
- [ ] **`tinyassets-catalog-bot` GitHub App installed** with scoped permissions per spec #32 §7.
- [ ] **`ops@tinyassets.io` + `security@tinyassets.io` aliases routed** to admin-pool (ForwardEmail.net or Google Workspace — §5.3 flag).
- [ ] **GitHub branch protection on `main`** for both repos: PR + ≥1 admin approval.

## C. Auth + gateway

- [ ] **OAuth 2.1 + PKCE flow works end-to-end** from a real browser chatbot client with the TinyAssets connector installed (Claude.ai, ChatGPT Developer Mode, or equivalent; per spec #27 §5.1).
- [ ] **`.well-known/oauth-authorization-server` returns valid metadata**.
- [ ] **`/mcp/health` responds 200** under load (p95 < 200ms in #26 S8 mixed test).
- [ ] **JWT secret in vault + rotation schedule set** (per `SUCCESSION.md §3.2`, 180d).
- [ ] **Rate-limit middleware live** with Upstash bucket; per-user limits enforced (per spec #27 §4).
- [ ] **RLS policies on every user-data table** match spec #25 §2.

## D. Data

- [ ] **All schema migrations from `docs/specs/2026-04-18-full-platform-schema-sketch.md` §1** applied cleanly on production Supabase.
- [ ] **`workflow_analytics` Postgres role** created + training-excluded fields access-revoked (per spec #25 §2.6).
- [ ] **`training_excluded = true` structural enforcement verified** — any query as `workflow_analytics` role errors on training-excluded rows (smoke test green).
- [ ] **Weekly pg_dump scheduled** + client-side-encrypted with `age` (per `SUCCESSION.md §3.2`).
- [ ] **Monthly manual restore-test** performed + row-counts verified within 30 days of launch.
- [ ] **`treasury_config` seeded** with Base Sepolia treasury address (testnet launch) + CC0-1.0 in `default_content_license` (per spec #29 §6.5 + #32 §5.1).

## E. Paid market + crypto

- [ ] **TinyAssets test-token (ERC-20) deployed to Base Sepolia** + address in `treasury_config`.
- [ ] **Treasury wallet seed in vault** (testnet single-key OK; multisig REQUIRED before any mainnet value accrues).
- [ ] **WalletConnect integration verified** end-to-end (connect → verify → top-up → bid → settle on testnet).
- [ ] **Min-bid threshold set** per capability (default 0.01 test-token).
- [ ] **Account-age + interaction-count gates** enforced on `place_paid_request` RPC.
- [ ] **Settlement batch cron** scheduled (weekly default per spec #29 §3).
- [ ] **Dispute window + auto-accept cron** live (per spec #29 §8).

## F. Moderation

- [ ] **`docs/moderation_rubric.md` v1.0 merged** into `TinyAssets/` main branch.
- [ ] **Admin pool includes ≥2 `host_admin` mod_role rows** in production Supabase (not just in runbook — in the database).
- [ ] **≥1 tier-3 volunteer mod recruited** + accepted rubric + granted `volunteer` mod_role.
- [ ] **All 4 moderation tables** (flags/decisions/appeals + users ALTER) migrated.
- [ ] **Rate limits on `flag_content` enforced** (10/hour/user).
- [ ] **2-mod concurrence for hard-delete enforced** at RPC level (not just UI).

## G. Content + SEO

- [ ] **`TinyAssets-catalog/` repo** has LICENSE (CC0-1.0) + CONTRIBUTING.md (DCO) + README.
- [ ] **≥10 seed nodes** live in catalog across ≥3 domains (research, fantasy, accounting, or similar) — not just example scaffolds.
- [ ] **Export sync pipeline** verified end-to-end: edit public node in Postgres → within 10 min commit appears in `TinyAssets-catalog/` by bot.
- [ ] **PR-ingest round-trip verified**: fork catalog → edit YAML → PR → validator green → merge → Postgres reflects change.
- [ ] **Sitemap.xml regenerates** on catalog-export deploy.
- [ ] **Landing page LCP ≤2.5s on mobile 3G** (Lighthouse pass).
- [ ] **`robots.txt` live** disallowing `/auth/`, `/editor/`, `/admin/`, `/earnings`.

## H. Tier-2 daemon host

- [ ] **Windows EV code-signing cert procured** (~$300/yr).
- [ ] **macOS Developer ID procured** ($99/yr, Apple Developer Program).
- [ ] **Linux GPG signing key + .deb repo** set up.
- [ ] **Installers for all 3 OSes build green in CI** on every `TinyAssets/` merge to main.
- [ ] **Installer links on `tinyassets.io/host` OS-detect correctly**.
- [ ] **First-run wizard completes in <5 min cold** (measured on at least 1 platform).
- [ ] **Tray singleton holds** — double-click shortcut 3× → exactly 1 tray process.
- [ ] **host_pool + Presence-based online status verified** end-to-end (register → go online → kill → goes offline within 90s).

## I. Load-test + scale proof

- [ ] **#26 harness scenarios S1-S5 all green** on production-equivalent test Supabase.
- [ ] **Scenario S8 mixed-workload green** (tier-share at 1k-DAU scale).
- [ ] **Load-test runs in CI** on every `main` merge (full S1-S8).
- [ ] **Nightly load-test scheduled** and alerting on failure.

## J. Safety + legal

- [ ] **Privacy policy live at `tinyassets.io/legal/privacy`**.
- [ ] **Terms of Service live at `tinyassets.io/legal/terms`**.
- [ ] **Privacy contract verified** against `docs/decisions/ADR-010-private-by-default.md` and the live chatbot instructions.
- [ ] **`control_station` prompt rendered from the directive-relocation audit §3** in production MCP gateway (historical filename: `docs/audits/2026-04-18-universe-server-directive-relocation-plan.md`).
- [ ] **DMCA counter-notice email address** published in ToS.
- [ ] **Security report channel `security@tinyassets.io` monitored** + initial response SLA documented.

## K. Post-launch readiness

- [ ] **`SUCCESSION.md` is current** — last-updated date within 30 days.
- [ ] **Incident-response runbook** tested (at least one fire-drill against §6 scenarios of `SUCCESSION.md`).
- [ ] **On-call rotation** defined among admin pool (24h-response for critical security issues).
- [ ] **Public changelog at `tinyassets.io/changelog`** started.
- [ ] **Public status page** (either `/status` on the app or a separate status.tinyassets.io) live.

## L. Anthropic connector submission

- [ ] **Live research on submission path complete** (`docs/ops/anthropic-connector-catalog-submission.md` §4 OPEN flags resolved).
- [ ] **Branding assets prepared** — logo 512×512, banner 1200×630, ≤30s demo clip.
- [ ] **Submission filed** with Anthropic directory.
- [ ] **Manual-paste fallback tested** on `/connect` page (user copies → pastes → first tool call lands in <60s).

---

## Non-blocking (fast-follow post-launch)

- `/account` delete-account + data-export — spec #35 §8, but fast-follow acceptable if 30-day grace is documented in ToS.
- `/earnings` web dashboard — tier-2 hosts can use tray dashboard at launch; web mirror is nice-to-have.
- OG image auto-generation — default OG image works; per-node OG is growth multiplier.
- Multi-language launch — English-only per memory `project_q13_q14_resolutions.md`; chatbot translates at tier-1 level.
- Linux .AppImage in addition to .deb — .deb covers Ubuntu/Debian majority.
- Package manager ingestion (`brew install`, `winget install`) — direct download works at launch.

---

## What counts as "green"

A checkbox is green only when:
1. **Verified by evidence** — a link to CI run, a screenshot, or a live URL that proves the assertion.
2. **Within 30 days of the launch date** — stale verifications don't count.
3. **Signed off by at least one admin-pool member who is NOT the person who verified it** — two-person accountability per `project_host_independent_succession.md`.

---

## Launch decision

When ALL boxes are green AND the admin pool agrees:
- Host posts the launch announcement.
- SUCCESSION.md `last-updated` timestamp bumped to launch date.
- First on-call shift begins.

If even one box is red at launch-decision time:
- Document why in a `launch-blocker` file.
- Either: fix the box, OR explicitly accept the risk + document the fast-follow commit.

**"Launch with known risks" is allowed — "launch without knowing" is not.**
