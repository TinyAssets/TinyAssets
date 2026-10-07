# Design: agent access controls

## D1. One readback: `read_graph target=access`

A new module, `tinyassets/api/agent_access.py`, provides `read_access(universe_id)`.
It composes existing reads and adds no store:

| key | source |
|---|---|
| `channels` | `cloud_connections(action="list")`: every connection granted to this universe, with `access` `exact`/`full` |
| `channel_consents` | `effector_consents.list_consents` (active rows, every sink) |
| `workspace_consents` | the same list, parsed the way `connections` already parses it |
| `spend_allowances` | `load_provider_assignment` for this universe, owned by the caller: each accepted model source with `model_scope`, `model_ids`, and `cost_caps` (`null` means free models only) |
| `waiting_requests` | the universe's pending requests with `origin` and `withdrawable` |
| `standing_decisions` | `list_suppressions` ("don't ask again") |
| `how_to_change` | the exact verbs for grant, revoke and withdraw |

Owner gate: `pending_requests._owner_gate` (an explicit `admin` ACL row for
the authenticated principal on this universe, uniform not-found otherwise).
It runs before any section is read. The served surface pins `graph_id`,
and the connector passes `graph_id` through the same gate.

## D2. Changing one channel: `source_channel action=revoke`

`api/source_channel.py` gains `_revoke_sink`, dispatched behind the existing
single owner gate. It takes `{channel_type|sink, destination}`, calls
`revoke_consent`, then reads `is_consent_active` and returns it as
`active: false`. The reply is `revoked`, or `not_held` when there was no
active row. `source_code` is refused because it is not a consent.
Revoking a `workspace` consent is allowed. The agent cannot grant one, but
giving one back only narrows the owner's authority.

The served `source_channel` wrapper admits `approve` and `revoke`. Approve
keeps its workspace and `source_code` refusals. Both actions bind the same
least-privilege `("write",)` identity and pin the universe.

## D3. Why not `get_policy`/`set_policy`

`storage/source_channel_policy.py` is read by exactly one function,
`apply_auto_approval_policy`, and that function has no callers. Since
`sandboxed-code-node`, approval gates no run. Serving `set_policy` would
report `policy_set` for a setting that nothing enforces. Rule 8 forbids
that. The consent row is the channel policy that enforcement reads, so
D2 exposes that.

**Resolved 2026-09-25: DELETED, not rewired.** Keeping it off the served
surface left the same lie on the connector, where the owner's own chatbot could
still set a policy and read it back. A rewire was the other option and it
contradicts an approved principle -- ADR-013 (founder-approved 2026-08-30):
authorship, not host approval, decides whose code runs, and the OS sandbox
bounds what it touches. There is no enforcement point left for an approval mode
to reach. So `set_policy`, `get_policy`, `apply_auto_approval_policy` and the
store are gone, and an unknown operation now names the two that exist. Any
future per-channel policy is a new authority change with its own proposal, not
a revival of this one.

## D4. Withdraw a stale request

Storage (`storage/pending_requests.py`):
- A new column, `origin TEXT NOT NULL DEFAULT 'agent'`, added to `_SCHEMA`
  and `_ADDED_COLUMNS`. `create_request(..., origin=)` and
  `request_from_user(..., origin=)` take it as a keyword. It is never read
  from the payload, so an asker cannot choose it. Onboarding's model
  bootstrap passes `origin="platform"`.
- `withdraw_request(udir, request_id, reason)` runs one `UPDATE ... SET
  status='withdrawn' WHERE request_id=? AND status='pending' AND
  origin='agent'`. The single statement means a racing answer and a
  withdrawal cannot both win. It writes no suppression, so the agent may
  ask again. On a miss it reads the row and names why: `not_found`,
  `already_resolved` (with the status), or `not_withdrawable` (with the
  origin).

API: `pending_requests.withdraw_request(universe_id, payload)` goes through
`_owner_gate` and refuses the synthesized `sys_connect_llm`. It returns the
row with its new status, and `still_on_rail: false` confirmed by
`list_pending`. After a withdrawal, `answer_request` reports
`already_resolved, status=withdrawn`.

## D5. Isolation

Every read and write resolves the universe from the pinned graph (served)
or `graph_id` plus an admin ACL row (connector). A caller without admin on
universe A gets `not_found`/`auth_failed` for the read, the revoke and the
withdraw. A's consents and requests remain unchanged, and the tests prove it.

## Residuals (tracked, not in this slice)

- Pending requests written before this change default to `origin='agent'`.
  So a legacy onboarding confirmation is withdrawable. That is recoverable:
  re-running model setup raises it again.
- Per-automation provider bindings (`ProviderWorkBinding.allowed_operations`)
  are not yet in the readback. Automations stay readable through
  `target=automations`.
- Narrowing a `full` connection back to `exact` is not offered. Removal
  already has its person-confirmed `remove_http` ask.
