# ADR-025: The Trusted Ledger and Conversations Live Outside the Box

## Status

Proposed. Would extend ADR-019 and N10.

## Date

2026-10-26

## Context

ADR-019 says everything inside a box is untrusted. A box's shell runs as the
owner's user with no further isolation (`owner_process.py` starts children
with plain `subprocess.Popen`), so it can rewrite any store the box holds.
Messaging between an owner's agents needs facts that mustn't be forged: who
sent a message, who it reaches, how deep a chain is, how many wakes remain,
what was delivered, and each agent's status.

ADR-012 already lists conversation stores as platform state outside boxes.

## Decision

- **The broker keeps the ledger.** It holds the agent registry, message
  metadata (sender, recipients, chain depth, body hash), room caps, wake and
  delivery state, MCP event subscriptions and receipts, in stores no box or
  daemon opens.
- **Bodies, rooms and the board live outside the box too** (founder
  decision, 2026-10-26). A per-owner conversation store in the trusted zone,
  never on a box's mount, holds message bodies, the task board and Tier A
  turn-based app rooms, on one revision primitive.
- **Who can record what:**
  - The router records the owner's and outside agents' messages and
    receipts from their verified identity.
  - The loop worker records its native agents' messages.
  - The box can post only as `native`, flagged as coming from the box.
- **Caps are owner policy (N10),** written only from a protected owner
  session.
- **Status comes only from receipts,** never from state inside the box.
- **Wakes are doorbells carrying IDs,** sent by the control-plane tick
  through broker egress. An ambiguous wake is marked `unknown` and never
  replayed.

## Consequences

- Reading or posting a message, moving a task or taking a turn-based move
  never wakes a box.
- Snapshot or rewind of a box can't roll back delivery state.
- MCP event callbacks are sent from the trusted side, not from a box (a box
  has no network: `--network=none`).
- `app_room.cas` converges onto the conversation store's revision primitive.
- Registry rows S30 and S33 guard the ledger.
