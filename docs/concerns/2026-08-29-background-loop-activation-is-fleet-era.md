---
severity: P2
title: The background loop cannot run on the current shape
filed: '2026-08-29'
summary: its activation layer is fleet-era; the "24/7 background self" has been dead since 2026-08-07
---

# P2 - The background loop cannot run on the current shape: its activation layer is fleet-era

**Filed:** 2026-08-29
**Severity:** P2 -- the founder's "24/7 background self" has been dead since 2026-08-07; no user is
harmed, but a promised surface is silently off and its refusals accrue every pump tick.

## Finding

The served carrier (`tinyassets/background_served_provider.py:912-1039`) is current-shape: it mints
its own work binding from the universe's serving assignment and runs on the user's deposited
credential. Everything that decides WHETHER it may run is not:

- The preflight and the later transactional fences pin the executor to a boot-unique runtime
  (`worker_id` changes every boot, `tinyassets/runtime/assigned_queue_consumer.py:129`) and to
  the provider recorded at preparation, not the universe's current assignment
  (`tinyassets/cloud_automation_runtime.py:233`, `tinyassets/storage/provider_work_authority.py:2709`).
  The continuation rebind forbids changing `provider_binding_id`
  (`tinyassets/storage/cloud_automation_continuation.py:352`). Result: `activate_error:
  ...trusted cloud worker assignment is absent or mismatched`, every tick, and a daemon restart
  can never heal it.
- The raise at `cloud_automation_runtime.py:511` aborts the whole pump for a principal instead of
  recording one refusal and continuing (safe to change: reads only up to that point).
- Automation issuance depends on a host manifest, `TINYASSETS_REQUESTER_PROVIDER_ENROLLMENTS_JSON`
  (`tinyassets/api/cloud_automations.py:513`, `tinyassets/provider_work_enrollment.py:20`) --
  authority the host supplies, not the user.
- The heartbeat scheduler is a second, independent dead path: its thread only starts under the
  dark inbound flag, it emits actor `scheduler:<id>` where the run function accepts only
  `universe:<id>` (`tinyassets/scheduler.py:632`, `tinyassets/universe_server.py:1259`), and
  registration accepts a caller-supplied `owner_actor` defaulting to `anonymous`
  (`tinyassets/api/runtime_ops.py:365`). Users can register schedules that can never fire.
- The retired host fleet is still declared: `deploy/compose.yml` `worker*` services,
  `tinyassets/cloud_worker.py` (2,171 lines), its healthcheck, and the canonical spec still
  requiring them (`openspec/specs/daemon-runtime-and-dispatch/spec.md:57,77`).

Founder principle recorded the same day (now ADR-009): **nothing runs unless
it lives inside a user's universe, under that user's control.**

## Shape that fixes it

Not a patch. An OpenSpec change (authority + storage shape): user-owned automations = a schedule or
trigger the owner creates in their universe, executed by the daemon on that universe's CURRENT
serving assignment through the same admission and budget as a foreground `run_graph`, controllable
(pause/resume/delete) from the owner's surface, with no host manifest and no executor-identity pin.
The fleet-era activation layer (continuations, audiences, runtime pinning, enrollment manifest) and
the fleet itself retire together. The 16 `background_branch_bindings` + 9 `cloud_automation_controls`
still authorizing the staging subject are retired, not migrated -- they are generated consequences of
preparation (Codex, finding 5).

Evidence: `docs/reviews/2026-08-29-codex-background-loop-shape.md`.
