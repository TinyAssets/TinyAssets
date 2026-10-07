# ADR-015: Only the Floor Blocks a Deploy

## Status

Accepted

## Date

2026-08-20

## Context

First drafts were held behind hardening gauntlets, yet only live users reveal
whether a shape is right.

## Decision

The floor, and only the floor, blocks a deploy:

- a cross-user read or effect;
- auth or credential exposure;
- unrecoverable loss of user data;
- wrong money;
- an irreversible external act without consent;
- the public connector down.

Everything else is tracked in `docs/concerns/` and re-judged after live use.
Work goes shape, live MVP, user test, then harden what live use shows matters.
Hard-to-reverse surfaces (public MCP/API surface, storage shape, authority,
migrations, money) get a spec before code; the rest is built and then specced
from what shipped.

## Consequences

- Cross-family review (`AGENTS.md`, loop step 4) covers floor-class changes and
  gate files only.
