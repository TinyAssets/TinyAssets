---
severity: P3
title: ta effects can finish after bash returns
filed: '2026-10-04'
summary: Closing the ta socket does not settle an in-flight external call; its outcome can be lost to the agent.
---

Source: PR #4439 cross-family review, finding 8 (verbatim):

> DISAGREE_CONCERN, P3: `JailBridge.__exit__` does not cancel or wait for an in-flight call. An external write can finish after bash has already returned, with no record the agent can see. This fits the deferred "durable provenance" item and should be recorded as a concern.

Verified 2026-10-04 in `tinyassets/ta_capabilities.py`: bridge exit closes the
listener and removes the socket directory, but a dispatched coroutine and its
effector worker can still complete. Killing or timing out bash therefore does
not prove the external action was cancelled. Blind retries can duplicate writes.

Follow-up: durable per-call intent and outcome records, with an unknown-outcome
state visible on the agent's next turn. Closing a bridge must revoke admission
of new calls and account for the active call without claiming that cancellation
undoes an external effect. Prove with a delayed write that outlives bash and is
reported exactly once to the initiating agent. This is deferred provenance work,
not a claim that the current socket lifecycle settles effects.
