## ADDED Requirements

### Requirement: Connection routes are general shapes inline in chat
The system SHALL support MCP attachment and browser-login shapes through the existing bound inline connection request and continuation mechanism, alongside generic OAuth/HTTP. It SHALL NOT require per-platform code, a directory entry or a platform LLM. The owner SHALL control route and approval policy; cross-user isolation SHALL remain the sole immutable platform behavioral invariant. Capture and execution credentials SHALL remain daemon-side.

#### Scenario: An unknown service is requested
- **WHEN** the owner asks to connect a service with a compatible MCP endpoint or browser login but no platform registration
- **THEN** their agent or direct owner controls configure the general shape without a platform patch
- **AND** connection progress, failure/retry and completion stay attached to the original chat line, with protected login takeover when needed

#### Scenario: Owner has no connected model
- **WHEN** an unpowered owner uses direct connection or removal controls
- **THEN** the deterministic connection operation remains available and any agent continuation waits for their own model
- **AND** no host, shared or platform model credential is used

### Requirement: MCP attachments share existing connection authority
MCP attachments SHALL use typed owner-scoped connection metadata and current use grants, remote HTTP custody or jailed stdio, negotiated initialization/list/call transport and ta names `mcp:<attachment-id>:<tool-name>`. Configuration, session and tool-catalog revisions SHALL be bound to connection incarnations. Untrusted server metadata SHALL NOT create authority. Stale/revoked calls SHALL fail before dispatch, and uncertain external calls SHALL NOT be blindly replayed.

HTTP streaming SHALL use broker-streaming-contract's incremental secret scanning, cancellation, backpressure and operation reconciliation without a bypass transport. Credentialed stdio SHALL use broker-side authentication injection through scoped proxy handles; raw secret environment variables/files SHALL NOT enter the agent jail. Servers that cannot operate through this shape SHALL be visibly unsupported in v1.

#### Scenario: A stream splits a secret or a stdio server requests a raw token
- **WHEN** an upstream MCP stream splits credential bytes across frames, or a stdio server cannot work without a raw token in its environment
- **THEN** the streaming broker withholds the credential material and propagates its typed failure, or the stdio configuration reports unsupported custody respectively
- **AND** neither case bypasses the broker or exposes a vault secret to make the connection appear successful

#### Scenario: Remote tools stream and their catalog changes
- **WHEN** an activated HTTP server returns streamed results or changes its tool schema
- **THEN** the transport handles negotiated streaming and ta discovers the new catalog revision
- **AND** a call with an obsolete schema is rejected or revalidated before execution, without replaying a call whose effect is unknown

#### Scenario: A local server is activated
- **WHEN** the owner permits an exact stdio executable/argv/cwd configuration
- **THEN** it runs only in the bound owner's jail with its scoped grant and no daemon credentials
- **AND** failure to start reports an error rather than executing on the host

#### Scenario: Attachment IDs or sessions are copied between owners
- **WHEN** another owner supplies an attachment ID, backing connection, catalog reference or server session handle
- **THEN** the dispatcher rejects it before reading private metadata, credentials or invoking a tool

### Requirement: Browser login keeps reusable credentials out of agent context
Browser login SHALL reuse the D5 context and protected owner takeover with an owner/session/draft/origin/expiry binding. Passwords, MFA values, cookies and reusable session credentials SHALL remain in daemon custody; agent code SHALL receive only scoped surrogate handles. Agent observation/control and login artifact capture SHALL be suspended during credential entry, and credential-bearing data SHALL be excluded when returning control.

The credentialed context SHALL be isolated from the agent shell/filesystem and expose only structured actions and sanitized page observations. It SHALL NOT expose arbitrary evaluation, DevTools, cookie/storage exports, profile files, raw network bodies/headers or credential-bearing input/URL fields. A site whose safe observations cannot be established SHALL remain owner-only with an explicit limitation.

#### Scenario: Agent tries to read credentials after takeover ends
- **WHEN** an agent attempts script evaluation, document.cookie/localStorage access, profile-file reads or a network trace in the credentialed context
- **THEN** the broker refuses those operations and exposes only sanitized permitted page observations
- **AND** a surface it cannot safely expose remains in owner-only control

#### Scenario: Owner logs into a site without an API
- **WHEN** the owner completes the protected login view from the inline card
- **THEN** the broker stores the session in daemon custody, activates the owner-bound connection and returns ordinary authenticated page access under current permission
- **AND** chat, screenshots, DOM snapshots, logs, network traces and extension inputs contain no captured credentials

#### Scenario: Login redirects or requires a passkey
- **WHEN** credential entry targets a new origin or the site needs owner-only challenge completion
- **THEN** the broker requests the new origin binding or keeps owner takeover waiting with an honest status
- **AND** it never forwards existing credentials to an unbound origin or pretends login succeeded

### Requirement: Connection finalization and removal are recoverable and scoped
New connection shapes SHALL reuse the existing coordinator, request idempotency, incarnation fencing and durable continuation with processed-ack. Cancellation, Stop, expiry, logout/account switch or a changed request SHALL invalidate pending capture/finalization. Revocation SHALL prevent further calls before cleanup and SHALL preserve unrelated connections. Schema migration SHALL preserve existing HTTP records and fail visibly on unsupported versions without deleting new-shape custody records.

#### Scenario: Login completion races Stop or restart
- **WHEN** a late callback arrives after Stop, or a process restarts between credential deposit and activation
- **THEN** cancelled work cannot activate, and valid recovery reconciles the same draft with at most one active grant and one committed continuation result
- **AND** abandoned staged custody is cleaned without leaking secrets or waking another owner's task

#### Scenario: Disconnect and reconnect use the same endpoint
- **WHEN** an owner removes an attachment/browser connection and reconnects later
- **THEN** old approvals, surrogates and transport sessions cannot authorize the new incarnation
- **AND** removing an attachment preserves an independent backing HTTP connection while removing that backing connection fences its dependents

### Requirement: One connect card supports labelled accounts and auth types
The system SHALL use one connect card from chat and settings branching on OAuth, API key or MCP, with basic/none where supported. It SHALL preserve multiple independently labelled accounts per service and bind each call to a selected connection incarnation. MCP OAuth SHALL support metadata discovery, PKCE S256, DCR, client metadata documents and explicit static-client fallback according to server support, with resource-bound tokens and existing protected callback custody.

#### Scenario: Connect a second account through MCP OAuth
- **WHEN** the owner chooses MCP and completes the server-supported DCR or client metadata document flow with PKCE
- **THEN** a distinct labelled connection is added without replacing the first account and its tools appear through ta
- **AND** a server supporting neither dynamic method can use explicit static registration or report unsupported, never borrow another resource's token

#### Scenario: Account choice or discovery is unsafe
- **WHEN** two accounts match a request or discovery redirects credentials to an unbound resource
- **THEN** the card requests account selection or refuses the unsafe destination respectively, without silent fallback

### Requirement: Private secret entry and saved extensions share egress custody
The protected secret-entry card SHALL deposit credentials directly into custody; the egress broker SHALL inject them only for the bound connection's allowlisted credential slots and authorized destination/method/path. Sandbox code SHALL receive no raw credentials. Agent-authored connectors SHALL be saved as exact-revision tested extensions, listed and revocable through connections, with declared credential slots and secret-free exports.

#### Scenario: Agent builds a connector from OpenAPI
- **WHEN** the owner's agent writes an extension from API docs/OpenAPI and a safe self-test succeeds
- **THEN** the tested revision and receipt are saved as a reusable connection extension with revocation controls
- **AND** an update requires a new test receipt; failed tests remain visibly failed

#### Scenario: Extension tries another credential or leaks its own
- **WHEN** an extension requests another connection's slot or the response contains injected credential bytes
- **THEN** the broker denies the foreign slot or withholds the secret-bearing result and reports failure
- **AND** credentials remain absent from sandbox files/environment, transcripts, logs and published packages
