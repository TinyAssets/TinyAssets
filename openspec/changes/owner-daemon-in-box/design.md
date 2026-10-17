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
| Cross-tenant views are published data, not reads of private stores | Commons publishing copies out at publish time; public listing reads only `.published/` |

Any finding in review that can't be expressed as a defect in the router, the
broker proxy or the waker belongs inside one box. It is fixed there, never by
adding a check in shared code.

## D1. The owner daemon

- **Process:** the same image and code, with entrypoint `python -m tinyassets.owner_daemon
  --data /home/owner/data --socket /run/owner/app.sock
  --secret-file /run/owner/secret`. It runs
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

- **Bootstrap.** The per-owner relay accepts first frame `{"op":"WHOAMI"}` without a center and returns `{"op":"OWNER_IS","principal":<bound owner>,"uid":<bound uid>}` from `bind()`, with no broker round trip or request identity; this admits startup as `owner-box`, while `BOX_CONNECT` stays unchanged.
- **Mode.** Missing or refused relay admission prevents startup; only successful admission installs immutable process-local owner mode; file helpers return no session binding in that mode, and the same app factory and existing request authentication remain in use until slice 2.
- **Tools.** Local file tools call `OwnerBoxd` in-process with generation/receipt state under `<data>/.owner-boxd`; bash launches locally with bounded output/time and the existing ta receipts and extension delivery over a local socket, without `BoxExecutor` or `start_exec`.
- **Authority.** Provider-work receipts accept `owner-box` alongside `cloud`; the executor class is derived from process admission, with no request-selected identity.
- **Egress.** `owner_runtime.broker_egress` is the slice 3 seam; slice 1 tests use the existing synthetic model-wire fixture.
- **Lifespan.** The existing app lifespan starts and stops the existing owner tick and loopback engine endpoints in owner mode; it needs neither host bootstrap protection nor a second box-facing engine socket.
- **Defaults.** Admitted owner startup enables the existing app-door and engine-tool flags; sandbox status uses that admission, and storage measurement walks the local owner root.
- **Loopback.** The `bash`/`ta` engine transport uses loopback TCP inside the
  box's own netns. That is allowed. The rule is no NON-loopback connection.
- **The `owner-box` verdict is a mode marker, not authority** (slice 1
  review). It lets the owner daemon record provider work in its OWN data
  root, and nothing else. Every boundary-crossing authority stays in
  infrastructure: egress and credentials come only through the broker
  (slice 3 makes owner-mode egress broker-only), and inbound traffic reaches
  a box only through the router's admission-derived socket (slice 2). A host
  process that fakes the relay gains no egress, no credentials and no routed
  traffic. A signed "I am in a box" proof is not added: a host process can
  forge it as easily as the socket, so it would protect nothing.
- **Data root = the boxhostd owner mount** (`/home/owner`, as in the C6
  design and `gvisor_runtime`), never a CLI path. Startup refuses if that path
  isn't a mount (slice 1 review: durable, quota-bound storage only).

## D2. Shared router

- Keeps the public doors (Cloudflare tunnel → `/mcp`, `/app`, `/api`,
  `/hooks/inbound`), WorkOS sign-in, MCP OAuth and API keys.
- **Per request:** authenticate → `owner_id` → box (broker `owner_machine`)
  → `ensure_awake` → stream the request to the box socket with headers
  `X-Owner` and `X-Box-Secret`. Streaming MCP and SSE pass through unchanged.
  The router parses no owner payload.
- Reads no owner data. Commons and published listings come from its own
  published store.

### D2 decisions (slice 2)

- **Router stores never open in a box.** Every router-store opener refuses inside an owner runtime (`refuse_router_store`); box code reaches router state only through router-forwarded identity metadata or the bound credential channel.
- **Account revocation.** `account_revoke` takes only the operation over the bound relay credential channel; the router revokes that owner's sessions, inline/hosted/OAuth flows, approval handoffs, live API keys and hook digests. Refresh records carry the verified owner in authenticated public metadata, preserved through rotation so all devices and grace handles can be revoked; legacy unattributed records refuse renewal. Anonymous native login flows contain no owner and are consumed before identity is known; canary rate counters belong to the configured canary. Account deletion in a box must report a failed router revocation as an error.
- **Handoff bound.** Each owner keeps at most 20 unexpired approval handoffs; insertion removes the oldest beyond the newest 19 in the same transaction.
- **One hook door:** `/hooks/inbound/<token>`; `/mcp/hooks/` is deleted and integrations re-point at cutover (named in the slice 7 report).

- Slices 2 onward change production serving, so they stay unmerged and merge together with the cutover slice as one maintenance deploy, after the `--archive` rehearsal (review of #5172).
- Secret delivery and rotation at sandbox start: slice 4 (boxhostd mints via the broker on every start and writes the read-only secret file); slices 2–7 merge together as one stack.
- **Credential channel.** The existing per-box relay adds `ROUTER_CREDENTIAL`; only its `bind()` supplies owner/uid, the broker checks live admission and forwards to a broker-uid-only router socket under `.runtime`; the router is the only key/hook-map writer, using the existing runtime group without host provisioning.
- **API key authority.** Owner-mode mint/list/revoke use the channel, with no box key database; grants remain local, a failed grant invalidates the newly minted router key, and a request reference never replays its secret.
- **Hook ordering.** The router mints hook tokens; the box stores only the digest and delivery configuration; all three revocation paths invalidate the router map before the local row, and failed local creation revokes the router token.
- **Deleted accounts never mint again.** `account_revoke` first writes a durable owner tombstone (`.router-deleted-owners.sqlite3`). Every router credential request runs under one dispatch lock and refuses a tombstoned owner, so a box request racing the revocation is either revoked or refused.
- **Retirement ends the box's secret** (slice 4): retiring a center or deleting an account makes boxhostd revoke the per-box secret and stop the box before the router state is cleared, so a forward resolved just before retirement fails at ingress. The router adds no recheck.
- **Single identity.** A valid protected session and bearer must name the same owner or the router returns 401 before resolving any box.
- **Approval handoff.** `/app/approval-handoff[/<ref>]` is sign-in navigation whose rows live in the router's owner-sessions store, so it stays at the door. It is never forwarded.
- **Bounded router rows.** Hook digests are capped per owner with an atomic count-and-insert. Revoked key rows beyond the newest 100 per owner are pruned, with their rate counters. Live keys keep the existing cap of 25.
- **Model-connection callbacks** stay at the door, and task 3c moves the whole connection flow there with broker custody. A box never holds flow state, so nothing is forwarded.

- **Transport.** One ASGI reverse proxy streams request bytes and raw response chunks over HTTP on a Unix socket, removes hop-by-hop headers, preserves MCP session headers, and closes the upstream on client disconnect; it never retries an owner request.
- **Resolution.** Fenced `OWNER_BOX_ROUTE(principal, rotate)` returns only the broker-reserved uid and secret for an owner with a live center admission; a reservation alone is not admission, and unknown/retired owners return 404 without opening a socket.
- **Socket.** The single `resolve_box` seam uses `TINYASSETS_BOXES_RUN/<broker uid>/app.sock` (default `/run/tinyassets-boxhostd`, the existing Compose mount); slice 4 adds waking there without changing route handlers.
- **Secret.** Admission mints a 256-bit per-owner secret in the broker's private identity database; explicit rotation replaces it, and owner startup requires `--secret-file`, whose value is checked with `hmac.compare_digest` on every request.
- **Identity.** The router replaces caller-supplied `X-Owner`, `X-Box-Secret` and `X-Owner-Identity`; the last carries the existing authenticated identity and capability metadata, while public bearer/cookie credentials are removed before forwarding.
- **Sessions.** Existing protected-owner cookies resolve only through the router session map; cookie-authenticated mutations retain exact-origin JSON admission, and the verified protected-view proof travels with the authenticated identity for box-local consumers.
- **Placement.** Router authentication resolves credentials only; MCP payload checks, outside capability admission and connection receipts run inside the owner daemon, and router logging never buffers owner payloads.
- **Hook map.** The router's `.hook-owners.sqlite3` contains only token digests and owners; the migration seeds existing digests; box mint/revoke requests now use the bound relay and broker to reach the router writer, while payload/delivery rows remain inside the box.
- **Startup.** The shared entrypoint no longer starts owner recovery, engine endpoints, ticks or storage initialization and refuses direct SSE/stdio serving; owner recovery moved into the existing owner lifespan.
- **Routes.** All owner-facing `/app/*` routes (including `/app/api/*`, home, chat, uploads and preferences), `/api` and descendants, MCP transport `/mcp[/]` and `/mcp/events`, and `/hooks/inbound/<token>` share the forwarder; discovery, sign-in/OAuth, pulse, published listing reads and billing retain router handlers.

### D3 decisions (slice 3: broker egress)

- **Transport.** Existing synchronous and streaming broker clients open `BOX_CONNECT` on the startup-admitted relay; the broker derives identity, center and fence from `BOX_CHANNEL`, with no host-client fallback.
- **Models.** Reuse the HTTP model executor, source/grant queries, accounting gate and credit-bounded `OPEN` stream; publish only validated, credential-free source descriptors into the broker ledger before inference.
- **Accounting.** An allocation holds a dedicated box channel with a ten-second heartbeat; the broker holds its liveness locks and releases them on disconnect, so accounting never reads a box file or depends on a host copy of it.
- **Connections.** Expose the existing bounded ledger, catalog, capability, HTTP-connect, disconnect and usage operations on the admitted box channel; credential refresh cannot fall back to daemon-side vault access.
- **Policy.** Policy reads, receipts, classification, decisions and proposals use the channel-bound owner; protected policy writes and approval issuance remain at the shared protected door.
- **CLIs (founder, 2026-10-16).** There is one model path: the broker's. Provider CLIs (Claude Code, Codex) as a model source are retired in owner mode, with no placeholder-credential proxy and no broker endpoint built for them. The in-box CLI provider route is deleted, not left refusing. Users whose subscription was connected through a CLI reconnect it through the broker's connection flow at cutover, and the cutover report names those accounts.
- **Shared reads.** `BOX_SHARED` uses the relay-bound owner without a center and exposes only named plan and published-definition/catalog reads; plan rows live in `.broker/plans.db`, published data in `.published/`, and slice 6 seeds these shared stores at cutover.
- **Enforcement.** Install one Python socket audit hook at owner startup for direct connects, datagrams and DNS; only loopback IPs/localhost and Unix channels are allowed, while the existing null netns remains the native-process boundary.
- **Custody.** Startup rejects the existing platform-secret inventory and provider/OAuth credential environment names; owner-mode credential-vault access and local billing-store access refuse rather than create or read a box-local secret store.

### D3c decisions (connection lifecycle at the door and the broker)

Research rules applied: secrets stay outside the instance; the shared layer
keeps identity and the credential proxy; one writer per state. The founder
rule (2026-10-16): every auth type a provider allows, any provider, with no
provider-specific code.

- **Secrets enter only at the door.** The router serves the whole connection
  flow: `/app/model-connect/*` (begin, exchange, resume, deposit, inline
  begin/poll/cancel, generic OAuth begin/exchange, source sign-in),
  `/app/model-callback/*`, `/app/oauth/client-metadata.json` and
  `/app/connections`. Flow rows (preset PKCE, generic OAuth, DCR client
  cache, inline popup) live only in router stores. A secret, code or token
  never passes through a box, not even in transit.
- **One custody writer: the broker.** The router hands a secret to the
  broker over its private socket and never persists it. The broker writes
  custody in shared `.platform` storage, which no box mounts, and commits the
  grant. The box sees grants and credential-free source descriptors only.
- **Which center.** The door accepts a center only if the broker's admission
  map names the authenticated owner for it (`admitted_owner(center) ==
  owner`). This is an identity read, not a tenant-data read.
- **Connects started in a box.** A keyless (`none`) connect goes to the
  broker's `HTTP_CONNECT` on the box channel. Any connect that needs a secret
  returns a door link, the existing tap-to-connect, and the box's MCP and
  approval actions take no secret value. A key pasted into chat is not
  accepted; the slice 7 report names this change.
- **Completion.** The box learns of a finished connect from the broker ledger
  (`HAS_MODEL_SOURCE`, `GRANTED_RESOURCE`), with no push and no shared flow
  row in the box.
- **Refresh** runs only inside the broker (`ConnectionTokens`). The box's
  `REFRESH` frame and every daemon-side vault writer are deleted.
- **Disconnect and revoke.** Disconnect at the door, or from the box as the
  credential-free `DISCONNECT` op. The broker calls the provider's RFC 7009
  `revocation_endpoint` when its directory data names one, then erases
  custody.
- **Provider-agnostic.** Auth stays data: schemes `none`, `bearer`, `basic`,
  `header`, `oauth1a`, `oauth2` and `url_secret`. OAuth uses discovery,
  RFC 7591 DCR, the client metadata document and registered-client JSON.
  Provider-specific code is deleted with the CLI stack: the OpenAI device
  flow and its four routes, `llm_deposit` (Claude/Codex), `sk-ant-` checks,
  and the CLI branches of `provider_resolver`, `credential_vault`,
  `providers/base` and `api/universe` `set_engine`. A generic RFC 8628
  device flow is not built; no remaining HTTP provider needs it, and it can
  be added later as data.
- **Plans.** Stripe webhook and checkout write `.broker/plans.db`. Account
  admission writes an explicit `free` row. A missing row is an error, never
  read as `free`.
- **Hooks** already publish through the router (slice 2).
- **Plans at cutover.** The census copies each owner's tier once into
  `.broker/plans.db` from the authoritative source: Stripe's customer and
  subscription, as recorded today in the per-center `.subscription_state.db`
  that the Stripe webhook writes. If a paid tier can't be attributed to
  exactly one owner, the census refuses and the cutover stops. It never
  defaults an account to `free`. Unpaid owners get the explicit `free` row.
- **Existing connections at cutover.** HTTP connections (vault `http`
  records under shared `.platform/credential-vaults/<center>/` plus the
  broker's grant rows) are already outside every box, in the custody store
  this section keeps. They carry over in place, with the broker as their
  writer: no copy and no reconnect. CLI `llm_subscription` records are
  deleted with the CLI stack, so those accounts reconnect through the door.
  The census verifies the vault location on the production copy.
- **One model path, so in-process local models go too.** The direct Ollama
  vision call in image ingestion is deleted. Images keep their metadata,
  and a description comes from the owner's model through the broker.
- **The slice 7 report names, per account:** CLI connections lost, keys
  pasted into chat no longer accepted, HTTP connections carried over,
  `/mcp/hooks/` integrations to re-point to `/hooks/inbound/`, and the loss
  of local image descriptions.

| Research rule | 3c |
|---|---|
| Secrets stay outside the instance | Entered at the door, held by the broker; the box holds descriptors |
| The door never reads tenant data | The door reads only identity (admission map) and its own flow rows |
| One writer per state | Flow rows: router. Custody and grants: broker. Plans: billing at the broker |
| Isolation is the box, not shared checks | No per-owner filter is added; the owner comes from authentication or the bound channel |

### 3c-2 as built

`ADMIT_CENTER` registers new centers on the bound box channel. `/app/connections`
and `/app/model-connect/*` run at the shared door using broker admission and catalog
state, without owner-store reads. `HTTP_CONNECT` prepares, commits and rotates
custody; `oauth_offer`, `oauth_exchange` and `preset_exchange` keep discovery and
exchanges in the broker. Box connect and rotate actions take no credential:
keyless connects use the box channel; other schemes return the existing `/app`
link with `request`, `center` and connection ID. The browser posts credentials
only to the door. Commit and rotation record `request_ref`; box ledger polling
through `GRANTED_RESOURCE` matches the pending request, applies its uses, resolves
it and wakes the existing continuation. There is no push or router request read.
The lifecycle tests exercise all nine auth variants from a separate box process,
then disconnect at the door or on the box channel and prove the next call fails.

### 3c-2a custody and refresh

- `HTTP_CONNECT` retains prepare/commit. Commit carries `secret` and the
  prepared revision; the broker rechecks both ledger rows, writes custody and
  commits the grant in the same operation. The prepare request has an empty
  secret. `rotate` carries destination, incarnation and secret;
  `inspect_custody` returns only a refusal code. Box channels refuse secret
  deposits and rotations; keyless connects remain available.
- Vault writers and erasers live in `broker/vault.py`. Their lock files live
  under `.platform/credential-vaults/<center>/`, never in owner trees or
  provider-assignment state. Ownership rows and deletion tombstones live in
  `.broker/credential-custody.db`. Task 6 migrates that metadata and changes
  directory ownership; there is no runtime compatibility reader.
- `DISCONNECT` erase removes custody before deleting the fenced ledger rows.
  Account erasure tombstones the principal in broker metadata and erases its
  vaults under the same locks as deposits and refresh. The daemon no longer
  stages credential directories during account deletion.
- `broker/tokens.py` resolves and refreshes OAuth credentials locally, under
  broker vault admission. The REFRESH/REFRESH_ACK protocol, daemon callback
  preparation and daemon OAuth-service refresh operation are deleted.
- Registered OAuth clients use the same trusted directory at exchange and
  refresh. The existing environment installer renders only `TINYASSETS_OAUTH_*`
  assignments into `/etc/tinyassets/broker.env`, loaded by the broker's Compose
  service. Router exchange still needs its existing client configuration;
  refresh never calls the router for a secret or token.
- RFC 7009 provider revocation is task 3c-2b, not part of this slice.

### 3c-2b connection controls and revoke

`/app/connections` is standalone at the shared door. Explicit centers use
`ADMITTED_OWNER`; omitted centers use the fenced `SOLE_CENTER` identity read
and require exactly one live admission. `CONNECTION_CATALOG` includes fenced
rows on request so interrupted cleanup stays reachable without an owner store.
`DISCONNECT` revokes the current OAuth token through the trusted directory's
RFC 7009 endpoint under the custody lock, then erases locally even on provider
failure. A failure leaves a revoked, credential-free connection receipt with
`revocation_error` and `custody_erased`; `HTTP_CONNECT` can replace it with a
fresh incarnation. Model-connect placement, box actions and ledger completion
are described in the 3c-2 as-built contract above.

### 3c-3 as built

- `broker.owner_reads.Plans` is the sole shared plan writer. Its `set_plan`
  (`apply_tier_event`) keeps ordered tiers, end dates, customer/subscription
  mappings and the owner's checkout attempt in `.broker/plans.db`.
  First center admission attaches that database and writes the explicit free
  row in the identity transaction. Further admissions never repair a missing
  account plan. Missing rows raise `PlanMissing`, including over `BOX_SHARED`.
- Router billing uses fenced `BILLING_PLAN` IPC because `.broker` belongs to
  the broker UID. Status, checkout and cancel use authenticated owner IDs;
  verified subscription events use the stored customer/subscription mapping,
  and first service-signed owner claims establish that mapping. Old signed
  center references require the mapping seeded by task 6. Checkout settlement
  resolves the stored session/attempt identity, ignoring client reference hints.
  Checkout retries retain their exact creation parameters and lease identity.
- Seat/storage usage is omitted from the billing response. Plan limits remain;
  billing does not need a private usage read to sell or cancel a subscription.
  Box quota reads and update preparation use `BOX_SHARED` plan reads, and broker
  provisioning reads the shared plan directly.
- `BOX_PUBLISH` copies a validated immutable definition into `.published/`,
  taking authorship only from the relay bind. Bundle ownership and idempotency
  stay enforced by the existing commons writer. `bundle_version` is a named
  shared read for later package publications. Private bindings may retain a
  public definition copy for their local foreign key.
- Public-run and broker catalog reads open only `.published/` and do not
  initialize schemas. Published SQLite data uses rollback journaling and is
  broker-written/router-readable. The box no longer writes a shared preview;
  uncached public previews render from published content without router writes.
- Task 6's census/copy and task 6a's remaining non-owner compatibility branches
  are not part of this slice. No deployment or independent review is claimed.

## D3. Lifecycle (boxhostd)

- boxhostd supports one persistent sandbox per owner, running the owner
  daemon with its socket bind-mounted to `/run/tinyassets-boxhostd/<uid>/app.sock`.
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
