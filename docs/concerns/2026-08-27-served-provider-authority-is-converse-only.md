---
severity: P2
title: Served provider authority is converse-only
filed: '2026-08-27'
summary: the live paste-inference call is refused every time (`provider request source is not trusted`); widening it is the rejected keystone, and the agent-asks rail needs no widening
---

# A served provider call may only ever be `converse`, so paste-inference cannot run

**Filed:** 2026-08-27
**Verified:** 2026-08-27 live, against deployed `#2604` on `tinyassets.io`
**Severity:** P2 — nothing is broken; a feature that shipped cannot function, and
the fix is an authority change that has been rejected before

## The finding

The paste-and-infer deposit (`tinyassets/api/connection_inference.py`, PR #2604,
live) asks the universe's own model to identify a pasted credential. **On the
live surface that call is refused every time.** Live test 2026-08-27 through
`tinyassets.io/mcp/app`: pasted a token plus a hostname hint, and the app
reported *"could not identify this service"*. The daemon log gives the cause:

```
resolve_connection: provider call failed
  File "/app/tinyassets/provider_assignment.py", line 1204,
       in authorize_served_provider_call
PermissionError: provider request source is not trusted
```

Two gates in `authorize_served_provider_call` make this structural, not a bug in
the caller:

| Line | Rule |
|---|---|
| `provider_assignment.py:1187-1204` | `accepted_request_sources` requires `tool_name` to be `converse` (or `slack_event`). A `write_graph` operation is never in the set. |
| `provider_assignment.py:1205` | `if role != "writer" or operation != "converse": raise` — **served authority is converse/writer only, by explicit design.** |

So a served model call that is not a conversation turn cannot be made at all. The
inference path degraded correctly — it falls back to the explicit fields, which
the live test confirmed — but its whole purpose is unreachable in production.

## Why this is not simply "widen the allowlist"

Adding `resolve_connection` to those gates is a change to **who may spend the
user's provider authority and for what**, which is the keystone area
`AGENTS.md` puts behind a spec and a cross-family gate, and where repeated
attempts have already been rejected (see [[background-run-provider-authority-keystone]],
[[consumer-budget-keystone-rejected-merged-inert]]). It should not be done to
rescue a feature that has a better shape available.

## The better shape already exists

The founder's later direction removes the need entirely: **the agent asks for
the credential it needs** (PR #2606, `tinyassets/api/pending_requests.py`). The
agent composes that request *inside an ordinary `converse` turn*, which is
exactly the authority this gate does permit — and it knows the endpoint it is
about to call, so there is nothing to infer and no second model call.

That makes the live failure a useful result rather than a setback: it says the
inference path is not just less good than the ask path, it is the one the
platform's own authority model does not allow.

## What to do

1. Prefer the request rail (#2606) as the path that actually reaches a user.
2. Leave the paste box as the manual fallback it already degrades to, and do
   **not** widen provider authority for it.
3. If inference is ever wanted server-side, it needs its own proposal against
   the authority model, not an allowlist edit.

## Related

- `2026-08-27-codex-paste-deposit-review.md (archived in git)`
- `docs/concerns/2026-08-27-credential-deposit-refusals-are-unobservable.md`
- `docs/concerns/2026-08-27-no-reachable-remove-for-http-connections.md`
