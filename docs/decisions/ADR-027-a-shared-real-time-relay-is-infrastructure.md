# ADR-027: A Shared Real-Time Relay Is Infrastructure

## Status

Proposed. Would amend ADR-009.

## Date

2026-10-26

## Context

ADR-009 lets infrastructure processes run only if they never call an LLM or
act as a command center. Real-time games (RTS, MMORPG and anything between)
need a low-latency channel between players' devices. Holding that channel in
a publisher's box would keep the box awake for the whole match, and the box
has no push transport. Research priced a Cloudflare Durable Object relay at
about $0.007 per 8-player match-hour.

## Decision

- **One relay object per match** forwards opaque frames between members
  holding a ticket the broker signed.
- **The relay never simulates, decides or calls a model.** It orders
  commands (lockstep), elects and fences a host from the device capability
  advert, and stores encrypted checkpoints whose key the publisher's box
  minted.
- **Players' devices do the work wherever they can.** A publisher may bring
  their own host instead of the relay.
- **The publisher's box stays the system of record:** joins, match ends and
  flushes of persistent state.
- **Clients:**
  - The parent app page owns the socket.
  - Game bundles stay sealed, and reach the relay only through bridge
    actions.
- **Box ingress:** websocket scopes are refused at the router and at box
  ingress.

## Consequences

- A second runtime vendor (Cloudflare) joins the operated surface. It is
  owned as a thin component rather than a hosted platform, given Hathora's
  and hosted PartyKit's shutdowns.
- Relay traffic is metered in bytes (N12).
