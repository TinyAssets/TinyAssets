## ADDED Requirements

### Requirement: The doors Worker is the only binder
One module of the doors Worker SHALL hold every `getByName` call and build every binding. Every binding field (owner, caller, outside origin, source, `authenticated_at`) SHALL come from the verified credential, never from a body, query, header or cookie; the session cookie is the one value a request supplies, and the object checks it.

#### Scenario: A request names another owner
- **WHEN** Alice's MCP token, API key or first-party token comes with an `owner`, `caller` or `origin` in a header, the query or the body
- **THEN** the call is served on Alice's object as Alice, a tool argument naming them is refused, and Bob's object is never reached

#### Scenario: A grant's props disagree with its token
- **WHEN** an MCP token's props name another owner, client or origin than the provider recorded for its grant
- **THEN** the binder refuses it as a bug and no object is reached

### Requirement: Our authorization server issues the connectors' tokens
The doors SHALL run their own OAuth authorization server, issuer `https://tinyassets.io`, on Workers OAuth Provider 1.2.4, with the resources `/mcp` (outside clients) and `/app` (first-party clients only). Client ID Metadata Documents SHALL be on; dynamic registration SHALL be on only behind its flag. Every authorization SHALL run the upstream sign-in with `prompt=login` and server-side PKCE, and SHALL take a sign-in only when at most 600 s old. Consent SHALL never be remembered.

#### Scenario: A client authorizes again after a revoke
- **WHEN** the owner revoked an MCP client and the client starts a new authorization
- **THEN** the consent screen is shown again, and the object admits the client only from the new, later sign-in

#### Scenario: An upstream that does not ask for a fresh sign-in
- **WHEN** an upstream's sign-in URL lacks `prompt=login`, the doors' S256 challenge, state or callback
- **THEN** the authorization fails loudly

### Requirement: The owner is named by the sign-in's issuer and subject
The doors SHALL name the owner from the verified sign-in's issuer and subject, against an explicit list: the owner issuer's (AuthKit's) subject as it stands, so today's owners keep their ids, and a listed foreign issuer's subject namespaced by that issuer. Any other issuer SHALL be refused, and with no owner issuer configured every sign-in SHALL fail.

#### Scenario: A second provider signs in a subject AuthKit also has
- **WHEN** a listed foreign issuer's sign-in carries a subject equal to an AuthKit owner's id
- **THEN** it names a different, namespaced owner, and the AuthKit owner's object is never reached

#### Scenario: The owner issuer is spelled differently
- **WHEN** a sign-in's issuer differs from the configured owner issuer by a trailing slash
- **THEN** it is refused loudly and no object is reached, never taken for a new, empty owner

### Requirement: An MCP call carries its grant's original sign-in
An MCP grant's encrypted props SHALL hold the time of the interactive sign-in that created it, and no token exchange SHALL change them, so every MCP call carries that time as `authenticated_at`. A native client's loopback port (`localhost`, `127.0.0.0/8`, `::1`) SHALL be no part of its agent identity, and its new grant SHALL replace its earlier ones from the same installation whatever their ports.

#### Scenario: A token refreshed twice
- **WHEN** a client signed in at T refreshes its token twice and calls a tool
- **THEN** the owner's object sees `authenticated_at` T

### Requirement: A revoke reaches the object first, then the provider
The owner's revoke of an MCP client SHALL be made in the owner's object first, then SHALL revoke every provider grant of that client, so its next refresh is `invalid_grant`. A revoke the object refuses SHALL revoke nothing in the provider.

#### Scenario: A revoked client refreshes
- **WHEN** the owner revokes an MCP client and the client refreshes
- **THEN** the token endpoint answers `invalid_grant`, and its access token is refused

### Requirement: One stateless MCP endpoint serves both revisions
`/mcp` SHALL serve MCP 2026-07-28 and 2025-11-25 from one stateless handler and one tool factory, with GET and DELETE answered `405`. A present `Origin` SHALL be refused `403` unless it is on the allow-list, which is empty.

#### Scenario: A 2025-11-25 client and a 2026-07-28 client
- **WHEN** each lists the tools and calls one
- **THEN** both see the same table and reach the same door

### Requirement: One external tool per outside-callable owner door
Each external tool SHALL call exactly one owner door an outside client can reach, with that door's capability and arguments, a title, and explicit `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`. No tool SHALL select an operation by a field, and the model's own tools (`read`, `write`, `edit`, `bash`, `ta`) SHALL never be served. Each outside-callable door without a tool SHALL be named with the slice that owes it.

#### Scenario: A tool added for an owner-only door
- **WHEN** a tool names `grantSet`, `policyWrite` or any door bound through `#owner` or `#widening`
- **THEN** the tool guard fails CI

### Requirement: API keys keep the Python's format and order
An API key SHALL be `ta_key_<16 hex id>_<43 urlsafe secret>`, its directory entry SHALL hold its owner, the SHA-256 hex of its secret and its mint time, compared in constant time. A mint SHALL write the grant before the entry, in the owner's session; a revoke SHALL revoke the grant before deleting the entry; a delete SHALL delete the entry before forgetting the grant. A key's call SHALL bind the key's own origin and its mint time.

#### Scenario: A key whose entry cannot be deleted
- **WHEN** the owner revokes a key and the directory delete fails
- **THEN** the key is refused at once by the object

#### Scenario: A wrong secret
- **WHEN** a key with a matching id but another secret calls `/api`
- **THEN** it is `401` and no object is reached

### Requirement: The session cookie rides only the owner's own JSON requests
The session cookie SHALL be `__Host-ta-owner`, Secure, HttpOnly, Path=/, SameSite=Strict, eight hours. The doors SHALL forward it only on a non-GET `application/json` request to `/app/api/*` whose `Origin` is exactly the public origin and whose `Sec-Fetch-Site`, when present, is `same-origin`, and SHALL remove the `Cookie` header from `/mcp`, `/api` and `/hooks`. An outside credential on `/app/api/*` SHALL be refused `403` before any object call.

#### Scenario: A cross-site form posts to the owner surface
- **WHEN** another site's page makes the owner's browser post to `/app/api/keys`
- **THEN** no session is forwarded and the object refuses the widening

### Requirement: The outside kill switch fails closed
Every outside call (an MCP call, an `/api` call, an outside client's authorization) SHALL pass only when the directory's `control` record reads, well formed, with `outside` on and the maintenance gate admitting its owner; a missing, malformed or unreadable record SHALL refuse it. The owner's own surfaces and sign-in SHALL be held by the maintenance gate, and SHALL stay open only when the record is missing.

#### Scenario: The control record is missing
- **WHEN** the directory holds no `control` record
- **THEN** outside calls are refused `503` before any object, and the owner's own surface stays open

#### Scenario: The control record cannot be read
- **WHEN** the record is malformed, or the directory read fails
- **THEN** outside calls and the owner's own surface and sign-in are all refused

### Requirement: Nothing but the owner surface is served on our origin
No published app, user content or third-party page SHALL ever be served on `https://tinyassets.io`: the session cookie, the exact-`Origin` rule and the first-party redirect all rest on every page of that origin being ours. No outside client SHALL be sent access on `tinyassets.io` or any of its subdomains.

#### Scenario: A client document names a redirect on one of our hosts
- **WHEN** a CIMD document or a registered client names a redirect on `tinyassets.io` or a subdomain such as `mcp.tinyassets.io`
- **THEN** its authorization is refused before the consent page

### Requirement: Sign-out ends the device's access, and devices sign in apart
Each sign-in of the owner's app SHALL keep its own grant, so devices stay signed in together. Sign-out SHALL close the session and revoke the presented token's grant, leaving other devices signed in.

#### Scenario: The owner signs out on one device
- **WHEN** the owner signs out on a phone while signed in on a laptop
- **THEN** the phone's token is refused at once and the laptop's still works

### Requirement: The doors Worker cannot deploy before cutover
The doors config SHALL bind exactly `OWNER` (the owner Worker's `OwnerDO`, by script name), `GATEWAY` (the gateway Worker's `ConnectGateway` entrypoint), `OAUTH_KV` and `DIRECTORY` (distinct placeholder ids) and the `DCR_ENABLED` var, with no routes, custom domain, workers.dev or preview URL, and the flags `nodejs_compat`, `disable_ctx_exports` and `global_fetch_strictly_public`.

#### Scenario: A route is added
- **WHEN** a route, another service, a real KV id or another binding is added to the doors config
- **THEN** the config guard fails CI

#### Scenario: The doors bind more of the gateway
- **WHEN** `GATEWAY` names the gateway's default entrypoint, or its `ConnectGateway` class serves anything but `seal`, `exchange` and `verify`
- **THEN** the config guard fails CI

### Requirement: A pasted key or OAuth code reaches only the gateway
A pasted key SHALL go only to the gateway's `seal`, and an OAuth code only to its `exchange`: neither SHALL appear in any call to the owner's object, its storage, a log, an error or an answer, and only one doors source SHALL reach `GATEWAY`.

#### Scenario: A key is pasted
- **WHEN** the owner pastes a key for a source record
- **THEN** the object sees `connectionPrepare` and then `connectionCommit` with the sealed blob, and the key reaches only the gateway and the record's setup check

### Requirement: The connect doors are the owner's own surface
The connect doors (`/app/api/connections/*`) SHALL take only the owner's first-party token, same-origin JSON within the body bound, and its live session. Each body SHALL be a closed set of fields: the owner SHALL come from the token alone, and the connection and its version from the object alone.

#### Scenario: A connect request names an owner, connection or version
- **WHEN** a connect body carries any field beyond its own
- **THEN** it is refused `400` before any object call

#### Scenario: An outside credential on a connect door
- **WHEN** an MCP token or an API key is presented to a connect door
- **THEN** it is refused `403` before any object call

### Requirement: A connect flow finishes only in the browser and session that began it
Beginning an OAuth connect SHALL set a per-flow `__Host-` binding cookie (SameSite=Lax, ten minutes) holding the digest of its state. The callback (`/oauth/connect/callback`) SHALL take only a top-level navigation with that binding, reach no object, and hand the code and state to the owner's app page in the URL fragment. The finish SHALL carry the binding and the session that began the flow, which the object uses up once.

#### Scenario: A callback from another browser
- **WHEN** the callback comes without the flow's binding cookie, or with another flow's
- **THEN** it is refused `403` and no object is reached

#### Scenario: A replayed, expired or cancelled flow
- **WHEN** a finish repeats a used state, comes after the flow expired, from another session, or after `sessionCloseAll` or the beginning session's sign-out
- **THEN** it is refused and the gateway is never asked to exchange

### Requirement: Source records are data checked by the gateway's own rule
Each model source SHALL be an editable JSON record in `platform/sources/` naming how it connects (`api_key`, `oauth`, or `cli` alone) and, for a gateway source, its pi-ai provider, API, base URL, auth header, setup check and endpoints. A record SHALL be checked whole when used and in CI, its gateway values by the gateway's own source check, so the gateway's `SUPPORTED_APIS` is the one list. A record marked unwired SHALL be refused with its reason.

#### Scenario: A record names an unsupported API
- **WHEN** a record names an API the gateway does not run, or breaks the schema
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: Sign in with ChatGPT before OpenAI's approval
- **WHEN** the owner begins a connect of the `chatgpt` record
- **THEN** it is refused `501` naming why, before any object call
