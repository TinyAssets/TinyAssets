# outbound-connection-provisioning (delta)

## ADDED Requirements

### Requirement: A universe owner can provision a generic http connection

The connector SHALL expose `write_graph target=connection operation=connect_http`
so a universe's owner can provision a generic outbound `http` connection that the
universe's graphs can later act on via the `authenticated_external_call` effector.
The operation SHALL be owner-only (authenticated + an explicit `admin` ACL row on
the target universe), deposit the supplied credential into the per-universe vault
(never echoing it), create an `http`-typed `ConnectionLedger` connection with a
non-empty, validated endpoint allow-list and the `bearer` single-secret auth scheme
(Slice 1 scope; `none`/`basic`/`header`/`oauth1a` deferred), and grant that
connection to the universe. The connection's SCOPE SHALL be the sorted,
de-duplicated union of its endpoints' HTTP methods — the verb string the
`authenticated_external_call` effector matches each request's verb against — never
a coarse connection-type token, which admits no HTTP verb and would make every
outbound call fail closed. The primitive is channel-agnostic: it hard-codes no
service — the owner supplies host, path, and secret, so an unanticipated channel
works identically. Creation and grant SHALL be idempotent with deterministic ids.
Every *refusal* (auth, validation, conflict) SHALL happen before any write, leaving
zero mutation. A rare mid-provision infrastructure fault SHALL leave only INERT
partial state (a connection with no grant, or a credential with no connection —
neither able to authorize a call), which the idempotent retry completes; it SHALL
never leave a usable half-connection.

#### Scenario: Owner provisions an http connection

- **GIVEN** an authenticated owner (admin ACL) of a universe
- **WHEN** they call connect_http with a destination key, a bearer secret, and at
  least one `{host, path_template, methods}` endpoint
- **THEN** the credential is stored in the per-universe vault under
  `vault://http/<destination>`, an `http` connection with that endpoint allow-list
  is created and granted to the universe, and a redacted projection is returned
  (`connection_id`, `grant_id`, `destination`, `auth_scheme`, `allowed_endpoints`)
  with the secret never echoed

#### Scenario: Non-owner or anonymous is refused

- **GIVEN** a caller who is anonymous, or is authenticated but lacks an `admin`
  ACL row on the universe
- **WHEN** they call connect_http
- **THEN** an anonymous caller gets `authentication_required` and an
  authenticated non-admin gets a uniform `not_found` (existence-probe safe); in
  both cases no credential, connection, or grant is created
- **AND** a second admin who is not the connection's owner is refused with
  `connection_conflict` before any vault write (ownership is not transferable
  through this surface)

#### Scenario: Invalid provisioning input is rejected fail-closed

- **GIVEN** connect_http input with an empty endpoint allow-list, an unsupported
  auth scheme (e.g. `oauth1a`), or a credential-ownership transfer to a different
  owner
- **WHEN** the operation runs
- **THEN** it returns a specific error, deposits nothing, and creates no
  connection or grant (atomic, fail-closed)

#### Scenario: Idempotent re-provisioning rotates the secret, policy is immutable

- **GIVEN** an owner who already provisioned a connection for a destination
- **WHEN** they call connect_http again for the same destination with an identical
  policy (same owner, type, class, auth scheme, scopes, credential_ref, and
  endpoint allow-list — the endpoint allow-list compared as an UNORDERED set of
  endpoints/methods/query names, so a reordered-but-identical policy is not a
  change)
- **THEN** the existing connection/grant are returned (deterministic ids), not
  duplicated, and the secret is rotated to the new value
- **AND** if any immutable field differs — including a genuinely changed endpoint
  allow-list, or a non-owner admin — the call returns `connection_conflict` before
  any vault write, so a re-provision never silently keeps the old egress policy
  under a rotated secret
- **AND** changing an existing connection's policy is UNSUPPORTED in Slice 1: there
  is no revoke-then-reprovision path (`revoke_connection` only stamps `revoked_at`,
  and a revoked deterministic resource then trips the `revoked_at` conflict on
  every re-provision), so a policy change requires a new destination until the
  dedicated policy-update operation follow-up lands

#### Scenario: A pre-scope-fix connection's legacy scope is migrated in place, never stranded

- **GIVEN** a connection provisioned before the scope was method-based, carrying the
  legacy coarse scope token, otherwise policy-identical to a re-provision request
- **WHEN** the owner calls connect_http again for that destination with the same policy
- **THEN** the connection's scope is upgraded in place to the method union — a bounded,
  one-directional migration guarded to the exact legacy token, so it can never widen,
  narrow, or alter a real method-scoped set — and the secret is rotated; the row is NOT
  stranded behind `connection_conflict` (which deterministic ids + the absence of a
  policy-update path would otherwise make unrecoverable)
- **AND** the migration is applied ONLY after the grant-conflict check passes AND the
  credential deposit succeeds, so a deposit failure or grant refusal leaves the legacy
  scope untouched and the connection inert (the inert-partial-state guarantee holds)

### Requirement: The deposit door accepts every auth scheme the broker can sign, generically

`connect_http` SHALL accept `auth_scheme` ∈ {`bearer` (default), `basic`, `oauth1a`} — the
schemes the broker child already signs — rather than `bearer` only. This is a scheme
unlock, never a per-service path: an OAuth 1.0a API (X/Twitter and any other) is
connected through the identical generic deposit with the owner-chosen `destination` as
its only identity. `header` stays off the door until the ledger persists a per-connection
header name; `none` has nothing to deposit. The vault SHALL store ONE opaque string per
connection: the raw token for `bearer`, `username:password` for `basic`, and a JSON
object `{api_key, api_secret, access_token, access_token_secret}` for `oauth1a` — the
same encodings the broker's `_build_http_secret_bundle` parses at request time. The
door SHALL validate the secret's SHAPE for the scheme before any write (mirroring the
broker's parser) so a malformed multi-value credential is refused up front, never
discovered as a failed outbound call; refusal messages SHALL carry no secret material.
The connection row SHALL record the scheme, and a re-provision with a DIFFERENT scheme
for the same destination is a policy change → `connection_conflict`, nothing rotated.

#### Scenario: An OAuth 1.0a service is connected with no service-specific code

- **GIVEN** an owner with the four OAuth 1.0a values for some service
- **WHEN** they call connect_http with `auth_scheme=oauth1a`, a JSON object of those
  four values as the secret, and that service's endpoint allow-list
- **THEN** the connection is provisioned with `auth_scheme="oauth1a"`, the bundle is
  stored as one opaque vault string, no value is echoed, and the universe's
  `authenticated_external_call` node can sign requests to it at run time

#### Scenario: A malformed multi-value secret is refused before any write

- **GIVEN** `auth_scheme=oauth1a` with a secret that is not a JSON object or lacks one
  of the four values (or `basic` without a `:`)
- **WHEN** connect_http runs
- **THEN** it returns `connection_setup_invalid` naming the missing field, deposits
  nothing, and creates no connection or grant

### Requirement: A universe can read back its own outbound connections, channel-agnostically

`read_graph target=connections` SHALL list EVERY connection granted to the universe —
generic http channel connections (any deposited destination: Slack, Discord, any HTTPS
endpoint) alongside github pipes — as REDACTED views. Each row SHALL carry the
`connection_id`, `grant_id`, `destination` label, `connection_class`, `scopes`, the
grant's `action_cap`, and the connection's redacted `allowed_endpoints`
(host/path/methods/query descriptors — the owner's declared egress policy, never the
credential), so the caller can build an `authenticated_external_call` node from the
listed facts WITHOUT the owner hand-copying ids. The listing SHALL be owner-scoped: it
returns only connections whose grant `owner_user_id` is the caller, so a collaborator on
a shared universe never sees another owner's connections, and no `credential_ref` or
vault secret ever appears. This read target SHALL also be available on the served
engine-MCP surface (graph-pinned to the agent's own universe).

#### Scenario: The list includes generic http channel connections, redacted

- **GIVEN** an owner who deposited an http connection for some destination via connect_http
- **WHEN** they read `target=connections`
- **THEN** the response includes that connection with its `connection_id`, `grant_id`,
  `destination`, `connection_class="http"`, and redacted `allowed_endpoints`, and it
  contains no secret or `credential_ref`

#### Scenario: The listing isolates by owner on a shared universe

- **GIVEN** two admins of the same universe who have each deposited a connection
- **WHEN** either admin reads `target=connections`
- **THEN** each sees ONLY their own connection, never the other owner's — because the
  listing filters on the grant's `owner_user_id`, not merely the universe
