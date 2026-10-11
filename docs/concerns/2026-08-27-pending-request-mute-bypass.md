---
severity: P1
title: Pending-request mute bypass
filed: '2026-08-27'
summary: 'different asks collide, and the asking agent can unmute itself Also: `dont_ask_again` and `unmute_request` are both reachable by the served agent, which authenticates as the user''s own principal; lifts are recorded and surfaced, not prevented'
---

# The agent can lift a mute the user set, because they share one principal

**Filed:** 2026-08-27 (Codex cross-family review, verdict ADAPT)
**Verified:** 2026-08-27 — reproduced: mute → agent reads key → agent unmutes →
the identical ask returns `pending`
**Severity:** P2 — the user's "don't ask me this again" is not enforceable
against the party it constrains

## The finding

`dont_ask_again` records a suppression, and `unmute_request` lifts it. Both are
reachable through `write_graph`, and **the served agent authenticates as the
user's own principal** — so every owner-gated operation the user can call, the
universe can call too. Nothing at the gate can distinguish *"the user lifted
this mute"* from *"the universe lifted the mute the user set"*.

`list_requests` also returns the raw `dedupe_key`, which is the handle
`unmute_request` needs, so the agent does not even have to guess it.

## What was fixed, and what was not

**Fixed** (same review): the suppression key was too coarse — it omitted `body`
and `fields`, and an `answer` action normalizes to a bare `{"type":"answer"}`,
so muting *"Approve this?"* about a harmless draft also silenced *"Approve
this?"* about deleting production data. The key now includes body and fields.

**Not fixed:** the shared principal. A real fix needs the authorization model to
separate "the human at the surface" from "the agent acting for them", which does
not exist today and is not something to invent inside this feature.

The interim measure is honesty rather than a fake boundary: a lift is **recorded
and surfaced** in the rail (`mutes_lifted`), so a user who set a mute can see it
was lifted and by what. That does not prevent it.

## Why it is not simply "make unmute user-only"

There is no user-only channel. The app and the agent both reach the daemon over
the same MCP surface with the same bearer identity. Adding a flag the app sets
and the agent is asked not to set would be a boundary enforced by convention,
which is the shape this repo has repeatedly rejected (an identity you cannot
verify is not a gate).

## What would resolve it

An actor distinction on the request carrier — the surface that originated the
call, verified rather than claimed — and then restricting `unmute_request` to a
human-originated one. That belongs with the provider-authority work, not here.

## Related

- `2026-08-27-codex-paste-deposit-review.md (archived in git)`
- `docs/concerns/2026-08-27-served-provider-authority-is-converse-only.md`
