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

### D1 decisions from the slice 1 stop (2026-10-16)

- **Runtime admission.** Today, cloud admission
  (`platform_runtime_provenance`) comes from DigitalOcean metadata over HTTP.
  It gates the app lifespan, the tick and provider authority. Inside a box, the
  verdict comes from the broker instead. boxhostd binds the broker socket only
  into a box the broker admitted. The owner daemon asks it once at startup
  (`WHOAMI` → admitted `owner_id`, executor class `owner-box`) and caches the
  answer as its process admission. With no broker socket, or a refused answer,
  the daemon refuses to start, as it does today with no cloud verdict. No
  network is used, and nothing is fixtured as "cloud".
- **Local tools.** In owner mode, the four tools and `ta` use the existing
  in-box core (`boxes/boxd.BoxdCore` over the owner root) in-process. That is
  the same file and exec API with no remote hop, and the `box_tools` →
  `start_exec` bridge is not used.
- **Loopback.** The `bash`/`ta` engine transport uses loopback TCP inside the
  box's own netns. That is allowed. The rule is no NON-loopback connection.

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

## D8. Shared stores stay shared, owner stores move (review gap 1)

The migration classifies every table of every shared root database:

- **Router store:** identity and WorkOS mapping, API keys and their owner,
  MCP OAuth clients and tokens, inbound hook token → owner, the canary
  account, published commons records.
- **Broker store:** `owner_identities`, `center_admissions`, `box_records`,
  credential custody, billing and plan state.
- **Owner box:** every table whose rows belong to one owner, split by owner
  principal or center id.

The census refuses only on an unknown table, or an owner-table row that
cannot be attributed. Shared tables are not split.

## D9. Router state, the per-box secret and the socket (review gaps 2, 3)

- `/hooks/inbound` and `/api` keys resolve to an owner from the router's
  own maps. That is router state.
- The router resolves owner → box socket only through broker admission
  (`owner_machine`), never from a request field.
- The broker mints the per-box secret when the box is admitted and rotates
  it on every box start. boxhostd passes it into the box as a read-only file.
  A restored box gets a fresh secret on its first start.

## D10. Waker floor and the interactive priority (review gaps 4, 5)

- The waker keeps the last reported `next_wake_at` until it is replaced, and
  wakes every admitted, unsuspended owner at least once every 24 hours. A box
  that dies before reporting is still woken.
- At the awake cap, an interactive request evicts the least recently used
  idle box. Scheduled wakes queue behind interactive requests.

## D11. Deploys, canary and rollback (review gaps 6–8)

- **Deploy:** the router rolls a new image per box. It waits until the box
  has no running turn, restarts it on the new image, health-checks it, and
  rolls back that box alone if the check fails. The router itself deploys as
  today.
- **Canary:** the canary account signs in through the router and runs one
  turn in its own box. That is the per-deploy acceptance.
- **Cutover rollback:** after the first owner-box write, the old image can't
  run on the split data. Rollback is restore-from-backup only, within the
  cutover's maintenance window.

## Founder decisions (2026-10-16)

- Build what the research says to build.
- A co-admin of a shared center is refused, as the research recommends by default. Sharing is by publishing.
- Features not ported into the owner daemon at cutover are dropped. The list is reported to the founder before the cutover deploy.
