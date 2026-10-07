# ADR-011: The Owner Door Is Complete; Only the Model Door Bounds

## Status

Accepted

## Date

2026-09-30

## Context

The founder's request rail vanished because a 34 KB queue crossed a 24 KB
model-context ceiling that the app shared with chatbots. A limit meant for a
model's context was silently applied to the owner's own app.

## Decision

There are two doors onto a command center's data.

- The **owner door** (`tinyassets/owner_door/`, `/app/api/*`) serves the
  owner's app on web, phone and desktop. It always returns complete data and
  has no size, limit or truncation logic.
- The **model door** (the MCP connector and the served agent) bounds what
  enters a model's context, as a projection applied only there.

Shared domain reads return every row. Paging is an explicit cursor the client
drives, never a silent default. The only per-account input that may change
behaviour is `AccountType` (free or subscription), resolved once per account.

## Consequences

- Enforced by structure: `tests/test_owner_door_import_boundary.py` keeps the
  owner door from importing the ceiling or projection modules.
- As-built: `openspec/specs/onboarding-web-app/spec.md` (owner door) and
  `openspec/specs/live-mcp-connector-surface/spec.md`; change
  `openspec/changes/archive/2026-09-30-owner-door-complete-reads/`.
