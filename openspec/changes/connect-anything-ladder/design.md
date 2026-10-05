## Context

The supplied audit B2/L6/L7 cites HTTP-only `outbound_connections`/ta inventory, the deferred attached-MCP section of universe-agent-harness D6a, and D5's unfinished browser broker. `inline-connect-and-approve` explicitly leaves MCP URLs and browser fallback to follow-up work. This change supplies those missing transport, storage and custody contracts; it does not create a parallel request inbox or approval engine.

## Goals / Non-Goals

Connect an arbitrary supported API, MCP server or login-only site inline through protocol shapes. The owner's own agent can read documentation and configure those shapes or write reusable skills; the platform does not call an LLM. The owner controls policy, with cross-user isolation the only immutable platform invariant. Credentials stay daemon-side as explicitly requested.

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
| `browser` | Schema version 1, normalized login origin, owner-assigned label, opaque vault/session reference, configuration revision, session generation, supported use metadata and activation state. |

No password, token, cookie, bearer-bearing URL, environment secret or upstream session ID is stored in metadata, chat, package exports or model context. Secret entry deposits directly into existing daemon custody. URLs with credentials/query tokens require the secure capture path and are normalized before persistence. Opaque references are scoped by owner and connection incarnation; possession of an ID is not authorization.

State is `draft -> connecting -> active`, with visible `failed`, `revoked` or `expired` outcomes. Persist a request idempotency key and staged custody reference before exchange; active authority is finalized under the existing connection/request coordinator only after successful binding. A crash resumes the same draft and reconciles deposit/activation; duplicate callbacks cannot create a second grant or wake. Revocation fences dispatch before cleanup, invalidates sessions/catalog handles and retries cleanup durably. Reconnection gets a new incarnation so prior handles/approvals cannot authorize it. Removing a wrapper attachment preserves an independently owned backing HTTP connection; removing that backing connection fences dependent attachments.

### MCP transport semantics

For HTTP, use a bound generic HTTP connection for endpoint/auth/egress. Support negotiated Streamable HTTP initialization, tools/list pagination and tools/call, including streaming responses and session renewal. Honor negotiated protocol versions; report unsupported servers rather than pretending single-request JSON-RPC is full support. Protocol auth failures return to the same connect card for generic OAuth or secure deposit. Bind server session IDs to this connection incarnation and execution principal, never globally by URL. Recheck current connection authority at every call/reconnect and before following a changed destination.

HTTP MCP depends on `broker-streaming-contract`, whose framing, backpressure, cross-chunk credential scanning, cancellation and durable op_id reconciliation must be used. Do not build a direct HTTP/SSE side channel around its scanner or generation fence. Until that broker is available, report streaming MCP as unavailable; a buffered single-call adapter is not an accepted substitute.

For stdio, the owner activates an exact executable/argv/cwd configuration in the jail. It is an argv vector, not a daemon shell command, with no-link paths inside that owner's execution environment. Configuration changes increment revision and pass the owner's current activation policy again. Subprocesses inherit only their scoped launch grants and permitted resources, not daemon environment credentials. Missing executable/dependencies produce actionable failure, never a host-process fallback. Transport support adds no new extension system: MCP remains a source of ta capabilities.

Credentialed stdio uses the existing scoped egress broker: metadata may reference permitted owner HTTP connection IDs/incarnations and secret-free local proxy addresses; the broker substitutes authentication only at the network boundary and scans replies. The child receives no raw token environment variables or credential files. A server that insists on raw secrets and cannot use that general broker shape is explicitly unsupported in v1, with HTTP MCP or owner takeover offered where possible. Do not satisfy such a server by exposing vault material inside the agent's jail. This is a protocol limitation, not a service-specific allowlist.

Expose search/describe and call through `mcp:<attachment-id>:<tool-name>`, with validated/escaped tool identifiers and no collisions with platform or local extension names. Catalog schemas/descriptions are untrusted data. Cache only within the owner/attachment/incarnation and catalog revision; calls validate against the current revision or return a stale-catalog error. Server-advertised tools do not grant new authority. Preserve operation IDs and cancellation; transport retry may repeat safe initialization/discovery, but an uncertain tools/call outcome requires reconciliation or owner-directed retry, never automatic duplicate side effects.

### Browser custody through D5

Reuse D5's owner/center/activity-bound browser context, live view and Take over/Return control. Add a daemon-owned login session bound to owner, connection draft/incarnation, expected origin, initiating owner session, task and expiry. The inline control launches a protected owner-only capture view; credentials, MFA codes, cookies and session storage flow to the daemon/browser broker and never through an agent message or extension payload. The agent receives only an opaque surrogate handle and public status. Surrogates authorize broker use under current owner permissions; they cannot be exchanged for raw vault material.

During login takeover, agent input, screenshots, DOM snapshots, network/body inspection and traces of the capture context are suspended. The broker suppresses password/OTP values and cookies from artifacts, error payloads and logging, not just from the chat renderer. Credentials may be injected only into the bound origin/context; redirects to a new credential-receiving origin require a new explicit binding in the protected capture view. Cross-origin pages cannot redeem capture handles. On successful return, discard login traces and expose only the ordinary authenticated page state with credential-bearing fields/headers excluded. Authenticated content is available according to owner permission; reusable credentials are not.

Credentialed browser contexts stay in the daemon broker's isolated process/profile, inaccessible to the agent's shell or filesystem. The agent interface offers structured navigation, click, nonsecret input and sanitized rendered-page observations; it offers no arbitrary JavaScript/evaluate, DevTools/CDP endpoint, cookie/storage export, request interception, profile download or raw network bodies/headers. Page scripts may use their own storage to function, but the agent cannot evaluate document.cookie or local/sessionStorage, read password/autofill fields, or retrieve authentication-bearing URL fragments. Scrub known captured/session credential values from allowed observations and never return the login page's retained input state. If the broker cannot safely expose a site's post-login surface, keep it owner-only and report that limitation instead of claiming credential-blind agent access. Browser implementation must demonstrate these controls before enabling the fallback; unrestricted evaluation plus output redaction alone does not satisfy this contract.

Where a passkey or challenge needs the owner, keep takeover open with truthful waiting state. A disconnected/expired login returns to the same card. Cancellation/Stop destroys staged session artifacts and cannot promote a late callback. Reuse vault/session cleanup and account-deletion lifecycle, including scoped backups, rather than retaining abandoned browser profiles. Logout from TinyAssets invalidates capture sessions; disconnecting a browser connection additionally revokes its reusable browser session. No claim is made to undo already-completed remote actions or delete the upstream account.

### Reconcile the existing lanes

The D6a attached-MCP deferral now points here for implementation; ta discovery/launch remains D6-owned. D5 still builds the browser/live-view substrate; this owns only its connection custody extension. Inline request authority and server continuation remain solely in inline-connect-and-approve. L3/L4 consume these shapes and the existing D10 starter delivery mechanism; this is acceptance for the ladder, not duplicate card/skill implementation. D9 exports inert connection requirements, never active attachments, cookies or source-owner bindings.

## Migration Plan

1. Add typed connection metadata with transactional schema migration, preserving HTTP rows and existing grants. Version checks fail visibly before older software can misinterpret new shapes.
2. Add MCP adapters and D5 custody behind existing connection lifecycle/inline request operations, then wire discovery into ta and L3/L4.
3. Prove a previously unknown MCP server and login-only site without adding platform-specific code. Rollback disables new-shape dispatch while retaining scoped records and revocation/cleanup access; it never converts new shapes into HTTP or drops custody/history. Existing HTTP stays operational.

## Risks / Trade-offs

- External servers can be unavailable or malicious: isolate per-owner transports and report truthful unsupported/error states; no model-based platform security agent.
- Browser artifacts can reveal credentials: suspend all agent observation while capturing and verify artifacts/logs after return, failure and cancellation.
- Streaming reconnect can duplicate effects: separate safe transport reinitialization from tools/call replay and surface uncertain outcomes.
- D5 is unfinished: build its existing broker first; do not ship a fake browser fallback.

## Open Questions

None about authority, custody or storage. Exact protocol-version support is negotiated and advertised by the implementation; unsupported versions must fail visibly and cannot be marketed as connected.

## Muse parity and build order (2026-10-04)

Source: [connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md), sections 3–4. Build in this order; each row names one implementation owner rather than creating competing paths:

| Order / research rows | Delivery owner and scope |
|---|---|
| 1 / 1,2,20,21 | This change consumes L3/L4: one connect card from chat or settings branching on OAuth / API key / MCP (and basic/none where supported), one connections list, several labelled accounts per service. |
| 2–3 / 3,5,8 | This change + generic-oauth-connections + broker-streaming-contract: MCP attach and credential-blind secret entry/egress together, before enabling credentialed tools. |
| 4 / 6,7,17 | inline-connect-and-approve: approval sheet, scoped classes/durations, Needs you and push; replaces all side requests panels. |
| 5 / 4 | This change: agent-authored saved, self-tested, revocable connector extensions. |
| 6 / 14,18,19 | Existing automation-event-triggers, channel-agnostic-inbound/outbound and activity history own triggers/ledger; channel bundle composition is agent-team-channel-templates. |
| 7 / 9,10 | D5 plus this change's browser custody: cloud live view, Take over / Return control / Stop and Needs you challenge handoff. |
| 8 / 13 | private-network-attach: owner overlay routes and first-contact host approval. |
| 9 / 11,12 | companion-local-mcp: desktop then phone local capabilities through the same attach/grant protocol. |
| 10 / 16,22 | exact-total-spend-rail owns payments; shared provider/connector publishing follows existing command-center-packages, recipient-updates and attribution ownership. |

### Unified card, accounts and standard MCP OAuth

The connect card carries a discriminated auth type and account label, not provider-specific form logic. Switching type keeps safe draft fields but invalidates staged credentials/approval for the old shape. Each service permits multiple independent connection IDs/incarnations; account identity is confirmed after authentication. Ambiguous account selection asks the owner and never overwrites an existing account or silently picks the first. Settings and ta connect enter the same request/finalization flow.

MCP remote attach discovers protected-resource and authorization-server metadata. Support OAuth with PKCE S256, dynamic client registration (DCR), a TinyAssets-hosted HTTPS client metadata document (CIMD), and explicitly configured static-client fallback; advertise which method the server supports rather than requiring all simultaneously. Bearer/API-key auth uses private capture, never URL query credentials. Reuse generic-oauth-connections and protected server-side PKCE/state/session validation; bind resource/audience and the registered redirect URI, validate discovered endpoints through broker policy, and never forward one resource's token to another resource. Unsupported registration produces an actionable error. No per-provider code and no account credential export.

### Secret entry and reusable connectors

A protected secret-entry card deposits directly to vault custody. The agent gets scoped surrogate references only. The egress broker resolves an allowlist of credential slots per connection incarnation and exact destination/method/path predicate, substitutes at the network boundary, and applies DNS/IP/redirect SSRF checks and secret scanning. Connection A cannot request B's secret; raw credentials never enter sandbox environment/files, logs, transcript, tests or package bytes. Existing sandbox egress must not bypass broker-governed credential use. Read-data/taint evidence can inform editable owner policy; it cannot grant access.

Given API docs or OpenAPI, the owner's agent writes an ordinary extension with declared credential slots and a connection binding, runs a non-destructive self-test (or separately approves a test effect), and saves the exact tested revision with test receipt. A failed test is visibly failed, never labelled connected. List saved connectors beside other connections with source/version and revoke/disconnect controls; updates invalidate the old test receipt until re-tested. Reuse the current extension runtime and activation permissions. Sharing exports code, slot requirements and safe test metadata, never credentials, sample private responses or live grants. Revocation fences new dispatch before cleanup.
