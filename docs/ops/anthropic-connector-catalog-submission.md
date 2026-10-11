# Anthropic MCP Connector-Catalog Submission — Research + Plan

**Status:** Research-in-progress + submission plan. This doc pre-draws the submission workflow so it's ready to execute when the platform is launch-ready.
**Priority:** **Launch-gate per the A-follow directive.** TinyAssets must appear in Claude's connector directory for tier-1 adoption. Without this, users must manually paste `tinyassets.io/mcp` — friction point that kills the zero-install funnel.
**Owner:** host (Jonathan) drives submission; admin-pool reviews.

---

## 1. What we know (from MCP + Claude Desktop ecosystem as of 2026-04 knowledge cutoff)

### 1.1 The connector catalog

Anthropic maintains a public directory of vetted MCP connectors accessible from within Claude.ai's "Connectors" UI + Claude Desktop. Listed connectors get a one-click "Add to Claude" path vs the manual-URL-paste path that un-listed servers require.

**Known as of cutoff:**
- MCP spec itself: open standard, anyone can host a server at any URL.
- `.well-known/oauth-authorization-server` endpoint required for OAuth 2.1 + PKCE flows (MCP spec 2025-11-25).
- Claude.ai's connector directory UI supports both user-added arbitrary URLs AND a curated directory.
- Early directory entries (GitHub, Slack, Linear, Zapier, etc.) suggest a mix of first-party partnerships + developer-submitted entries.

**Not yet confirmed from public docs (needs live research — see §4):**
- The exact submission portal URL.
- Whether submissions go through `support.anthropic.com`, a dedicated developer portal, GitHub PR to a public registry, or email.
- Approval criteria — SOC 2? Privacy audit? Simple manual review?
- Approval timeline — days / weeks / months.
- Per-connector branding guidelines (logo, description length, category).
- Post-listing requirements (uptime SLA, response to incident reports).

### 1.2 Comparable ecosystems to pattern-match

As research anchors, these directories exist with documented submission flows:

| Directory | Submission pattern | What Anthropic's likely resembles |
|---|---|---|
| **Slack App Directory** | Submit app via developer portal + manual review + security check | Most likely pattern if connectors are positioned as "apps." |
| **Google Workspace Marketplace** | Developer-portal submission + OAuth verification + security audit | Higher friction; probably NOT where Anthropic lands at this stage. |
| **Microsoft Teams App Store** | Manifest submission + Partner Center + validation | Middle-weight. |
| **Zapier integration** | Dev-portal submit + testing + published | Zapier is already in Anthropic's directory → same pattern possible. |
| **GitHub Marketplace Apps** | Publish a GitHub App + fill out manifest + listing request | Lightweight if the MCP server is framed like a GitHub App. |

**Best guess (flag as unconfirmed):** Anthropic's initial process is probably a simple form at `claude.com/developers` or similar, plus manual Anthropic-side review. That pattern matches Anthropic's engineering-culture preference for lightweight coordination over heavy portal infrastructure.

---

## 2. Artifacts the submission will almost certainly require

Based on comparable ecosystems + MCP spec + common-sense directory requirements. **Prepare these regardless of which submission pattern turns out to be live.**

### 2.1 Server metadata

- **Server name:** `TinyAssets` (matches MCP `serverInfo.name` per the #7 rebrand + #27 gateway spec).
- **Display title:** "TinyAssets — Daemon Engine for Long-Horizon AI"
- **Short description (≤140 chars):** "Summon a daemon. Design custom multi-step AI workflows — research, writing, recipe trackers, fantasy worlds — with real execution, not simulation."
- **Long description (≤500 chars):** (draft below)

```
TinyAssets is a daemon engine. You summon the daemon; it drives your
workflow. Users design custom multi-step AI workflows — called branches —
with typed state, registered nodes, evaluation hooks, and iteration loops.
Fantasy authoring is one benchmark; research papers, recipe trackers,
wedding planners, investigative journalism, news summarization all work.
The daemon is a real executor, not a chatbot simulation. Open commons
(CC0 content), zero-install for tier-1 users, one-click install for hosts.
```

- **Category:** "Productivity" or "Developer tools" — likely the closest fits. Flag for the real directory's category list (§4 Q2).
- **Tags / keywords:** `workflow`, `daemon`, `ai-agents`, `graph-execution`, `open-commons`, `fantasy-writing`, `research`, `productivity`.
- **Maintainer / organization:** TinyAssets — `ops@tinyassets.io`.
- **Website URL:** `https://tinyassets.io/`
- **Support URL:** `https://tinyassets.io/support` (must exist + respond per §3 launch-readiness).
- **Privacy policy URL:** `https://tinyassets.io/legal/privacy` (per web-app spec #35 §2).
- **Terms of service URL:** `https://tinyassets.io/legal/terms` (per #35 §2).

### 2.2 Technical artifacts

- **MCP server URL:** `https://tinyassets.io/mcp`
- **OAuth 2.1 + PKCE metadata:** served at `https://tinyassets.io/mcp/.well-known/oauth-authorization-server` per #27 §5.1.
- **Health endpoint:** `https://tinyassets.io/mcp/health` — returns 200 + JSON payload indicating gateway + Supabase + Realtime status.
- **Version:** `1.0.0` at launch; server info MCP handshake also advertises this.

### 2.3 Branding assets

Typical directory requirements:

- **Logo:** 512×512 PNG, transparent background. The "U" daemon-circle from `tinyassets_tray.py` is the current visual; needs a standalone catalog version (transparent BG, higher-contrast).
- **Banner / card art:** 1200×630 PNG (OG-card ratio). Likely reused between this catalog + our own `/` landing page + per-node OG images.
- **Screenshot / demo:** ≥1 screenshot showing a user in Claude.ai doing a TinyAssets operation. Ideally a quick GIF / video (≤30s) of "summon daemon → create universe → see daemon work."

### 2.4 Compliance + security

- **Public privacy policy** at stable URL. Content follows `docs/decisions/ADR-010-private-by-default.md` and the deployed schema.
- **Terms of service.** Likely includes "you must meet your chatbot provider's age requirements" per `project_q10_q11_q12_resolutions.md` Q11 + the CC0 licensing commitment.
- **Security contact:** `security@tinyassets.io` — must be monitored per SUCCESSION.md §7.
- **Incident-response policy:** public statement that security reports get a <72h first response + coordinated disclosure timeline.
- **OAuth 2.1 + PKCE handshake:** must actually work (verified via `.well-known` + `/mcp/token` endpoint per #27 §5.1).
- **Rate limits:** documented per-account limits (per #27 §1 + #36 rate-limit specs). Public rate-limit documentation helps.

### 2.5 Operational

- **SLA / uptime target:** 99% target for launch; stretch to 99.5% post-scale-audit green. Not a hard commitment until load-test #26 passes.
- **Support response time:** 72h for non-critical, 24h for critical security issues.
- **Change policy:** MCP serverInfo version bumps for breaking API changes; release notes published at `tinyassets.io/changelog`.

---

## 3. Submission workflow (skeleton, pending §4 confirmation)

This is the workflow we'd run if the submission is lightweight (form + manual review). Adjust based on §4 research findings.

### Step 1: Pre-submission readiness audit

Before submitting, verify:

- [ ] `tinyassets.io/mcp` is live, OAuth 2.1 flow works end-to-end with a real Claude.ai client.
- [ ] `.well-known/oauth-authorization-server` returns valid metadata.
- [ ] `/health` endpoint responds 200 under load (#26 S8 mixed-workload passes).
- [ ] Privacy policy + ToS pages live at their canonical URLs.
- [ ] `ops@` + `security@` email aliases routed + monitored.
- [ ] Branding assets prepared (logo 512×512, banner 1200×630, screenshot, ≤30s demo).
- [ ] Draft submission text approved by admin-pool (2 members review).
- [ ] SUCCESSION.md runbook + admin pool ≥2 seated (directory may want to see operational readiness).

### Step 2: Submit

Exact form varies by submission portal (§4 Q1). Template fields to fill:

```
Connector name: TinyAssets
Short description: [from §2.1]
Long description: [from §2.1]
Category: Productivity
Maintainer: TinyAssets <ops@tinyassets.io>
Server URL: https://tinyassets.io/mcp
Auth: OAuth 2.1 + PKCE
Privacy policy: https://tinyassets.io/legal/privacy
Terms of service: https://tinyassets.io/legal/terms
Support URL: https://tinyassets.io/support
Logo: [attach 512×512 PNG]
Banner: [attach 1200×630 PNG]
Demo video: [attach or link to ≤30s demo]
Why should Claude users have one-click access?
  [150-word pitch — prepare from §2.1 description + daemon-as-viral-hook framing]
Describe your data handling:
  [pointer to privacy catalog + dual-layer concept/instance model from #25/§17]
```

### Step 3: Anthropic review window

Expected: unknown — **7-30 days guess**. Comparable app-directory reviews typically take 5-15 business days.

During review, Anthropic may:
- Test the OAuth handshake against our server.
- Verify the health endpoint.
- Ask clarifying questions (respond within 72h per §2.5).
- Request changes to description, policies, or branding.
- Approve / deny / defer.

### Step 4: Post-approval

- Connector appears in Claude.ai's directory.
- Tier-1 users get "Add TinyAssets" one-click path.
- Monitor inbound traffic + support channel for any spike in user questions.
- Any significant version change (API breaking, major features) may require re-submission or notification — unclear, flag Q5.

---

## 4. OPEN flags — what we don't know yet

These require live research (WebFetch / WebSearch on Anthropic's public docs + developer-relations channels). Dev can't answer them from training data.

| # | Question | Where to research |
|---|---|---|
| Q1 | **Exact submission portal URL + process.** Is it a form at `claude.com/developers`? GitHub PR to a registry repo? Email to `developer-relations@anthropic.com`? Something else? | Check `docs.anthropic.com`, `claude.com/developers`, Anthropic DevRel Twitter/LinkedIn, MCP GitHub org's docs. |
| Q2 | **Category list.** What categories does Anthropic's directory use? Do we pick from a fixed list or is "free-form tag" OK? | Check the directory itself — view existing listings, note their category assignments. |
| Q3 | **Approval criteria + review timeline.** What's the actual bar? Any SOC-2 or security-audit requirement? Timeline SLA from Anthropic? | Check MCP GitHub issues for prior submitters' experience; check X/Twitter for dev community discussion. |
| Q4 | **Post-listing obligations.** Does listing commit us to an uptime SLA, response-time SLA, or similar operational promises? | Anthropic Terms for Developers — if published. |
| Q5 | **Version-change policy.** Do breaking API changes require re-submission? Notification-only? Silent? | Anthropic developer FAQ + comparable directories' policies. |
| Q6 | **Branding compliance.** Is "Claude" allowed in our descriptions? Can we say "for Claude.ai users"? | Anthropic trademark/branding guidelines. |
| Q7 | **Multi-provider dual-listing.** If we simultaneously support Codex/Gemini MCP clients, can we say so in the Anthropic listing, or are listings Claude-only-framed? | Anthropic directory listings for connectors that explicitly multi-serve. |
| Q8 | **Developer agreement / click-through.** Is there a Developer Agreement we sign during submission? Any exclusivity clauses? | Submission portal's terms page. |
| Q9 | **Analytics / attribution.** Does Anthropic provide directory-install analytics (click-through rates, install count)? Useful for measuring the viral-hook. | Post-listing dashboard — if available. |
| Q10 | **Localization.** Is a multi-lingual listing supported, or is launch-English-only per `project_q13_q14_resolutions.md` acceptable? | Directory UI observation + multilingual connectors' listings. |

---

## 5. Research action plan

### Step 1: Direct research (immediate, when dev or host has WebFetch/WebSearch)

Check these URLs (in priority order):
1. `https://docs.anthropic.com/en/docs/claude-code/mcp` — Claude Code's MCP docs often reference the broader ecosystem.
2. `https://docs.anthropic.com/en/api/` — official API docs; may include developer-directory pointer.
3. `https://claude.com/developers` — speculative; check if it exists + lists submission path.
4. `https://modelcontextprotocol.io/` — MCP spec site; may have "list of registered servers" pointer.
5. `https://github.com/modelcontextprotocol` — MCP GitHub org; check for `awesome-mcp` registry or similar.
6. Claude.ai itself — open the Connectors UI, inspect the "Add custom connector" flow for form-submission hints.
7. Anthropic's DevRel Twitter/X, LinkedIn, blog — announcements of "submit your MCP connector" processes.
8. Any MCP-community Discord / Slack.

Document findings in this file (§1 + §2 + §4) as a follow-up commit.

### Step 2: Outreach (if §1 research is inconclusive)

- Email `developer-relations@anthropic.com` (if it exists) with a concise: "We're launching an MCP connector at `tinyassets.io/mcp` — could you point us at the submission process for the Claude directory?"
- Post in Anthropic DevRel Discord / community (if one exists) — same question.
- File an issue in the MCP GitHub org asking about submission canonical path.

### Step 3: Submit + iterate

Once §4 answers are gathered: run through §3 steps 1-4.

---

## 6. Alternatives if Anthropic directory isn't available at launch

Directory listing may not be achievable at MVP launch if the submission process is slow, invite-only, or requires SOC-2-level security audit. Fallback paths:

1. **Manual-paste fallback (already the default).** User copies `tinyassets.io/mcp` into Claude.ai's custom-connector field. Friction: ~60 seconds vs ~5 seconds one-click. Documented in `/connect` page per #35.
2. **MCP community aggregator listings.** Several community-maintained lists (e.g. `awesome-mcp-servers` on GitHub) accept PRs. Lower-reach than the official directory but some SEO + discovery value.
3. **Viral content loop.** Per #35 §7, the per-node catalog URLs are SEO-indexable (CC0 license + OG tags). "Found this cool workflow → how do I run it?" → landing page → manual-paste. Works without directory.
4. **Direct partnerships.** For very early-adopters, 1:1 outreach to researchers / journalists / specific communities that would benefit; bypass directory entirely.

**Recommendation:** pursue directory listing from day one (§3/§4) but DON'T gate launch on it. Launch with manual-paste + start directory submission in parallel. Directory listing, when it lands, is a growth multiplier not a launch gate.

---

## 7. Cross-reference

- **Spec:** `2026-04-18-mcp-gateway-skeleton.md (archived in git)` — OAuth 2.1 + PKCE implementation.
- **Spec:** `2026-04-18-web-app-landing-and-catalog.md (archived in git)` §2 — `/connect` + `/legal` pages.
- **Runbook:** `SUCCESSION.md` §2 — `ops@` + `security@` aliases that the submission references.
- **Memory:** `project_daemon_product_voice.md` — "Summon the daemon" copy.
- **Memory:** `project_q13_q14_resolutions.md` — multilingual posture (relevant to Q10).
- **Memory:** `project_license_fully_open_commons.md` — CC0 content licensing for user-facing framing.
