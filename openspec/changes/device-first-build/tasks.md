Each slice below carries its row of the plan's Build slices table ("TinyAssets — Agents
Everywhere, Device-First: Architecture Plan", Code spec tab): Requirement IDs, Ships, Deletes,
Live acceptance and Depends on are the plan's words, with code formatting added. The order
follows dependencies, so nothing is built in a wrong shape to be rebuilt later. Metering (M) runs
beside the others from S2 on. Slice names (S0, S2a, ...) are build slices, not registry row IDs.

The checklist items are this repo's additions. An item is ticked when its PR merged. A merged item
whose live acceptance is still owed says so.

## S0 Repairs

- **Requirement IDs:** P0-1 to P0-8
- **Ships:** Restored event tests, websocket refusal, outside-identity claim, callbacks through broker egress, deliveries on the tick, zero idle tail, size-class admission, multiplexed relay
- **Deletes:** The private delivery loop; the 900 s idle default; the flat awake count
- **Live acceptance:** A ChatGPT subscribe that failed now succeeds; two connectors show as two agents; with thin work only, far more than 6 boxes run at once
- **Depends on:** Nothing

- [x] P0-1, P0-4, P0-5 Restored event tests; callbacks through broker egress; deliveries on the broker tick (#5317). Live acceptance owed: the live ChatGPT subscribe succeeds.
- [x] P0-2 Refuse websocket scopes (#5315).
- [ ] N10 owner authority in the broker (P0-3's prerequisite): action rules, once-approvals, effector consents and outside-client grant modes in `broker/policy.py`'s per-owner store (plan module map: "Room caps and per-agent grant modes as owner policy (N10)"; founder 2026-10-26: trusted per-owner stores are broker-held SQLite), written only from a protected owner session and enforced at OPEN; the box channel cannot widen authority. Connect-flow consent is built with W7/W11, not here.
- [ ] P0-3 Outside agents distinguishable from the owner: grant modes (full by default, scoped per agent), `check_identity` consults grants, then the claim probe and setting.
- [x] P0-6 Zero idle tail (#5316). Live acceptance owed: the canary still wakes during a deploy (fix PR #5330 open).
- [x] P0-7 Admission against a memory budget (#5319).
- [x] P0-8 Multiplexed relay (#5320).

## S1 Architecture on record

- **Requirement IDs:** ADRs, plus registry rows S29 to S35 and N12
- **Ships:** The ADR drafts in the ADR drafts tab; openspec changes carry the requirement IDs
- **Deletes:** Superseded ADR wording, including ADR-019's loop placement and C6 step P3
- **Live acceptance:** ADRs merged; new registry rows listed as unbuilt with their guards
- **Depends on:** Nothing

- [x] ADR-024 to ADR-029 and ADR-031 accepted (#5324); ADR-023 and ADR-030 accepted (#5328).
- [x] Registry rows S29 (#5322), S30 to S35 and N12 (#5328) listed with their planned guards.

## S2 Placement

- **Requirement IDs:** W1-R1 to W1-R4
- **Ships:** `execution` on every source record with all placements (`thin_loop`, `hosted_box`, `device`); `device` refuses loudly until S7; `serves` checked; dispatch otherwise unchanged
- **Deletes:** The `free` field; S29 starts at 27 branches
- **Live acceptance:** A connected Codex on staging keeps working with no reconnect
- **Depends on:** S1

- [x] `execution` on every source record; the `free` field deleted (#5322). Live acceptance owed: a connected Codex on staging keeps working with no reconnect.

## S2a Loop worker

- **Requirement IDs:** A8
- **Ships:** The execution owner (D11) runs as its own pool of unprivileged processes outside the daemon; turns resume from the generation-fenced journal; extension points call into the box
- **Deletes:** The loop's home in the shared daemon
- **Live acceptance:** Killing a worker mid-turn leaves the turn held and a fresh worker picks it up with no replay; box memory falls to tool time in the metering receipts
- **Depends on:** S0, S1

- [ ] The loop worker. Open first: since C6 the loop runs in the box, not the shared daemon, and no slice moves serving out of the box (`docs/concerns/2026-10-27-no-slice-moves-serving-out-of-the-box.md`, a founder decision at S2a).

## S3 Ledger and first outside agent

- **Requirement IDs:** W3-R1 to W3-R6, W4
- **Ships:** Agent registry, message ledger, broker ops, `outside_wakes.py`, `mailbox.message`, `read_graph target=mailbox`, `mailbox_ack`
- **Deletes:** None
- **Live acceptance:** The first users' agent wakes on a message, reads it and replies; time to reply recorded
- **Depends on:** S0, S1

- [ ] Ledger and first outside agent.

## S4 Rooms, status and onboarding

- **Requirement IDs:** W5, W6, W7
- **Ships:** Room bodies, `room_message`, `converse(room_id)`, caps; agent recipes, including Muse over MCP with a scoped key; bubbles, "+ add agent", handshake; status from receipts
- **Deletes:** External-agent panel, `renderExternalAgents()`, the agent `<select>`
- **Live acceptance:** A phone-only user connects an agent and sees the bubble after the handshake; a two-agent loop stops at the cap
- **Depends on:** S2a, S3

- [ ] Rooms, status and onboarding.

## S5 One revision primitive

- **Requirement IDs:** W8, W9 Tier A
- **Ships:** The generic collection; the board on it; published app rooms moved onto it
- **Deletes:** `app_room.py`'s own compare-and-swap
- **Live acceptance:** Board race; the existing post-c6 multiplayer tests pass on the shared primitive
- **Depends on:** S1

- [ ] One revision primitive.

## S6 App manifest and agent players

- **Requirement IDs:** W9 manifest, agent interface, AI payment
- **Ships:** Manifest fields (runtimes, minimum device, host, tier, agent interface, AI payment); seat-scoped grants for agents in someone else's app; match records
- **Deletes:** The second app shape
- **Live acceptance:** A turn-based game played by two players' own agents
- **Depends on:** S2, S5

- [ ] App manifest and agent players.

## S7 Device runner

- **Requirement IDs:** W2-R1 to W2-R5
- **Ships:** Pairing, parked polls at the broker (`DEVICE_SEND`), device launcher with pinned templates on the `SubscriptionRuntime` contract, capability advert, presence
- **Deletes:** `tinyassets/desktop/` tray, dashboard, notifications; pystray
- **Live acceptance:** A headless runner holds polls for 24 hours while the box sleeps; a Claude Code job on the computer streams into a bubble
- **Depends on:** S1, S2

- [ ] Device runner.

## S8 Real-time relay

- **Requirement IDs:** W9 tiers B and C
- **Ships:** Real-time Worker with one Durable Object per match, `MATCH_TICKET` broker op, bridge actions (the parent page owns the socket), host election from the S7 advert, checkpoints, zone leases, catch-up package
- **Deletes:** None
- **Live acceptance:** Lockstep RTS: 10,000 turns with no desync across iPhone, Android and desktop; an agent commander's orders applied within two turns
- **Depends on:** S6, S7

- [ ] Real-time relay.

## S9 Claude and Grok wakes

- **Requirement IDs:** Recipes
- **Ships:** Scheduled-task and webhook recipes; tokens stored through the reshaped vault
- **Deletes:** None
- **Live acceptance:** A scheduled task fired with IDs only reads the mailbox and replies
- **Depends on:** credential-vault-reshape

- [ ] Claude and Grok wakes.

## S10 Sign in with ChatGPT

- **Requirement IDs:** W11
- **Ships:** Source record on device placement (locally hosted)
- **Deletes:** None
- **Live acceptance:** A Plus user's TinyAssets agent runs on their plan from the paired computer
- **Depends on:** S7, OpenAI eligibility

- [ ] Sign in with ChatGPT.

## M Metering

- **Requirement IDs:** W10-R1, W10-R2
- **Ships:** Box-awake intervals by size class, peak memory, relay bytes, daily rollup, founder-only read
- **Deletes:** None
- **Live acceptance:** A week of receipts with cost per driver in dollars
- **Depends on:** S2

- [ ] Metering (branch `m-metering`, PR pending).
