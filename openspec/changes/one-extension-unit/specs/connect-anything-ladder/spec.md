## ADDED Requirements

### Requirement: Current remote MCP is compatible without effect replay
The adapter SHALL support 2026-07-28 per-request metadata, mirrored headers,
JSON and request-scoped SSE through the existing broker, and legacy negotiated
2025-11-25, 2025-06-18 and 2025-03-26 sessions. It SHALL propagate bounded
broker-scanned authentication challenges as structured host-only failure data.
Modern HTTP cancellation SHALL close the response stream; legacy cancellation
SHALL retain its best-effort notification. HTTP or transport failures SHALL NOT
cause automatic tool replay.

#### Scenario: A modern endpoint requires URL input
- **WHEN** a tools/call returns a valid input_required URL request
- **THEN** the trusted host receives the full URL, server binding and pending operation
- **AND** after host consent the adapter continues that same operation with exact opaque requestState and matching inputResponses, using fresh request IDs without a chat continue message
- **AND** changed authority, host cancellation, changed URL or uncertain delivery cannot reuse prior consent for another operation

#### Scenario: A legacy endpoint rejects the modern discovery probe
- **WHEN** a read-only modern probe receives a non-modern 400 rejection
- **THEN** the client initializes a supported legacy version and binds its session to that instance
- **AND** recognized modern version or header errors are surfaced or negotiated without an indiscriminate legacy fallback

#### Scenario: A round is lost after transmission
- **WHEN** a continuation POST has an uncertain result
- **THEN** it is not resent, and its opaque state does not appear in model-visible errors

### Requirement: Connection routes are general shapes inline in chat
The system SHALL support MCP attachment shapes through the existing bound inline connection request and continuation mechanism, alongside generic OAuth/HTTP. It SHALL NOT require per-platform code, a directory entry or a platform LLM. The owner SHALL control route and approval policy; cross-user isolation SHALL remain the sole immutable platform behavioral invariant. Credentials SHALL remain daemon-side by default, with only explicit owner-approved stdio injection into that server's separate owner-bound sandbox permitted.

#### Scenario: An unknown service is requested
- **WHEN** the owner asks to connect a service with a compatible MCP endpoint but no platform registration
- **THEN** their agent or direct owner controls configure the general shape without a platform patch
- **AND** connection progress, failure/retry and completion stay attached to the original chat line

#### Scenario: Owner has no connected model
- **WHEN** an unpowered owner uses direct connection or removal controls
- **THEN** the deterministic connection operation remains available and any agent continuation waits for their own model
- **AND** no host, shared or platform model credential is used

### Requirement: MCP attachments share existing connection authority
MCP attachments SHALL use typed owner-scoped connection metadata and current use grants, remote HTTP custody or jailed stdio, negotiated initialization/list/call transport and ta names `mcp:<attachment-id>:<tool-name>`. Configuration, session and tool-catalog revisions SHALL be bound to connection incarnations. Untrusted server metadata SHALL NOT create authority. Stale/revoked calls SHALL fail before dispatch, and uncertain external calls SHALL NOT be blindly replayed.

HTTP streaming SHALL use broker-streaming-contract's incremental scanning, cancellation, backpressure and reconciliation. Credentialed stdio SHALL default to broker injection. The owner SHALL be able to opt in to named own-key injection for an exact server configuration revision after a protected warning naming the exact server code and its author as the only parties that can read/use the injected key. The raw-key server SHALL run in its own sandbox with a separate process, user identity and filesystem view. The agent shell, hooks and other extensions SHALL reach it only over its mediated stdio pipe, with no access to its /proc entries, environment, arguments or files. Both stdout and stderr SHALL pass through the egress broker's incremental secret scanner, including secret bytes split across chunks, before reaching model context, logs or transcript. Matching bytes SHALL be withheld with a credential-free typed failure. This grant SHALL NOT include other owners' secrets, survive configuration changes or be exported in packages.

#### Scenario: A stdio server requires a raw key
- **WHEN** the owner explicitly permits named own-key injection into that exact server revision
- **THEN** only that key is injected into that server's separate owner-bound sandbox
- **AND** without that opt-in the broker-only default remains and the card offers the owner the choice instead of declaring the server permanently unsupported

#### Scenario: Other code cannot inspect a raw-key server
- **WHEN** the agent shell or an activated extension from another author attempts to read the raw-key server's /proc entries, environment, arguments or files
- **THEN** sandbox isolation denies access and the only communication path is the mediated stdio pipe
- **AND** neither the agent nor the unrelated extension receives the injected key

#### Scenario: A raw-key server echoes its credential
- **WHEN** the server emits injected secret bytes on stdout or stderr, including bytes split across chunks
- **THEN** the reused egress broker scanner withholds those bytes before any model context, log or transcript sink receives them and reports a credential-free typed failure

#### Scenario: Streaming splits credential bytes
- **WHEN** a broker-authenticated response splits a key across frames
- **THEN** the broker withholds credential material and reports the typed failure without bypassing scanning

#### Scenario: Remote tools stream and their catalog changes
- **WHEN** an activated HTTP server returns streamed results or changes its tool schema
- **THEN** the transport handles negotiated streaming and ta discovers the new catalog revision
- **AND** a call with an obsolete schema is rejected or revalidated before execution, without replaying a call whose effect is unknown

#### Scenario: A local server is activated
- **WHEN** the owner permits an exact stdio executable/argv/cwd configuration
- **THEN** it runs only in the bound owner's sandbox with its scoped grant and no inherited daemon credentials, using a separate sandbox when raw-key injection is opted in
- **AND** failure to start reports an error rather than executing on the host

#### Scenario: Attachment IDs or sessions are copied between owners
- **WHEN** another owner supplies an attachment ID, backing connection, catalog reference or server session handle
- **THEN** the dispatcher rejects it before reading private metadata, credentials or invoking a tool

### Requirement: Connection finalization and removal are recoverable and scoped
New connection shapes SHALL reuse the existing coordinator, request idempotency, incarnation fencing and durable continuation with processed-ack. Cancellation, Stop, expiry, logout/account switch or a changed request SHALL invalidate pending capture/finalization. Revocation SHALL prevent further calls before cleanup and SHALL preserve unrelated connections. Schema migration SHALL preserve existing HTTP records and fail visibly on unsupported versions without deleting new-shape custody records.

#### Scenario: Login completion races Stop or restart
- **WHEN** a late callback arrives after Stop, or a process restarts between credential deposit and activation
- **THEN** cancelled work cannot activate, and valid recovery reconciles the same draft with at most one active grant and one committed continuation result
- **AND** abandoned staged custody is cleaned without leaking secrets or waking another owner's task

#### Scenario: Disconnect and reconnect use the same endpoint
- **WHEN** an owner removes an attachment and reconnects later
- **THEN** old approvals, surrogates and transport sessions cannot authorize the new incarnation
- **AND** removing an attachment preserves an independent backing HTTP connection while removing that backing connection fences its dependents

### Requirement: Standard MCP OAuth uses existing connection authority
Remote MCP SHALL support protected-resource and authorization-server metadata discovery, PKCE S256, DCR, HTTPS client metadata documents and explicit static-client fallback according to server support. Tokens SHALL bind the intended resource/audience, redirect URI and initiating owner session. The registered provider directory and platform Google client SHALL remain optional data, never a required registration or per-platform code path.

Discovery SHALL use an actual Bearer challenge, then endpoint-path and root
well-known metadata, validate resource identity and exact issuer equality, and
apply SSRF checks to every target. Registration SHALL prefer an accepted existing
client, then advertised CIMD, then DCR with application_type web. Public client
registrations created by the platform SHALL persist by exact issuer and redirect
URI in the OAuth flow store, without owner tokens. Requester-supplied client IDs
SHALL NOT enter that shared cache. DCR entries SHALL expire within one hour;
an expired entry on a pending card SHALL trigger fresh registration or an
actionable registration_required failure. Token-endpoint client rejection SHALL
evict the cached identity; no callback is assumed for authorization-page refusal. GET /app/oauth/client-metadata.json SHALL publish the
stable HTTPS TinyAssets client identity and exact callback with public auth none.
The token bundle SHALL retain optional resource and issuer without breaking old
bundles. Authorize, exchange and refresh SHALL send that resource. Requested or
challenge scopes absent from AS metadata SHALL NOT be rejected for that absence.

#### Scenario: Unknown path endpoint advertises CIMD
- **WHEN** an unlisted endpoint challenges with protected-resource metadata and its AS advertises CIMD
- **THEN** the existing connect card uses the published client identity before DCR, and tokens retain the exact resource through refresh

#### Scenario: Registration and grant failures need different recovery
- **WHEN** registration is unavailable or rejected, or a token grant is revoked
- **THEN** registration_required and reconnect_required respectively identify the recoverable failure without exposing credentials

#### Scenario: Resource or issuer identity differs
- **WHEN** metadata names a different resource or an issuer differing even by a trailing slash
- **THEN** discovery rejects it before registration or authorization

#### Scenario: A server is absent from the provider directory
- **WHEN** its metadata offers DCR, client metadata documents or configured static registration
- **THEN** the generic OAuth path connects it without platform code and without forwarding another resource's token

### Requirement: Private secret entry shares egress custody
Protected secret entry SHALL deposit directly into custody; the broker SHALL inject only the bound connection's allowlisted slots and destination/method/path. Broker-only custody SHALL be the default; only the exact owner-approved stdio raw-key opt-in above permits injection into the server's separate sandbox.

#### Scenario: A connector requests another connection's credential
- **WHEN** code requests a slot outside its owner's granted connection incarnation
- **THEN** the broker denies it before resolving the secret and records no credential bytes in output
