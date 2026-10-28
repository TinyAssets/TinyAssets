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

### Requirement: An MCP call carries its grant's original sign-in
An MCP grant's encrypted props SHALL hold the time of the interactive sign-in that created it, and no token exchange SHALL change them, so every MCP call carries that time as `authenticated_at`.

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
Every outside call (an MCP call, an `/api` call, an outside client's authorization) SHALL pass only when the directory's `control` record reads, well formed, with `outside` on and the maintenance gate admitting its owner; a missing, malformed or unreadable record SHALL refuse it. The owner's own surfaces SHALL be held only by the maintenance gate.

#### Scenario: The control record is missing
- **WHEN** the directory holds no `control` record
- **THEN** outside calls are refused `503` before any object, and the owner's own surface stays open

### Requirement: The doors Worker cannot deploy before cutover
The doors config SHALL bind exactly `OWNER` (the owner Worker's `OwnerDO`, by script name), `OAUTH_KV` and `DIRECTORY` (distinct placeholder ids) and the `DCR_ENABLED` var, with no routes, custom domain, workers.dev or preview URL, and the flags `nodejs_compat`, `disable_ctx_exports` and `global_fetch_strictly_public`.

#### Scenario: A route is added
- **WHEN** a route, a service, a real KV id or another binding is added to the doors config
- **THEN** the config guard fails CI
