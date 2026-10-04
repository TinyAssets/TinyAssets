# The control plane is the only always-on layer: one owner tick, decayed proactive cadence

**Storage shape + authority.** Target-architecture slice S8b (design D7, D11;
`openspec/changes/target-architecture`, #4263). One cross-family refute is
owed before landing.

## Why

The approved target shape (founder, 2026-10-01: "approved, go with the sealed
box design"; 2026-10-02: "do things correct the first time") puts every
command center in a sealed box that is **awake only while acting**. That only
holds if nothing inside a box keeps time, and if the control plane can decide
*when* to wake a box without reading the box. Today:

- Timers are scattered across the serving process with no inventory. A new
  periodic loop can land anywhere, including in code that will run inside a
  box.
- The automation pump is the de-facto trigger table, but no lease seam exists,
  so S8a's owner handover has nothing to fence.
- The harness's always-on proactive wake (harness §4.5: idle research, 4 h,
  08:00–22:00, on for a new user) has no scheduler, and if built as a fixed
  4/day it costs a dormant account ~1.1% box duty forever. D7 sets the floor at
  ~0.1% through engagement decay.

## What changes

1. **Inventory.** Every clock-driven loop in `tinyassets/` is classified in
   `tests/control_plane_timer_inventory.py` as `control_plane`, `call_scoped`,
   `delete`, `client` or `box` (67 sites). A test scans the package and fails on an
   unclassified loop, a stale entry, or any `box` entry (forbidden). See design
   §Inventory.
2. **Lease seam.** `tinyassets/control_plane/lease.py`: `OwnerLease`
   (`generation`, `held()`, `check()`, `proof`, `verify()`) plus
   `verify_lease_proof`, with `SingleProcessLease` installed
   today (generation 1, always held). S8a replaces it via
   `install_owner_lease`. The owner tick (assigned-queue consumer poll) pumps
   automations and control-plane triggers only while the lease is held.
3. **Trigger table.** `.control_plane.db` at the data root (platform state,
   D8a row 4): `triggers` (one row per control-plane trigger; today kind
   `proactive`) and `trigger_fires` keyed `(trigger_key, due_at)` — the fence.
   User-declared schedules stay `.automations.db` rows, pumped by the same tick.
4. **Coalescing.** At most one pending fire per trigger: a due fire whose
   previous run is live waits (single flight); windows it outlives collapse
   onto the latest and are counted.
5. **Engagement-decayed cadence as a policy.** 4/day engaged → 1/day after 7
   days without owner interaction → weekly after 30 → back to engaged on the
   next interaction. One config default (`cadence.DEFAULT_POLICY`), deploy
   override `TINYASSETS_PROACTIVE_CADENCE`, per-command-center owner override
   stored on the trigger row. **The founder has not confirmed the decay
   values**; equal periods turn decay off with no code change.
6. **Wake semantics.** A fire calls the handler registered for its kind
   (`wake.py`), which starts work on the run path and returns a run id; under
   the box it is `BoxProvider.bind` + `ensure_awake`. A kind with no handler is
   never fired, so nothing is claimed for work that cannot run.
7. **Platform state only.** The tick reads trigger rows, the owner's stored
   timezone and run status; a test audits every file the tick opens and fails
   on any path inside a command center's directory.
8. **Metrics.** Due-vs-fired lag (proactive fires and automation attempts),
   coalesced count, decay-state counts and fire outcomes:
   `python -m tinyassets.control_plane metrics`.

## Out of scope (owned elsewhere)

- The generation-fenced lease, transactional re-checks on owner stores and
  `owner_generation` on `agent_turns` — **S8a** (lane `turn-handover`).
- The proactive research turn itself, its read-only capability and the
  Scheduled-view surface that lets the owner edit/disable the cadence —
  **harness D3**. It registers the `proactive` wake handler and enrols command
  centers (`ensure_proactive_trigger`).
- Moving the per-universe consumer heartbeat and `.pause` sentinel out of the
  command-center directory — the **cutover** (#4262, D8a row 2).
- Deleting the `delete`-class loops (host-pool client, fantasy_daemon
  heartbeat) — dark-code lanes.

## Acceptance

- Dormant-account duty, measured by the real tick on a simulated clock
  (`tests/test_control_plane_duty.py`, awake time per fire calibrated to the
  1.1% figure): engaged 4.0/day = **1.10%**; cooling 1.0/day = **0.275%**;
  dormant (day 31+) 0.136/day = **0.037%**; 90-day average after one
  interaction = 0.18%.
- No double fire across a restart (`test_no_double_fire_across_a_restart`,
  `test_a_claim_that_never_started_is_settled_lost_not_replayed`).
