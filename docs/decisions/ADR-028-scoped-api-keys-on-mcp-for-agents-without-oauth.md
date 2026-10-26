# ADR-028: Scoped API Keys on MCP for Agents Without OAuth

## Status

Proposed (founder decision, 2026-10-26). Would amend the rule in
`tinyassets/auth/middleware.py` that an API key "is a REST credential only";
consistent with ADR-016.

## Date

2026-10-26

## Context

Some outside agents can't complete OAuth. Meta Muse builds its own
connectors on its cloud VM and works best with a pasted API key. Today the
MCP door accepts OAuth only, so those agents can't connect as first-class
agents.

## Decision

- **Who mints a key:** the owner, from a protected owner session, one key
  per outside agent. The key is bound to that agent's registry row, so it
  carries the agent's identity and its power toggle.
- **What a key may do:** call `/mcp` and `/api` as that agent. It can never
  become an app session, approve anything, or write owner policy.
- **How keys are kept:** hashed at rest, like today's API keys. Each key can
  be revoked on its own, and a revocation fences the next call. Keys are
  rate-limited per agent.
- **How the owner hands one over:** the key is shown once, for pasting into
  the agent's own credential prompt, and never through chat.

## Consequences

- Muse connects over MCP as a full agent. Agents with OAuth keep using
  OAuth.
- This is a credential and authority change, so it needs a floor review
  (ADR-015).
