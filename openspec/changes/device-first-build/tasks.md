Build order is the founder's 2026-10-26 re-sequence: "Trusted front → S3a ledger → S5a collections → P0-3 code → S3b events → ADR-030 tool-by-tool → S2a loop worker → S4/S5b", then the plan's later slices.

Each slice below carries its row of the plan's Build slices table ("TinyAssets — Agents
Everywhere, Device-First: Architecture Plan", Code spec tab): Requirement IDs, Ships, Deletes,
Live acceptance and Depends on are the plan's words, with code formatting added. Open slices
follow the founder's order above; a split slice (S3a and S3b, S5a and S5b) carries its plan row
once, at its first part. Metering (M) runs beside the others from S2 on. Slice names (S0, S2a,
...) are build slices, not registry row IDs.

The checklist items are this repo's additions. An item is ticked in the PR that lands it. A landed
item whose live acceptance is still owed says so.

## S0 Repairs

- **Requirement IDs:** P0-1 to P0-8
- **Ships:** Restored event tests, websocket refusal, outside-identity claim, callbacks through broker egress, deliveries on the tick, zero idle tail, size-class admission, multiplexed relay
- **Deletes:** The private delivery loop; the 900 s idle default; the flat awake count
- **Live acceptance:** A ChatGPT subscribe that failed now succeeds; two connectors show as two agents; with thin work only, far more than 6 boxes run at once
- **Depends on:** Nothing

- [x] P0-1, P0-4, P0-5 Restored event tests; callbacks through broker egress; deliveries on the broker tick (#5317). Live acceptance owed: the live ChatGPT subscribe succeeds.
- [x] P0-2 Refuse websocket scopes (#5315).
- [x] P0-6 Zero idle tail (#5316). Live acceptance owed: the canary still wakes during a deploy (fix PR #5330 open).
- [x] P0-7 Admission against a memory budget (#5319). Live acceptance owed: with thin work only,
  far more than 6 boxes run at once.
- [x] P0-8 Multiplexed relay (#5320).

P0-3 is open; it is built fourth, under "P0-3 code" below.

## S1 Architecture on record

- **Requirement IDs:** ADRs, plus registry rows S29 to S35 and N12
- **Ships:** The ADR drafts in the ADR drafts tab; openspec changes carry the requirement IDs
- **Deletes:** Superseded ADR wording, including ADR-019's loop placement and C6 step P3
- **Live acceptance:** ADRs merged; new registry rows listed as unbuilt with their guards
- **Depends on:** Nothing

- [x] ADR-024 to ADR-029 and ADR-031 accepted (#5324); ADR-023 and ADR-030 accepted (#5328).
- [x] Registry rows S29 (#5322), S30 to S35 and N12 (#5328) listed with their planned guards.
  Deviation: S33 to S35 read drifted, not unbuilt, because their second path is live today; each
  row's "Lands in" follows this table, not the plan's registry table, which is off by one.

## S2 Placement

- **Requirement IDs:** W1-R1 to W1-R4
- **Ships:** `execution` on every source record with all placements (`thin_loop`, `hosted_box`, `device`); `device` refuses loudly until S7; `serves` checked; dispatch otherwise unchanged
- **Deletes:** The `free` field; S29 starts at 27 branches
- **Live acceptance:** A connected Codex on staging keeps working with no reconnect
- **Depends on:** S1

- [x] `execution` on every source record; the `free` field deleted (#5322). Live acceptance owed: a connected Codex on staging keeps working with no reconnect.

## Trusted front

The founder's words: "Insert a 'trusted front' slice (router answers registered MCP tools itself, forwards the rest to the box) before S3/S5".

- [x] One registry in the router (`owner_router.TRUSTED`): MCP requests keyed by JSON-RPC method, tool and target, app requests by path and method, answered from the verified identity; everything else forwarded with its original bytes, `tools/list` included. The `events/subscribe` door and the router's shared app routes fold into it; `test_trusted_front` holds the one dispatch point. No new targets.

## S3a Ledger

- **Requirement IDs:** W3-R1 to W3-R6, W4
- **Ships:** Agent registry, message ledger, broker ops, `outside_wakes.py`, `mailbox.message`, `read_graph target=mailbox`, `mailbox_ack`
- **Deletes:** None
- **Live acceptance:** The first users' agent wakes on a message, reads it and replies; time to reply recorded
- **Depends on:** S0, S1

- [x] S3a.1 Message ledger core (#5336): `broker/message_ledger.py` (`outside_agents`,
  `ledger_messages`, `ledger_recipients`, `ledger_receipts`, `ledger_cursors`) and
  `broker/conversations.py` (`message_bodies`), broker-held per owner; owner-channel ops
  `OUTSIDE_AGENTS`, `ROOM_POST` (1:1) and `MAILBOX_RECEIPT`; `pull` delivery only; registry
  row S30 converged with `test_ledger_boundary`. No public surface yet.
- [x] S3a.2 Doors (`mailbox_door.py`): trusted-front registrations for `read_graph
  target=mailbox`, `write_graph room_message` and `mailbox_ack` (legacy clients get the
  box's SSE framing), `GET /api/mailbox`, `POST /api/mailbox/ack` and
  `/api/mailbox/replies`, and the owner's `/app/api/outside-agents` and
  `/app/api/agent-messages`; the box answers the targets `served_on_trusted_side`. Until
  P0-3, its MCP doors refuse a caller with no `outside_origin` rather than read it as the
  owner. **Live acceptance still owed:** an API-key agent reads and replies, and time to
  reply is recorded.

## S5a Collections

- **Requirement IDs:** W8, W9 Tier A
- **Ships:** The generic collection; the board on it; published app rooms moved onto it
- **Deletes:** `app_room.py`'s own compare-and-swap
- **Live acceptance:** Board race; the existing post-c6 multiplayer tests pass on the shared primitive
- **Depends on:** S1

- [x] Collections: broker collection store (`collection_rows` in `broker/conversations.py`,
  owner-channel op `COLLECTION`: compare-and-swap `write`, `pull` after a `seq` cursor, private
  rows); the board expressible as `collection='board'` (its doors are S5b); published app rooms
  (`/app/<slug>/state`) answered on the trusted side by `room_door.py` from the publisher's store
  as `room:<app>:main`; `app_room`'s own compare-and-swap and the box-side `state.json` deleted;
  registry row S34 converged with `test_revision_primitive`. Live acceptance owed: the board
  race and the post-c6 multiplayer flow against staging. Gaps: an erased player's rows stay in
  other publishers' stores until that publisher clears them; the box-side
  `apps/<slug>/state.json` files are left on volumes, unmigrated (founder decision).

## P0-3 code

S0's row above.

- [ ] N10 owner authority in the broker (P0-3's prerequisite): action rules, once-approvals, effector consents and outside-client grant modes in `broker/policy.py`'s per-owner store (plan module map: "Room caps and per-agent grant modes as owner policy (N10)"; founder 2026-10-26: trusted per-owner stores are broker-held SQLite), written only from a protected owner session and enforced at OPEN; the box channel cannot widen authority. Connect-flow consent is built with W7/W11, not here. Stage a1 (action rules and once-approvals) is PR #5333.
- [ ] P0-3 Outside agents distinguishable from the owner: grant modes (full by default, scoped per agent), `check_identity` consults grants, then the claim probe and setting.

## S3b Events

S3a's row above.

- [x] Wakes: `ledger_wakes`, the `AGENT_WAKE` broker op, `control_plane/outside_wakes.py` and
  `mailbox.message` events; the wake delivery methods are admitted with them. Built: `pull`
  and `mcp_event` admitted (routine and webhook refused until S9, decided 2026-10-25);
  `ROOM_POST` opens one wake per `mcp_event` recipient; `AGENT_WAKE` runs on the broker's
  delivery pass under the op journal (429 waits Retry-After, an ambiguous send is `unknown`
  and never replayed); `mailbox.message` in the events registry, subscribed only by the
  agent's own client; disabling an agent suppresses its wakes and erases its callback;
  registry row S31 converged with `test_outside_wakes`. **Live acceptance still owed:** a
  ChatGPT agent wakes on a message, reads it and replies (an `mcp` agent registers only
  once P0-3 configures the outside-client claim). **Owed:** M3, a revoked outside-client
  grant ends the agent's wakes and erases its callbacks (waits on #5343; blocks S31 reading
  converged once #5343 lands); L5, bounding `ledger_wakes` growth and the per-pass scan of
  every owner's ledger (S4 caps).

## ADR-030 tool by tool

ADR-030 (accepted): owner MCP, app and `/api` requests are served on the trusted side through `BoxProvider` calls.

- [ ] Move each owner tool and app route to the trusted side as a trusted-front registration, one by one; the box keeps tools, user code and owner files.

## S2a Loop worker

- **Requirement IDs:** A8
- **Ships:** The execution owner (D11) runs as its own pool of unprivileged processes outside the daemon; turns resume from the generation-fenced journal; extension points call into the box
- **Deletes:** The loop's home in the shared daemon
- **Live acceptance:** Killing a worker mid-turn leaves the turn held and a fresh worker picks it up with no replay; box memory falls to tool time in the metering receipts
- **Depends on:** S0, S1

- [ ] The loop worker. Open first: since C6 the loop runs in the box, not the shared daemon, and no slice moves serving out of the box (`docs/concerns/2026-10-27-no-slice-moves-serving-out-of-the-box.md`, a founder decision at S2a).

## S4 Rooms, status and onboarding

- **Requirement IDs:** W5, W6, W7
- **Ships:** Room bodies, `room_message`, `converse(room_id)`, caps; agent recipes, including Muse over MCP with a scoped key; bubbles, "+ add agent", handshake; status from receipts
- **Deletes:** External-agent panel, `renderExternalAgents()`, the agent `<select>`
- **Live acceptance:** A phone-only user connects an agent and sees the bubble after the handshake; a two-agent loop stops at the cap
- **Depends on:** S2a, S3

- [ ] Rooms, status and onboarding.

## S5b Board

S5a's row above.

- [x] Task board on the collection store (`board_door.py`): trusted-front registrations for
  `read_graph target=board` (`cursor` is the `since` seq) and `target=board_task` (`query`
  is the task id), `write_graph target=board_task` (`payload_json {task_id, patch}`,
  `expected_revision`), `GET /api/board`, `GET`/`POST /api/board/task` and the owner's
  `/app/api/board` (`DELETE` deletes one task); the box answers the targets
  `served_on_trusted_side`. Each agent is its own store writer (`agent:<agent_id>`); the
  task's history is the store's `collection_history`, and each claim and completion in it
  is mirrored as a `MAILBOX_RECEIPT board` receipt on every write. **Live acceptance
  owed:** two agents of different vendors race for one claim and an expired lease reopens
  the task, against staging.
- [ ] W8 as decided 2026-10-27, replacing the board's claim rules: a measured lease,
  `clamp(3 x p95 of the claimant's check-in gap, 60 s, 24 h)` from its own receipts (seeds
  under 5 samples: 30 min `mcp_event`, 3 h `pull`, 30 min owner), a requested lease
  clamped, renewed by any accepted holder write or a `heartbeat` op with an optional note,
  a 7-day hold cap (`hold_cap`), `input_required` with `expired_3x` after 3 expiries, a
  disabled or revoked holder released lazily and an expired holder's late write refused;
  `everyone` is one claimant, first claim wins, and an owner-only create fans out one
  child task per agent; A2A v1.0 states at the edge (`open`, `claimed`, `working`,
  terminal `completed`/`failed`/`canceled`/`rejected` never reopened, a follow-up linked by
  `context`, dependency-blocked computed, `input_required` stored, a lease expiry ending
  the attempt as `failed`); a `claim.epoch` fence, with field-disjoint owner edits merged
  under the row compare-and-swap. Sources: SQS visibility timeout
  (awsdocs/amazon-sqs-developer-guide `sqs-visibility-timeout.md`), Pub/Sub p99 deadline
  (googleapis/python-pubsub `streaming_pull_manager.py`), Temporal heartbeats
  (`detecting-activity-failures.mdx`), Kubernetes Lease (`node-status.md`), Faktory
  reservations (`manager/working.go`), Sidekiq poison pill (Reliability wiki), the Claude
  runner heartbeat, A2A Task lifecycle (`a2a.proto`), Asana assign copies.

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

- [x] Box-awake intervals, peak memory, relay bytes, storage samples, daily rollup, founder-only
  read. Live acceptance owed: a week of receipts with cost per driver in dollars.
