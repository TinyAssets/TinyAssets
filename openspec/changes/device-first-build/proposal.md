## Why

ADR-023 to ADR-031 set the device-first architecture: the core loop, the
messaging ledger, conversations, rooms and the board run on the trusted side,
and the box keeps only tools, user code and owner files. Since C6, though,
each owner's box serves everything: `owner_daemon` builds the whole app
server, so owner MCP, app and `/api` requests, the converse handler and the
loop all run inside the box. The router forwards bytes.

Checking each slice against the code (2026-10-26) showed that the loop
worker (ADR-023), the mailbox (ADR-025) and the shared revision primitive all
need the trusted side to answer some owner requests itself, and no slice
built that. #5317 added the first such answer: the router serves the MCP
events methods. Without one shared door, each feature would parse requests
on its own.

## What Changes

- **A trusted front first.** The router reads MCP tool calls, answers the
  tools registered as trusted, and forwards the rest to the box. The events
  methods fold into it. Each later slice registers its tools there.
- **The build order changes** (founder, 2026-10-26), as listed in `tasks.md`:
  the trusted front, then the ledger, collections, outside identity and
  events, then owner tools leave the box one by one (ADR-030), then the loop
  worker (ADR-023).
- **Trusted per-owner stores are broker-held SQLite**, like
  `.broker/accounts/<sha>/policy.db`. One collection store holds room rows and
  message bodies. No new store service.
- **The loop worker's shape is approved** for when it is built: its own
  deploy unit and OS user, a box tool door for the four tools and extension
  points, the journal and leases in a trusted per-owner area, and resume from
  the journal with unrecorded tool outcomes left unknown.

## Capabilities

### New Capabilities

Each slice adds its own capability and registry row (S30 to S35, N12) with
its guard, in the PR that builds it.

### Modified Capabilities

## Impact

- **Router** (`owner_router.py`): it parses MCP bodies for the first time.
- **Broker:** new stores.
- **Box harness:** it shrinks.
- **`docs/architecture.md`:** rows S3 and S8 say where the loop and serving
  run today, and that they move.
