# ADR-006: The Platform Has No LLM

## Status

Accepted

## Date

2026-09-24

## Context

Shared host logins and maintainer credentials once served universes. One
person's subscription became everyone's fallback, and it was unclear who paid
for and authorized each model call.

## Decision

Only a powered command center calls an LLM, with its owner's own connected
credentials, for that command center alone. The platform never makes, needs or
brokers an LLM call for its own operation: onboarding, selection, moderation,
ranking, investigation, maintenance and monitoring all run without one. No
platform, host, maintainer or shared credential serves a command center, not
even as a fallback. The founder's subscription belongs to the founder's own
command center, like any user's.

## Consequences

- The shared host logins and their keepalives were removed
  (`deploy/retire_platform_llm_logins.sh`).
- A command center with no connected source is unpowered and says so; it never
  borrows authority.
- A compute market, if one emerges, is users lending their own connected
  compute; until then market code makes no LLM call.
