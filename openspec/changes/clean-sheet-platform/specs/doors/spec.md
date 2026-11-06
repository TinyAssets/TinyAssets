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
The doors SHALL run their own OAuth authorization server, issuer `https://tinyassets.io`, on Workers OAuth Provider 1.2.4, with the resources `/mcp` (outside clients) and `/app` (first-party clients only). Client ID Metadata Documents SHALL be on; dynamic registration SHALL be served only behind its flag (`DCR_ENABLED`), and then SHALL take a registration only when its `client_name` names a vendor `apps.json` marks `registration: "dcr"` (a vendor that offers no CIMD: the founder's ruling, the one pre-login exception to no anonymous traffic) and every one of its `redirect_uris` is one of that vendor's recorded redirects; a `dcr` vendor with none recorded SHALL take none. Any other registration SHALL be refused `invalid_client_metadata` before anything is stored. A refused registration whose name names a `dcr` vendor SHALL be logged in one bounded, escaped line (`dcr_refused`: the app, `client_name`, `redirect_uris`), and no other. At sign-in the doors SHALL name the client's vendor from `apps.json`: a CIMD client by its document URL's host, verified only when every redirect it registered is that app's own, else unverified; any other by a word of its registered name (unverified); none when no app matches; the grant's props carry it, and its binding every call. Every authorization SHALL run the upstream sign-in with a forced fresh sign-in (`max_age=0`, and no `prompt` of the upstream's own) and server-side PKCE, and SHALL take a sign-in only when at most 600 s old. Consent SHALL never be remembered.

#### Scenario: A vendor's client registers itself
- **WHEN** `DCR_ENABLED` is true and clients register as "Grok" with one of Grok's recorded redirects, as "Grok official support" with a stranger's redirect, as "Gemini" before Gemini's redirects are recorded, and as "ChatGPT"
- **THEN** the first is registered; the other three are refused `400 invalid_client_metadata`

#### Scenario: A CIMD document on a vendor's host sends access elsewhere
- **WHEN** a client's metadata document is on `chatgpt.com` and its redirect is a stranger's host
- **THEN** its grant is recorded `chatgpt`, unverified

#### Scenario: A client authorizes again after a revoke
- **WHEN** the owner revoked an MCP client and the client starts a new authorization
- **THEN** the consent screen is shown again, and the object admits the client only from the new, later sign-in

#### Scenario: An upstream that does not ask for a fresh sign-in
- **WHEN** an upstream's sign-in URL lacks `max_age=0`, carries a `prompt` of its own, or lacks the doors' S256 challenge, state or callback
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
The owner's revoke of an MCP client SHALL be made in the owner's object first, then SHALL revoke every provider grant of that client, so its next refresh is `invalid_grant`. A revoke the object refuses SHALL revoke nothing in the provider. The same revoke of an API key's client SHALL also delete the key's directory entry, after the grant's revoke, as the key's own revoke does.

#### Scenario: A revoked client refreshes
- **WHEN** the owner revokes an MCP client and the client refreshes
- **THEN** the token endpoint answers `invalid_grant`, and its access token is refused

### Requirement: One stateless MCP endpoint serves both revisions
`/mcp` SHALL serve MCP 2026-07-28 and 2025-11-25 from one stateless handler and one tool factory, with GET and DELETE answered `405`. A present `Origin` SHALL be refused `403` unless it is on the allow-list, which is empty.

#### Scenario: A 2025-11-25 client and a 2026-07-28 client
- **WHEN** each lists the tools and calls one
- **THEN** both see the same table and reach the same door


### Requirement: Each tool result is sent once, as text, within one answer
A tool's result SHALL be one `text` content block holding its JSON, with no `structuredContent` (no tool declares an `outputSchema`, so none is required, and every client reads text), on `/mcp` and as `/api`'s body alike. Its JSON SHALL be measured before it is built; past `RESULT_CHARS` it is a bug, logged and failed as `internal error`, never cut.

#### Scenario: A result past one answer
- **WHEN** a door answers a result whose JSON passes `RESULT_CHARS`
- **THEN** the call fails as `internal error` and the size is logged, and no truncated text is sent

### Requirement: One external tool per outside-callable owner door
Each external tool SHALL call exactly one owner door an outside client can reach, with that door's capability and arguments, a title, and explicit `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`. No tool SHALL select an operation by a field, and an engine's own tools (`read`, `write`, `edit`, `bash`, `ta`) SHALL never be served as ours. Each outside-callable door without a tool SHALL be named with the slice that owes it, or as the doors' own (`toolProfile`).

#### Scenario: A tool added for an owner-only door
- **WHEN** a tool names `grantSet`, `policyWrite` or any door bound through `#owner` or `#widening`
- **THEN** the tool guard fails CI

### Requirement: No tool is open world, and the run tools are run doors
Every tool SHALL be confined to the owner's account, `openWorldHint` false. The run tools (slice R) SHALL be run doors: `converse` (`agentSubmit`, `converse` held where its agent acts) and `run_graph` (`agentRun`, `run_graph`) start an engine run in the owner's sandbox, destructive and not idempotent; `run_status` (`agentStatus`) reads one of the caller's own runs or schedules, from the run's receipt; `run_cancel` (`agentCancel`) cancels one, destructive and idempotent. The owner's `/app/api/agent/{submit,run,status,cancel}` routes SHALL call the same doors. A run's own MCP door SHALL serve the `run` profile: the table but `converse` (work between agents is mail) and the mailbox tools (`mailbox_read`, `mailbox_ack`, `inbox_digest`; slice ID); its `run_graph`, `run_status` and `run_cancel` are held to its grant (the founder, 2026-11-03).

#### Scenario: A tool marked open world
- **WHEN** a tool is marked open world
- **THEN** the tool guard fails CI

#### Scenario: Another client asks for a run
- **WHEN** an outside client reads or cancels a run another client started
- **THEN** it is refused `no_run`, as for a run that does not exist

### Requirement: Each caller is served exactly its own tool profile
A profile SHALL be data (`doors/profiles.json`) listing table tools in the table's order: `custom`, the owner's own choice (by default every tool), which every grant on `/mcp` and every API key names until the owner names another, since each is a custom connector; `directory`, every tool but `code`, a generic executor, as OpenAI's plugin guidelines require (each operation its own tool, no generic executor enabling operations not exposed for review), cited there, for a listing's own MCP resource once it is submitted, `search` and `fetch` among them in the shape ChatGPT's deep research and company knowledge call; `run`, the table but `converse` and the mailbox tools. `/mcp`, a run's MCP door and `/api` SHALL serve a request only the tools the object answers for its caller (`toolProfile`), asked before any tool is listed; a caller the object refuses SHALL be answered that refusal (`403`) and no tool, and a tool outside the profile SHALL be unknown (`/api`: `404`).

#### Scenario: Two clients of one owner
- **WHEN** the owner names one client's profile `directory` and leaves another's
- **THEN** the first lists exactly the directory's tools and the second the custom ones, `code` among them

#### Scenario: A directory client searches the owner's files
- **WHEN** a client served the directory profile calls `search` with a word in a file's text, then `fetch` with the id it answered
- **THEN** `search` answers `{results: [{id, title, url}], next_cursor: null}` as one text block of its JSON, and `fetch` the file's id, title, text, url and metadata

#### Scenario: A revoked client lists its tools
- **WHEN** a revoked client sends `tools/list`
- **THEN** it is answered `403` "outside client revoked"

### Requirement: An outside client's agent is told the shared brain's rules
`/mcp` SHALL serve the `custom` and `directory` profiles fixed MCP `instructions` (`CLIENT_INSTRUCTIONS`), and a run's door its own (`RUN_INSTRUCTIONS`). The client text's first 512 characters SHALL hold the brain's rules: the user's goals index read before acting, look here first (`search`, then `fetch`; `/workspace/index.md`) before asking, save what the user says (`file_append` adds, `file_edit` corrects), and claim an act with a new board row before taking it. It SHALL then say how to onboard a user new to TinyAssets (no `Onboarded` date and no time zone in `/workspace/USER.md`) and to offer, once, an assistant `agents_list` shows not connected, recording the offer or the no in `USER.md`. It SHALL name only the table's tools, never another vendor's, never "always", and SHALL ask no agent to save to its own memory or install anything.

#### Scenario: A client connects
- **WHEN** an outside client's MCP session initializes
- **THEN** its `instructions` are `CLIENT_INSTRUCTIONS`, whose first 511 characters are the brain's rules

### Requirement: An outside agent writes back what it learns
`/mcp` SHALL serve every profile three file writers over the owner's file doors, each held to `write_files` and to the agent-trees rule: `file_append` (text added after a file's bytes, the file made when absent; it never changes a byte already there, so `destructiveHint: false`; it writes at the etag it read and reads again on a conflict, so two appends never lose each other, and a chunked file keeps its whole chunks), `file_write` (a whole file's text at its etag, or `""` to create it only where absent; the object SHALL refuse an outside client's or a run's write with no etag, so no agent replaces a file it did not read; `file_remove`, `file_rename` and `file_restore` SHALL likewise take a string etag from them) and `file_edit` (the one exact occurrence of `old_text` replaced at the file's etag; none, or more than one, refused with how many), both destructive. `file_append` SHALL refuse a JSON, TOML or YAML file (an append breaks what it means); a file that stays small SHALL be read and written in one unit, never retried; a larger one SHALL recheck the agent trees on each try and stop with `file_busy` after `APPEND_TRIES` lost tries. `fetch`'s metadata SHALL carry the file's etag. `search`, `fetch` and `file_read` SHALL each say when to use them (what the user or their other agents already saved), and `collection_write` that a new row is a claim. File reads, listings and writes, and `agents_list`, SHALL wait for a new owner's starter seed, after the caller's authority is checked.

#### Scenario: Two vendors' agents share one brain
- **WHEN** one client appends a nonce to `/workspace/USER.md` and another client of the same owner searches for it
- **THEN** the second finds `/workspace/USER.md`, its text holds the nonce and the seeded profile whole, and it corrects the line with `file_edit` at the etag `fetch` gave

#### Scenario: Two agents claim one act at once
- **WHEN** two clients `collection_write` the board row `offer:connect:grok` with `expected_etag: ""` together
- **THEN** exactly one creates it and the other is answered the conflict, and one row exists

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

### Requirement: The doors serve the owner app on the owner surface's origin
The doors Worker SHALL serve the owner app at `/app`, `/app/signed-in`, `/app/connected` and its named files under `/app/static/`, from its own `ASSETS`, and nothing else under `/app` but the owner surface. Each answer SHALL carry `Referrer-Policy: no-referrer` and a strict CSP (this origin only, nothing inline, no framing). Each return page's first and only script SHALL clear the code from the address bar before anything else.

#### Scenario: The connect callback hands the app its code
- **WHEN** a provider returns the owner's browser to `/oauth/connect/callback/<source>`, which hands the code to `/app/connected` in the fragment
- **THEN** the page's own script clears the fragment before any other script runs, and posts the code to `connections/oauth/finish` with no referrer

#### Scenario: A path under /app that the app does not name
- **WHEN** a browser asks for `/app/index.html` or any unnamed file
- **THEN** the doors answer `404`, and the assets are reached for nothing

### Requirement: The owner app's routes are each one door
The owner surface SHALL serve the owner app's reads and settings, each one owner door behind the first-party token: `account` (the owner's tier with the control record's numbers and the file usage), `notices`, `policy`, `policy/write`, `grants`, `grants/set`, `files/read`, `files/write` (text, against etags), `sources`, `capabilities`, `tools`, `agent/doorbell/kinds`, and `tools/<name>` (one table tool, the owner as itself). A revoke SHALL go by `grants/revoke` alone.

#### Scenario: Any owner-surface answer opened as a page
- **WHEN** a browser opens an owner-surface answer as a document
- **THEN** it carries `nosniff` and a CSP of none, so it is never sniffed, run or framed

#### Scenario: The owner sets a client's grant to revoked through grants/set
- **WHEN** the owner's app posts `grants/set` with mode `revoked`
- **THEN** it is refused `400`, and nothing changes

### Requirement: Sign-out ends the device's access, and devices sign in apart
Each sign-in of the owner's app SHALL keep its own grant, so devices stay signed in together. Sign-out SHALL close the session and revoke the presented token's grant, leaving other devices signed in.

#### Scenario: The owner signs out on one device
- **WHEN** the owner signs out on a phone while signed in on a laptop
- **THEN** the phone's token is refused at once and the laptop's still works

### Requirement: The doors Worker cannot deploy before cutover
The doors config SHALL bind exactly `OWNER` (the owner Worker's `OwnerDO`, by script name), `GATEWAY` (the gateway Worker's `ConnectGateway` entrypoint), `EGRESS` (its `EgressGateway` entrypoint), `OAUTH_KV`, `DIRECTORY` and `CONTROL` (distinct placeholder ids), `ASSETS` (the owner app's files, `./app`, the Worker first on every request) and the `DCR_ENABLED` var, with no routes, custom domain, workers.dev or preview URL, and the flags `nodejs_compat`, `disable_ctx_exports` and `global_fetch_strictly_public`.

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

### Requirement: The sandbox runs behind one Worker that reaches only E2B
The `tinyassets-sandbox` Worker SHALL be the only holder of the E2B API key and SHALL reach nothing but E2B's own hosts (its platform API and envd) and the ring queue, binding only its own two classes (one `SandboxControl` an owner, which serializes that owner's E2B calls; one `SandboxSlots`, the platform's line for one more running sandbox), the control record's namespace, read for the record alone, and the ring queue's producer. A start SHALL find the run's authority's sandbox running, resume it (memory kept, its lockdown re-checked) or create it (`secure`, so envd takes only the sandbox's own token) from the versioned template with the lease's network, its timeout the control record's `sandbox_runtime_ceiling_s` and paused on timeout; a resume or create SHALL wait its turn in the line, and past `sandbox_concurrency_ceiling` (E2B's own team-wide count, advisory: an unknown count tries the create) or refused by E2B for room SHALL answer `sandbox_waiting`, the owner keeping its place; each sandbox the platform pauses or kills SHALL ring the first owner in line, who is rung again, backing off, while it waits; a run whose doors are not `https` on the egress host SHALL be refused. A run on the vendor's own sign-in SHALL widen the network to its record's login and provider hosts and run its `status_argv` there first, answering `signin_required` when none is signed in; the lease's secrets SHALL go to the runner's root-only state directory and the start document without them. Before the runtime ceiling (E2B's own `endAt`) the Worker SHALL renew each running sandbox by a pause with its memory and an immediate resume that never waits its turn, at a quiet moment of its CLIs inside the control record's `sandbox_renewal_window_s`, or forced at the measured edge; a sandbox with no live run SHALL be paused instead, one idle a lease's TTL SHALL be reaped, one E2B paused while a lease runs in it SHALL be resumed, and one E2B kept filesystem-only SHALL restart its runs from their sessions. Its alarm SHALL be armed again on every path, a failed look backing off and an unreadable control record read as the last numbers read. The owner's erasure SHALL kill every sandbox of theirs, paused ones and strays its metadata tag finds included, and delete its state of them. No number of the plan's SHALL be in code: the ceilings and the window are the control record's, raised with no deploy.

#### Scenario: Renewal at a quiet moment
- **WHEN** a sandbox with a live run is inside its renewal window and its runner reports its CLIs quiet
- **THEN** the runner posts `ta.renewing`, holds its posts, the sandbox is paused and resumed, and the runner posts `ta.renewed` and goes on

#### Scenario: The concurrency ceiling reached
- **WHEN** E2B runs as many sandboxes as the control record allows and a start needs one more
- **THEN** the start answers `sandbox_waiting`, nothing is created, and the owner waits in the line until a freed sandbox rings it

#### Scenario: A newcomer while an owner waits
- **WHEN** a sandbox frees while one owner waits and another owner's start arrives first
- **THEN** the newcomer waits behind it, and the owner that waited longer is rung

### Requirement: The sync agent reaches its own doors from inside its sandbox alone
The doors SHALL serve the in-VM sync agent's doors on the egress host (`/sync/changes`, `/sync/list`, `/sync/read`, `/sync/chunks`, `/sync/upload_begin`, `/sync/upload_chunks`, `/sync/commit`) by the lease's sync token (`x-ta-sync`), its MAC verified as the `sync` kind before any object, together with the egress token E2B's transform sets on the call (verified as the egress doors verify it, naming the same owner), then their expiry and the maintenance gate; no token, another kind's or a forged one, or a sync token without its sandbox's egress token, SHALL be refused, and a request with an `Origin` 404. The object SHALL hold the two tokens to one live lease, each read to the readable set its change feed is filtered by, a path outside it answered as missing, each upload to the lease's own writer, and each commit to the live lease and epoch. A file past one inline write SHALL go by the upload doors, never refused for its size.

#### Scenario: The egress token at a sync door
- **WHEN** a request presents the egress token as the sync token
- **THEN** it is refused 401 and no object is reached

#### Scenario: A sync token taken out of the VM
- **WHEN** a request presents a live sync token without its lease's egress token
- **THEN** it is refused and no lease's files are read or written

### Requirement: A subscription is signed in inside the sandbox by the vendor's own flow
The owner SHALL sign in to a source only by the record's `cli.login`, run on a terminal inside the owner's sandbox (or one client's, for that client) as a user of its own; only the record's pinned page or device code and a sign-in's fixed states SHALL leave the VM, checked again by the owner's object; each source's sign-in SHALL sit behind its own control-record switch, held by the object on every path, defaulting to the record's cited verdict on hosted use.

#### Scenario: A pasted code
- **WHEN** the owner pastes the vendor's code into a live sign-in
- **THEN** it reaches only that login's stdin, is never logged or stored, and is refused once the login has ended

#### Scenario: A look-alike sign-in page
- **WHEN** the runner relays a URL off the record's login hosts
- **THEN** the object refuses it, the sign-in ends `refused`, and the owner never sees the link

#### Scenario: A source whose terms are unverified
- **WHEN** the owner asks to sign in to a source whose switch is off, or a run would use its vendor login
- **THEN** the object refuses it `signin_off`, and still lets the owner sign that source out

#### Scenario: A sign-in left open
- **WHEN** the vendor's code's cited lifetime passes
- **THEN** the sign-in ends `expired`, its CLI stopped and its lease freed, and the app offers to start again

### Requirement: The runner runs each CLI from its record, as its own user
The in-VM runner SHALL post an empty batch as soon as it runs, before the sync; set the run's environment to the allow-list alone (`config.env` filled and `config.inherit` from its own), `HOME` the run's own home, made from its engine's store (the record's `secret_paths` and `session_paths` alone); run the CLI as a user of the run's own from the template's pool (no `sudo`, checked at each boot), killing every process of that user at the run's end, with `/proc` mounted `hidepid=2` and the runner's secrets in its root-only state directory; never follow, as root, a path another user could have placed (each write and ownership change by a no-follow descriptor, a link refused or replaced); keep no path writable by another user but those the design needs, each named with its reason, verified at every boot and resume; invoke the record's `argv`, or `resume_argv` with the session id, each option joined to its value (`--model=<id>`); and post its events within the object's bounds, one post in flight, at least every 20 s, an event past a frame's bound cut and marked, a post answered `events_busy` posted again after `Retry-After`, the end after the sync-out. A CLI's line shaped as the runner's own event (a `type` beginning `ta.`, or the cut marker `ta_cut`), which the run's agent and every tool it runs may write, SHALL never be posted as it: it is posted as `ta.cli_refused` with its text, so every `ta.` event the doors and the object read is the runner's. It SHALL refuse a run whose env files the CLI would load (its home's settings, the agent's project files, and each ancestor's `.env` and `.gemini/.env` from its cwd up) set a variable its credential forbids, a credential- or endpoint-shaped one, or a credential helper; project into each engine's skills path the agent's own skills and the owner's shared ones (`/workspace/.agents/skills`, `/workspace/skills`), the agent's winning a clash, a client's sandbox only what it may read; and SHALL stop the CLI with SIGTERM, then a kill after its grace, on a cancel or when the object ended the run.

#### Scenario: A project env file
- **WHEN** the agent's `.env` sets `ANTHROPIC_API_KEY`
- **THEN** the CLI never runs and the run ends failed with a `ta.refused` frame

### Requirement: An agent works in scratch apart from the owner's storage
Each agent SHALL have a scratch area on its sandbox's own disk (`/scratch/<agent>`, group `ta`), never synced and never counted in the owner's storage, bounded only by the sandbox's disk (the sandbox vendor's limit, the same on every tier), kept between runs in the paused sandbox as a cache. Before a run, scratch unused for the control record's `scratch_retention_days` SHALL be deleted, then, while the disk's free space is under a tenth of it, the least recently used, never a live run's agent's; what went SHALL be told to the agent and the user in plain words, saying their storage is unaffected and never offering an upgrade. A run's engine SHALL be told where scratch is (`TA_SCRATCH`, its tools' caches pointed there, and the run-scoped MCP door's instructions): clones and builds go there, and what the user keeps goes to `/workspace` or leaves by `git push`.

#### Scenario: A repository cloned for a task
- **WHEN** a run clones a repository into its scratch and builds it
- **THEN** none of it is synced or counted, and the next run of that agent finds it there unless the disk needed the room or it went unused past the retention

### Requirement: Sync-out streams, the sessions first, the owner's ignore list kept
Sync-out SHALL read a file a chunk at a time, hashing and scanning it as it reads, and send each batch as it fills, so what it holds is one batch's small files and one chunk whatever the run changed. The engines' sessions SHALL be committed first, in commits of their own. Sync-in SHALL stream each file's chunks to a temporary file beside it, renamed into place when whole, so a file larger than the sandbox's memory syncs in; and sync-out's memory SHALL not grow with the number of paths (the mirror an on-disk database, the tree walked a directory at a time, removals paged). Paths the owner's `/workspace/.ta/sync-ignore` names (regenerable trees by default, the file written where the owner sees and edits it) SHALL never be offered nor removed. Its lines SHALL mean what they mean in a `.gitignore` at `/workspace`, as git reads one (`git check-ignore` the oracle; nothing had shipped, so no list changed meaning): a name at any depth, a path with a `/`, `/` at the end for folders only, `!` re-including with the last match winning and nothing under an ignored folder coming back, `*` and `?` within a segment, `**`, classes and escapes; the file itself is never ignored, and every agent of the owner and every full-power client may edit it (a narrowed grant is refused as at every file door). Each change to it SHALL record a receipt and one owner notice, both naming who changed it (the owner, the agents whose runs synced it, or the outside client by name). Any write that adds a line not a default one SHALL make the notice say its lines may hold back kept files, whatever count rides with it. A sync offers it last, carrying its sync agent's count of the kept files its lines hold back (and of those with changes), counted in the sandbox; the notice adds that count only for the owner's own sandbox, whose mirror is the whole tree. The object SHALL never match the list against the owner's files in the write's own unit, and the matcher SHALL put a line whose cost does not grow with the lines into an index (literal sets, tries, Aho–Corasick) and every other into one bit-parallel automaton; automaton lines past `AUTOMATON_STATES` together, and the list past `LIST_BYTES`, SHALL not be applied, in file order (the files they name go on syncing), said in the run's report with the lines, their count and the ceiling, and in the owner's notice, never in silence; a sync whose matching took long SHALL say so in the run's report. A file or folder whose name is not UTF-8 SHALL not sync, and SHALL be said in the run's report, its bytes escaped, with their count, and in the owner's notice, never skipped in silence; the notice in the object's own words, only the count and the escaped paths taken from the report. The run's heartbeat SHALL go on posting while the sync agent runs, so a sync-out longer than the lease's TTL keeps its lease. A default pattern SHALL only stop new paths: a file already synced under it keeps syncing. A kept file whose changes another line holds back SHALL be reported by each sync-out that leaves it (`ta.sync_skipped`), never removed. A file's identity (size and change time) SHALL be read when it is offered, and the mirror SHALL record the hash of the bytes actually sent: a large file's chunks are checked against its offer as they are sent, and one changed or deleted before it is sent is skipped and reported (`ta.sync_skipped`), never committed from a mix. A change time within the filesystem's coarsest tick of its read SHALL not be trusted, so the next sync hashes that file again and a same-size edit in that tick is sent. An op refused SHALL be refused alone, with the doors' whole message, the rest of the batch going on; one refused `storage_full` SHALL stay in the sandbox, unmirrored, and be offered again by the next run, the run reporting it once (`ta.storage_full`, `uploads_open` its own report): the only place an upgrade is offered, once a sync. The mirror SHALL be saved after each committed batch, and an upload given up SHALL be aborted at the doors.

#### Scenario: A large build in the workspace
- **WHEN** a run leaves hundreds of MB of changed files under `/workspace`
- **THEN** the sync agent's memory stays bounded, the transcript is committed before them, and an ignored tree is never sent

#### Scenario: The storage fills partway through a sync
- **WHEN** a large file's upload is refused `storage_full` after smaller files committed
- **THEN** the committed files are mirrored, the large file stays in the sandbox and is reported once with the refusal's own words, and the next run offers it again

### Requirement: A full disk never loses the run
The runner SHALL keep a reserve on the disk (`RESERVE_BYTES`) that it lets go when its own state write meets `ENOSPC`, and before it stages a run's sessions for sync-out whenever the disk has less free than the reserve; it SHALL stage only the sessions the owner's files do not hold as they are (by the sync mirror's hash), so a session an earlier sync-out did not commit is staged again by the next run whether or not that run touches it, a session read and hashed again only when its size or change time moved, and one refused for a secret's bytes reported once until it or the secrets change; staging happens before the sync agent's lock, ext4's root blocks beyond the reserve; a state write that fails even so SHALL leave the state in memory and the run going, so a full disk never leaves a run `unknown`.

#### Scenario: The disk fills mid-run
- **WHEN** a run's clone fills the sandbox's disk
- **THEN** the tool's write fails `ENOSPC`, the runner's state and the run's end are still written and posted, and the sync agent has its room for sync-out

### Requirement: The runner never syncs a secret's bytes
The sandbox runner (slices R and S) SHALL refuse to sync out any file, or any transcript line, that contains the bytes of a file under its engine's `secret_paths`, read from the records, so a secret an engine's own tool printed or copied never leaves the sandbox by its content (ADR-031 decision 2), whatever path it was written to.

#### Scenario: An engine prints its own credential
- **WHEN** an engine's tool prints a `secret_paths` file into its transcript, or copies it under `/workspace`
- **THEN** the runner refuses to sync that line or file, and nothing of the secret reaches the owner's files

### Requirement: The egress door admits a sandbox call by its egress token
The doors SHALL serve `egress.tinyassets.io/r/<run>/<secret>/c/<connection>/*`, a run's own MCP and event doors (`/r/<run>/<secret>/mcp`, `/r/<run>/events`) and the sandbox's sign-in report (`/signin`), and nothing else on that origin, and serve the egress door on no other. A call SHALL carry the sandbox's egress token (`x-ta-sandbox`) and the egress generation its network was made under (`x-ta-network-generation`), both set by E2B's transform outside the VM. The token SHALL name its owner itself and its run's deadline, and carry a MAC (`<owner id>.<expires>.<256-bit secret>.<HMAC-SHA-256 under ROUTING_MAC_KEY over its kind, owner, expiry and secret>`; `routing-mac.ts`, the one module the owner Worker mints with and the doors verify with, the key a Worker secret of both, declared by name in both configs), so the door reaches the owner's object with no directory read; a missing, malformed, forged, altered, other-kind or expired token SHALL be refused by its fixed code before any stub call, the placeholder key the client sent never read, and the object SHALL check the secret's hash, the lease and its expiry. The door SHALL read none of the body: it refuses a declared length past `EGRESS_BODY_MAX` (`egress_too_large`), and only once the object admitted the call SHALL the body go, as the stream it came as, to the gateway's `EgressGateway.forward`, with `reserve` (a function the gateway calls back over RPC once its one parse read the body's model and tier, which the door answers with the object's `egressReserve`, keeping a refusal to answer the object's own code and receipt; none for a read), its path after `/r/<run>/c/<connection>`, the provider's answer streaming back as it is written (an SSE stream unbuffered) but for the gateway's own headers. Only one doors source SHALL reach `EGRESS`.

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

### Requirement: The MCP door serves MCP Events for doorbells
The MCP door SHALL advertise `events` in its `initialize` and `server/discover` capabilities and serve `events/list` (the one event, `tinyassets.changed`, its arguments and its payload, to an outside client whose grant reads), `events/subscribe` and `events/unsubscribe`, webhook delivery only. A subscribe SHALL be checked before any byte (the event, its arguments, an `https` callback the record admits, a `whsec_` secret) and authorized for the caller's own subscribing agent in that command center; then the gateway SHALL verify the callback with a signed single-use challenge, reading at most a bounded answer and comparing it in constant time, and seal it. A failed verification SHALL answer the record's code (`-32015`, OpenAI's) with a `data.reason` category, never the endpoint's body. The id SHALL be derived from the caller, URL, event and arguments and never accepted; `ttlMs` SHALL be granted as `refreshBefore` (omitted, the record's default; `null`, none; one no date can write refused before anything is stored). An agent's subscriptions past its bound SHALL answer `-32025` with `data.max`. A run's MCP door SHALL serve none of it: it lists no event and may subscribe or end none.

#### Scenario: A ChatGPT dot subscribes
- **WHEN** a client whose agent is registered for MCP Events subscribes a callback that echoes the challenge
- **THEN** the answer carries the derived id, a `refreshBefore` a day out, `cursor: null` and `truncated: false`, and the object holds the sealed blob alone

#### Scenario: A callback that does not echo the challenge
- **WHEN** the callback answers another challenge, or `404`
- **THEN** the subscribe answers `-32015` with `challenge_failed` or `http_4xx`, and nothing is stored

### Requirement: The owner export is an owner-surface stream
`POST /app/api/export`, with at most a `cursor`, SHALL answer the owner's export as newline-delimited JSON (`application/x-ndjson`): its generation's objects in order, up to `EXPORT_PAGES` a response (a technical ceiling on one request's work), every response but the last ending with a `{"t":"cursor"}` line that carries the cursor and the export's id, so a client resumes from the last cursor it read; an object its generation has not yet written SHALL end the response early with that cursor, never wait or answer a partial object. The first object SHALL be read before the response begins, so a refusal is the door's status.

#### Scenario: The owner takes its data
- **WHEN** the owner's app posts `{}` with its session, and posts again from each cursor it reads
- **THEN** the lines begin with the header and end with `{"t":"end"}`

### Requirement: The owner's sign-in is the owner issuer's User Management code flow
The doors SHALL sign the owner in through AuthKit's User Management code flow with PKCE for the environment's client, as the deploy-written `UPSTREAM` record names it. The code SHALL be exchanged on the doors' own request to `api.workos.com`, never redirected, with the verifier and the secret, which stand for a nonce. The access token SHALL be RS256 under the one JWKS key its `kid` names, its `iss`, `client_id`, times, `sub` (the answer's `user.id`), `sid` and `auth_time` checked.

#### Scenario: A token without auth_time, from another issuer or for another client
- **WHEN** the token endpoint answers an access token missing `auth_time` or `sid`, naming another `iss` or another `client_id`, signed by another key, or whose `sub` is not the answer's `user.id`
- **THEN** the sign-in fails with a 502 naming the check and never a token or a secret, a wrong `iss` named as the token carries it, and no owner is bound

#### Scenario: A stolen code played into another flow
- **WHEN** someone redeems the owner's code in their own doors flow, with another PKCE verifier
- **THEN** WorkOS refuses the exchange. This is an assumption, not a check of the doors': WorkOS does not document that it checks `code_verifier` when the client secret is also sent

### Requirement: The deploy proves the sign-in's client and callback before anyone signs in
Before any Cloudflare change, the deploy SHALL list the callback at WorkOS (registering it when missing, outside the plan), then request the WorkOS authorize URL the doors build (not `tinyassets.io/authorize`), with a throwaway S256 challenge, and stop unless WorkOS redirects it to the owner issuer's sign-in.

#### Scenario: WorkOS refuses the doors' client or callback
- **WHEN** the deploy's authorize check is redirected to an error page (`client-id-invalid`, `redirect-uri-invalid`) or anywhere but the owner issuer's host
- **THEN** the deploy stops before any Cloudflare change, naming where it was sent

#### Scenario: A fresh environment's callback is not registered yet
- **WHEN** WorkOS does not list the callback
- **THEN** `dark` registers it and then runs the check, and the plan, which cannot list it, reports a `redirect-uri-invalid` answer as `dark`'s to register rather than stopping

### Requirement: Sign-up is the platform's explicit admit, and erasure its outside record
The sign-in callback SHALL admit an owner the directory has no `o:` record of (the object first, then the record), and refuse an owner whose record says erased or whose object is marked erasing. The erasure door SHALL only mark the object erasing, durable before it answers. The object's erasure job SHALL then put the owner on the erasure queue at its mark and at each attempt until the doors confirm, and the doors' consumer SHALL, each step safe to run again, write the owner's `o:` record erased, delete its `k:`, `h:` and `i:` entries, revoke its every OAuth grant and delete the identity at the issuer, then confirm; the object SHALL wipe only after that confirmation, so a half-done erasure always finishes.

#### Scenario: An erasure interrupted after its mark
- **WHEN** the request that marked an owner erasing is lost, or a consumer step fails
- **THEN** the object's job sends the owner again until the doors confirm, and the erasure finishes

#### Scenario: An erased owner's name
- **WHEN** an erased owner's key, capability URL, refresh token or sign-in is presented, or a stale `getByName` reaches its wiped object
- **THEN** each is refused, and the object refuses every door, never admitted again

### Requirement: Inbound events are proved and routed by the binder
`/hooks/inbound/<token>` SHALL exist only while the control record's `inbound` gate is on, take a body within the platform's one request ceiling, prove a shared app's request by the platform's signing secret its data record names (an HMAC over the timestamp and raw bytes within its skew) and route its single installation by the directory's `i:` entry, and route a capability URL by its token's hash (`h:`).

#### Scenario: A bad HMAC, and a replay past the skew
- **WHEN** a shared app's request carries a signature that does not verify, or a timestamp older than five minutes
- **THEN** it is refused `401` and no object is reached

### Requirement: No caller is rate-capped by the doors
The doors SHALL count or cap no caller's requests, authenticated or not, and SHALL never refuse a valid credential because of what others sent from its address. A metadata document a `client_id` names SHALL be fetched only within the OAuth provider's own bounds (public URLs, 5 KiB, 10 s, one fetch a request). An owner object the runtime reports overloaded SHALL answer `429` with `Retry-After`.

#### Scenario: A shared backend after many failures
- **WHEN** an MCP host's backend address sends many expired bearers or failed refreshes
- **THEN** a valid key and a live refresh from that address still succeed

### Requirement: The binder streams an upload's bytes to FILES
The owner surface's upload SHALL take one chunk a `PUT`, in any order and again after any failure, put it to `FILES` when the intent still needs it, through the files Worker's upload entrypoint alone (`FilesUpload`, `putChunks` and nothing that reads a chunk, a generation or a key), refuse bytes that are not that declared chunk or a body shorter or longer than it, and answer the chunk's SHA-256 as `Content-Digest`; a file SHALL be bounded by the owner's policy, never by one request, and a refusal mid-upload SHALL answer its fixed code.

#### Scenario: A tampered chunk
- **WHEN** an upload's body differs from its declared chunk
- **THEN** it is refused `400`, and nothing of that chunk is reported stored
