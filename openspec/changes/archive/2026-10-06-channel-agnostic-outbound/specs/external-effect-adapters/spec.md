## ADDED Requirements

### Requirement: One general authenticated-external-call adapter dispatches every channel
The runtime SHALL route every declared outbound-write effect through a single
`authenticated_external_call` adapter selected from a channel-type registry, rather than a
hard-coded per-sink dispatch ladder. The adapter SHALL parse one matching packet from the
node's declared output keys; resolve the packet's named connection under authority; apply, in
order, the existing soul-authority gate, the connection-descriptor resolution (no secret), the
shared `(sink, destination)` consent gate, and the per-connection endpoint-allowlist egress
gate; and only then fire through the credential-blind execution seam so that NO secret is
touched before consent. `github`, `slack`, `twitter`, and `http` SHALL be connection *types*
resolved from the registry as data, not code branches, and a new channel type SHALL be
expressible as a registry descriptor plus, only if needed, one new auth-scheme handler, without
editing a dispatch ladder. The adapter SHALL NOT raise into the run-completion path; every
refusal, dry run, hold, or failure SHALL be structured evidence.

#### Scenario: A declared external call dispatches through the one adapter
- **WHEN** a completed node declares the `authenticated_external_call` effect and one declared output key holds a matching packet naming a resolvable connection
- **THEN** the runtime dispatches the single general adapter, which runs the ordered gates and records structured per-node/per-sink evidence

#### Scenario: No secret is resolved before consent
- **WHEN** a packet's connection resolves but its `(sink, destination)` consent is absent
- **THEN** the adapter dry-runs with `missing_consent` and no connection secret has been resolved

#### Scenario: A new channel type needs no dispatch edit
- **WHEN** a channel type is added as a registry descriptor over an existing auth scheme
- **THEN** its connections dispatch through the same general adapter with no change to any dispatch ladder or per-sink branch

### Requirement: Outbound connections resolve under authority and never expose their credential reference
The adapter SHALL resolve a named connection only through the authority-checked seam that
requires the authenticated request principal, an active per-universe grant owned by that
principal, and the server-known universe — never by connection id alone against an
authority-free lookup that would return a raw credential reference. Every projection of a
connection returned to an adapter, a create/list/revoke response, or run evidence SHALL be a
redacted view containing no `credential_ref` and no secret material; the storage record SHALL
NEVER be returned verbatim. The secret SHALL be resolved as a TYPED, per-connection-type bundle
(for example: Slack keeps a bot token and a non-interchangeable app-level token distinct;
Twitter carries its four OAuth 1.0a values) rather than an untyped single secret. Per-universe
isolation SHALL be inherent in the grant: a universe with no active grant to a connection SHALL
be unable to make the call, with no allowlist, tier, roster, host, or maintainer credential
substituted, and a revoked grant or connection SHALL fail closed with no in-flight fallback.

#### Scenario: Resolution requires an authenticated grant, not a bare connection id
- **WHEN** a packet names a connection but the request has no authenticated principal holding an active grant to it for the server-known universe
- **THEN** the adapter fails closed as an unresolved connection and no credential reference or secret is read

#### Scenario: No projection exposes the credential reference
- **WHEN** a connection is returned to an adapter, a create/list/revoke response, or run evidence
- **THEN** the returned view contains no `credential_ref` and no secret, and the raw storage record is not returned

#### Scenario: The typed bundle keeps non-interchangeable secrets distinct
- **WHEN** a Slack connection is resolved for a message post
- **THEN** the bundle exposes the bot token to the post path and does not conflate it with the separate app-level token

### Requirement: The general adapter enforces a strict SSRF transport plus a per-connection endpoint allowlist
The credential-blind execution seam SHALL enforce, in addition to the per-connection
`allowed_endpoints`, a strict outbound transport, because a per-connection allowlist alone does
not stop URL, DNS-rebinding, redirect, or proxy attacks. The seam SHALL accept exactly one
absolute `https://` URL and SHALL reject any URL bearing userinfo, a fragment, control
characters or whitespace, a backslash, an encoded or malformed host, an unexpected port, a `.`
or `..` path segment, or double-encoding; and SHALL reject any caller-supplied `Host`,
`Authorization`, `Cookie`, or proxy header. The seam SHALL require the final bound host and path
to match exactly one `allowed_endpoints` entry (host equality, path-template match, each
substituted parameter validated against its declared pattern), and SHALL treat an empty
`allowed_endpoints` as permitting no call. The seam SHALL reject any resolved address that is not
`ipaddress`-global — including IPv4-mapped IPv6, unspecified, reserved, shared/CGNAT, loopback,
link-local (including the cloud metadata address), private, unique-local, and multicast, and
unusual IP literal forms — and SHALL validate ALL A/AAAA results. The seam SHALL pin the selected
validated public address for the actual connection while preserving TLS SNI and certificate
hostname verification, and SHALL validate the address actually connected to rather than
preflight-resolving and then issuing a plain request. The seam SHALL disable HTTP redirects by
default and, if a connection ever enables them, SHALL re-run the full check per hop and SHALL
NOT forward credentials across origins. The seam SHALL disable ambient/environment proxies. The
seam SHALL bound response body size, header count and size, connect and read timeouts, redirect
hops, and decompression. All of this SHALL run inside the credential-blind seam, never in graph
or adapter code reachable by a run.

#### Scenario: A non-canonical or credential-bearing URL is refused
- **WHEN** a bound request URL contains userinfo, a fragment, a backslash, an unexpected port, a dot-segment, double-encoding, or a caller-supplied `Host`/`Authorization`/`Cookie`/proxy header
- **THEN** the seam refuses the call before opening a socket and returns structured egress-refusal evidence

#### Scenario: A non-global resolved address is refused and rebinding is defeated
- **WHEN** an allowed host resolves — at any A/AAAA result, including an IPv4-mapped IPv6 or the cloud metadata address — to a non-global address, or resolves differently between preflight and connect
- **THEN** the seam refuses the call, validating and pinning the address actually connected to, and performs no request

#### Scenario: Redirects and ambient proxies cannot exfiltrate the request
- **WHEN** the destination returns a redirect or the environment defines an HTTP proxy
- **THEN** the seam does not auto-follow the redirect and does not route through the ambient proxy, and credentials are never forwarded cross-origin

#### Scenario: An empty allowlist permits no call
- **WHEN** a connection declares no allowed endpoints
- **THEN** every packet referencing it is refused at the egress gate

## MODIFIED Requirements

### Requirement: Twitter effects preserve destination binding with transitional authority and optional receipts
The `twitter_post` behavior SHALL be an instance of the general `authenticated_external_call`
adapter over a per-universe `twitter` connection whose four OAuth 1.0a values are resolved as a
typed bundle inside the credential-blind seam, and SHALL NEVER read credentials from host process
environment variables. Migration SHALL provision and verify the vault `twitter` connection for a
universe first, then flip that universe atomically to the connection-backed path; it SHALL NOT
dual-read vault-then-environment, because a dual-read preserves the cross-universe environment
credential and leaves two credential paths live. Once a universe is on the connection-backed path
the legacy `TWITTER_*` / `TWITTER_<HANDLE>_*` environment resolution SHALL NOT be consulted, and a
disabled or failing new path SHALL fail closed rather than fall through to environment credentials.
The adapter SHALL still derive the posting account from the authorized `destination` and reject a
payload handle that resolves to a different account. A soul-authority resolver result of denied
SHALL dry-run while undeclared authority SHALL fall through to exact destination consent. OAuth
1.0a request signing SHALL be preserved by lifting the existing signer into the seam's `oauth1a`
handler and proving it structurally equivalent to the original modulo nonce and timestamp. The
shared receipt lifecycle and existing duplicate/finalize evidence semantics are unchanged by this
delta.

#### Scenario: Twitter credentials come from the connection, never host env
- **WHEN** a universe is on the connection-backed Twitter path and its vault `twitter` connection is missing or unresolved
- **THEN** the adapter fails closed and does NOT fall back to any `TWITTER_*` process environment variable

#### Scenario: Cutover is atomic, not a dual-read
- **WHEN** a universe's Twitter connection is provisioned and verified and the universe is flipped to the connection-backed path
- **THEN** exactly one credential path is live for that universe and the environment path is no longer consulted

#### Scenario: Twitter payload cannot redirect the authorized account
- **WHEN** a Twitter packet's payload handle differs from the account derived from its authorized destination
- **THEN** the adapter returns `handle_authority_mismatch` and performs no post
