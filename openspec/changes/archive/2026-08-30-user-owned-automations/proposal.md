## Why

The founder's "24/7 background self" has been dead since 2026-08-07: every pump tick refuses with
`trusted cloud worker assignment is absent or mismatched` and the refusal table grows. Two reviews
on 2026-08-29 (Claude Explore, then a Codex ADAPT refutation --
`2026-08-29-codex-background-loop-shape.md (archived in git)`) agree on the cause: the execution
carrier already runs on the universe's own serving assignment and deposited credential, but the
layer that decides WHETHER it may run is fleet-era -- it pins a boot-unique executor runtime and
the provider recorded at preparation, aborts the whole pump on one principal, and issues
automation authority from a host manifest (`TINYASSETS_REQUESTER_PROVIDER_ENROLLMENTS_JSON`).
The heartbeat scheduler is a second dead path: its thread never starts, its actor never matches,
and registration accepts an anonymous owner. The retired host fleet is still declared in
`deploy/compose.yml` and required by the canonical spec.

The founder's principle, recorded the same day (PLAN.md, Cross-Cutting Principles): **nothing runs
unless it lives inside a user's universe, under that user's control.** A patch to the resolver
cannot satisfy it (Codex finding 1); the shape has to change. Now, because Google sign-in went
public on 2026-08-29 and a promised surface that silently does nothing is a live-user defect.

## What Changes

- **An automation is a universe-owned row** its owner creates, pauses, resumes and deletes from
  their own surface (`write_graph target=automation`): branch to run, trigger (cadence or event),
  inputs, desired state. Ownership is the authenticated principal with an admin ACL on the
  universe -- never a caller-supplied actor, never anonymous.
- **Execution derives authority per run from the universe's CURRENT serving assignment and
  custody**, through the existing served carrier, under the same admission and budget as a
  foreground `run_graph`. No executor-identity pin (the daemon's id is boot-unique by design),
  no provider pinned at preparation, no host enrollment manifest. The attempt fence is keyed by
  `(automation_id, due_at)` so a restart cannot double-fire and cannot wedge.
- **Every skip is a recorded refusal, never an abort**: one principal's problem never stops the
  pump for the others (Codex finding 2 -- safe, the pre-activation path is read-only).
- **Schedules follow the same rule**: registration requires the authenticated owner, refuses with
  a named error when the scheduler cannot fire, and the tick lifecycle is split from the inbound
  event flag. The run actor is the universe principal.
- **The host fleet is retired for real**: the four `worker*` compose services,
  `tinyassets/cloud_worker.py`, its healthcheck, `reconcile-stale-fleet.yml`, the
  `DEFAULT_HOST_USER` import in `fantasy_daemon`, their tests and scripts, and the spec
  requirements for a supervisor/healthcheck. Then the pipeline may ship `deploy/compose.yml`
  (after reconciling the droplet's live copy -- `docs/concerns/2026-08-27-deploy-drops-compose-sync.md`).
- **The fleet-era activation layer retires after the new path is proven live** (continuations,
  executor audiences, runtime pinning, enrollment manifest). The 16 `background_branch_bindings`
  and 9 `cloud_automation_controls` still authorizing the staging WorkOS subject are explicitly
  retired, not migrated -- they are generated consequences of preparation (Codex finding 5).
- Supersedes `execute-assigned-queue-consumer` (archived with this proposal): its consumer and
  served carrier are kept; its "preserve the existing fleet" non-goal is inverted.

## Capabilities

### New Capabilities
- `user-owned-automations`: universe-owned, owner-controlled scheduled/triggered branch runs
  executed on the universe's current serving assignment with no host prerequisites.

### Modified Capabilities
- `daemon-runtime-and-dispatch`: the supervisor/healthcheck/fleet requirements are removed; the
  daemon's own consumer executes due automations on the current assignment.
- `background-branch-execution-authority`: execution authority derives from the current
  assignment and custody at run time; no runtime-identity or preparation-time provider pin.

## Impact

- Code: `tinyassets/runtime/assigned_queue_consumer.py`, `tinyassets/background_served_provider.py`
  (kept, re-pointed); `tinyassets/scheduler.py`, `tinyassets/api/runtime_ops.py`,
  `tinyassets/api/cloud_automations.py`, `tinyassets/cloud_automation_setup.py`,
  `tinyassets/universe_server.py` (lifecycle); deletions listed above.
- Storage: one automation table (owner, universe, branch, trigger, inputs, desired_state,
  retired_at); attempt fence `(automation_id, due_at)`; existing `cloud_automation_*` and
  `background_branch_*` tables retire with the activation layer.
- Deploy: `deploy/compose.yml` loses `worker*`; the droplet's live compose is reconciled first.
- Env: `TINYASSETS_REQUESTER_PROVIDER_ENROLLMENTS_JSON`, `TINYASSETS_WORKER_*`,
  `UNIVERSE_SERVER_HOST_USER` leave the catalog.
- Specs: delta files for the two modified capabilities; the fleet requirements are REMOVED.
