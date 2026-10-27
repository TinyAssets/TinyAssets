## Why

The device-first plan of record (the Claude doc "TinyAssets — Agents Everywhere, Device-First:
Architecture Plan", Code spec tab) sets the target architecture. ADR-023 to ADR-031 hold its
decisions. Its build slices land in dependency order. The founder's rule is to build the
correct overall shape from the start, so nothing is built in a wrong shape and rebuilt later.

## What Changes

- ADR-023 (the core loop runs in a loop-worker pool outside every box) and ADR-030 (the box
  holds only tools, user code and owner files; owner requests are served on the trusted side)
  are accepted, as the founder decided on 2026-10-26 (A8, A9). An earlier commit marked them
  deferred; that reversed a founder decision and is corrected here.
- The build order in `tasks.md` is the plan's Build slices table.
- Registry rows S30 to S35 and N12 are listed as unbuilt, each with its planned guard; each
  slice turns its guard on.

## Capabilities

### New Capabilities

Each slice adds its own capability, with the requirement IDs from the plan's Workstream specs.

### Modified Capabilities

## Impact

`docs/decisions/`, `docs/architecture.md` and `scripts/check_shapes.py` (registry rows only).
