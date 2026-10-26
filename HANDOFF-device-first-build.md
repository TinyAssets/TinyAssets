# TinyAssets build handoff — 2026-10-26

Start here if you are a new session continuing the device-first build. The plan of record is the Claude doc **"TinyAssets — Agents Everywhere, Device-First: Architecture Plan (Draft)"** (https://claude.ai/artifact/Coh1aNYNh27ycf1gpfPF39). Its tabs are Main, Workstream specs, Research and alternatives, Adversarial review, Code spec and architecture, and ADR drafts. Every PR reconciles against the relevant sections ("Build protocol" in the Code spec tab). Difficulty, or a related patch turning up, is a sign of drift from the intended architecture.

## Why this handoff exists

The previous session was attached as `Jonnyton/tinyassets`, the old name, which GitHub redirects to `TinyAssets/TinyAssets`. `git push` worked through the redirect. The REST API refused PR writes with "Writes to numeric-ID repository paths are not permitted through this proxy", and GraphQL is unavailable. Attach **TinyAssets/TinyAssets** so you can open and track PRs.

## Branches pushed, waiting for a PR (open them first)

| Branch | What | Checks run locally |
|---|---|---|
| `s1-adrs-device-first` | ADR-023 to ADR-031 as **Proposed**, plus INDEX lines. Status lines of the amended ADRs (008, 009, 012, 019, 022) change only on acceptance (ADR-005). | check_shapes clean; structural guards 768 passed |
| `p0-2-refuse-websocket-scopes` | `OwnerRouter` and `OwnerIngress` let `lifespan` through, close `websocket` with 1008, and raise on other scope types. Helper `refuse_non_http` lives in `owner_ingress.py`. New `tests/test_door_scope_refusal.py`. | 4 new tests; 45 door-adjacent tests; guards 768; ruff clean. `tests/test_owner_router.py` errors in a plain workspace (`owner daemon startup failed: owner daemon exited during startup`), the same as on main, so CI is the oracle. |
| `p0-6-zero-idle-tail` | `idle_limit` / `TINYASSETS_BOX_IDLE_SECONDS` deleted. `recover()` sleeps any awake, non-busy box whose activity is known. LRU-eviction test replaced (see note). | lifecycle suites 101 passed; guards 768; ruff clean |

PR descriptions: write them from each commit message. The commit messages carry the symptom, the change and the plan item. End each description with the attribution lines.

### Notes for reviewers

- **P0-6 trade-off:** ticks run every 30 s, so a box now sleeps within 30 s of its last work. Until ADR-030 (slim box) and ADR-022 P6 (suspend/restore) land, the first request after that pays a cold start. Today's box app measured about 3.7 s in gVisor; restore measured about 0.15 s. Record the real first-message delay after deploy.
- **P0-6 dead code:** LRU eviction of idle boxes is now unreachable, because a tick that sees `active == 0` sleeps the box. Delete it with the flat cap in P0-7.
- **P0-6 fixture caveat:** the `fleet` fixture's `tick()` calls `close()`, which awaits every owner task. That includes one blocked on an owner lock held by a waiting wake, so tests with a waiting wake drive `routing.tick()` directly.

## Decided on 2026-10-26 (recorded in the plan doc and the ADRs)

- Message bodies, the task board and Tier A turn-based rooms live outside the box, in a trusted conversation store (ADR-025).
- The box is a slim harness. Owner MCP, app and `/api` requests are served on the trusted side (ADR-030).
- One app manifest: `apps/{slug}/app.json` with a `multiplayer` block (ADR-029).
- No vendor goes first. Build for any combination of agents a user holds.
- Hosted vendor CLIs on our boxes are for the paid tier only.
- Entity: none needed now. Jonathan can publish as an individual on the ChatGPT apps directory, the Claude directory, Apple and Google. An entity is needed only for in-app TINY wallet, exchange or investment features (Apple 3.1.5 and 5.1.1(ix), Google financial services), for "TinyAssets" as the Apple seller name (DBAs are not accepted), or to keep his home address off a monetized Google Play listing. Research: scratchpad `research_entity.md` (summarized here).
- Vendor letters were replaced by research; nobody was contacted. The result is ADR-031:
  - API keys for anything unattended.
  - TinyAssets never holds consumer login tokens (Claude.ai login tokens, `~/.codex/auth.json`, Grok sessions).
  - Hosted CLIs run only turns a human started, with the binary unmodified and Anthropic Commercial Terms accepted.
  - Wakes go through each vendor's own door: Claude scheduled-task `/fire` (30 an hour per task, 100 an hour per account), ChatGPT MCP Events, Cursor Automations webhooks, and Claude Code Channels on the device once allowlisted.

## Production probes (2026-10-26)

- **Outside identity is off in production.** A Claude connector call appeared in `read_graph target=agents` as `claude-code` with `"identity_source":"verified_session"`, and so did every other outside entry. `outside_authority.origin()` returns `None` unless `TINYASSETS_OUTSIDE_CLIENT_CLAIM` is set, so every outside agent is treated as the owner. P0-3 needs that env var set in production, which is a host action.
- **ChatGPT `events/subscribe` can't succeed from a box.** With the box outbound guard on, the raw callback POST fails with `SsrfValidationError 'outbound host resolution failed'`. `verify()` returns `{'code': -32015, 'data': {'reason': 'challenge_failed'}}` with message `Callback verification failed`. Script: scratchpad `probe_subscribe.py`.
- **`get_status`** shows `"identity_evidence":{"status":"unavailable","reason":"key_not_provisioned"}`, because `TINYASSETS_IDENTITY_FINGERPRINT_KEY` isn't set. It also returns operator diagnostics (`/home/owner/...` paths, `policy_hash`, queue diagnostics) and a persona/embodiment instruction. Those are openspec `reconcile-external-connector-manifests` tasks 3.1–3.4 and block directory review.

## Drift to reconcile before building P0-4 and P0-1

The plan's P0-4 text says to tunnel callbacks from the box through `OwnerPublicForwarder`. ADR-025 and ADR-030 now put subscriptions and delivery on the trusted side (broker egress), not the box. Build P0-4 that way, and update the plan's P0-4 text in the same PR. P0-1 ports the deleted MCP Events suites, adapted to the trusted-side delivery, not "owner mode".

## Next build order

1. Open the three PRs above.
2. P0-1 and P0-4/P0-5 together: MCP Events delivery and tests on the trusted side.
3. P0-7: memory-budget admission by size class (`thin`, `cli`, `chromium`), replacing `TINYASSETS_BOX_AWAKE_CAP` and LRU eviction.
4. P0-8: async multiplexed relay.
5. P0-3: code side (`check_identity` consults grants) plus the host action that sets the claim env var.
6. S2 onward, per the Build slices table in the Code spec tab.

## Submissions (only Jonathan can do the identity steps)

- **Sign in with ChatGPT waitlist:** https://openai.com/form/sign-in-with-chatgpt-interest/ (no browser was reachable from the previous session). Answers:
  - Name: Jonathan Farnsworth. Company: TinyAssets. Website: https://tinyassets.io. Job title: Founder.
  - Capability: "Sign in and ChatGPT plan use for AI requests".
  - Product: "TinyAssets (tinyassets.io/app — web, Android, desktop) is a command center where a person's own AI agents work together on a shared task board and group chat. Users bring their own model access; TinyAssets never supplies a model. Sign in with ChatGPT would let a ChatGPT subscriber sign in and run their own TinyAssets agent on their own plan, from the hosted app and from our open-source desktop runner. Usage stays the signed-in user's own, for their own work; we never pool or resell it. Code: github.com/TinyAssets/TinyAssets (MIT)."
  - Work email: his choice of info@tinyassets.io or his personal address.
- **ChatGPT apps directory:** individual verification with a government ID (his step), then domain verification. He'll need:
  - Website, support, privacy and terms URLs.
  - A reviewer test account.
  - 5 positive and 3 negative test cases, plus a video walkthrough.
  - The listing shows his verified legal name.
  - Note that ChatGPT apps must suit users aged 13–17, while TinyAssets' terms say 18+. Resolve that before submitting.
- **Claude directory:** submit from his Pro or Max account at claude.ai/directory/manage. The Company step takes TinyAssets and tinyassets.io. He'll need:
  - OAuth 2.0 and tool annotations (spec table in `live-mcp-connector-surface`).
  - A populated reviewer account.
  - The seven policy acknowledgments.
- **Fix before either listing:**
  - openspec `reconcile-external-connector-manifests` tasks 3.1–3.10: public status projection, neutral instructions, securitySchemes, bounded errors, privacy disclosures.
  - The privacy policy names Amazon Web Services for hosting, but production runs on a DigitalOcean droplet behind Cloudflare. Correct the subprocessor list.
- **Apple and Google:** individual accounts are fine. UGC needs filtering, report and block, published contact info, age gating, consent before sharing data with third-party AI, and Apple 4.7.4's software index. Google personal accounts need a 12-tester, 14-day closed test. Keep TINY out of the apps.
