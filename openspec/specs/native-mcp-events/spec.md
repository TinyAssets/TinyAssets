# Native MCP Events

## Intent and compatibility

Expose owner-scoped `workflow.succeeded` webhook subscriptions on the authenticated
public MCP endpoint. This is notification plumbing, not a shared conversation or
an agent impersonation layer. No UI, live grant, subscription or deployment changes.

Protocol references (verified 2026-10-14):
- https://developers.openai.com/plugins/build/mcp-events
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http

Preserve FastMCP <4 and its request-context authorization. Modern requests use a
stateless HTTP adapter around the same registered tools; legacy sessions retain
their existing transport. Advertise only implemented capabilities. Modern responses
carry `resultType`; cacheable reads use private, zero-TTL results. Validate mirrored
headers before dispatch. No MRTR, event replay, polling or stream subscription is
advertised. Use OpenAI's documented callback error -32015 (the experimental draft
currently differs). Events and tools share the existing bearer and Origin boundary.

## Subscription contract

- Require an authenticated named principal recorded by the canonical owner resolver
  as the selected command center's owner, with admin access when the center is
  private, and read access to the selected workflow. Public visibility alone and
  another owner's delegated admin grant do not confer subscription ownership.
  All of a person's explicitly owned centers are eligible, not only their home.
  Outside clients retain
  their existing `read_graph` scope; subscriptions do not create or broaden grants.
- Require exact `command_center_id` and `branch_def_id`; optional scalar equality
  filters on workflow inputs and outputs narrow successful storage receipts.
- Never emit a workflow whose input operation is `reply`. Do not emit failures,
  cancellations, raw UI events, arbitrary file writes or conversation content.
- Notify with command center, workflow and run identifiers only. Successful workflow
  completion is not intrinsically proof of an inbox save: choose output filters for
  the storage workflow's successful-save receipt when subscribing.
- Identity is canonical JSON of principal, callback URL, event name and arguments.
  Subscribe is idempotent; unsubscribe stops that identity idempotently.
- Persist subscriptions, callback configuration and signing keys in the broker's
  private `mcp-events.db`, which no box or daemon opens (ADR-025). They are
  broker-held records like every other broker credential; S28's key hierarchy
  seals them with the rest. A box never sees a callback URL or signing key.
- Send every challenge and delivery from the broker through the subscriber's
  owner egress policy (`broker/egress.py:destination`, N5); a box has no network.
  Validate HTTPS URLs, resolve all addresses on every connection, reject any
  nonpublic address, pin the connected IP with original-host TLS verification,
  reject redirects, and bound time and response size. Verify a signed random
  single-use challenge before activating; cache verified owner+URL+key briefly.
  Limit uncached verification attempts (including failures) to 32 per owner per
  60-second window in durable storage; enforce before network I/O across restarts.
- Default/max lifetime is one day; honor shorter positive `ttlMs`. `null` is granted
  a finite lifetime. Refresh rotates keys with a short dual-signature window.
- Recheck owner/resource and saved outside-client authority in the box at subscribe
  and at capture; a refused recheck revokes the subscription. Delivery sends only
  identifiers, and every read they lead to rechecks authority at the door.
  Revocation and expiry stop delivery and erase encrypted callback credentials.

## Where each step runs (ADR-025, ADR-030)

- The router answers `events/subscribe` and `events/unsubscribe` from the caller's
  verified identity; the request body never names the owner. A subscribe first
  asks the owner's box, with the delivery block removed, whether that caller may
  follow the workflow, then subscribes in the broker. Unsubscribe never wakes a box.
- The box answers `events/list`, `server/discover` and the authorization question.
  An `events/unsubscribe` that reaches a box is a routing fault and is refused.
- After a run completes, the box reports identifiers only to its bound broker
  channel. The broker names the live subscriptions for that scope; the box
  rechecks each and applies the input and output filters; the broker queues only
  the ones the box named, and only inside the owner and scope it bound.
- Deliveries and retries run on the broker's shared clock (each waker tick) and
  are kicked at once on capture. No box timer delivers, and no retry needs a box.

## Durability and acceptance

The terminal-run transaction already creates an outbox entry. Before acknowledging
that entry, fan out matching MCP deliveries durably with a unique subscription/event
key. Retries use stable event IDs, fresh Standard Webhooks timestamps/signatures,
bounded exponential backoff, attempt leases and a finite delivery lifetime. A crash
after remote receipt can duplicate a delivery; consumers deduplicate by event ID.
410 revokes the subscription; 413 and other permanent failures stop that delivery.
A 2xx acknowledges receipt only. Processing and replies are asynchronous and may be
batched by ChatGPT; there is no latency SLA.

An undecryptable subscription is revoked without blocking unrelated fanout;
non-scalar or non-finite run fields cannot match scalar filters. Transient storage
or seal-key unavailability retains the source event only within the existing
one-day delivery horizon; restoring service cannot revive expired source events.

Tests must cover protocol/legacy compatibility and per-request identity; cross-owner
refusal; callback rebinding/redirects/signatures; restart, rotation, expiry,
revocation, deduplication and bounded retry; failure/reply suppression and exact
filters. Live plugin rescan, subscription approval, idle wake and batching-on/off
acceptance are separate parent-owned checks. Room adapters can later subscribe to
their canonical-log persistence workflow and resolve authorized context by run ID.
