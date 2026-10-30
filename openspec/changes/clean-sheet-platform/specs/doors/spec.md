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

### Requirement: Only the agent's tools are open world
`openWorldHint` SHALL be true only for the tools that hand work to the owner's agent (`converse`, `run_graph`), which acts on outside services; every other tool is confined to the owner's account.

#### Scenario: A closed-world tool marked open
- **WHEN** a tool other than `converse` or `run_graph` is marked open world, or either of those closed
- **THEN** the tool guard fails CI

### Requirement: The agent's tools reach the caller's own agent work
`converse` (the object's `agentSubmit`, scope `converse`) and `run_graph` (`agentRun`, scope `run_graph`) SHALL be destructive, not idempotent and open world; `run_status` (`agentStatus`) SHALL be a read, idempotent; `run_cancel` (`agentCancel`) SHALL be destructive and idempotent. Each SHALL be served on `/mcp` and as `POST /api/tools/<name>`, and the owner's own surface SHALL serve the same doors as `/app/api/agent/submit`, `run`, `status` and `cancel`, refusing an outside credential there.

#### Scenario: Another owner's operation
- **WHEN** a client of one owner asks `run_status` or `run_cancel` for an operation id another owner's client was given
- **THEN** its own owner's object answers "no such operation", and the other owner's object is not reached

#### Scenario: A key scoped without converse
- **WHEN** an API key whose scopes hold `read_graph` alone calls `/api/tools/converse` or `run_graph`
- **THEN** it is refused `403` with "outside client capability not granted", and no thread is made

### Requirement: Long agent work is polled until the SDK serves MCP tasks
Past `converse`'s bounded wait the answer SHALL be `running` with an `operation_id`, polled through `run_status`, since the MCP SDK 2.3.1 serves no part of the Tasks extension; when it does, a task id SHALL be the operation id, `tasks/get` `agentStatus` and `tasks/cancel` `agentCancel`.

#### Scenario: A turn longer than the wait
- **WHEN** a client's message starts a turn that outlasts its `wait_ms`
- **THEN** `converse` answers `running` with the operation id, and `run_status` later answers the turn's reply

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
Beginning an OAuth connect SHALL set a per-flow `__Host-` binding cookie (SameSite=Lax, ten minutes) holding the digest of its state. The callback SHALL take only a top-level navigation with that binding, reach no object, and hand the code, the state and the redirect URI it arrived on to the owner's app page in the URL fragment. The finish SHALL carry the binding and the session that began the flow, which the object uses up once.

#### Scenario: A callback from another browser
- **WHEN** the callback comes without the flow's binding cookie, or with another flow's
- **THEN** it is refused `403` and no object is reached

#### Scenario: A finish that never reaches the object
- **WHEN** a finish comes cross-origin, as another media type or past the body bound
- **THEN** it is refused and the flow stays open

#### Scenario: A replayed, expired or cancelled flow
- **WHEN** a finish repeats a used state, comes after the flow expired, from another session, or after `sessionCloseAll` or the beginning session's sign-out
- **THEN** it is refused and the gateway is never asked to exchange

### Requirement: Each source returns to its own callback
Each OAuth source SHALL have its own redirect URI, `/oauth/connect/callback/<source>`, a key-exchange source's state one more path segment, and the callback SHALL take only that source's own form. The finish SHALL refuse unless the code came back on the flow's own redirect URI, so a code one provider issued never reaches another's token endpoint (the mix-up attack, RFC 9700 §4.4).

#### Scenario: A code from another provider
- **WHEN** a flow begun for one source is finished with a code that came back on another source's callback, or on the bare callback
- **THEN** it is refused, the code reaches no token endpoint, and the pending connection is given up

#### Scenario: An RFC 6749 state in the path
- **WHEN** an RFC 6749 source's callback carries its state as a path segment, or a key-exchange source's carries it in the query
- **THEN** the callback is refused `400`

#### Scenario: A repeated response parameter
- **WHEN** a callback repeats `state`, `code`, `iss` or `error` (RFC 6749 §3.1), in either order
- **THEN** it is refused `400`, never read as its first or last copy

### Requirement: No uncurated source shares the flow family with a key exchange
A source on an authorization server the platform does not curate (an MCP server, or a source from an owner's package) SHALL NOT be connectable through these connect flows while a key-exchange source (OpenRouter's, which sends no `state` and no `iss`) can be: its own slice SHALL close the key exchange's residual mix-up first.

#### Scenario: A package declares its own OAuth source
- **WHEN** a source record from an owner's package names an authorization server
- **THEN** it is not served by these connect doors until that slice has closed the residual mix-up

### Requirement: A source declaring its issuer is answered with it
A record declaring its issuer and RFC 9207's `authorization_response_iss_parameter_supported` SHALL be answered only with that `iss`, and any `iss` present SHALL be the record's issuer.

#### Scenario: A missing or wrong issuer
- **WHEN** such a source's callback carries no `iss`, or another issuer's
- **THEN** the callback is refused `400`

### Requirement: A failed connect leaves nothing live
A pasted key SHALL be held to the gateway's own secret grammar before any object call. A connect that fails before anything is issued SHALL give its pending connection up at once, and one that fails after the provider issued a credential SHALL commit it `failed`, never drop it.

#### Scenario: A key with a newline or a non-ASCII character
- **WHEN** the owner pastes a key outside the gateway's secret grammar
- **THEN** it is refused `400` before any object call

#### Scenario: A key the provider refuses
- **WHEN** the setup check refuses a pasted key
- **THEN** the answer is the gateway's fixed code and the connection holds no slot

### Requirement: Source records are data checked by the gateway's own rule
Each model source SHALL be an editable JSON record in `platform/sources/` naming how it connects (`api_key`, `oauth`, or `cli` alone) and, for a gateway source, its pi-ai provider, API, base URL, auth header, setup check and endpoints. A record SHALL be checked whole when used and in CI, its gateway values by the gateway's own source check, so the gateway's `SUPPORTED_APIS` is the one list. A record marked unwired SHALL be refused with its reason.

#### Scenario: A record names an unsupported API
- **WHEN** a record names an API the gateway does not run, or breaks the schema
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: Sign in with ChatGPT before OpenAI's approval
- **WHEN** the owner begins a connect of the `chatgpt` record
- **THEN** it is refused `501` naming why, before any object call
