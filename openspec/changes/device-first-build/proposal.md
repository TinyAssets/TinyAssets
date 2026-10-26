## Why

ADR-023 to ADR-031 set the device-first architecture. Checking each slice
against the code (2026-10-26) showed three things the plan had missed:

- **Serving and the loop run in the box.** Since C6, each owner's box runs
  everything: `owner_daemon` builds the whole app server, so owner MCP, app
  and `/api` requests, the converse handler and the loop all run inside it.
- **Moving serving out is large and costs isolation.**
  - The served code (about 66,000 lines) uses about 113 SQLite stores on the
    owner volume.
  - Only the box host's root can mount that volume, and SQLite locking
    across gVisor is unsafe, so every store would have to move before any
    code could follow.
  - A trusted server shared across owners would turn owner isolation back
    into an app check, which C6 replaced with the OS (Direction 5).
- **The box can rewrite its own authority.** The agent's shell runs as the
  same user as the box's app server, so it can rewrite its own action rules
  and approvals (`.action-policy.db`), effector consents and outside-client
  grants. ADR-019 says these live outside the box; registry row N10 lists
  "C8 action rules stay in-box" as its second path.

## What Changes

Founder decisions, 2026-10-26:

- **Accepted:** ADR-024 to ADR-029 and ADR-031.
- **Deferred:** ADR-023 (the loop leaves the box) and ADR-030 (the box
  holds only tools) stay Proposed. They are revisited with metering
  receipts (N12) at real user counts. ADR-023's context is corrected now.
- **Each owner's app server stays in its own box.** Only two kinds of state
  leave the box:
  - what the box must not control: authority, N10;
  - what spans owners: the ledger, rooms and events, ADR-025.
- **Trusted per-owner stores are broker-held SQLite**, like
  `.broker/accounts/<sha>/policy.db`.
- **Box cost is cut by measuring** what the box loads and then slimming it,
  not by relocating serving.
- **The build order is `tasks.md`.** Slice names follow the plan document's
  build-slice table; they are not registry row IDs.

## Capabilities

### New Capabilities

Each slice adds its own capability and registry row, with its guard, in the
PR that builds it.

### Modified Capabilities

## Impact

- **Broker:** owner authority stores move here, and so do the ledger and
  collection stores.
- **Router:** answers the few cross-owner MCP requests registered with it.
- **`docs/architecture.md`:** rows S3 and S8 say where the loop and serving
  run today.
