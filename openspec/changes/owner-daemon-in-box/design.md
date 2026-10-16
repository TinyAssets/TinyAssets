# Design: the owner's daemon in the owner's box

## Shape check against the research (required for every slice)

| Research rule | This design |
|---|---|
| The shared door authenticates, maps the identity to an instance, forwards, and never reads tenant data | Router: identity → `owner_id` → box socket; no owner file or database path in the shared process (D7 guard) |
| Isolation is infrastructure, not shared-code filters | The gVisor box per owner with a null netns and its own uid and quota; no cross-owner checks in shared code |
| Secrets stay outside the instance; egress goes through a proxy | Broker credential and egress proxy; the box has no raw secrets (C6 P5) |
| The clock is outside, the rows are inside | The box keeps its schedule rows; the shared index keeps only `next_wake_at` |
| A flag lives with its reader and has one writer | Pause, heartbeats and the ledger are box state; account suspension is router state |
| Durable truth survives the instance | The box tree on an XFS volume, plus off-host snapshots (C6 P6/S27) |
| Cross-tenant views are published data, not reads of private stores | Commons publishing copies out at publish time; the listing reads only the caller's own box |

Any finding in review that can't be expressed as a defect in the router, the
broker proxy or the waker belongs inside one box. It is fixed there, never by
adding a check in shared code.

## D1. The owner daemon

- **Process:** the same image and code, with entrypoint `tinyassets
  owner-daemon --data /home/owner/data --socket /run/owner/app.sock`. It runs
  inside the owner's gVisor box under the owner's uid. `data_dir()` is the
  owner's root. It serves the existing ASGI app (`/app/api/*`,
  `universe_server` MCP routes, `/api` handlers) on the unix socket, not TCP.
- **Single owner:** the code paths stay as they are. With only one owner's
  rows present, the shared root databases (`.tinyassets.db`, `.runs.db`,
  `.automations.db`) become that owner's databases, and the tick sees one
  owner. No multi-tenant filter is added or needed.
- **Agent loop:** runs in-process, so tools are local filesystem and exec
  inside the box (the existing `universe_tools`). The exec bridge is deleted.
- **Outbound:** the null netns is kept. Model calls, connections and
  webhooks-out go to the broker over a socket bound into the box (one per
  box), which injects credentials. Provider CLIs run in the box, as on main.
- **What it gets from the shared layer:** the authenticated `owner_id` (the
  router sets it, and the box trusts only the router's per-box secret), the
  plan tier, and published commons reads, all through the broker socket.

## D2. The router (the shared daemon, reduced)

- Keeps the public doors (Cloudflare tunnel → `/mcp`, `/app`, `/api`,
  `/hooks/inbound`), WorkOS sign-in, MCP OAuth and API keys.
- **Per request:** authenticate → `owner_id` → box (broker `owner_machine`)
  → `ensure_awake` → stream the request to the box socket with headers
  `X-Owner` and `X-Box-Secret`. Streaming MCP and SSE pass through unchanged.
  The router parses no owner payload.
- Reads no owner data. Commons and published listings come from its own
  published store.

## D3. Lifecycle (boxhostd)

- boxhostd supports one persistent sandbox per owner, running the owner
  daemon with its socket bind-mounted to `/run/boxhostd/<uid>/app.sock`.
- It wakes on a router request or on the waker. It sleeps after N minutes
  idle with no running turn, and the daemon flushes on SIGTERM.
- It restarts on a crash, with backoff.
- Memory: each box gets a cgroup cap. A cap on concurrently awake boxes
  queues wakes (8 GB host).

## D4. Waker

- At the end of every tick the box reports its `next_wake_at` to the
  broker. The shared waker wakes boxes when it comes due.
- Suspended accounts (billing or abuse) are never woken.
- Delivery is at least once, so the tick is idempotent, as today.

## D5. Billing

- Stripe webhooks land at the router. Plan state is kept in the shared
  billing store, keyed by `owner_id`.
- The box reads its tier from the broker. It never holds a Stripe secret.

## D6. Migration (one cutover, after a verified full backup)

For each admitted owner:

1. Start from the owner's box tree. The C6 move already copies center trees
   in.
2. Move that owner's per-center platform files (conversation, pending, runs,
   pause, ledger and so on) into the box root.
3. Extract that owner's rows from each shared root database into the box
   root's database of the same schema. The key is the owner principal or the
   owner's center ids.
4. Verify counts and integrity, then remove the host per-center directories.

Any row or file the census cannot attribute to exactly one owner refuses the
cutover. The `--archive` rehearsal on the private production copy must:

- show the per-owner row counts summing to the original totals;
- show each owner daemon serving that owner's history, pending requests and
  pause after the move;
- prove a restore of the paired backup returns today's layout.

## D7. Guard and deletions

- Guard test: the router package imports no owner store module and opens no
  path under `data_dir()` other than its own shared stores.
- Deleted: per-center host dirs; `box_tools` exec bridge; `file_binding`
  owner routing; daemon-side `_call_writer` placement; cross-owner listing
  filters; multi-owner tick loop.

## Open questions for the founder

1. Is a co-admin of a shared center refused, with sharing done by
   publishing? The research's default is refusal.
2. Does each owner's daemon run the full feature set from the first cutover,
   or does the cutover drop features not yet ported (e.g. fantasy domain
   readers)? Proposed: drop, and list them.
