# Native MCP Events

## Intent and compatibility

Expose owner-scoped `workflow.succeeded` and, for registered outside agents,
`mailbox.message` webhook subscriptions on the authenticated public MCP endpoint. This is notification plumbing, not a shared conversation or
an agent impersonation layer. No UI, live grant, subscription or deployment changes.

Protocol references (verified 2026-10-14):
- https://developers.openai.com/plugins/build/mcp-events
- https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning
- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http

Live plugin rescan, subscription approval, idle wake and batching-on/off acceptance
are separate parent-owned checks. Room adapters can later subscribe to their
canonical-log persistence workflow and resolve authorized context by run ID.

## Requirements

### Requirement: Modern envelopes keep legacy compatibility

Preserve FastMCP <4 and its request-context authorization. Modern requests use a
stateless HTTP adapter around the same registered tools; legacy sessions retain
their existing transport. Advertise only implemented capabilities. Modern responses
carry `resultType`; cacheable reads use private, zero-TTL results. Validate mirrored
headers before dispatch. No MRTR, event replay, polling or stream subscription is
advertised. Use OpenAI's documented callback error -32015 (the experimental draft
currently differs). Events and tools share the existing bearer and Origin boundary.

### Requirement: Only an owner follows an exact workflow; only IDs leave

- Require an authenticated named principal recorded by the canonical owner resolver
  as the selected command center's owner, with admin access when the center is
  private, and read access to the selected workflow. Public visibility alone and
  another owner's delegated admin grant do not confer subscription ownership.
  All of a person's explicitly owned centers are eligible, not only their home.
  Outside clients retain their existing `read_graph` scope; subscriptions do not
  create or broaden grants.
- Require exact `command_center_id` and `branch_def_id`; optional scalar equality
  filters on workflow inputs and outputs narrow successful storage receipts.
  Non-scalar or non-finite run fields cannot match scalar filters.
- Never emit a workflow whose input operation is `reply`. Do not emit failures,
  cancellations, raw UI events, arbitrary file writes or conversation content.
- Notify with command center, workflow and run identifiers only. Successful workflow
  completion is not intrinsically proof of an inbox save: choose output filters for
  the storage workflow's successful-save receipt when subscribing.

### Requirement: The router subscribes; a box never sees the callback

The router answers `events/subscribe` and `events/unsubscribe` from the caller's
verified identity; the request body never names the owner (ADR-025). A
subscribe first asks the owner's box, with the delivery block removed, whether
that caller may follow the workflow, then subscribes in the broker. Unsubscribe
never wakes a box. The box answers `events/list`, `server/discover` and that
authorization question; an `events/unsubscribe` that reaches a box is a routing
fault and is refused. Envelope checks run before the box or the broker is asked.
A `mailbox.message` subscribe never asks a box: the broker checks it against the
agent registry.

### Requirement: mailbox.message rings only the agent's own client

S3b, W4. Subscribe input is `{command_center_id, agent_id}`. It is accepted only
when the caller's verified outside client is that enabled agent's registry row and
its delivery method is `mcp_event`; otherwise it is refused (-32024). One callback
per agent: a new subscription revokes the agent's earlier one. The payload is
`{command_center_id, agent_id, cursor}`, IDs and the mailbox cursor only; the event
ID is `evt_` plus a SHA-256 of owner, agent and wake. `AGENT_WAKE` sends it once
(trusted-message-ledger); it does not use the delivery outbox's retries. Disabling
the agent, or moving it off `mcp_event`, revokes its subscriptions and erases their
callback credentials.

### Requirement: Subscriptions live in the broker and expire

- Persist subscriptions, callback configuration and signing keys in the broker's
  private `mcp-events.db`, which no box or daemon opens (ADR-025). They are
  broker-held records like every other broker credential; S28's key hierarchy
  seals them with the rest. A box never sees a callback URL or signing key.
- Identity is canonical JSON of principal, callback URL, event name and arguments.
  Subscribe is idempotent; unsubscribe stops that identity idempotently. At most
  128 live subscriptions per owner; an existing one may refresh at capacity.
- Default/max lifetime is one day; honor shorter positive `ttlMs`. `null` is granted
  a finite lifetime. Refresh rotates keys with a short dual-signature window.
- Revocation and expiry stop delivery and erase the callback credentials. An
  unreadable subscription is revoked without blocking unrelated fanout.

### Requirement: Callbacks leave only from the broker via owner egress

Send every challenge and delivery from the broker through the subscriber's owner
egress policy (`broker/egress.py:destination`, N5); a box has no network. Validate
HTTPS URLs, resolve all addresses on every connection, reject any nonpublic
address, pin the connected IP with original-host TLS verification, reject
redirects, and bound time and response size. Verify a signed random single-use
challenge before activating; cache verified owner+URL+key briefly. Limit uncached
verification attempts (including failures) to 32 per owner per 60-second window
in durable storage; enforce before network I/O across restarts. Refusals carry a
bounded code and reason, never a callback URL, key or response body.

### Requirement: A box reports runs as IDs and rechecks authority

The terminal-run transaction already creates an outbox entry. Before acknowledging
that entry, the box reports the run's identifiers to its bound broker channel. The
broker names the live subscriptions for that scope; the box rechecks owner/resource
and saved outside-client authority for each and applies the input and output
filters; the broker queues only the ones the box named, and only inside the owner
and scope the relay bound. A refused recheck revokes the subscription. Only the
owner's own runs are reported. A broker that cannot be reached keeps the source
event owed, only within the one-day delivery horizon.

### Requirement: Deliveries are durable and retried on the broker clock

Fan out with a unique subscription/event key. Retries use stable event IDs, fresh
Standard Webhooks timestamps/signatures, bounded exponential backoff, attempt
leases and a finite delivery lifetime. A crash after remote receipt can duplicate a
delivery; consumers deduplicate by event ID. 410 revokes the subscription; 413 and
other permanent failures stop that delivery; a refused egress is retried. A 2xx
acknowledges receipt only; there is no latency SLA. Deliveries and retries run on
the broker's shared clock (each waker tick) and are kicked at once on capture. No
box timer delivers, and no retry needs a box.
