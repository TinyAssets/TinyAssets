# Deep-Dive Findings — What TinyAssets Actually Is

## TL;DR
TinyAssets is **a global goals engine + paid-market AI workflow platform** with a real production deployment, an active daemon running fantasy-novel generation, 47 promoted wiki pages, and existing detailed specs for both the website (track B) and the tokenomics (track E). The Claude design's "Summon the daemon" hero is **not random** — it matches the planned landing-page hero from the spec. The new site needs to honor what's already designed, not invent a parallel reality.

## What's actually built / specified

### Platform (already shipping)
- **Live MCP at `tinyassets.io/mcp`** — production endpoint, gated for OAuth + RLS
- **Cloudflare Worker + GitHub Actions** for deploy, uptime canary, DR drills, secret rotation
- **Active universe** `concordance` running fantasy-book daemon (`fantasy_daemon/`)
- **Wiki system** — 47 promoted pages: 33 bug reports filed by chatbot users, plus concept/plan/builder-notes pages
- **Three tiers of users**:
  - **T1** — browser/Claude.ai users, copy `tinyassets.io/mcp` into chatbot, no install
  - **T2** — daemon hosts, run Windows tray app (`tinyassets_tray.py`), can earn money completing bids
  - **T3** — OSS contributors writing nodes/branches/goals
- **MCP tools live now** (I just called them): `wiki`, `get_status`, `universe`, `goals`, `gates`, `extensions`

### Tokenomics (specced, not yet shipped)
Source: `2026-04-18-paid-market-crypto-settlement.md (archived in git)` (Track E, ~4 dev-day estimate)
- **Crypto settlement on Base L2** — Base Sepolia testnet (`chain_id: 84532`) → Base mainnet (`8453`) by config flip
- **TinyAssets test-token (ERC-20)** as the bid currency
- **1% fee to treasury / 99% to daemon-host** on each settled bid
- **Off-chain ledger + batched on-chain payouts** (Option A): preserves micro-bids, weekly batch settlement
- **WalletConnect v2** — wallet only required at bid placement; browse + run free nodes wallet-less
- **48h dispute window** with auto-accept; daemon can respond with corrected work
- **Anti-sybil**: same-user bidding refused, account-age gate, GitHub OAuth only
- **Outcome gates** with bonus staking — `gates` MCP tool already has `stake_bonus / unstake_bonus / release_bonus` actions (needs `TINYASSETS_PAID_MARKET=on`)

### Website plan (specced, not yet shipped)
Source: `2026-04-18-web-app-landing-and-catalog.md (archived in git)` (Track B, ~7-8 dev-day estimate)
- **Hero copy from spec: "Summon the daemon"** — exact match to Claude design
- **Stack pick: SvelteKit** with dual adapter (`adapter-static` for catalog, `adapter-node` for dynamic routes)
- **Hosting: GitHub Pages primary + GoDaddy cPanel SFTP as fast-fallback** (both fronted by Cloudflare)
- **16 URL surfaces** with this map:

| URL | Type | Purpose |
|-----|------|---------|
| `/` | SSG | Hero + 3-CTA (Connect / Host / Contribute) |
| `/catalog/` | SSG + Realtime | Top nodes, goals, branches |
| `/catalog/nodes/<slug>` | SSG | One canonical page per node |
| `/catalog/goals/<slug>` | SSG | Goal page + leaderboard |
| `/catalog/branches/<slug>` | SSG | Branch graph + run-it CTA |
| `/catalog/search` | SSR | `discover_nodes` semantic + structural search |
| `/connect` | SSG | T1 onboarding — copy `tinyassets.io/mcp` URL |
| `/host` | SSR | T2 onboarding — OS-detect → installer download |
| `/contribute` | SSG | T3 onboarding — clone + CONTRIBUTING + live PR list |
| `/status` | SSR + Realtime | Host count, inbox depth, catalog freshness, ticker |
| `/auth/*` | Dynamic | Supabase Auth + GitHub OAuth |
| `/editor/*` | Dynamic (auth) | In-browser node/goal/branch editor |
| `/earnings` | Dynamic (auth, T2+) | Daemon-host payout dashboard |
| `/admin` | Dynamic (role) | Moderation triage |
| `/account` | Dynamic (auth) | Session, exports, **delete account** (GDPR) |
| `/legal` | SSG | ToS + privacy + license (CC0 content / MIT platform) |

## What this means for our merge

**The Claude design is correct in spirit** — "Summon the daemon" hero, dark ink + parchment palette, workflow showcase diagrams. It's the landing-page art direction for the spec'd site. What it's missing:
- The 15 other surfaces (catalog, connect, host, contribute, status, editor, earnings, admin, account, legal)
- The 3-CTA tier flow (Connect / Host / Contribute)
- The token / earnings / wallet UX
- Real data hooked up via MCP

**The current GoDaddy site** still serves the legacy Tiny Assets crypto messaging. The TinyAssets rebrand is half-done in the unpublished editor draft.

**What's actually missing** to ship the spec'd site:
- Frontend code (none exists yet — just markdown specs)
- Solidity contract for the test-token (deferred until Track E dispatched)
- Supabase backend wiring for catalog reads / search / auth
- Tray-side wallet registration

## Conflicts to resolve before I scaffold anything

1. **Stack**: Spec says SvelteKit. You said "you pick, easy to iterate." Plain HTML can't deliver `/catalog/search`, `/editor/*`, `/status` real-time widgets, or auth-gated routes — those need a real framework + Supabase. Recommend SvelteKit per spec. Plain HTML would only cover landing + maybe a static catalog snapshot.

2. **Hosting**: Spec says GitHub Pages (primary) + GoDaddy cPanel SFTP (fallback) — both fronted by Cloudflare. You said custom HTML/JS on GoDaddy domain. These are reconcilable: keep `tinyassets.io` pointing through Cloudflare to GH Pages, with cPanel SFTP mirror as failover. GoDaddy stays as the registrar / DNS surface, not the primary host.

3. **Scope**: 16 surfaces is real work (~7-8 dev-days per spec; closer to 23-26 dev-days for full track B per the navigator's revised estimate). Should we:
   - (a) ship the full spec'd site
   - (b) ship just landing + connect + catalog snapshot (~2-3 days)
   - (c) ship landing-only first as a brand refresh (~1 day), iterate from there

4. **Token surfacing**: Site spec § doesn't have a dedicated "tokenomics" page. The token is mentioned only in `/host` (earnings preview), `/earnings` (dashboard), `/admin` (moderation). The Tiny Assets brand from the legacy site (Buy Tiny / Add Liquidity / Market Price / Portfolio) is **not in the new spec at all** — those pages would be deprecated. Worth checking: is the new site the *replacement* for the Tiny Assets crypto-investor surface, or do those pages stay accessible somewhere?
