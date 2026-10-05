## ADDED Requirements

### Requirement: Connection routes are general shapes inline in chat
The system SHALL support MCP attachment shapes through the existing bound inline connection request and continuation mechanism, alongside generic OAuth/HTTP. It SHALL NOT require per-platform code, a directory entry or a platform LLM. The owner SHALL control route and approval policy; cross-user isolation SHALL remain the sole immutable platform behavioral invariant. Credentials SHALL remain daemon-side by default, with only explicit owner-approved stdio injection into that owner's jail permitted.

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

HTTP streaming SHALL use broker-streaming-contract's incremental scanning, cancellation, backpressure and reconciliation. Credentialed stdio SHALL default to broker injection. The owner SHALL be able to opt in to named own-key injection for an exact server configuration revision after a protected warning that server code can read the key. This grant SHALL NOT include other owners' secrets, survive configuration changes or be exported in packages.

#### Scenario: A stdio server requires a raw key
- **WHEN** the owner explicitly permits named own-key injection into that exact server revision
- **THEN** only that key is injected into the bound owner's jailed process
- **AND** without that opt-in the broker-only default remains and the card offers the owner the choice instead of declaring the server permanently unsupported

#### Scenario: Streaming splits credential bytes
- **WHEN** a broker-authenticated response splits a key across frames
- **THEN** the broker withholds credential material and reports the typed failure without bypassing scanning

#### Scenario: Remote tools stream and their catalog changes
- **WHEN** an activated HTTP server returns streamed results or changes its tool schema
- **THEN** the transport handles negotiated streaming and ta discovers the new catalog revision
- **AND** a call with an obsolete schema is rejected or revalidated before execution, without replaying a call whose effect is unknown

#### Scenario: A local server is activated
- **WHEN** the owner permits an exact stdio executable/argv/cwd configuration
- **THEN** it runs only in the bound owner's jail with its scoped grant and no inherited daemon credentials
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

#### Scenario: A server is absent from the provider directory
- **WHEN** its metadata offers DCR, client metadata documents or configured static registration
- **THEN** the generic OAuth path connects it without platform code and without forwarding another resource's token

### Requirement: Private secret entry shares egress custody
Protected secret entry SHALL deposit directly into custody; the broker SHALL inject only the bound connection's allowlisted slots and destination/method/path. Broker-only custody SHALL be the default; only the exact owner-approved stdio raw-key opt-in below permits scoped jail injection.

#### Scenario: A connector requests another connection's credential
- **WHEN** code requests a slot outside its owner's granted connection incarnation
- **THEN** the broker denies it before resolving the secret and records no credential bytes in output
