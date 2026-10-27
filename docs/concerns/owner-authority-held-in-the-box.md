---
severity: P1
title: Owner action rules, approvals and consents are held and enforced only inside the box
filed: '2026-10-27'
summary: 'The broker OPEN never checks action rules, one-time approvals or effector consents, and the box shell can rewrite the stores that hold them, so an agent can bypass the owner''s approval rules; #5326 grew past its slice and is closed until the spec places this'
---

# Owner authority is held and enforced only inside the box

**Requirement IDs.** N10 (owner-only policy layer), ADR-025 ("Caps are owner policy (N10),
written only from a protected owner session"), ADR-030 (the split rule: trusted code that
reads or writes owner data runs outside the box), plan A1 (grant modes).

## Symptom (verified on main, 2026-10-26)

- `.action-policy.db` (`tinyassets/action_policy.py`: "owner action rules and approvals are
  evaluated in the owner's box"), `.universe-sidecars/*/.effector_consents.db` and
  `.outside-client-authority.sqlite3` live on the owner volume. The box's shell runs as the
  same uid as its app server, so it can rewrite them.
- The broker's OPEN (`broker/server.py` `_open` / `admit_and_start`) checks admission, the
  ledger, the fence and spend caps, never action rules, once-approvals or consent. Those gates
  run only in the box (`effectors/authenticated_external_call.py`). The relay socket is
  read-write inside the box, so the shell can send OPEN directly and skip them.
- The router forwards the owner's protected-session row into the box identity
  (`owner_router.py`), and the broker accepted it from the box for policy writes and for
  HTTP_CONNECT's door commit.

## What the clean architecture says

The plan's module map puts owner policy in `broker/policy.py` (room caps and per-agent grant
modes, next to egress and spend rules), written only from a protected owner session.
ADR-030 serves owner requests on the trusted side, so policy writes and checks belong there,
and the box keeps only tools, user code and owner files.

## Why #5326 was closed

#5326 moved rules, approvals and consents into the broker and gated OPEN. Over six review
rounds it grew to about 2,000 changed lines and reworked today's model-connect and
approval-card flows (consent on connect, an "Allow live calls" action, importing old rules
from box files). Those flows are replaced by the plan's W7 onboarding recipes and W11 brains,
so the work patched code the plan deletes: the build protocol's stop signal. The branch
`n10-authority-broker` is kept for reference.

## Options

1. Land only the security core now: the broker holds rules, approvals and consents and
   enforces them at OPEN; the box channel cannot widen authority; owners start from default
   rules (no import from box files); connect-flow consent is built with W7/W11.
2. Build it as part of S2a and ADR-030, where owner requests move to the trusted side.
3. Keep #5326's full scope.

Founder decision needed before code.
