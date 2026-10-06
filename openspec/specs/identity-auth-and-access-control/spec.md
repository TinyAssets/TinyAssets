# Identity, Auth, and Access Control

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

WorkOS OAuth 2.1 resource-server auth with named principals on every request,
pre-dispatch OAuth challenges, founder home auto-birth, and two-axis
authorization (universe visibility plus ownership ACL).
## Requirements
### Requirement: Auth provider is selected by configuration and always names a principal

The auth provider SHALL be selected by `UNIVERSE_SERVER_AUTH`: unset or false
selects the dev provider, `true`/`oauth` the full OAuth provider,
`optional`/`resolve` the optional OAuth provider, `workos` the WorkOS
provider. In every mode a request without a valid bearer SHALL be challenged;
the modes differ only in how a bearer is validated and whether named action
scopes are enforced on writes. The dev provider SHALL require
`UNIVERSE_SERVER_DEV_USER` and resolve every bearer to that named identity.

#### Scenario: dev mode without a named user
- **WHEN** the server starts with `UNIVERSE_SERVER_AUTH` unset and `UNIVERSE_SERVER_DEV_USER` unset
- **THEN** startup fails naming `UNIVERSE_SERVER_DEV_USER`

### Requirement: Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound

The WorkOS provider SHALL validate bearer JWTs with a pinned RS256 key set,
SHALL require `exp` and `sub`, SHALL reject any token whose subject is
missing, and SHALL bind the audience. A token that fails validation SHALL
resolve to nothing, and the transport SHALL answer 401 `invalid_token`; no
identity is bound for it.

#### Scenario: expired token
- **WHEN** a request carries an expired bearer
- **THEN** token resolution returns nothing and the response is HTTP 401 with `error="invalid_token"`

### Requirement: Protected Resource Metadata advertises the AuthKit issuer and OIDC scopes only

In WorkOS mode the server SHALL advertise, at the Protected Resource Metadata endpoint
(`/.well-known/oauth-protected-resource` and its `/mcp`-prefixed mirror), the AuthKit issuer as the
authorization server plus the registered MCP resource indicator, and SHALL list only standard OIDC
scopes (`openid`, `profile`, `email`, `offline_access`).
It SHALL NOT advertise internal `tinyassets.*` action scopes, because AuthKit cannot issue them and
per-action authorization is enforced at the Resource Server via founder grants, not via OAuth scopes.
The metadata is produced in `tinyassets/auth/wellknown.py`.

#### Scenario: WorkOS PRM lists only OIDC scopes
- **WHEN** a client fetches the Protected Resource Metadata in WorkOS mode
- **THEN** the advertised authorization server is the AuthKit issuer and the resource indicator is present
- **AND** `scopes_supported` contains only OIDC scopes and no internal `tinyassets.*` scope

### Requirement: Founder home auto-births exactly once on first authenticated contact

The server SHALL, on the first authenticated `converse` call with no `graph_id`
(the founder's opening relay, and the only handle that performs first-contact
birth), ensure the founder has a home universe. It SHALL check the create scope
BEFORE reserving any home id, so a founder lacking create scope leaves no
phantom binding and the conversation entry returns a creation failure with
`auth_scope_required=true`. Reservation SHALL be atomic (an `INSERT ... ON
CONFLICT DO NOTHING` on the founder key) so concurrent first-contact calls
across worker threads yield exactly one home id, and materialization SHALL be
serialized so a reserved id is created once, with success defined as the
universe's `soul.md` being present. After successful materialization and
binding, the resolver SHALL return the bound home id to the originating
`converse` entry path. Whether that conversation can select and invoke universe
intelligence is a subsequent authority/execution decision outside this birth
contract; successful birth SHALL NOT guarantee provider execution or a
first-person reply. An unauthenticated request SHALL be challenged before it
can trigger birth. Both `get_status` and the `read_graph target=status` alias
SHALL pass through as pure reads without first-contact birth. This logic lives
in `tinyassets/api/first_contact.py` with the atomic claim in
`tinyassets/daemon_server.py`.

Per the 2026-07-22 host directive
(`docs/design-notes/2026-07-22-first-contact-birth-moves-to-converse.md`), birth
moved off `get_status` and its `allow_first_contact_birth` parameter was
deleted, because a mutating *opening* call proved refusable in production: the
assistant declined to call `get_status` on the grounds that its own tool
description advertised a side effect. The 2026-07-15 commitment this replaces
-- a founder never needs to know an incantation -- is upheld, since the opening
message is itself the relay.

#### Scenario: first authenticated converse births one home
- **WHEN** an authenticated founder with create scope and no bound home issues their opening `converse` with no `graph_id`
- **THEN** exactly one home universe is reserved, materialized, and bound to the founder
- **AND** the originating conversation entry continues with that bound home as its target
- **AND** completion of birth does not by itself assert that provider execution or a first-person reply succeeded

#### Scenario: read-only founder leaves no phantom binding
- **WHEN** an authenticated founder lacking create scope issues their opening `converse` with no home
- **THEN** no home binding is created
- **AND** the result reports that the home could not be created or loaded with `auth_scope_required=true`

#### Scenario: unauthenticated first contact never births
- **WHEN** an unauthenticated request targets `converse` or `get_status`
- **THEN** the transport returns an authentication challenge and no home universe is created

#### Scenario: get_status never births
- **WHEN** an authenticated founder with create scope and no bound home calls `get_status`
- **THEN** no home universe is created and the call is a pure read
- **AND** a repeated call returns the identical snapshot

### Requirement: The permission actor is the authenticated subject with no environment fallback

The permission actor SHALL be exactly the authenticated request subject. With
no identity bound, actor resolution SHALL raise `PermissionError`; it SHALL
NOT resolve to any stand-in, and no universe-server environment variable
SHALL confer identity, authorship or write authority.

#### Scenario: unauthenticated request
- **WHEN** a permission check runs with no identity bound
- **THEN** it raises `PermissionError` and no action is authorized

### Requirement: Access is controlled on two orthogonal axes — visibility and ownership

Universe access SHALL be decided on visibility (public, private, rule-scoped)
and on the ownership ACL, for authenticated callers. A public universe SHALL
be readable by any authenticated caller; a private universe by its ACL rows
only; all writes by an actor holding write or admin on that universe. There
is no unauthenticated reader.

#### Scenario: signed-in reader of a public universe
- **WHEN** an authenticated caller with no ACL row reads a public universe
- **THEN** the read succeeds

#### Scenario: signed-in reader of a private universe
- **WHEN** an authenticated caller with no ACL row reads a private universe
- **THEN** the response is the uniform not-found envelope

### Requirement: Status identity evidence varies across three response shapes

`get_status` SHALL report `request_identity.bearer_present` and a
`principal_fingerprint` derived from the authenticated subject only. There is
no anonymous fingerprint prefix and no environment-actor fallback shape; the
three shapes are: founder of the universe, authenticated non-founder, and
the `canary` service principal.

#### Scenario: canary reads status
- **WHEN** the canary bearer calls `get_status`
- **THEN** `request_identity.principal_fingerprint` is the canary's and `release_state` is present

### Requirement: Scoped wiki canary bearer grants no general identity

The canary bearer SHALL resolve to the `canary` service identity whose only
admitted requests are the allowlist above; it SHALL NOT hold write, costly or
admin on any universe. Its confinement is the allowlist enforced before
dispatch, not anonymity and not a capability set.

#### Scenario: canary attempts a write
- **WHEN** the canary bearer calls `write_graph`
- **THEN** the request is refused before dispatch and nothing is written

### Requirement: Voice-session signaling requires the authenticated founder identity
The provider-capability configuration path, `GET /app/voice/status` capability check, and `POST /app/voice/session` broker SHALL require a resolved authenticated subject, SHALL derive the founder home universe and current serving provider through the same canonical home resolver instead of caller-selected authority, and SHALL fail closed before mutation, connection lookup, or network activity when identity or ownership cannot be proven. Capability configuration additionally requires both a current admin ACL and exact connection-grant ownership by the authenticated actor.

#### Scenario: Anonymous caller configures or requests Voice
- **WHEN** a request reaches capability configuration, Voice status, or the voice-session broker without a resolved authenticated subject
- **THEN** the server returns an authentication challenge or denial
- **AND** it performs no capability mutation, connection lookup, or network request

#### Scenario: Authenticated owner configures the current provider capability
- **WHEN** a founder with a current admin ACL who exactly owns the current connection grant declares or revokes realtime capability for their home universe
- **THEN** the server derives the home universe and current provider through that subject
- **AND** the request body cannot select a universe, provider, connection, or grant

#### Scenario: Caller graph disagrees with derived home
- **WHEN** `write_graph` supplies a `graph_id` that does not exactly match the authenticated subject's canonically derived founder home
- **THEN** capability configuration is refused before connection lookup or mutation
- **AND** the caller-selected graph is not ignored or substituted

#### Scenario: Non-owner administrator tries to configure capability
- **WHEN** an administrator who does not own the current connection grant attempts to declare or revoke its capability
- **THEN** the server refuses before mutation
- **AND** the owner's next Voice tap cannot use metadata authored by that administrator

#### Scenario: Authenticated founder requests the home voice session
- **WHEN** a founder with a materialized home universe requests a voice session
- **THEN** the broker resolves that home and current provider through the authenticated subject
- **AND** it does not accept a body parameter that could select another founder's universe, provider, connection, or grant

#### Scenario: Authenticated founder checks Voice capability
- **WHEN** a founder requests Voice status
- **THEN** the server derives that founder's home universe and current serving provider and returns only secret-free readiness metadata
- **AND** readiness requires exact owner, universe, connection, grant, capability, revocation, type, method, and endpoint matches
- **AND** readiness cannot be borrowed from another founder, the host environment, a maintainer account, or another provider

#### Scenario: Founder home is absent
- **WHEN** an authenticated subject without a materialized founder home requests a voice session
- **THEN** the broker returns an actionable not-ready failure
- **AND** it does not auto-create a universe or access any credential

### Requirement: Every request carries a named principal or is challenged

The auth middleware SHALL resolve a bearer token to an `Identity` or to
nothing; it SHALL NOT construct a stand-in identity for a missing or invalid
token in any auth mode. `current_identity()` SHALL raise `PermissionError`
when no identity is bound. On every path outside the exempt table the ASGI
middleware SHALL answer HTTP 401 with a `WWW-Authenticate: Bearer` challenge
carrying the resource-metadata URL when no valid bearer is present, including
for JSON-RPC `initialize`, `tools/list` and every `tools/call`. No
environment variable SHALL supply an actor or a git author. The dev provider
SHALL resolve a named local identity from `UNIVERSE_SERVER_DEV_USER` and the
server SHALL refuse to start in dev mode without it.

#### Scenario: missing bearer on the MCP endpoint
- **WHEN** a client POSTs `initialize` to `/mcp` with no `Authorization` header
- **THEN** the response is HTTP 401 with `WWW-Authenticate: Bearer resource_metadata="..."`
- **AND** no handler runs and no identity is bound

#### Scenario: invalid bearer in dev mode
- **WHEN** the provider is the dev provider and a request carries a bearer it cannot resolve
- **THEN** the response is HTTP 401 with `error="invalid_token"`, not a downgrade to any identity

#### Scenario: code that reads identity outside a request
- **WHEN** `current_identity()` is called with nothing bound
- **THEN** it raises `PermissionError("Authentication required")`

### Requirement: Exempt paths bind their own named principal

Exactly these paths SHALL be served without the MCP bearer, each binding a
named principal or reading no state: the OAuth discovery routes
(`/.well-known/*` and `/mcp/.well-known/*`); the app shell `/app` and
its PKCE exchange, refresh and logout route `/app/token` (the signed-in
user, or the flow itself); inbound hook routes `/mcp/hooks/<id>` (exactly one path segment,
the existing predicate; the hook's owner is stamped on the emitted event);
`/app/billing/webhook` (Stripe-signed; the handler binds the customer
from the event). No other path
SHALL be exempt, and no exemption SHALL be a wildcard prefix.

#### Scenario: inbound hook runs as its owner
- **WHEN** a valid hook secret arrives on `/mcp/hooks/<id>`
- **THEN** the emitted event carries the hook owner's principal and the run binds to it without a further lookup

#### Scenario: a hook with no recorded owner
- **WHEN** a valid secret arrives for a hook stored before owners were recorded
- **THEN** the hook refuses to emit, naming the hook, and nothing runs

#### Scenario: pulse without a principal
- **WHEN** `GET /mcp/pulse` is called with no bearer
- **THEN** it receives the OAuth 401 challenge and no release data

### Requirement: Runs and events carry an explicit actor

`create_run` SHALL require `actor`; the `runs.actor` column SHALL have no
default; a dispatch SHALL never treat a missing actor as a principal.
Scheduled runs, automation runs and Source-event runs SHALL each carry the
owner principal stored with the schedule, automation or hook.

#### Scenario: legacy anonymous run rows
- **WHEN** the migration finds a run row whose actor is `anonymous`
- **THEN** it fails loud listing the rows, and no such row is ever re-dispatched

### Requirement: Operational probes are the canary service principal

The bearer configured as `TINYASSETS_WIKI_CANARY_TOKEN` SHALL resolve to the
identity `canary`. The canary SHALL hold no capability set; instead the auth
middleware SHALL admit a request under it only when every item of its
JSON-RPC body (single or batch) is one of: `initialize`,
`notifications/initialized`, `tools/list`, `tools/call get_status` with no
arguments, `tools/call read_graph` with `target=status`, or the wiki canary's
exact `write_page` / `read_page` shapes. Exact `GET /mcp/pulse` is also
admitted under the canary bearer. Any other item SHALL be refused before
dispatch. Every probe script in `scripts/` that calls the MCP endpoint
SHALL send the bearer; without the variable set the script SHALL exit 2
naming it. `scripts/mcp_public_canary.py` SHALL assert that an
unauthenticated `initialize` answers the 401 challenge, then use the bearer
for `--assert-handles`. With an explicit MCP URL, `scripts/deployed_sha.py`
SHALL read `/mcp/pulse` with the canary bearer and keep its `image_tag`
corroboration. Deploy assertions and build-image's production-receipt read
SHALL explicitly select this authenticated mode.

The default local deployed-SHA read SHALL use unauthenticated `HEAD /app` and
the existing `X-TinyAssets-Build` projection of `release_state.git_sha`. This
mode SHALL read no response body, require a full 40-character hexadecimal SHA,
and identify its evidence as a public receipt projection without image-tag
corroboration or binary-freshness proof. It SHALL NOT widen access to
`/mcp/pulse` or expose additional receipt fields. Missing or malformed headers
and network failures SHALL exit 2; a known commit absent from the served
history and not runtime-equivalent SHALL exit 1; a proved assertion SHALL
exit 0. Canary-only provenance SHALL remain unknown in public mode.

#### Scenario: canary without its token
- **WHEN** `mcp_public_canary.py --assert-handles` runs with the variable unset
- **THEN** it exits 2 and names `TINYASSETS_WIKI_CANARY_TOKEN`

#### Scenario: canary attempts anything else
- **WHEN** the canary bearer calls `write_graph`, `run_graph`, `converse`, or `read_graph` with any target but `status`
- **THEN** the request is refused before dispatch and nothing is read or written
