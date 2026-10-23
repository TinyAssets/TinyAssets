## Context

N8 follows Direction 8 and `owner-daemon-in-box/design.md` T1, T4, T6 and T8. `companion-local-mcp` describes possible device capabilities; this change fixes where the personal computer link belongs.

## Goals / Non-Goals

**Goal:** Give an owner an optional connector from their hosted box to their computer while every hosted surface remains usable without it.

**Non-goals:** A platform host path, a required desktop tray, shared device-data processing, or provider-specific integration.

## Decisions

1. The owner adds a computer connector package to their own box. Connection configuration, local capability choices and tool results are owner data in that box. The platform router only authenticates and forwards to the box; broker credential and egress custody remains scoped to its existing duties. A platform desktop service would violate T4/T8 and Direction 8.
2. A user-installed companion makes an outbound, owner-bound connection. The box dispatches only capabilities the owner enabled and the companion's OS permits. The connection carries no general host mount into the platform. This can reuse `companion-local-mcp` and existing node identity rather than introduce another registry.
3. Offline, unpaired or revoked computers return an explicit unavailable result for local actions. Hosted chat, MCP, API, inbound hooks, schedules and box lifecycle continue independently. No shared waker or admission rule depends on computer presence.

## Risks / Trade-offs

- Local OS permissions and device availability vary; advertise only current supported capabilities and report offline state.
- Revocation races active local work; the connector fences new dispatch and reports an unknown outcome rather than replaying an uncertain action.
