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
Each external tool SHALL call exactly one owner door an outside client can reach, with that door's capability and arguments, a title, and explicit `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`. No tool SHALL select an operation by a field, and an engine's own tools (`read`, `write`, `edit`, `bash`, `ta`) SHALL never be served as ours. Each outside-callable door without a tool SHALL be named with the slice that owes it, or as the doors' own (`toolProfile`).

#### Scenario: A tool added for an owner-only door
- **WHEN** a tool names `grantSet`, `policyWrite` or any door bound through `#owner` or `#widening`
- **THEN** the tool guard fails CI

### Requirement: No tool is open world, and the run tools are run doors
Every tool SHALL be confined to the owner's account, `openWorldHint` false. The run tools (slice R) SHALL be run doors: `converse` (`agentSubmit`, `converse` held where its agent acts) and `run_graph` (`agentRun`, `run_graph`) start an engine run in the owner's sandbox, destructive and not idempotent; `run_status` (`agentStatus`) reads one of the caller's own runs or schedules, from the run's receipt; `run_cancel` (`agentCancel`) cancels one, destructive and idempotent. The owner's `/app/api/agent/{submit,run,status,cancel}` routes SHALL call the same doors. A run's own MCP door SHALL serve the `run` profile: the table but the run tools.

#### Scenario: A tool marked open world
- **WHEN** a tool is marked open world
- **THEN** the tool guard fails CI

#### Scenario: Another client asks for a run
- **WHEN** an outside client reads or cancels a run another client started
- **THEN** it is refused `no_run`, as for a run that does not exist

### Requirement: Each caller is served exactly its own tool profile
A profile SHALL be data (`doors/profiles.json`) listing table tools in the table's order: `custom`, the owner's own choice (by default every tool), which every grant on `/mcp` and every API key names until the owner names another, since each is a custom connector; `directory`, every tool but `code`, a generic executor, as OpenAI's plugin guidelines require (each operation its own tool, no generic executor enabling operations not exposed for review), cited there, for a listing's own MCP resource once it is submitted; `run`, the table but the run tools. `/mcp`, a run's MCP door and `/api` SHALL serve a request only the tools the object answers for its caller (`toolProfile`), asked before any tool is listed; a caller the object refuses SHALL be answered that refusal (`403`) and no tool, and a tool outside the profile SHALL be unknown (`/api`: `404`).

#### Scenario: Two clients of one owner
- **WHEN** the owner names one client's profile `directory` and leaves another's
- **THEN** the first lists exactly the directory's tools and the second the custom ones, `code` among them

#### Scenario: A revoked client lists its tools
- **WHEN** a revoked client sends `tools/list`
- **THEN** it is answered `403` "outside client revoked"

### Requirement: A main agent spends few calls
`/mcp` SHALL serve `inbox_digest` and `board_digest` (what changed for the caller since its cursor, deletions included, in one read) and `code` (one script calling the caller's other tools as `await tools.<name>(args)`, its VM in the hooks Worker, refused once the owner's policy turns code mode off, on by default, or where the caller's profile lacks it).

#### Scenario: A script of two calls
- **WHEN** a new client calls `code` with a script that writes a board row and reads the board digest
- **THEN** one MCP call answers the script's value, with two calls counted `ok` and the write listed as landed

### Requirement: The tier's numbers ride the control record to the run doors
The control record (`c:control`) MAY hold `tiers`: each tier's simultaneous runs (1 to 1,000, the owner database's ceiling) and storage bytes, which the founder edits with no deploy. A record whose `tiers` are malformed SHALL be malformed whole. The doors SHALL read the record once per outside or owner call and hand its `tiers` in the binding (`withTiers`), never from a request; a record without them hands `null`, and the object then forgets the numbers it kept and refuses every run `tier_unread`.

#### Scenario: The founder edits the free tier's runs
- **WHEN** the record's free tier says one run, and a free owner starts a second while the first runs
- **THEN** the second is refused `tier_runs`, with no deploy

### Requirement: A run's own doors are on the egress host, by its egress token
`egress.tinyassets.io/r/<run>/<secret>/mcp` SHALL be the run's MCP door, by the run's own secret its path carries, or `/r/<run>/mcp` with the secret as the `x-ta-run` header, where the CLI's record says it sends one (`run_secret_missing` without either; a model call's path likewise); `/r/<run>/events` its event door and `/signin` the sandbox's report of its engines' sign-ins, by the runner's secret (`x-ta-runner`, else `runner_token_missing`); each also by the egress token E2B's transform sets outside the VM (its MAC verified before any object, then its expiry, its generation and the maintenance gate), bound with the run its path names (`bindRun`; the sign-in report with none, `bindSandbox`) and the SHA-256 of the secret presented, never the secret. A request carrying an `Origin` SHALL be refused, and an events post past `EVENTS_BODY_MAX` or its frame bounds refused `events_too_large`. An events post the object holds no room for (`events_busy`) SHALL be answered 503 with `Retry-After`. A run's model calls SHALL be `/r/<run>/<secret>/c/<connection>/...`.

#### Scenario: A forged token at a run's door
- **WHEN** a request reaches `/r/<run>/events` with a token the routing key did not MAC
- **THEN** it is refused `egress_token_unknown` and no object is reached

### Requirement: The owner app's live socket is opened by a single-use ticket
`POST /app/api/agent/socket-ticket` SHALL mint, behind every first-party token check, a ticket for one of the owner's conversations, bound to the token's grant and expiry: a routing-MAC token of the `socket` kind, which the doors verify (MAC and expiry) before any object. `GET /app/api/agent/socket?conversation_id=&ticket=` with `Upgrade: websocket` SHALL read no bearer; the binder SHALL forward a fresh request of exactly the binder's shape to the object's `fetch`, which consumes the ticket once. A sign-out SHALL tell the object first, cookie or not, so that grant's sockets close `signed_out` before the grant is revoked (A6, salvaged).

#### Scenario: A ticket replayed
- **WHEN** a ticket opens a socket and is presented again, or for another conversation
- **THEN** it is refused `ticket_refused`

#### Scenario: A bearer at the socket door
- **WHEN** the owner's token comes as a bearer, or as the ticket
- **THEN** no socket opens

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
Every outside call (an MCP call, an `/api` call, an outside client's authorization), and each tool call of a `code` script in the object, SHALL pass only when the `control` record (its own `CONTROL` namespace) reads, well formed, with `outside` on and the maintenance gate admitting its owner; a missing, malformed or unreadable record SHALL refuse it. The owner's own surfaces and sign-in SHALL be held by the maintenance gate, and SHALL stay open only when the record is missing.

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
The doors config SHALL bind exactly `OWNER` (the owner Worker's `OwnerDO`, by script name), `GATEWAY` (the gateway Worker's `ConnectGateway` entrypoint), `EGRESS` (its `EgressGateway` entrypoint), `OAUTH_KV`, `DIRECTORY` and `CONTROL` (distinct placeholder ids) and the `DCR_ENABLED` var, with no routes, custom domain, workers.dev or preview URL, and the flags `nodejs_compat`, `disable_ctx_exports` and `global_fetch_strictly_public`.

#### Scenario: A route is added
- **WHEN** a route, another service, a real KV id or another binding is added to the doors config
- **THEN** the config guard fails CI

#### Scenario: The doors bind more of the gateway
- **WHEN** `GATEWAY` or `EGRESS` names the gateway's default entrypoint, its `ConnectGateway` class serves anything but `seal`, `exchange` and `verify`, or its `EgressGateway` class anything but `forward`
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
Each model source SHALL be an editable JSON record in `platform/sources/` naming how it connects (`api_key`, `oauth`, or `cli` alone). A gateway source SHALL name its pi-ai provider, API, base URL, auth, setup check, endpoints and `engines` (the CLIs that run on its key). A record SHALL be checked whole when used and in CI, by shape only, unknown keys refused, its gateway values by the gateway's own check. A record marked unwired SHALL be refused with its reason.

#### Scenario: A record names an unsupported API
- **WHEN** a record names an API the gateway does not run, or breaks the schema
- **THEN** reading it fails loudly naming the record, and CI fails

### Requirement: A CLI record is an engine, never a backend
A `cli` record SHALL have no single-inference `invoke` (ADR-033 decision 2). It SHALL declare its pinned install, its credentials, its new-run and resume invocations, event matchers, instruction and skills paths, MCP template and trust handling, tool filters, unattended and no-update flags, its home, its `session_paths` and `secret_paths`, and per credential whether unattended hosted runs are allowed (ADR-034 decision 5).

#### Scenario: A secret path could leave the sandbox
- **WHEN** a CLI record's `secret_paths` or `session_paths` holds a path not under `{home}` (an absolute path, `/proc/self/cwd`, `/workspace`, a relative path), `{home}` itself, a `..` segment or a `*` first segment, or a secret path inside (or holding) a session path
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: A prompt that could be read as an option
- **WHEN** a CLI record carries `{prompt}` anywhere but on stdin or as one engine argument joined to its option (`--prompt={prompt}`): bare, after `--`, as `--prompt {prompt}`, in an env value or a template
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: An engine configured to nothing
- **WHEN** any argument of a CLI record is empty or bare quotes, sets an option to nothing, or holds an empty list or map (`--tools ""`, `mcp_servers={}`), or an env value or template holds one (`tools.core: []`)
- **THEN** reading it fails loudly naming the record, and CI fails; the runner fills a tool filter with at least one tool and refuses an empty list, so no run is a backend with its tools off

#### Scenario: A record that would carry a credential
- **WHEN** a CLI record's own variable is shaped like a credential or an endpoint, is `HOME` or a process basic, or a settings-file template cannot be read whole (a `.json` file that does not parse; in any other file a quoted key, an escape, an inline table or a line that is not a bare key or table), or its real keys or values hold an environment block, a credential-shaped key or helper (`apiKeyHelper`), a variable its credential's `forbid_env` names, or, in a file a signed-in run gets, a key that moves its endpoint
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: A sign-in with regions
- **WHEN** a CLI record's sign-in serves several regions (`regions`, each with its hosts)
- **THEN** its sign-in carries `{region}`, the owner's choice fills it and adds that region's hosts, and no region is forced

#### Scenario: An unattended verdict with no citation
- **WHEN** a CLI record allows unattended runs on a credential without the vendor page that permits them and the day it was read (ADR-033 decision 3)
- **THEN** reading it fails loudly naming the record, and CI fails; a record that cannot cite one says `allowed: false` with its reason

#### Scenario: A CLI as a backend
- **WHEN** a CLI record carries an `invoke` section
- **THEN** reading it fails loudly naming the record, and CI fails

#### Scenario: An API key that the egress door cannot meter
- **WHEN** an API-key source's API has no rules in `api-rules.ts`, or pi-ai's own model call for it lands outside its sealed endpoints
- **THEN** CI fails

#### Scenario: Sign in with ChatGPT, parked
- **WHEN** the owner begins a connect of the `chatgpt` record, which served the in-object loop ADR-034 removes
- **THEN** it is refused `501` naming why, before any object call

### Requirement: The runner never syncs a secret's bytes
The sandbox runner (slices R and S) SHALL refuse to sync out any file, or any transcript line, that contains the bytes of a file under its engine's `secret_paths`, read from the records, so a secret an engine's own tool printed or copied never leaves the sandbox by its content (ADR-031 decision 2), whatever path it was written to.

#### Scenario: An engine prints its own credential
- **WHEN** an engine's tool prints a `secret_paths` file into its transcript, or copies it under `/workspace`
- **THEN** the runner refuses to sync that line or file, and nothing of the secret reaches the owner's files

### Requirement: The egress door admits a sandbox call by its egress token
The doors SHALL serve `egress.tinyassets.io/r/<run>/<secret>/c/<connection>/*`, a run's own MCP and event doors (`/r/<run>/<secret>/mcp`, `/r/<run>/events`) and the sandbox's sign-in report (`/signin`), and nothing else on that origin, and serve the egress door on no other. A call SHALL carry the sandbox's egress token (`x-ta-sandbox`) and the egress generation its network was made under (`x-ta-network-generation`), both set by E2B's transform outside the VM. The token SHALL name its owner itself and its run's deadline, and carry a MAC (`<owner id>.<expires>.<256-bit secret>.<HMAC-SHA-256 under ROUTING_MAC_KEY over its kind, owner, expiry and secret>`; `routing-mac.ts`, the one module the owner Worker mints with and the doors verify with, the key a Worker secret of both, declared by name in both configs), so the door reaches the owner's object with no directory read; a missing, malformed, forged, altered, other-kind or expired token SHALL be refused by its fixed code before any stub call, the placeholder key the client sent never read, and the object SHALL check the secret's hash, the lease and its expiry. Only once the object admitted and charged the call SHALL it go to the gateway's `EgressGateway.forward`, metered (a read with no metering), its path after `/r/<run>/c/<connection>`, the provider's answer streaming back as it is written (an SSE stream unbuffered) but for the gateway's own headers. Only one doors source SHALL reach `EGRESS`.

#### Scenario: A sandbox client sends its placeholder key
- **WHEN** a sandbox's client posts its model call to `egress.tinyassets.io/r/<run>/c/<connection>/openai/v1/chat/completions` with a live egress token and a placeholder in `authorization`, `x-api-key`, a cookie and `?key=` (of any case)
- **THEN** the provider receives the sealed key in the sealed header, none of the placeholders, the token or the generation, and an output cap no larger than the call's reservation, and the client receives the provider's stream

#### Scenario: A refused call
- **WHEN** the token is missing or malformed, or the object refuses the call
- **THEN** the door answers the refusal's fixed code (and the object's receipt) and nothing is forwarded

#### Scenario: A forged token
- **WHEN** a token's MAC is not the routing key's for an egress token (forged under another key, altered, moved to another owner, or a sync token)
- **THEN** it is refused `egress_token_unknown` and no owner's object is reached

### Requirement: Every admitted egress call is settled once
The egress door SHALL settle every reservation the object admitted, once, with the bytes it streamed, the provider's status and the gateway's metered outcome, on every end: `complete`, `aborted` (the client stopped reading, or left before the answer), `failed` (the gateway failed), `deadline` (the gateway's five minutes, kept by the door too) or `ended` (the object's end signal, which a policy write, a suspension, a revoke, the lease's close and the owner's erasure close). The settle SHALL be registered with `waitUntil` when the call is admitted, and go through a fresh stub with one retry, so a client that leaves and an object that resets both settle. Every stop SHALL abort the provider's request and error the client's stream.

#### Scenario: A policy write mid-stream
- **WHEN** the owner writes its policy while a sandbox response streams
- **THEN** the stream is cut, its reservation settled `ended` with its spend `unknown`, the upstream request cancelled, and when the write changed the egress table, the next call refused until the sandbox's network is applied under the new egress generation

#### Scenario: The owner's object resets mid-stream
- **WHEN** a deploy resets the owner's object while a sandbox response streams
- **THEN** the stream ends, and the settle reaches the object's new instance

### Requirement: A sandbox token reaches no other door
A sandbox token SHALL be refused at `/api`, `/mcp` and `/app`, and the owner's object SHALL refuse a sandbox binding at every door but its own: the egress token at the egress doors and, with a run, at that run's MCP and event doors (every door held to the run's grant), the sync token at the sync doors alone, for its lease and epoch.

#### Scenario: A token presented as a bearer
- **WHEN** a sandbox's egress or sync token is sent to `/api/tools/*`, `/mcp` or `/app/api/*`
- **THEN** each answers `401`
