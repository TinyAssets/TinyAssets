## Why

The device-first plan of record (the Claude doc "TinyAssets — Agents Everywhere, Device-First:
Architecture Plan", Code spec tab) sets the target architecture. ADR-023 to ADR-031 hold its
decisions. Its build slices land in dependency order. The founder's rule is to build the
correct overall shape from the start, so nothing is built in a wrong shape and rebuilt later.

## What Changes

- ADR-023 (the core loop runs in a loop-worker pool outside every box) and ADR-030 (the box
  holds only tools, user code and owner files; owner requests are served on the trusted side)
  are accepted, as the founder decided on 2026-10-26 (plan A8 and A9, both "Decided
  2026-10-26"). #5324 deferred them on the founder's 2026-10-26 answer "Narrow it". That
  deferral is withdrawn by the founder's 2026-10-27 instruction to build the plan of record:
  > "review the doc and build only the correct architecture even if that leaves things
  > broken until the rest is done" (founder, 2026-10-27)

  Earlier, on 2026-10-26, the founder had asked:
  > "I thought we were going to fix it so that each box doesn't build the full app server?"
- How owner isolation holds once serving leaves the box is open:
  `docs/concerns/2026-10-27-no-slice-moves-serving-out-of-the-box.md`, a founder decision at
  S2a.
- The build order in `tasks.md` is the plan's Build slices table.
- Registry rows S30 to S35 and N12 are added, each with its planned guard; each slice turns
  its guard on. S33, S34 and S35 read drifted, because their second path is live today
  (inferred liveness in `live_view`, `app_room.cas`, the loop in the box); the rest read
  unbuilt.

## Capabilities

### New Capabilities

Each slice adds its own capability, with the requirement IDs from the plan's Workstream specs.

### Modified Capabilities

## Impact

`docs/decisions/`, `docs/architecture.md` and `scripts/check_shapes.py` (registry rows only).
