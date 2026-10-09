---
severity: P2
title: An activity's yield trusts the route's caller-named session
filed: '2026-10-06'
summary: '`_yield_activity` and the route ActivityFence read the activity id from the engine route''s `session` query parameter, which the launcher names but does not sign. Any engine call in the same universe that names `activity:<id>` can move that activity to `waiting_on_you` (ending its run). Owner-scoped, not cross-user; the fence itself only refuses.'
---

# An activity's yield trusts the route's caller-named session

Carried over from the resolved native-activity boundary concern (2026-10-03),
which is closed now that every provider's tools cross the engine route's
`ActivityFence`.

`engine_mcp_server._yield_activity` takes the activity id from
`engine_steering._session_key()`, the route's `session` query parameter. The
launcher sets it from `ModelConfig.agent_session`, but the parameter is only
covered by the launch grant's MAC when a grant is present, and the yield does
not check that. So an engine call in the same universe that names
`session=activity:<id>` and raises an owner request moves that activity to
`waiting_on_you` and ends its run.

The same parameter selects which activity `ActivityFence` checks. That path
only refuses tool calls, so a forged session there can only stop its own calls.

Fix shape: carry the activity id in the signed launch grant (or a separately
signed claim on the route) and have `_yield_activity` act only on a verified
one. Owner-scoped: every caller of this universe's route is already the owner's
own agent or tooling.
