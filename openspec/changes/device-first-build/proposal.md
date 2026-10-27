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
  > broken until the rest is done" (founder, 2026-10-27T01:23Z)

  Earlier, at 2026-10-26T19:22Z, the founder had asked:
  > "I thought we were going to fix it so that each box doesn't build the full app server?"
- Owner isolation once serving leaves the box: owner-bound broker channels (ADR-030,
  founder decision 2026-10-27), built as the "Owner-bound serving" slice.
- The build order in `tasks.md` is the plan's Build slices table.
- Registry rows S30 to S35 and N12 are added, each with its planned guard; each slice turns
  its guard on. S33, S34 and S35 read drifted, because their second path is live today
  (inferred liveness in `live_view`, `app_room.cas`, the loop in the box); the rest read
  unbuilt. The plan's S1 says "listed as unbuilt"; drifted is the registry legend's word
  for a row whose second path is live.
- Deviation: each row's "Lands in" follows the plan's Build slices table (S30, S31 in S3;
  S32, S33 in S4; N12 in M). The plan's own registry table is off by one (S30, S31 in S2;
  S32, S33 in S3; N12 in S4), and no earlier slice builds those rows.

## Capabilities

### New Capabilities

Each slice adds its own capability, with the requirement IDs from the plan's Workstream specs.

### Modified Capabilities

## Impact

`docs/decisions/`, `docs/architecture.md` and `scripts/check_shapes.py` (registry rows only).
