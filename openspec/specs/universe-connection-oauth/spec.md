# Universe connection OAuth

## Purpose

Connect user-owned HTTP services through authorization code and PKCE, using
provider data rather than provider-specific code. This is the as-built public
discovery flow plus platform-registered clients.

## Requirements

### Requirement: Resolve platform registrations before public-client discovery

For each connect request the platform SHALL first try its daemon-owned JSON
provider directory, then existing RFC 9728, RFC 8414/OIDC discovery and public
dynamic registration, then key paste. A directory match SHALL cover every
declared host by exact case-insensitive hostname comparison; a subdomain or
unrelated additional host SHALL NOT match. Entries lacking their client ID or
secret SHALL be inactive. Invalid optional configuration SHALL be logged with a
fixed code and its entries unavailable, without blocking engine or broker launch.
Unavailable directory/RPC lookups SHALL fall through to discovery and key paste;
unexpected errors and confidential exchange/refresh failures SHALL still fail.

The request MAY supply `oauth.scopes`, `oauth.use` (a named default scope set),
and a public `oauth.client_id`. It SHALL NOT supply endpoints, provider IDs,
secret names, client secrets or offer provenance. An active directory match
SHALL take precedence over an agent-supplied client ID.
Explicit scopes SHALL be a subset of the union of the entry's declared scope
sets; an out-of-policy request SHALL NOT use the platform registration.

#### Scenario: Registered service without discovery
- **WHEN** the declared hosts match an active directory entry
- **THEN** its configured endpoints and client ID form the sign-in offer without any discovery or registration request

#### Scenario: Inactive registration
- **WHEN** either configured credential is absent
- **THEN** public-client discovery runs as before, and key paste remains available if discovery cannot produce an offer

#### Scenario: Default scopes
- **WHEN** explicit scopes are absent
- **THEN** the named `use`, or the entry's `host_uses` mapping, selects default scope sets; ambiguous shared hosts without scopes or a selected use fall through

### Requirement: Providers are daemon configuration

The packaged directory SHALL live at
`tinyassets/connection_oauth/providers.json`. `TINYASSETS_OAUTH_DIRECTORY` MAY
select a replacement JSON file by absolute path; relative overrides SHALL fail
with `oauth_directory_invalid`. Entries SHALL declare `id`, `hosts`,
`authorization_endpoint`, `token_endpoint`, `client_id_env`, `client_secret_env`,
and `token_endpoint_auth_method`. They MAY declare `issuer`,
`revocation_endpoint`, `default_scopes`, `host_uses`, and `extra_auth_params`.
Endpoints SHALL be HTTPS and remain subject to the outbound SSRF restrictions.
Reserved protocol parameters SHALL NOT be overridden by extra parameters.
The optional revocation endpoint is metadata; this capability does not invoke it.

#### Scenario: A new provider
- **WHEN** an operator adds a valid entry and its named environment credentials
- **THEN** matching connections use it without adding provider-specific Python code

#### Scenario: Packaged example
- **WHEN** the packaged Google entry has both named environment credentials
- **THEN** Gmail and Calendar hosts can use its documented authorization and token endpoints, with offline access and consent parameters; otherwise the entry remains inactive

### Requirement: Confidential exchange retains PKCE and flow ownership

Sign-in SHALL retain authorization code, S256 PKCE, one-use state, action digest,
owner/universe/request binding, and RFC 9207 issuer checks when advertised.
The fixed callback SHALL be `https://tinyassets.io/app/model-callback/connect`
on the public deployment. `client_secret_post` SHALL put the client credential
in the token request form; `client_secret_basic` SHALL use RFC 6749 HTTP Basic
authentication with form-encoded client ID and secret. Public-client requests
SHALL continue without confidential authentication.

#### Scenario: Completed consent
- **WHEN** the same owner redeems a valid flow and verifier
- **THEN** the daemon exchanges the code and deposits an `oauth2` token bundle through the existing connect answer path; the response contains no token or client secret

#### Scenario: Replay or another owner
- **WHEN** another owner or universe tries to redeem a flow, or a verifier is wrong or a flow is replayed
- **THEN** the exchange and deposit are refused

### Requirement: Platform secrets stay daemon-side

Only the trusted directory SHALL select a client secret by name. Names SHALL
use `TINYASSETS_OAUTH_*_SECRET`; the entire OAuth configuration namespace SHALL
be filtered from engine child environments without provider-specific Python
inventory entries. At daemon startup and again before engine or scoped broker
spawn, the daemon SHALL remove platform OAuth secrets from its inherited environment into
process-local memory. Bundles and offers SHALL contain only the provider ID,
never a secret value or a secret-name reference. The client ID and token URL
SHALL be rechecked against current directory data before secret resolution.

Confidential token errors SHALL contain fixed codes/status only, not provider
prose. Transport exceptions SHALL NOT expose request material. Successful token
responses echoing a client secret or its transmitted encodings SHALL be refused.
The client secret SHALL NOT enter browser responses, MCP responses, pending
requests, vault bundles, audit records, logs, or jail environments.

#### Scenario: Malicious token response
- **WHEN** the endpoint echoes the secret in an error, token type, scope or token
- **THEN** a fixed failure is returned and the secret is neither deposited nor returned

#### Scenario: Redirected stored bundle
- **WHEN** a bundle's provider, client ID or token URL no longer matches its directory entry
- **THEN** no request carrying the platform secret is sent

### Requirement: Confidential refresh runs in the daemon and stays isolated

The broker SHALL refresh before expiry and once after a 401. Confidential
refresh from a child SHALL use a private loopback daemon service with a random
capability bound by the launcher to one owner and universe. The daemon SHALL
recheck canonical admin access or the founder-home binding, verify deposit
ownership, and load the named connection from that universe's vault itself.
The child SHALL NOT supply a path, owner,
endpoint, secret name or token bundle. The service SHALL return success only;
the broker SHALL reread its own vault. Public-client refresh SHALL retain its
existing path.
Each daemon launch SHALL receive a separate capability, revoked when its engine
stops/restarts or its proxy closes/fails startup. Child-launched proxies reuse
the parent engine capability. Owner code can request its own refreshes while
that capability is live; it grants no broader owner or connection access.

Refresh SHALL use the existing per-connection thread/process locks, reread
inside the locks, and vault admission before spending a refresh token. Rotated
tokens SHALL be persisted before use. Tokens SHALL remain separated by owner,
universe and connection, and SHALL NOT be pasteable or usable under another
authentication scheme.

#### Scenario: Child without secrets
- **WHEN** an engine or scoped broker needs a confidential refresh
- **THEN** its owner-bound capability asks the daemon to refresh the stored connection, without inheriting the platform client secret or receiving one in the reply

#### Scenario: Cross-owner or cross-connection attempt
- **WHEN** a caller supplies another owner/universe, an absent connection, or an invalid capability
- **THEN** it cannot refresh or read the other owner's connection; a valid refresh changes only the bound connection

#### Scenario: Concurrent refresh
- **WHEN** concurrent callers encounter the same stale token
- **THEN** the daemon's existing single-flight refresh spends the refresh token once and persists rotation atomically

## Verification and delivery scope

`tests/test_platform_oauth_clients.py` exercises configuration, real callback
flows for both client authentication methods, a spawned child using the daemon
service, secret reflection/error handling, and owner/connection isolation.
`tests/test_generic_oauth_connections.py` retains public-client and PKCE coverage.
This branch is a commit/push delivery; deployment and a live registered-provider
consent pass are not claimed by this specification.
