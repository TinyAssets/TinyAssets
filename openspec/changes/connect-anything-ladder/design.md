## Context

The supplied audit B2/L6/L7 cites HTTP-only `outbound_connections`/ta inventory, the deferred attached-MCP section of universe-agent-harness D6a, and D5's unfinished browser broker. `inline-connect-and-approve` explicitly leaves MCP URLs and browser fallback to follow-up work. This change supplies MCP transport, storage and secret custody; browser-login-custody separately consumes completed D5 and saved-agent-connectors owns tested extensions; it does not create a parallel request inbox or approval engine.

## Goals / Non-Goals

Connect an arbitrary supported API, MCP server inline through protocol shapes. The owner's own agent can read documentation and configure those shapes or write reusable skills; the platform does not call an LLM. The owner controls policy, with cross-user isolation the only immutable platform invariant. Credentials default to daemon custody; exact-server owner raw-key opt-in is described below.

Do not promise that every remote service supports automation or that every login challenge can be completed unattended. Unsupported transport, CAPTCHA, passkeys or device confirmation must produce an honest owner handoff in the same chat line. No service-name switch statements, provider SDKs or required registry additions; upstream OAuth registration requirements remain an owner-visible external requirement, not a new adapter project.

## Decisions

### One inline ladder, existing request authority

The editable L4 skill chooses a compatible route from supplied information: generic discovered/configured OAuth or API access, an attached MCP URL/stdio command, then browser login where an API route is unavailable. This is a suggested starter order, not immutable policy; the owner can pick a route directly. Reuse L3's single inline card at the originating turn with status, secure input/Take over and cancellation. Browser/provider login may open a protected view or popup, but progress, result and retry stay in that original chat line.

Use the existing bound pending request with an opaque connection draft ID and normalized shape metadata. It stores owner/center/agent/task/turn, revision, deadline and public status, never credentials. The protected body displays actual origin/endpoint or stdio command, requested use and the owner's current policy. Current owner rules choose whether activation needs a one-time decision or is already permitted. A text reply such as Approved cannot activate a connection.

Finalization and continuation use inline-connect-and-approve's existing coordinator, effect intent, durable event and processed-ack. Stop, cancellation, logout/account switch, expiry or a changed action revision prevent finalization and invalidate its capture handle. Failure reports a recoverable error or explicit unsupported state; it never creates a plausible active connection. An unpowered owner can deposit, connect and disconnect through deterministic UI operations; agent planning/resumption waits for their model connection.

### Extend the connection record, not a second authority store

Extend the existing owner-scoped connection store with a discriminated shape and schema version. Existing HTTP rows remain unchanged and readable. Add typed child metadata keyed by `(owner_id, connection_id, incarnation)` with foreign keys to the connection authority; use the store's existing center/agent use grants rather than baking a second ownership model into the metadata. Every read, mutation, transport lookup and vault reference resolves the authenticated owner and applicable center grant first.

| Shape | Persisted nonsecret metadata |
|---|---|
| `mcp` | Schema version 1, transport (`http` or `stdio`), display name, normalized endpoint plus backing HTTP connection/incarnation OR relative executable/argv/working directory, configuration revision, protocol negotiation result, tools catalog revision/hash and activation state. |

No password, token, cookie, bearer-bearing URL, environment secret or upstream session ID is stored in metadata, chat, package exports or model context. Secret entry deposits directly into existing daemon custody. URLs with credentials/query tokens require the secure capture path and are normalized before persistence. Opaque references are scoped by owner and connection incarnation; possession of an ID is not authorization.

State is `draft -> connecting -> active`, with visible `failed`, `revoked` or `expired` outcomes. Persist a request idempotency key and staged custody reference before exchange; active authority is finalized under the existing connection/request coordinator only after successful binding. A crash resumes the same draft and reconciles deposit/activation; duplicate callbacks cannot create a second grant or wake. Revocation fences dispatch before cleanup, invalidates sessions/catalog handles and retries cleanup durably. Reconnection gets a new incarnation so prior handles/approvals cannot authorize it. Removing a wrapper attachment preserves an independently owned backing HTTP connection; removing that backing connection fences dependent attachments.

### MCP transport semantics

For HTTP, use a bound generic HTTP connection for endpoint/auth/egress. Support negotiated Streamable HTTP initialization, tools/list pagination and tools/call, including streaming responses and session renewal. Honor negotiated protocol versions; report unsupported servers rather than pretending single-request JSON-RPC is full support. Protocol auth failures return to the same connect card for generic OAuth or secure deposit. Bind server session IDs to this connection incarnation and execution principal, never globally by URL. Recheck current connection authority at every call/reconnect and before following a changed destination.

HTTP MCP depends on `broker-streaming-contract`, whose framing, backpressure, cross-chunk credential scanning, cancellation and durable op_id reconciliation must be used. Do not build a direct HTTP/SSE side channel around its scanner or generation fence. Until that broker is available, report streaming MCP as unavailable; a buffered single-call adapter is not an accepted substitute.

For stdio, the owner activates an exact executable/argv/cwd configuration in the jail. It is an argv vector, not a daemon shell command, with no-link paths inside that owner's execution environment. Configuration changes increment revision and pass the owner's current activation policy again. Subprocesses inherit only their scoped launch grants and permitted resources, not daemon environment credentials. Missing executable/dependencies produce actionable failure, never a host-process fallback. Transport support adds no new extension system: MCP remains a source of ta capabilities.

Credentialed stdio defaults to scoped egress-broker authentication injection using permitted owner connection IDs/incarnations and secret-free proxy handles. For a server requiring a raw key, the owner can explicitly opt in per exact server configuration revision and named own secret. The protected view names the exact server code and its author and warns that only that server's code and its author can read/use the injected key; inject only that named secret into the server's isolated sandbox, never daemon-wide credentials. A raw-key stdio server runs in its own sandbox with a separate process, user identity and filesystem view. The agent shell, hooks and other extensions reach it only over its mediated stdio pipe and have no access to its /proc entries, environment, arguments or files. Reuse the egress broker's incremental secret scanner on both stdout and stderr, including secret bytes split across chunks, before any output reaches model context, logs or transcript; withhold matching bytes and report a credential-free typed failure. Revoke or changed configuration invalidates the opt-in. This exception cannot expose another owner's secret and is never exported with a package. Broker-only custody remains the starter default.


Expose search/describe and call through `mcp:<attachment-id>:<tool-name>`, with validated/escaped tool identifiers and no collisions with platform or local extension names. Catalog schemas/descriptions are untrusted data. Cache only within the owner/attachment/incarnation and catalog revision; calls validate against the current revision or return a stale-catalog error. Server-advertised tools do not grant new authority. Preserve operation IDs and cancellation; transport retry may repeat safe initialization/discovery, but an uncertain tools/call outcome requires reconciliation or owner-directed retry, never automatic duplicate side effects.

### Delivery ownership and dependency order

1. `inline-connect-and-approve` solely owns L3 card consolidation, labelled multi-account selection, auth-shape switching and onboarding-web-app changes. This lane consumes that card; L4/D10 own editable starter skill delivery.
2. `generic-oauth-connections` and `broker-streaming-contract` precede this lane's MCP attach, secret entry and standard MCP OAuth. D6 owns ta integration.
3. `saved-agent-connectors` consumes this lane's slots/egress and lifecycle for tested extensions.
4. `browser-login-custody` consumes this lane's lifecycle and is additionally gated on completed D5. It is independent of saved connectors.

The registered OAuth provider directory, including the platform Google client, stays as optional data. Only per-platform code is out; no directory entry is required for arbitrary service attachment. D9 exports inert requirements, never live owner bindings.

## Migration Plan

1. Add typed connection metadata with transactional schema migration, preserving HTTP rows and existing grants. Version checks fail visibly before older software can misinterpret new shapes.
2. Add MCP adapters behind existing connection lifecycle/inline request operations, then wire discovery into ta and L3/L4.
3. Prove a previously unknown MCP server  without adding platform-specific code. Rollback disables new-shape dispatch while retaining scoped records and revocation/cleanup access; it never converts new shapes into HTTP or drops custody/history. Existing HTTP stays operational.

## Risks / Trade-offs

- External servers can be unavailable or malicious: isolate per-owner transports and report truthful unsupported/error states; no model-based platform security agent.
- Streaming reconnect can duplicate effects: separate safe transport reinitialization from tools/call replay and surface uncertain outcomes.
- D5 gates browser-login-custody only; MCP delivery does not wait for that independent substrate.

## Open Questions

None about authority, custody or storage. Exact protocol-version support is negotiated and advertised by the implementation; unsupported versions must fail visibly and cannot be marketed as connected.

## Standard MCP OAuth

MCP remote attach discovers protected-resource and authorization-server metadata. Support OAuth with PKCE S256, dynamic client registration (DCR), a TinyAssets-hosted HTTPS client metadata document (CIMD), and explicitly configured static-client fallback; advertise which method the server supports rather than requiring all simultaneously. Bearer/API-key auth uses private capture, never URL query credentials. Reuse generic-oauth-connections and protected server-side PKCE/state/session validation; bind resource/audience and the registered redirect URI, validate discovered endpoints through broker policy, and never forward one resource's token to another resource. Unsupported registration produces an actionable error. No per-provider code and no account credential export.

### Secret entry

A protected secret-entry card deposits directly to vault custody. The agent gets scoped surrogate references only. The egress broker resolves an allowlist of credential slots per connection incarnation and exact destination/method/path predicate, substitutes at the network boundary, and applies DNS/IP/redirect SSRF checks and secret scanning. Connection A cannot request B's secret; raw credentials never enter sandbox environment/files under the broker-only default; explicit per-server raw-key opt-in is the exception above. Neither mode exports secrets to logs, transcript, tests or package bytes. Existing sandbox egress must not bypass broker-governed credential use. Read-data/taint evidence can inform editable owner policy; it cannot grant access.
