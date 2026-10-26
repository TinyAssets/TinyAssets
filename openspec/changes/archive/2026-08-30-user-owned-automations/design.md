## Context

Evidence: the 2026-08-29 fleet-era background-activation review (resolved when
#5182 deleted `tinyassets/background_served_provider.py` and
`tinyassets/runtime/assigned_queue_consumer.py`) and the two reviews it cites. What works today: `background_served_provider.py:912-1039` mints a work binding
from `provider_assignments` + `llm_credential_custody` and runs on the user's credential; the
daemon's consumer (`assigned_queue_consumer.py`, flag `TINYASSETS_ASSIGNED_QUEUE_CONSUMER`) claims
and runs. What does not: the activation preflight and the later transactional fences require an
`author_runtime_instances` row whose `worker_id` equals this boot's consumer id and whose
provider equals the provider pinned when the automation was prepared
(`cloud_automation_runtime.py:233`, `storage/provider_work_authority.py:2709`,
`daemon_registry.py:933-960`); `consumer_id` is boot-unique (`assigned_queue_consumer.py:129`);
the continuation rebind forbids changing `provider_binding_id`
(`storage/cloud_automation_continuation.py:352`). The scheduler emits actor `scheduler:<id>`
where the run function accepts only `universe:<id>` (`scheduler.py:632`,
`universe_server.py:1259`) and registration accepts `owner_actor` defaulting to `anonymous`
(`api/runtime_ops.py:365`).

## Goals / Non-Goals

**Goals:**
- The owner of a universe can create a recurring or triggered branch run, see it, pause it,
  delete it -- from the app or the connector -- and it runs on their own subscription.
- A daemon restart, an identity migration, or a provider switch never wedges an automation:
  authority is derived at run time from what the universe currently has.
- One principal's failure is one recorded refusal, never an aborted pump.
- No host-supplied authority, model, or actor anywhere on the path (PLAN principle 2026-08-29).

**Non-Goals:**
- Multi-daemon distribution. One daemon runs everything; the fence is per automation, not per
  executor.
- Migrating existing controls/bindings across identities. They are retired and re-created by
  the owner.
- Keeping the fleet alive "just in case". Git holds it.

## Decisions

- **D1 -- Authority is derived, never pinned.** Each due run resolves the universe's CURRENT
  `provider_assignments` row and custody exactly as a foreground turn does, then launches through
  the served carrier. Rationale: the only stable identities in this system are the universe and
  its owner; executor and provider identities rotate (Codex finding 1). Corollary: the
  `author_runtime_instances` audience check and preparation-time provider pin have no role and
  are deleted with the activation layer.
- **D2 -- The fence is `(automation_id, due_at)`.** A `BEGIN IMMEDIATE` insert of that pair claims
  the run; a restart that re-computes the same `due_at` finds the row and skips. This is the
  same TOCTOU-safe pattern as `_engine_run_admit`.
- **D3 -- Owner = authenticated principal with admin ACL, checked at registration AND at each
  run.** If the owner lost admin between ticks, the run is a recorded refusal and the automation
  is auto-paused with a reason the owner can read. The run actor is `universe:<id>` acting for
  that owner -- the same principal shape the foreground path uses.
- **D4 -- Fail loud at registration.** `write_graph target=automation operation=create` and
  schedule registration return `automation_unavailable` / `scheduler_unavailable` with the
  reason (flag off, no serving assignment, no admin ACL) rather than storing a row that can
  never fire. Hard Rule 8; Hard Rule 4 is satisfied because refusal IS the safe default.
- **D5 -- Recorded refusals, one table.** `assigned_queue_refusals` stays as the single place a
  skip lands, keyed by automation, with the reason; the owner's surface reads it.
- **D6 -- Retirement order.** (1) fleet out of compose + droplet reconciled; (2) fleet code,
  workflow, tests, scripts, spec REMOVED; (3) new automation path built and proven live on the
  founder's universe; (4) activation layer and old-principal rows retired; (5) pipeline ships
  compose. Each step lands alone.
- **D7 -- Budget and admission are the foreground ones.** No separate background budget; the
  per-universe caps already in the carrier (`TINYASSETS_ASSIGNED_QUEUE_UNIVERSE_MAX_*`) remain the
  runaway guard.

## Risks / Trade-offs

- A runaway cadence spends the owner's subscription: mitigated by D7 caps and by refusing
  cadences below a floor at registration.
- Deleting ~15k lines of shared authority code in step (4) risks breaking the foreground served
  path: step (4) waits for step (3)'s live proof and its own Codex refutation; the served carrier
  is kept, only the activation layer goes.
- Owners lose old automations: nine controls exist, all the founder's, all dead since Aug 7; the
  founder re-creates the ones still wanted.
- `no_serving_assignment` carries two causes -- no ready assignment, and a ready assignment on an
  open `api_key_http` provider -- so a client cannot branch on it. The owner-facing sentence
  covers both for a human reader, but an app wanting to route "not provisioned yet" to
  provisioning and "open provider" to connect-a-subscription has nothing to switch on. Accepted
  for now because the two remedies overlap and the surface is pre-launch; splitting it is a
  distinct token and a spec change, not a copy edit. Raised by the core builder who introduced
  the second cause (2026-08-29).
