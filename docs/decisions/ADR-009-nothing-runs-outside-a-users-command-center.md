# ADR-009: Nothing Runs Outside a User's Command Center

## Status

Accepted

## Date

2026-08-29

## Context

A host-run cloud-worker fleet was still declared in `deploy/compose.yml`. It
executed work that no user owned or could see.

## Decision

Every execution (a chat turn, a branch run, a background automation, a schedule
firing) belongs to exactly one command center and to the person who owns it,
and that person can see it, pause it and delete it from their own surface. The
platform never runs an actor of its own: no host-run worker fleet, no platform
agent container, no "host user" acting inside command centers.

1. A registration that can never fire (a schedule whose scheduler is dark, an
   automation whose executor cannot activate) refuses loudly at registration.
2. Re-issuing execution authority under a new identity goes through the user's
   surface, never a server-side mint on their behalf.
3. Infrastructure processes (canary, reconciler, log shipper) may run, but they
   never call an LLM or act as a command center.

## Consequences

- The cloud-worker fleet was removed; the user-owned background loop stays.
- Deploy and Docker tests derive the long-running service set rather than
  hand-listing it, so a new fleet cannot slip back in.
