# ADR-016: Identity Follows the Account, Not the Client

## Status

Accepted

## Date

2026-09-08 (WorkOS AuthKit chosen 2026-09-03)

## Context

Users reach TinyAssets through ChatGPT, Claude, local agents, other MCP hosts
and the app. An earlier plan used GitHub OAuth at launch.

## Decision

WorkOS AuthKit is the identity primitive. OAuth 2.1 with PKCE at the MCP edge
maps every client session to the same stable subject, so one person resolves to
the same principal and home command center from every client. There is no
anonymous principal: an unauthenticated request for platform data or actions
fails closed. Discovery and sign-in bootstrap may be reachable before
authentication only when they confer no principal, data or action; operational
probes use a named service principal.

Clients get parity. Browser-only users get every actionable capability. A
public chatbot feature launches on both Claude and ChatGPT, and a bug on one
provider is P1, not "use the other client".

## Consequences

- As-built: `openspec/specs/identity-auth-and-access-control/` and
  `openspec/specs/external-app-principal-mapping/`.
