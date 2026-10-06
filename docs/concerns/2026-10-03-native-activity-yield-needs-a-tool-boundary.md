---
severity: P2
title: Native activity runs are refused until the yield has a tool boundary
filed: '2026-10-03'
summary: 'Activities now REFUSE a native executor: `WorkAgentAdapter.infer` raises `ProviderAuthorityHeldError` for any `native_agent` round of an activity run, first selection or mid-turn switch, before any launch. Fail-closed, so the blocking defect is gone; what remains is the capability gap (a native CLI''s internal tool loop has no pre-tool boundary, and its tools are not all on the engine MCP route) plus an owner-scoped yield-id defect'
---

# Native activity runs are refused until the yield has a tool boundary

**Filed:** 2026-10-03
**Verified:** 2026-10-03, Windows/Python 3.14, PR #4221 — `pytest
tests/test_activity_http_yield.py tests/test_activity_dispatch.py
tests/test_agent_activities.py`
**Severity:** P2 (was P1 while a native activity run could keep acting after a
yield; the refusal below closed that)

Activities REFUSE a native executor. `WorkAgentAdapter.infer` raises
`ProviderAuthorityHeldError("activity runs need an engine-inference executor
until native yield is fenced")` whenever the adapter carries an activity binding
and the round's execution kind is `native_agent`. Because `infer` runs every
round, that refuses a native first selection AND a mid-turn switch onto a native
candidate, before any launch (`tests/test_activity_http_yield.py`).

Why refused rather than left open: the Activities contract requires an
owner-request yield to end the run as completed and release the seat, with no
further tools, and pause/stop need the same boundary. Engine inference HAS that
boundary -- the activity's server-captured run and generation ride the
coordinator's existing per-round and per-tool `check`, so a yielded run ends
normally and releases the work claim without another model request
(`tests/test_activity_http_yield.py` covers it, including a model reply that
batches another tool after the owner request).

A native agent has no such boundary. It executes its own internal tool loop
inside ONE provider call, which the HTTP coordinator's between-step check cannot
fence; native providers also expose tools outside the engine MCP route (for
example WebFetch), so an MCP-only check would not establish the promised
boundary either. Polling activity state and cancelling a native process leaves a
next-tool race and cannot show that effects settled before a completed yield.

Lifting the refusal needs a dedicated design/disposition establishing a native
pre-tool boundary and ordinary yield completion with process/seat release,
proven on Linux. This change does not introduce a native routing protocol or
change native tool policy. Account activation and reset-triggered resumption
remain outside this repair.

Related, still open: the yield's activity id comes from the caller-supplied
route session parameter (`engine_steering.py` via `_calling_session`), so any
engine call in the same universe can force an activity to `WAITING_ON_YOU` and
end its run. Owner-scoped, not cross-user.

## Orphan-ready incident, 2026-10-06

Run a8248a4f0ad54349 failed before inference at 04:59:36Z on the owner's
subscription. Turn 57cadc0f43974776aa19cd319df207ce remained ready with zero
rounds; the server tree stayed alive and Stop could not reach it. The orphan
repair adds task-lifetime claims, recovery, failure delivery, and refuses a
native-only activity start before creating it. It does not lift the native
execution guard. Remaining work is a native pre-tool fence covering ALL native
tools, with owner-request yield completion and process/seat release proved on
Linux, then removal of both admission and per-round refusals. Polling/cancelling
alone cannot prove that no further effects occurred after yielding.
