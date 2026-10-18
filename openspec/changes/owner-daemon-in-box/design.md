# Design: the owner's daemon in the owner's box

## Restore keeps monotonic facts

Restore reads current live facts with every consumer stopped and both layouts
locked. It carries them into the staged shared and owner volumes before publishing
either volume or starting any daemon. Failure discards staging and preserves the
pre-restore volumes. Snapshot contents never overrule these facts:

- Owner erasures: router `deleted_owners`, broker `owner_box_revocations`, custody
  `deleted_principals`, and host `owner_destroys`. Restored erased owners follow
  deletion's local sweep and complete tree destruction, with shared custody and
  router credentials erased as well.
- Shared revocations: API-key `revoked_at`; removed hook-owner mappings; removed
  owner sessions, login flows/completions, approval handoffs, inline/hosted model
  flows, OAuth codes/access/refresh tokens and sealed refresh-session files;
  removed or replaced router credentials; broker connection/grant `revoked_at`,
  removed connection capabilities, erased custody slots and their metadata,
  revoked browser-vault records and deleted policy authority. A removed credential
  is a revocation even when its writer retains no tombstone: snapshot authority
  must intersect live authority, including its credential incarnation.
- Consumed operation IDs and epochs in every owner's `.owner-boxd/boxhost.db`.
  Restore unions IDs as refusals and advances epochs beyond both generations.
  Every mutating tool, including bash, consumes its ID before execution.

This single carry-forward mechanism replaces newer-deletion, identity-frontier,
destruction-receipt and shared-manifest rollback refusals. Pair validation still
rejects malformed archives and mismatched identities. Restore never calls remote
billing or provider endpoints. Rehearsal proves post-snapshot key revocation and
owner erasure survive, and pre-restore write and bash IDs cannot execute again.

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
  --socket /run/owner/app.sock --relay /run/boxhostd/relay.sock
  --secret-file /run/boxhostd/secret`. It runs
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
- **Account revocation.** The shared door clears router sessions, inline/hosted/OAuth flows, approval handoffs, live API keys and hook digests in-process after box-local deletion, the durable tombstone and broker retirement. No box credential operation can revoke router account state. Refresh records carry the verified owner in authenticated public metadata, preserved through rotation so all devices and grace handles can be revoked; legacy unattributed records refuse renewal. Anonymous native login flows contain no owner and are consumed before identity is known; canary rate counters belong to the configured canary.
- **Handoff bound.** Each owner keeps at most 20 unexpired approval handoffs; insertion removes the oldest beyond the newest 19 in the same transaction.
- **One hook door:** `/hooks/inbound/<token>`; `/mcp/hooks/` is deleted and integrations re-point at cutover (named in the slice 7 report).

- Slices 2 onward change production serving, so they stay unmerged and merge together with the cutover slice as one maintenance deploy, after the `--archive` rehearsal (review of #5172).
- Secret delivery and rotation at sandbox start: slice 4 (boxhostd mints via the broker on every start and writes the read-only secret file); slices 2–7 merge together as one stack.
- **Credential channel.** The existing per-box relay adds `ROUTER_CREDENTIAL`; only its `bind()` supplies owner/uid, the broker checks live admission and forwards to a broker-uid-only router socket under `.runtime`; the router is the only key/hook-map writer, using the existing runtime group without host provisioning.
- **API key authority.** Owner-mode mint/list/revoke use the channel, with no box key database; grants remain local, a failed grant invalidates the newly minted router key, and a request reference never replays its secret.
- **Hook ordering.** The router mints hook tokens; the box stores only the digest and delivery configuration; all three revocation paths invalidate the router map before the local row, and failed local creation revokes the router token.
- **Deleted accounts never mint again.** The shared door owns `/app/account/delete`: authenticated identity, same-origin JSON and `confirm == "DELETE"`. It forwards to the secret-authenticated `/_box/account/delete` route, which deletes only box-local data and returns its receipt (blocked/refused outcomes stop the orchestration). The door then stores the receipt with `DeletedOwners.add` under the credential dispatch lock, fencing all new mints. A retry with a tombstone resumes every unfinished shared phase from the durable receipt; the tombstone refuses new work, never cleanup.
- **Retirement ends the box's secret** (slice 4): after the local drain and sweep and the shared tombstone, the door retires all owner center admissions through the fenced broker. The broker permanently revokes box admission and requires boxhostd to stop the box, destroy its entire volume and quota limits, and remove its relay/app socket directories and secret. The door records broker custody erasure, owner-keyed billing cancellation and plan update, router credential revocation, then external identity deletion. Billing runs even if destruction fails; identity survives until earlier cleanup completes so the owner can retry. Only a complete receipt returns success. Every failed step stays loud and retryable after the tombstone. There is no box-to-router account-revoke operation; a stale forward fails at ingress.
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

- **Model grant metering.** The broker latches `inference_required` from an HTTP connection's `model_use`/`model_discovery` capabilities or the first accepted source descriptor naming the grant. Descriptor publication and the latch commit together; platform registration publishes through the same broker operation before returning success. Backfill reads only broker-held capabilities and broker-published descriptors; it never scans private host center metadata. Missing connection rows retain the existing fail-closed default. Destinations do not classify grants. Removing capabilities or descriptors never removes metering; an unregistered HTTP grant without a model capability remains ordinary, and a latched model POST without an inference envelope refuses before credential access.
- **Send admission.** Every box stream checkpoint repeats OPEN's bound-center admission check, including after resolution/connect and before sending credentials or request bytes.
- **Shared-read admission.** Every BOX_READ rechecks the relay-bound owner's route in the broker; an established BOX_SHARED channel grants no reads after the owner's last live center retires.
- **Transport.** Existing synchronous and streaming broker clients open `BOX_CONNECT` on the startup-admitted relay; the broker derives identity, center and fence from `BOX_CHANNEL`, with no host-client fallback.
- **Models.** Reuse the HTTP model executor, source/grant queries, accounting gate and credit-bounded `OPEN` stream; publish only validated, credential-free source descriptors into the broker ledger before inference.
- **Accounting.** An allocation holds a dedicated box channel with a ten-second heartbeat; the broker holds its liveness locks and releases them on disconnect, so accounting never reads a box file or depends on a host copy of it.
- **Connections.** Expose the existing bounded ledger, catalog, capability, HTTP-connect, disconnect and usage operations on the admitted box channel; credential refresh cannot fall back to daemon-side vault access.
- **Policy.** Policy reads, receipts, classification, decisions and proposals use the channel-bound owner; protected policy writes and approval issuance remain at the shared protected door.
- **CLIs (founder, 2026-10-16).** There is one model path: the broker's. Provider CLIs (Claude Code, Codex) as a model source are retired in owner mode, with no placeholder-credential proxy and no broker endpoint built for them. The in-box CLI provider route is deleted, not left refusing. Users whose subscription was connected through a CLI reconnect it through the broker's connection flow at cutover, and the cutover report names those accounts.
- **Shared reads.** `BOX_SHARED` uses the relay-bound owner without a center and exposes only named plan and published-definition/catalog reads; plan rows live in `.broker/plans.db`, published data in `.published/`, and slice 6 seeds these shared stores at cutover.
- **Enforcement.** Install one Python socket audit hook at owner startup for direct connects, datagrams and DNS; only loopback IPs/localhost and Unix channels are allowed, while the existing null netns remains the native-process boundary.
- **Native public egress (6a parts 1f/1g).** Startup gives OwnerBoxd the admitted relay and starts one in-process HTTP/CONNECT byte forwarder on `127.0.0.1:3128` for the box lifetime. Every bounded child receives upper/lowercase HTTP, HTTPS and ALL proxy defaults and `NO_PROXY=localhost,127.0.0.1`; invocation-scoped authenticated git's local `http_proxy` still overrides that default. The listener sends centerless `BOX_PUBLIC`; the root relay supplies only its bound principal to the broker, waits for `BOX_ADMITTED`, then acknowledges `BOX_CONNECTED` and copies raw bytes. The broker admits the owner's verified box record, retaining revocation, deletion and suspension refusals, destination pinning/private-address refusal and owner/host caps. The shared router starts no listener. OwnerBoxd.close (including daemon SIGTERM for idle sleep) cancels children and closes/joins listener and active bridges; no per-command fork or output descriptor is retained.
- **Relay admission as built (6a parts 1f/1g).** Public channels use owner-keyed broker admission; connector channels keep center-keyed BOX_ADMISSION, never the deleted per-call OwnerRegistry session-grant APIs. The relay bind supplies identity; broker admission, per-stream scope/checkpoints and lifecycle retirement remain the authority. Git's binary exchange uses BrokerClient's existing connect seam so it performs BOX_CONNECT/BOX_CONNECTED before sending OPEN in owner mode, rather than writing OPEN directly to the relay socket. This keeps authenticated git on the real owner relay after backend retirement.
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
  of local image descriptions, and billing status no longer showing seat
  and storage usage (plan limits remain).

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
only to the door. Commit and rotation record `request_ref` only at the door, with broker-stamped
`committed_via` derived from transport; the box channel rejects request references.
Only door-committed grants can complete asks. Box ledger polling
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

### D3 as built (slice 4)

- **One lifecycle writer.** `boxes/owner_daemons.py` runs in boxhostd. The
  router uses its control socket for wake/stop and the in-box activity status
  supplies idle truth; the per-call file/exec backend is deleted.
  Startup's existing private runsc census stops survivors before accepting work.
- **Native public bridge (6a parts 1f/1g).** The admitted owner startup constructs
  OwnerBoxd with its relay and one `owner_public_egress` listener at
  `127.0.0.1:3128`. Its bounded launcher supplies proxy defaults to bash,
  provisioning and probes; authenticated git uses its invocation route and
  the standard relay handshake. Public admission is owner-keyed through
  `box_admission.resolve_owner`: only `{op: BOX_PUBLIC, principal: owner}`
  reaches the broker, which verifies the permanent mapping and box record,
  refuses revoked/deleted or suspended owners, and admits boxes with zero
  centers. Center-bearing BOX_PUBLIC is invalid at the relay; center-scoped
  BOX_CONNECT still uses `resolve`. Destination pinning and caps remain
  broker-owned. SIGTERM for shutdown/idle sleep reaches
  OwnerBoxd.close, which cancels children and closes/joins all proxy streams
  and threads before exit; the next wake can immediately bind the fixed port.
- **Control socket.** The existing `daemon.sock` accepts `ensure_awake(owner,
  scheduled: bool)` and `status(owner)` from kernel uid/gid 1001; only the
  broker's 1002 peer can send `retire(owner, uid)`. Its group is the existing
  shared 1102 group. Root replies are credential-checked. Status includes awake,
  failure count and the last error, never a secret. Broker retirement has four
  reserved listener workers. Eight scheduled waiters may occupy workers; the remaining listener capacity stays available for interactive
  requests. Queue overflow refuses loudly.
- **Admission.** Root-only broker `BOX_LIFECYCLE(principal, rotate)` reads the
  live admissions and box record. The UID, XFS quota and centers come from the
  broker. `rotate=true` mints the next secret. Router resolution first waits for
  boxhostd, then obtains the current secret through `OWNER_BOX_ROUTE`; it opens
  no owner data and starts no process. Control waits expire at 65 seconds and
  become `box_unavailable` (503); unknown/deleted owners remain 404.
- **Launch.** runsc starts `/opt/venv/bin/python -B -m tinyassets.owner_daemon`
  with `--socket /run/owner/app.sock --relay /run/boxhostd/relay.sock
  --secret-file /run/boxhostd/secret`. The only data root is `/home/owner`.
  The app socket is exported in `/run/tinyassets-boxhostd/<uid>/app.sock`;
  ephemeral secret files live in the root-private `secrets/` directory and are
  owner-readable mode 0400, mounted read-only. The owner UID/GID, empty capability
  sets, static PATH/HOME/browser environment, null network and cgroup limits are
  explicit. `--host-uds=all` allows opening the bound relay and creating the
  exported socket. No host credential environment is inherited.
- **Defaults.** Idle threshold 900 seconds (`TINYASSETS_BOX_IDLE_SECONDS`),
  awake cap 6 (`TINYASSETS_BOX_AWAKE_CAP`), memory 1 GiB per box
  (`TINYASSETS_BOX_MEMORY_BYTES`, existing supported sizes 1 or 2 GiB).
  The service's aggregate MemoryMax is 7G. Interactive wakes evict the least
  recently used idle box; scheduled wakes wait without eviction and behind
  interactive tickets. Wakes have a 60-second capacity/readiness deadline.
- **Idle and turn truth.** `owner_activity` counts HTTP requests and all turns
  through `control_plane.session.start`. Secret-authenticated local
  `GET /_box/status` reports active count, idle seconds and drain/revoke state.
  `POST /_box/drain` atomically closes admission only when no work runs and the
  requested idle threshold has passed. New requests and turns then refuse.
  Boxhostd uses this handshake before idle/LRU shutdown; unknown status never
  authorizes eviction. SIGTERM uses uvicorn's existing lifespan shutdown and
  closes the local tools. Passive MCP subscriptions do not hold the work gate;
  graceful shutdown cancels remaining subscriptions after ten seconds. A
  30-second flush timeout is loud, never treated as a successful sleep.
- **Crash clock.** The existing one-second host loop checks runtime liveness,
  sleeps idle boxes and restarts crashes. Backoff is 1, 2, 4, 8... seconds,
  capped at 60; after five consecutive failures, wakes fail immediately with
  a loud status while the host continues retrying without human intervention.
  A box surviving an idle interval resets its failure count. A failed launch
  whose cleanup cannot prove absence keeps its capacity reservation and is
  never advertised as ready.
- **Wake lease.** Router resolution atomically wakes and leases a box through
  boxhostd's router-only `resolve(owner, cleanup)` operation. LRU and idle sleep
  skip leased boxes. The router releases the opaque lease after upstream response
  headers prove ingress admission, or on forwarding failure; abandoned leases
  expire after ten seconds. Cleanup resolution may reach a draining ingress.
- **Deletion drain.** The authenticated internal deletion route closes the same
  activity gate used by requests and turns before waiting on their completion condition for up to twenty seconds
  for active work. It admits only deletion/control calls while draining. A bound
  timeout reports `drain` unfinished; a retry may finish quiescence. No sweep
  starts while a turn can still write.
- **Retirement and account erasure.** Center retirement still stops without
  destroying retained centers. Account retirement permanently fences admission
  and secret rotation, then requires boxhostd's broker-only `destroy(owner, uid)`
  acknowledgment: prove stop, erase the complete XFS owner tree without following
  links or mounts, clear quota limits, remove relay/app socket directories and
  the secret. Every part is idempotent, including after broker tombstoning.
  The door serializes deletion attempts and stores a durable shared receipt after
  the local sweep. It records destruction, broker custody erasure, owner-keyed
  Stripe cancellation plus shared plan update through BILLING_PLAN, router
  credential revocation, and existing WorkOS identity deletion. The door imports identity
  erasure from `router_account_deletion`; billing uses the owner-keyed
  `billing.stripe_adapter.cancel_account_billing`. The box-local
  `account_deletion` module performs neither shared phase. Billing runs even
  after destruction failure. Identity deletion waits until the other steps finish
  so sign-in remains available for retries. Only a fully confirmed receipt returns
  200; every other outcome names unfinished phases. A tombstone blocks new work,
  never unfinished cleanup. No owner path or store is read at the door.

- **Linux proof.** The oracle has no runsc. Lifecycle/OCI tests use the existing
  runtime-double pattern; the router process fixture now launches actual owner
  daemons through boxhostd, including wake, HTTP serving, SIGTERM flush, secret
  rotation and retirement. The real control-socket proof runs as root with an
  unprivileged router client. Native gVisor/XFS/cgroup integration still needs
  the deployment venue; unit/protocol results are not that proof.

## D4. Waker

- At the end of every tick the box reports its `next_wake_at` to the
  broker. The shared waker wakes boxes when it comes due.
- Suspended accounts (billing or abuse) are never woken.
- Delivery is at least once, so the tick is idempotent, as today.

### D4 as built (slice 5)

- **Bound report.** Every owner `AssignedQueueConsumer.poll_once` finishes by
  computing the local minimum and sending `NEXT_WAKE {"at": epoch_seconds|null}`
  over `BOX_SHARED`. The relay's bind supplies the owner. The broker accepts
  exactly `op` and `at`, rejects non-finite or non-numeric times, and rechecks
  live admission on every report. No request can select an owner or UID.
- **Rows remain inside.** The projection reuses automation due rules (including
  cron zones, one-shot retries, retirement and pause), proactive trigger cadence
  and active hours, pending agent wakes, bound-result continuations and answer
  delivery retry timestamps. It reads only this box's stores. An empty or paused
  schedule reports null. A report failure is a failed tick with a logged error;
  it never clears the last broker value.
- **Index.** The broker is the only writer of `next_wakes` in
  `.broker/state/next-wake.db`, beside its identity database. Its columns are
  `owner`, `next_wake_at`, `reported_at`, `last_woken_at`. Reports replace only
  the reported time; successful wake acknowledgements replace only
  `last_woken_at`. Due delivery never consumes the report, including across
  broker restart or a report racing a wake acknowledgement.
- **Host process and defaults.** The waker runs in `broker.process`'s existing
  asyncio loop after boxhostd registration. That process already owns the
  index and authenticated boxhostd control calls; it needs no polling RPC or
  second index writer. It scans immediately, then every 30 seconds
  (`--wake-poll-seconds`). It considers every live admitted owner whose report
  is due or whose last successful wake is at least 86,400 seconds old. A never
  reported/never woken owner is eligible immediately. Retired centers and
  permanently deleted owners are excluded through the admission map.
- **Capacity.** Every dispatch uses `ensure_awake(owner, scheduled=True)`.
  The broker UID may request only scheduled wakes. At most two control calls
  are in flight, leaving two of boxhostd's four broker workers for retirement;
  the existing scheduled queue remains behind interactive requests. Slow
  control calls do not block the scan clock. Failed calls remain due and are
  logged, and attempt ordering prevents a failing owner starving later owners.
- **Suspension source.** `.broker/plans.db` is the single source. The existing
  free/paid schema had no suspension state; it now also holds independent
  `billing_suspended` and `abuse_suspended` booleans, initially false on explicit
  admission. The shared door can change them through fenced `BILLING_PLAN`
  `set_suspension`; boxes cannot. Tier updates preserve both flags. Missing
  plan rows raise `PlanMissing`, never imply unsuspended. Both the waker and
  boxhostd's broker launch admission check suspension, including after a
  capacity wait. Production passes the same plan store to identity admission,
  billing and the waker, avoiding a second plan file under `.broker/state`.
- **Linux proof.** The slice 4 launcher runs a real owner daemon process with
  the runtime double, a file clock and the existing automation `_execute` seam.
  The box reports a future time, boxhostd drains and stops it, the shared waker
  starts it without an incoming request, and the real tick claims and settles
  the automation and reports its next occurrence. A duplicate restart at the
  same instant leaves one execution and one durable attempt. Fake-clock tests
  cover the daily floor, null/future reports, missing plans, both suspension
  reasons, retirement, retry after failed delivery and bounded concurrent wakes.

## D5. Billing

- Stripe webhooks land at the router. Plan state is kept in the shared
  billing store, keyed by `owner_id`.
- The box reads its tier from the broker. It never holds a Stripe secret.

## D6. Migration (one cutover, after a verified full backup)

The one migration builds each admitted owner's box root on the box store,
copies center trees and per-center platform files, and extracts that owner's
rows from shared root databases with their table/index/trigger schemas. It
seeds the router, broker and published stores, regenerates platform-local state,
verifies attribution totals and SQLite integrity, removes host owner trees,
and publishes the final owner-daemon marker.

Every row and file must be attributed to its destination, explicitly dropped,
or explicitly regenerated. Anything else refuses the cutover. The `--archive` rehearsal on the private production copy must:

- show the per-owner row counts summing to the original totals;
- show each owner daemon serving that owner's history, pending requests and
  pause after the move;
- prove a restore of the paired backup returns today's layout.

### D6 as built (slice 6; synthetic harness accepted)

The lead's archive command is:
`python scripts/rehearse_box_move.py --archive <absolute-tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz> --image tinyassets-c6-rehearsal:s6-<head>`.
The archive must retain the `tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz`
filename. The image must already be built from the checked-out head; setup
refuses with `image not found: <tag>` before creating any volume otherwise. Every
container runs without a network. Development uses generated archives only.
The synthetic root end-to-end run passes every step, including both crash
boundaries, reconnect confirmation, the post-write restore and report privacy.
The harness creates disposable Docker volumes and a real XFS loop mount with
project quotas. It calls deploy's `box_migration_container` directly through
the script's explicit disposable runner, sharing caps, mounts, tmpfs and memory
limits. Both paths invoke read-only preflight, refusing census, ordinary apply
and final manifest/quota/marker verification. Apply verifies before sealing the
final owner-daemon marker; the extra verify command reads that sealed output.

The venue bind mount becomes private before mounting XFS, preventing propagation.
Cleanup removes containers, then discovers every mount of the venue loop device
and every venue bind mount in the engine namespace. It unmounts deepest first,
using lazy unmount only after ordinary unmount fails, detaches the loops and
asserts no venue mount or loop remains before removing any volume. A failed
assertion retains the volumes and fails loudly, including after partial setup.

Admitted centers' LanceDB directories and knowledge SQLite stores (including
sidecars) are owner data copied byte-for-byte, never regenerated. Only runtime,
consumer-liveness and workspace-staging artifacts are regenerated. The legacy
root `knowledge.db` moves whole only when every row in its known knowledge
tables resolves through its memory-scope center/user columns to one admitted
owner. Unknown/unscoped or competing ownership is a counted `no_owner_key` or
`conflicting_owners` drop; no default owner is inferred. Knowledge SQLite sidecars follow that disposition. Root `lancedb/` is always
dropped and counted as `unattributed_root_vectors`; it never inherits a knowledge owner. Census reports knowledge files and bytes per
owner and reason-coded dropped files and bytes. C12 receives the originals.

An uninterrupted generation establishes manifests, then the real paired
restore validates the original source bytes, converts through the same forward
cutover, and verifies the restored owner-box generation. Two independent disposable
source fixtures then crash at `after_owner_tree:1` and
`after_seeding`. Each resumes to the uninterrupted shared and owner manifests.
The test-only `CUTOVER_CRASH_AT` hook calls `os._exit(137)` inside apply; the
former self-SIGKILL was ignored because Python was namespace PID 1. Deploy
and restore entry points refuse the variable before host actions and override
image defaults with an empty value. Only the explicit disposable runner can
forward it, after checking the generated volume family and mountpoints.
Serving starts the real owner lifecycle manager, apps, router and broker with
kernel UID peers. This process venue uses private mount namespaces and the
fixed owner mount; gVisor isolation remains separate acceptance. Every admitted
center reads history and pending requests, pauses/resumes, reads its broker plan
and writes history. Reconnect confirmation uses the real shared model-connect
door and model-use reconciliation on a disposable shared-store clone: a new
connection changes shared custody authority, which the paired restore must continue to
fence. Reconnect owner writes remain on the migrated XFS trees. The original
serving generation is then paired-restored with explicit owner-write-loss
acceptance and compared again. Reports expose only fixed step
names, numeric counts and PASS/FAIL, one line per reached step:
`<step> PASS [<counter>=<nonnegative integer> ...]` or `<step> FAIL`.
The ten steps are setup, preflight, census, apply, verify,
restore_before_write, crash_resume, serving, restore_after_write and cleanup.
The synthetic run records 2 owners, 4 centers, 27 census rows, 3 manifests,
2 interruptions and 2 resume comparisons; serving records 4 history reads,
4 history turns, 4 pending reads, 4 saved requests, 8 controls, 8 plan reads,
4 verified writes and 2 reconnect confirmations. Both restores record one
byte comparison. The after-write step additionally records 2 fenced owners and
6 refused write/bash replay IDs, one carried key revocation and one complete
owner erasure after a same-layout restore. Tests scan stdout and the report for identifiers, paths and
content, enforce numeric counters, and check the redacted failure report.

Synthetic proof venue:
`python scripts/linux_oracle.py --as-root --docker-engine -- -q --no-header -p no:cacheprovider tests/test_box_move_rehearsal.py`.
The Docker-engine opt-in is root-only and leaves ordinary oracle invocations
without an engine socket. Task 6 remains open for the lead's archive run and
the remaining acceptance, regardless of synthetic results.

`box_layout_migration` is the sole cutover module.
Host admission reads deletion tombstones from the canonical broker custody
database (`deleted_principals.digest`); it must not reopen the retired shared
owner table. Missing custody state still refuses admission and destruction.
Seeding creates the broker-owned credential directory even when no HTTP
credentials survive, so a source-free owner can reconnect after cutover.
Provider definitions remain private owner files (0600); registration already
publishes the credential-free descriptor through the broker channel. An owner
process never changes a definition file to the retired broker-reader group.
Inference source validation runs only in the broker's accounting operation and
reads only that published descriptor plus the live grant, regardless of the
accounting row's liveness-token prefix. No broker owner-file reader remains.
Cutover quota planning converts the legacy tier from the read-only census with
`usage_policy.account_type`; the runtime account-type API reads the admitted
box's broker plan and is not a migration reader.

The census reports six classes:
owner, router, broker, published, dropped and regenerated, with per-table totals,
per-owner counts and report-local account labels. Policy account paths are also
pseudonymous. The broker admission map is authoritative; creation/home bindings
only corroborate it. An unknown table, conflicting ownership, unclassified file,
unattributable paid tier or HTTP slot without its owner's connection refuses.
The census materializes attribution columns, never history/checkpoint payloads.
An admitted center attributes its rows to its owner even when their named
principal is unadmitted, an earlier identity or a platform actor. This applies
through reviewed parent joins as well as direct center keys; another admitted
owner still conflicts and a missing parent still drops as `orphan_reference`.
`universe:<center>` actors resolve through live admission. Branch versions use
the admitted definition author, otherwise their admitted publisher, otherwise
drop; their own snapshot survives an absent definition. Schedules resolve
through their definition or version. Competing admitted owners still refuse.
Interchange receipts/links join their stage and receipt, and
published stages join their created definition's author. Idempotency uses its
operation to select stage, receipt or definition. Checkpoints can join several
runs only when every run resolves to the same owner.
The apply streams payload rows and rechecks their census attribution.

Dropped state includes the listed pre-ownership runs and dependent records,
never-admitted centers without owner bindings, unowned rows in the reviewed
small owner-table sets, retired CLI records/homes and the stale database copy.
The stale copy is never opened and remains only in the paired snapshot.
Owner leases, fences, accounting counters and branch-version migration records
are recreated empty in owner stores. Center-keyed event/delivery/lease records
use the admitted center or are explicitly counted as dropped. Each unresolved
row gets exactly one reason, counted per table. Precedence is
`conflicting_owners`, `orphan_reference`, `unadmitted_principal`,
`unknown_center`, `no_owner_key`. Conflicts refuse; the other four explicitly
drop. A known center without admission reports `unadmitted_principal`;
an absent center reports `unknown_center`. Missing-parent and parent-owner
failures propagate to dependents. An admitted center, including one reached
through a parent, clears unadmitted-principal, unknown-center and
ownerless-parent evidence. An unadmitted actor alone cannot invent an owner.
Dropped versions and schedules
appear under their candidate owner's `workflow history` with separate version
and schedule counts. Unresolved usage rows retain their drops, described as
`usage reservation history` and `provider bindings`.
Candidate owners survive other failures so a broken key cannot mask conflict.
Permanent identities alone do not admit a principal. Binding/admission integrity
checks and unknown-table/schema-review refusals remain. Foreign-key violations
on explicitly dropped rows are accounted losses; violations on retained rows
still refuse. No owner is invented.

File placement follows exact code-backed locations: broker identity, plan,
policy, outbound, custody and operation databases; broker fence/deletion
markers; legacy hashed browser keys beside either outbound ledger; router
provenance/release markers; regenerated SQLite sidecars, custody/refresh locks,
PID/liveness/heartbeat state and interrupted atomic-publication files. Real
SQLite files retain table-by-table schema review. Unknown files still refuse
where required and expose only `<file>.<ext>` (or `<file>` with no conventional
extension), with code-constant filenames allowed verbatim. No unknown platform
vault index or key is assumed safe merely from its extension. The round-6
identifications classify all 16 supplied files in synthetic proof; only the
lead's next authorized census establishes the real-data result.

Broker egress audit JSONL files and architecture signing state remain broker
files. The three door handshake files remain private router state; neither
their names nor contents appear in reports, and they never enter boxes.
The engine route table, reset barrier and blob-root lock regenerate. Stale
backup WAL/SHM files drop without opening them. Executor configuration and
external-agent refresh markers drop under `CLI login state`.
Run-file bodies resolve through `run_file_objects.storage_key + '.body'`,
matching the reader and cleanup code; they move or drop with that row and
their losses are counted. Objects also resolve their operation parent.

The transaction splits `.scheduler_fires.jsonl` by admitted `universe_id`
and `ledger.json` by admitted actor, falling back to admitted target. Each
owner gets the original relative filename at its box data root, preserving
entry order and the JSONL/list shapes. `api.engine_helpers._append_ledger`
and `api.branches._append_global_ledger` use this root ledger path. The retired
scheduler has no current reader; its same-path file preserves owner history.
Unknown/non-admitted entries drop with explicit counts. `history_splits`
reports `scheduled-run history` and `activity ledger`, per-account entries,
original totals and drops; `workflow_history` reports carried version/schedule
counts. `loss_summary` separately lists dropped history entries.
Both splits read back counts and content and require per-owner totals plus
drops to equal the original count before publication. Unpublished files rebuild
on resume; `after_scheduled_history_split` exercises a real exit between them.
Only verified publication removes the source histories and custody bodies.
Reports also list all five accepted user costs from the founder's decisions.

The transaction holds the exclusive layout lock and verifies a fresh paired
snapshot before its first write. A root-private `.owner-cutover.json` records
build and publish phases, the snapshot digests, owner plan and file manifests.
Build leaves the original stores intact. Each owner gets its centers,
`.agent-sessions`, `.universe-sidecars`, attributed transcripts and extracted
root databases. Shared staging contains router identity/keys/OAuth/sessions,
hook digests, broker admissions/box records/policy/outbound/custody, explicit
free or paid plans, published records and an empty next-wake index. Legacy
principal tombstones become the broker's digest-only schema; restore authority
checks read that final custody store. HTTP vault
slots stay in their existing custody location, with broker ownership; CLI slots
are filtered out. Regenerated rows are not copied.

After row reconciliation, SQLite integrity checks, rsync checksums and durable
file manifests, publication uses atomic replacement on the destination
filesystem. A crash in either phase resumes against the same snapshot pair;
a changed verified output or different pair refuses. Only verified publication
removes the host trees and writes `owner_daemon=1`, `box_layout=owner-box` and
`state=stable`. Completed application is idempotent. The deploy's step 3d keeps
its canary, idle-turn, mount, preflight and fresh-paired-snapshot gates and uses
this transaction once on a forward deploy. An intermediate owner-box marker
without the final owner-daemon version now refuses.

Deleted P2b paths: credential copying into host custody, CLI agent-home copying,
and the second activity-conversion path and its helpers. Their whole obsolete
tests were retired; the independent retired-workflow guard remains.

Synthetic Linux proof covers two owners with two centers each, every census
class, legacy runs/checkpoints/transcripts, a never-admitted center, plans,
custody, shared key/hook authentication, crash/resume and paired restoration.
The restore uses the real shell/SQLite/tar implementation; only host service,
mount/quota and absent sandbox-runtime queries are modeled. A completed cutover
accepts only its verified source pair or a valid same-layout pair. A legacy
source is normalized through the forward cutover in unpublished staging. The
single monotonic-facts projection then carries current authority, erases deleted
owners and fences owner journals. Only malformed/mismatched source receipts
refuse; newer facts are applied, not compared for equality. Services remain stopped on return.
This is not real XFS quota, sandbox, deployment or production-data proof.

The process rehearsal starts two real owner apps against the migrated roots
using the existing process launcher. Production HTTP routes return each of four
centers' conversation history and pending requests. An authenticated test probe
calls the real chat-history and pending-request writers and pause/status/resume
operation, and reads each owner's migrated
plan through the real relay and broker. Foreign-center history is absent. The
rig substitutes the fixed mount and broker peer UID, not these application
operations; it does not prove native sandbox isolation.

The platform-wide `outside_switch(id, enabled)` is router state:
`OutsideClientAuthority.set_enabled` has no owner argument, and `admit` reads
id 1 before checking any owner's grant. It is never replicated into boxes.
Device registrations, authentication receipts and UI preferences have no
session/connection-to-owner foreign key: only their recorded authenticated
subject can carry them. Reviewed rows without any real ownership key are
counted drops, including the retired public-reader marker; no default owner.

Legacy center-local `.credential-vault.json` files are counted drops and never
opened or copied by census. The old `credentials_outside_broker_custody` count
was files, not slots. Retained HTTP slots stay in the platform vault, with
broker UID ownership and custody metadata. CLI records/homes are counted drops.
Census emits an explicit all-accounts reconnect line when carried HTTP is zero,
and points to door model-connect plus model-use confirmation.

Every enumerated file has a placement (including refused/unattributed), a
masked directory pattern and count. `file_pattern_kinds` pairs each pattern
and placement with counts by `admitted_center`, `unadmitted_center`,
`unknown_dir`, `account_hash`, `broker_account`, `platform_store`, or `root_file`.
Registered shared roots keep their code-defined placement. Other unknown
and never-admitted directory contents drop; an account hash is never an owner
default. Unrecognized root, account, broker and platform files still refuse
with their kind. Identity/plan databases are extracted by their reviewed table
rules, policy deletion markers and the broker fence stay broker-owned, and
custody/refresh locks regenerate. Browser-vault keys belong outside this data
root; an arbitrary key file here refuses.
`loss_summary` groups reason and description by pseudonymous account, with
explicit dropped class and row/file counts: e.g. `CLI login state` and
`orphaned budget reservations`. The separate unowned bucket never invents an
account. Existing `losses` retains aggregate tables and masked patterns only.
Sessions and consent/seed sidecars follow
their center's admission. Center LanceDB files are preserved; provider runtime, process locks,
PID/liveness files and checkout staging are regenerated; legacy scratch and
root activity logs are dropped. Arbitrary owner filenames remain verbatim.
Unclassified root files refuse. Classification also drives output exclusion and
source cleanup, so regenerated/dropped files cannot silently survive the move.

Task 6 remains unchecked pending the lead's real-data census, real-mount
rehearsal and CI shards. Synthetic proof cannot assert the remaining real-data
refusal counts without that rerun.

## D7. Guard and deletions

- Guard test: the router package imports no owner store module and opens no
  path under `data_dir()` other than its own shared stores. The D7 AST scan in
  `tests/test_router_store_guard.py` permits runtime-mode predicates only at
  the 29 reviewed file/function role boundaries, each with its reason. It
  resolves import aliases, captured runtime/mode variables and delegated
  broker predicates/store refusals. Branchless owner identity/tool reads and
  the runtime module's own lifecycle are not alternative execution paths.
  Temporary module-copy mutation tests prove new forks are detected.
- Deleted: per-center host dirs; `box_tools` exec bridge; `file_binding`
  owner routing; daemon-side `_call_writer` placement; cross-owner listing
  filters; multi-owner tick loop.

## D8. Shared stores stay shared, owner stores move (review gap 1)

Published workflow definitions retain their authoritative row in the admitted
author's box and a published copy in commons. This matches runtime publication:
`publish_requests._flip_if_unchanged` updates the local definition, then
`_publish_snapshot` sends the frozen publication through the broker. Census counts these
local published definitions per owner separately from exclusive source placement.
Apply verifies local workflow references before publishing its completed marker.

Restore uses the single **Restore keeps monotonic facts** rule above. Legacy
pairs are normalized through this forward migration before projection, so every
consumed operation has its one owner-local journal destination. The shared
`.boxhost` retains only host destruction authority. Rehearsal verifies restored
content and refused snapshot/later write IDs and bash IDs, retained key
revocation, and complete erasure in the same paired restore.

The migration classifies every table of every shared root database:

- **Router store:** identity and WorkOS mapping, API keys and their owner,
  MCP OAuth clients and tokens, inbound hook token → owner, the canary
  account, published commons records.
- **Broker store:** `owner_identities`, `center_admissions`, `box_records`,
  credential custody, billing and plan state.
- **Owner box:** every table whose rows belong to one owner, split by owner
  principal or center id.

The census reports router, broker, owner, published, dropped and regenerated
classes with per-table counts. Dropped legacy state and never-admitted center
state are excluded from the move; platform leases, fences and accounting are
regenerated. Unknown tables, conflicting ownership and any item outside those
explicit classes refuse. Shared tables are not split.

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

### D11 as built (slice 6b, 2026-10-17)

- **One deployment image.** The shared deployment is unchanged. After its
  health checks, `scripts/roll_owner_boxes.py` resolves that same immutable
  OCI digest to its image ID and installs a static `harness-<image-id>` rootfs
  without executing the image or mounting owner data. Existing extracted
  images remain available for individual rollback. The current harness link
  advances only after boxhostd accepts the target. No host service restart is
  part of a roll.
- **Authority and metadata.** Kernel-authenticated `daemon.sock` accepts
  `roll(image[, uid])` only from root (the deploy caller) or broker UID 1002;
  router UID 1001 cannot roll. The no-UID call durably publishes the next-wake
  image and returns awake box UIDs plus the count of known sleeping boxes.
  The driver makes no per-box call for those sleeping boxes. Per-UID calls return `rolled`,
  `deferred_busy`, `asleep`, or `rolled_back` with reason `health_failed`.
  Root-private `/var/lib/boxhostd/state/images.json` records the current image
  and each UID's last launch image, using file and directory fsync plus atomic
  replacement. The launch record is written before starting a process, because
  a process can write before readiness. It survives service restart and sleep.
  These are deployment records, never reads of owner trees or connections.
- **Drain and bounds.** An awake box gets 60 seconds to drain; existing turns
  and requests hold slice 4's Activity gate. A successful atomic drain closes
  admission before SIGTERM. There is a 30-second graceful flush budget, the
  existing 60-second authenticated readiness deadline, two-second lifecycle
  HTTP calls and bounded runtime commands (spec/state five seconds, run 30,
  liveness/kill/delete/census ten). Quota subprocesses have ten-second bounds.
  A failed candidate gets a separate 60-second drain budget before rollback;
  scheduled work that started during startup cannot be killed by rollback.
  If drain/restore cannot complete safely, the control call fails loudly;
  maintenance only drains that failed box safely, and never kills its work.
  Retirement waits at most 600 seconds for an in-flight restart to settle.
- **Wakes and concurrency.** Roll reuses slice 4's atomic 10-second
  `resolve(owner, cleanup=...)` / `release(owner, lease)` wake leases, stored
  on each Box. Router forwarding releases the lease after response admission or
  on failure/cancellation and uses the normal broker route. Sleep, initial
  roll and candidate rollback use the same lease expiry, idle guard and
  drain decision, including the active-turn and deletion refusals. A dead
  router cannot pin a box forever. Startup/shutdown release the fleet lock
  during a roll, retaining capacity reservations; other interactive wakes still work and start on the
  latest published image. The driver has at most two box operations in flight.
  A busy box is deferred without interruption. Sleeping boxes are untouched.
- **Failure containment and cutover.** Only the failed box is restored to its
  own previous image. Static parsing of the image's layout declaration must
  prove `ACTIVE_BOX_LAYOUT = "owner-box"` before any launch or rollback.
  Missing or pre-cutover declarations refuse even before the first write;
  there is no override. The refusal directs the cutover operator to
  `deploy/backup-restore.sh`; this slice does not implement restore or edit
  migration/deploy fail-safe logic. Failed cleanup never advertises readiness.
- **Driver and receipt.** Root control calls have a 600-second socket bound;
  SSH has a 610-second bound and a ten-second connect bound. Static extraction
  bounds create/remove at 30 seconds each and export/tar at 180 seconds each.
  The workflow step has a 50-minute bound. The driver writes one JSON receipt
  with counts for the four outcomes plus `failed`, sorted failing UIDs only,
  and `control`/`canary` pass/fail states. It never copies a control error,
  owner identity, reply, connection record or credential into that receipt.
  Rollback, control failure or failed canary makes the driver exit nonzero;
  deferred busy and sleeping boxes are explicitly counted.
- **Acceptance.** `scripts/owner_box_canary.py` uses the existing pinned
  canary account's password and door-key environment from `load_secrets.sh`
  using the opt-in `TINYASSETS_SECRETS_PROFILE=canary`, also supported by
  PowerShell. CI injects the same secret names. It signs in at the public router door,
  verifies the configured owner and home through `/app/me`, then initializes
  public `/mcp` and invokes one `converse` nonce turn. It requires the exact
  nonce reply. Every MCP response must carry the same owner-box UID and exact
  deployed SHA as `/app/me`. OwnerIngress replaces handler provenance headers
  with the startup-admitted UID and `/etc/tinyassets-build-sha`, baked into the
  immutable image rather than inherited environment or shared release state.
  Missing build evidence fails acceptance. This uses the ordinary router,
  owner daemon and broker-backed model call, with no canary fixture endpoint.
- **Workflow hold.** The new required step follows existing public/shared
  health probes and uploads its one receipt. It does not invoke global image
  rollback on a box failure. The founder's external `disabled_manually` hold
  is unchanged; no workflow was enabled, dispatched or run in this slice.

## Billing and deletion at cutover (review round, 2026-10-18)

Billing remains shared-layer state. The census derives each paid owner's verified
customer/subscription mapping from retained billing records and carries it into
the shared plan. Missing or conflicting authority refuses with counted reason
codes on the pseudonymous account report; a paid plan is never seeded without
its mapping. Every legacy attempt with a session ID becomes a permanent
`checkout_settlements` mapping, including expired leases and settled sessions.
The one active lease remains in `plans.attempt` for checkout resumption. Only
sessionless attempts drop, with `dropped_sessionless_attempts` counted per account.
Webhooks resolve the permanent session mapping; deletion queries every retained
mapping before declaring billing absent. Settlement never deletes the mapping.
Malformed or conflicting mappings refuse census.

The router tombstone fences checkout creation, serialized with in-flight checkout
and webhook processing. Billing cleanup expires outstanding sessions through the
Stripe adapter, confirms any resulting subscription cancelled, and records a
distinct expired-attempt outcome. A subscription delivery for a tombstoned owner
immediately cancels and records that subscription. Deletion succeeds only after
no payable checkout or live subscription remains; uncertain outcomes stay retryable.

Restore authority includes router deletion receipts and owner-box revocations.
Newer deletion facts are carried into staging and complete erasure runs before publication.
Lifecycle maintenance contains suspension, refusal and authentication failures per
owner, reports an explicit suspended outcome and stops/refuses only that box.
The authenticated door deletion flow alone may request deletion-only lifecycle
admission for a suspended owner, preserving drain and tombstone ordering. Such a
wake admits local deletion only. The outer ASGI lifespan returns before all
scheduler, delivery, consumer and maintenance startup; no egress forwarder starts.
Background execution entry points hold the same activity gate as turns, so drain
observes running work and queued work cannot start after quiescence. No owner-store reader or compatibility path is added to the door.

## Founder decisions (2026-10-16)

- Build what the research says to build.
- A co-admin of a shared center is refused, as the research recommends by default. Sharing is by publishing.
- Features not ported into the owner daemon at cutover are dropped. The list is reported to the founder before the cutover deploy.

### 3c review corrections

- A credential-free grant records its commit transport (`door` or `box`), chosen
  by the broker. Only `door` plus the matching request reference completes an
  existing connection ask; a keyless agent connection cannot approve model use.
- Model receipts continue in the browser to the protected owner approval endpoint
  in the box. It verifies the live door grant, prepares only credential-free
  model metadata and raises the existing `bind_model_access` ask. The owner then
  approves that ask. Existing automatic selection ranks all accepted sources;
  the first source powers the default agent. No secret or flow enters the box.
- Failed provider revocation still erases custody, but returns
  `removed_locally_revoke_failed` and a durable provider recovery message.
  Since the token is erased, recovery is manual revocation at the provider,
  named from directory data; retrying removal retains that outcome.

## Founder decisions (2026-10-17)

- **Fresh backup for the final rehearsal: approved.** The lead pulls a fresh backup of today's production data read-only onto its own machine, never shown to an external model, runs the final rehearsal network-less, and reports only redacted pass/fail and counts.
- **Unattributable legacy data is dropped**, and the cutover report lists it with counts. That covers pre-ownership runs (Aug 5–19), never-admitted centers, unknown-center runs, orphaned usage records and unknown-directory files. Nothing that has an attributable owner is dropped silently.
- **The cutover's user costs are accepted.**
  - Every account reconnects a model source; CLI connections are retired.
  - Keys pasted into chat are no longer accepted (tap-to-connect).
  - `/mcp/hooks/` integrations re-point to `/hooks/inbound/`.
  - Local image descriptions are dropped.
  - Billing status no longer shows usage figures.
- **The cutover deploy goes ahead when every gate passes**: a clean census, the real-data rehearsal passing on a fresh backup, the lead's receipt on every slice, green CI, and the report delivered. The founder's condition: "Just make sure the architecture isn't sacrificed on review rounds or hard parts." No weakened shape, compatibility path or skipped gate gets a slice through.

## Index-only corruption at cutover

`sqlite_integrity.check` is shared by read-only census and stopped apply (including
the deploy and rehearsal apply container). Census reports named repairable indexes
without opening the source SQLite files. After the paired backup is verified,
apply repairs exactly those indexes and verifies integrity again, recording
`reindexed: <db>/<index>` in its durable report. Any other integrity error refuses;
no table repair or blanket REINDEX is permitted.

The complete shared revocation inventory is read by `restore_facts.carry_shared`
from the stopped live volume and applied only to the unpublished restored volume:

| Store | Revocation authority |
| --- | --- |
| `.api-keys.sqlite3` | `api_keys.revoked_at` |
| `.hook-owners.sqlite3` | Removed `hook_owners` rows |
| `.auth.db` | Removed/replaced `oauth_clients`, `authorization_codes`, `access_tokens`, `refresh_tokens` |
| `.outside-client-authority.sqlite3` | `outside_switch` disabling outside access |
| `.hosted-model-auth.db` | Consumed/removed `hosted_model_flows`, `connection_oauth_flows` |
| `.runtime/owner-sessions.db` | `owner_sessions`, `owner_login_flows`, `owner_login_completions`, `owner_refresh_sessions`, `owner_approval_handoffs`, `canary_sign_in_attempts`, `approval_handoffs`, `inline_model_flows` |
| `.tinyassets.db` | Legacy `user_sessions` |
| `.broker/credential-custody.db` | `llm_credential_deposit_owners`, `llm_credential_custody`, `llm_credential_deposit_commits`, `llm_credential_refresh_state`; permanent `deleted_principals` |
| `.broker/outbound.db` | `outbound_connections`, `outbound_connection_grants`, `connection_capabilities`, `browser_vault` |
| `.broker/state/owner-identities.db` | `owner_box_secrets`; permanent `owner_box_revocations` |
| `.broker/accounts/*/policy.db` | Latest `policy` generation/document and consumed `once_approvals`; `.deleted` markers |
| Files | Removed/replaced `.runtime/app_refresh_sessions/*.json`, `.connect/*`, and credential records in `.platform/credential-vaults/*/.credential-vault.json` |

Rows/files representing live authority are intersected by identity and credential
incarnation; missing live authority is revoked, never restored. Explicit revocation
fields remain revoked. Owner identity/admission/quota reservations, deletion
receipts and checkout settlement mappings are also retained. Erasure uses the
existing broker local sweep and descriptor-based owner-tree destruction. External
browser-vault keys are outside the archive and remain untouched during staging.
`box-store-quotas.fence_owner_journals` reads both owner stores, retains all consumed
IDs as `unknown_after_restore` refusals and advances epoch, generation and incarnation.
