The order is the plan's Build slices table (Code spec tab), unchanged: dependencies first,
nothing built in a wrong shape to be rebuilt later. Slice names (S0, S2a, ...) are the plan's
build slices, not registry row IDs.

## S0 Repairs

- [x] P0-1, P0-4, P0-5 Restored event tests; callbacks through broker egress; deliveries on the broker tick (#5317).
- [x] P0-2 Refuse websocket scopes (#5315).
- [ ] P0-3 Outside agents distinguishable from the owner: grant modes (full by default, scoped per agent), `check_identity` consults grants, then the claim probe and setting.
- [ ] P0-6 Zero idle tail (#5316). Acceptance still owed: the canary still wakes during a deploy.
- [x] P0-7 Admission against a memory budget (#5319).
- [x] P0-8 Multiplexed relay (#5320).

## S1 Architecture on record

- [x] ADR-023 to ADR-031 accepted.
- [x] Registry rows S29 to S35 and N12 listed, unbuilt rows with their planned guards.

## S2 Placement

- [x] `execution` on every source record; the `free` field deleted (#5322).

## S2a Loop worker (A8, ADR-023)

- [ ] The execution owner runs as its own pool of unprivileged processes outside the daemon and every box; turns resume from the generation-fenced journal; extension points call into the box. Live: killing a worker mid-turn leaves the turn held and a fresh worker picks it up with no replay; box memory falls to tool time in the metering receipts.

## S3 Ledger and first outside agent

- [ ] Agent registry, message ledger, broker ops, wakes on the tick, `mailbox.message`, `read_graph target=mailbox`, `mailbox_ack`. Live: the first user's agent wakes on a message, reads it and replies; time to reply recorded.

## S4 Rooms, status and onboarding

- [ ] Room bodies, `room_message`, `converse(room_id)`, caps; agent recipes, including Muse over MCP with a scoped key; bubbles, "+ add agent", handshake; status from receipts. Deletes the external-agent panel, `renderExternalAgents()` and the agent `<select>`.

## S5 One revision primitive

- [ ] The generic collection; the board on it; published app rooms moved onto it. Deletes `app_room.py`'s own compare-and-swap.

## S6 App manifest and agent players

- [ ] Manifest fields; seat-scoped grants for agents in someone else's app; match records. Deletes the second app shape.

## S7 Device runner

- [ ] Pairing, parked polls at the broker, device launcher with pinned templates, capability advert, presence. Deletes `tinyassets/desktop/` tray, dashboard, notifications and pystray.

## S8 Real-time relay

- [ ] One relay object per match, `MATCH_TICKET`, bridge actions, host election, checkpoints, zone leases, catch-up package.

## S9 Claude and Grok wakes

- [ ] Scheduled-task and webhook recipes; tokens stored through the reshaped vault.

## S10 Sign in with ChatGPT

- [ ] Source record on device placement (locally hosted).

## M Metering

- [ ] Box-awake intervals by size class, peak memory, relay bytes, daily rollup, founder-only read. Acceptance still owed: a week of receipts with cost per driver in dollars.
