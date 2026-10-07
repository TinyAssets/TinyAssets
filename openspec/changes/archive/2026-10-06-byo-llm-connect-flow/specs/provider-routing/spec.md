# Provider routing

## MODIFIED Requirements

### Requirement: Served turns use only fresh universe-owned provider authority

Every universe-scoped provider call SHALL pass served-authority validation;
universe configuration and process configuration are projections, never
authority. A `converse` call is eligible only when the explicitly carried
current-request capability, selected agent binding and revision,
requester-local assignment, active `ProviderWorkBinding`, and current opaque
vault custody reference all agree on the authenticated founder, universe,
provider, generations, digests, operation, role, expiry, and budgets. The
authorized chain SHALL contain exactly that provider and SHALL never widen to
an ambient host credential, runtime preference, default chain, or another
universe's binding. Other universe-scoped operations SHALL hold until an exact
server authority for that operation exists.

#### Scenario: Exact served authority is current

- **WHEN** a founder `converse` request carries one live server-issued request
  lease and every selected server-held record is fresh and exact
- **THEN** the router calls only the assignment's provider under its token,
  cost, and invocation ceilings

#### Scenario: No universe-authorized serving binding

- **WHEN** the served request has no current serving binding or any selected
  principal, revision, assignment, binding, custody, operation, role,
  generation, digest, state, expiry, or budget check fails
- **THEN** the router raises the typed provider-authority hold with
  connect-provider guidance before provider or credential access
- **AND** no ambient provider is attempted

#### Scenario: Configuration names a provider without live authority

- **WHEN** a universe-scoped call, including `run_graph`, has provider
  preferences or an allowlist but no matching live server-issued authority
- **THEN** the call holds before provider access
- **AND** configuration alone cannot select or invoke a provider

#### Scenario: Credential rotates after request selection

- **WHEN** the vault custody record changes before the sink snapshots it
- **THEN** the call holds and the provider receives zero calls

#### Scenario: Credential rotates after snapshot admission

- **WHEN** another process replaces path-backed credential material after the
  sink derived custody from copied snapshot bytes
- **THEN** the in-flight provider reads only that sealed snapshot through a
  read-only sandbox mount
- **AND** the next admission revalidates the source and cannot reuse stale custody

### Requirement: Provider request authority originates at the current message

Authenticated MCP provider authority SHALL be re-derived only from
`request_ctx.get().request` for the exact non-deferred `tools/call` message.
Middleware SHALL reserve a one-shot principal/session/request/tool token, the
registered wrapper SHALL claim it in the actual worker, and both boundaries
SHALL revoke it before result release. Outer ASGI identity, inherited or
snapshotted HTTP helpers, initialize state, anonymous wiki-canary authority,
serialized lookalikes, and prior-message state SHALL grant no provider
authority. Signed founder Slack ingress SHALL mint the same bounded capability
class with its exact app-ingress mechanism and routed agent revision.

#### Scenario: Outer request identity exists without current message request

- **WHEN** an authenticated outer ASGI context exists but the FastMCP
  per-message request is absent
- **THEN** middleware mints no provider reserve or capability

#### Scenario: Signed founder Slack event

- **WHEN** the HMAC-authenticated app ingress recognizes a current founder and
  routes an exact channel binding
- **THEN** it claims one app-event request capability bound to the founder,
  event, session, mechanism, issuer, and routed agent revision
- **AND** revokes it after the synchronous turn returns

### Requirement: Assignment readers and writers are requester-local fenced

The requester-local admission fence SHALL serialize cooperative assignment
publication, credential-vault replacement, provider-reference changes, and
serving-state changes for the canonical universe. Credential safety SHALL
NOT depend on direct filesystem writers taking that advisory fence. At each
served admission the sink SHALL copy exact credential material into a unique
launch directory, derive and compare custody generation/digest from the copied
bytes, and launch only from that snapshot. Snapshot cleanup SHALL be
best-effort and SHALL NOT mask the provider result or error.

#### Scenario: Non-cooperating credential writer races a served launch

- **WHEN** a child process replaces the source credential without taking the
  admission fence while an admitted provider is running
- **THEN** the running process cannot observe the replacement through its sealed snapshot
- **AND** a later admission observes the changed source generation and fails closed

### Requirement: Served-call budgets are durable launch authority

Immediately before provider launch, the router SHALL atomically revalidate the
assignment and credential generation and reserve invocation, token, and cost
budget in the authority database. Concurrent reservations, completed usage,
and indeterminate failures SHALL count against the exact binding generation.
Each admitted request capability SHALL independently limit provider calls to a
small reply/learning budget; the durable binding-generation invocation ceiling
SHALL be a high-water anti-runaway bound spanning multiple normal requests, not
the per-request limit.
After completion, actual adapter usage SHALL be recorded and the reservation
settled; a result whose actual usage exceeds the per-call reservation estimate
SHALL be marked `exceeded` and DELIVERED (never withheld) — the reply was
already generated and metered on the requester's own subscription, and a served
adapter's real input is dominated by context it injects itself (a workspace +
tool schemas), so a normal turn routinely exceeds a prompt-derived estimate.
The aggregate anti-runaway bound is the durable binding-generation invocation
high-water within the rolling window, not per-call withholding. (2026-08-22:
this supersedes the earlier withhold-on-over-ceiling rule, which discarded
legitimate paid-for replies on nearly every served turn.)

#### Scenario: Provider actual usage exceeds the reservation estimate

- **WHEN** actual token or cost usage exceeds the per-call reservation
- **THEN** the reservation is marked `exceeded`, actual usage is recorded, and
  the generated reply is returned to the requester
- **AND** the durable binding-generation invocation high-water still bounds a
  runaway loop across requests

#### Scenario: Two founder turns use one binding

- **WHEN** two consecutive admitted requests each make a reply and learning call
- **THEN** all four calls fit their independent request budgets without a rebind
- **AND** the durable binding high-water still rejects calls beyond its ceiling

### Requirement: Bindable Codex serving runs in an OS sandbox

A served Codex adapter call SHALL execute under an external OS sandbox that
read-only mounts the selected universe, exposes only its contained Codex auth
snapshot, follows any installed wrapper to the real executable tree and mounts
that tree read-only, disables Codex shell execution and user/project rules, and
emits machine-readable usage accounting. The credential snapshot subtree SHALL
be hidden from the workspace mount. If the sandbox, executable-tree mount, or
accounting output is unavailable, the adapter SHALL fail closed before
returning a reply.

The served launch SHALL disable the Codex `apps` feature so the subscription
account's installed host-owned connectors — including TinyAssets' own public
`/mcp` connector — are never exposed to the served model as tools. It SHALL also
clear any project-level `mcp_servers` so a mounted universe's own Codex config
cannot inject an MCP server into the served turn. A served turn is the model
answering as the universe, never a client of the account's connectors. Because
Codex rejects unknown feature names, an adapter that cannot express the
apps-disable SHALL fail closed rather than launch with host apps enabled.

#### Scenario: Sandboxed Codex turn completes

- **WHEN** a live Codex serving authority reaches the real adapter
- **THEN** Codex runs inside the OS sandbox and returns its reply plus token
  accounting
- **AND** no unconfined fallback launch is attempted

#### Scenario: Host-owned account connectors never reach the served model

- **WHEN** the founder's subscription account has the TinyAssets connector (or
  any other ChatGPT app) installed and a served Codex turn runs
- **THEN** the launch disables the `apps` feature and clears project
  `mcp_servers`, so no `codex_apps` tool is offered to the model
- **AND** the universe replies as itself instead of relaying the turn back
  through its own connector and returning a reauthentication notice
