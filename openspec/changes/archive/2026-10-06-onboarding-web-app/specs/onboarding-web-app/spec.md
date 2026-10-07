# Onboarding Web App

## ADDED Requirements

### Requirement: Daemon-Served Same-Origin Onboarding App

The daemon SHALL serve a self-contained onboarding single-page app at `/mcp/app`,
same-origin to the canonical `/mcp` endpoint, requiring no CORS, no proxy, and no
server-side token custody. The route SHALL be registered on the same HTTP app as
the MCP transport and resolve ahead of it. The app SHALL be served under `/mcp/`
so the production front door — which routes only `/mcp/*` to the daemon — reaches
it with no infrastructure change, and SHALL ship in the daemon image so it becomes
available purely by deploying the daemon, with no local-machine dependency.

#### Scenario: The app is reachable through the production front door

- **WHEN** a browser requests `https://tinyassets.io/mcp/app` and serving is enabled
- **THEN** the daemon returns the self-contained onboarding page
- **AND** the page is same-origin with `/mcp`, so it calls `/mcp` with no CORS preflight

#### Scenario: The page loads before sign-in

- **WHEN** an unauthenticated browser requests `GET /mcp/app`
- **THEN** the request reaches the daemon and returns the page (it is not answered
  with an authentication challenge)
- **AND** the page's own calls to the `/mcp` write handles remain authenticated

#### Scenario: The surface needs no separate host to run

- **WHEN** the daemon is deployed to the cloud and no developer machine is running
- **THEN** the onboarding app is served entirely by the deployed daemon

### Requirement: Dark-Flagged Serving

The onboarding route SHALL serve the app only when an explicit environment flag is
enabled, and SHALL otherwise return `404`. Enabling the surface SHALL be a pure
environment change requiring no code change.

#### Scenario: Disabled returns not-found

- **WHEN** the onboarding flag is unset and `GET /mcp/app` is requested
- **THEN** the daemon returns `404` with no onboarding content

#### Scenario: Enabled serves the app

- **WHEN** the onboarding flag is enabled and `GET /mcp/app` is requested
- **THEN** the daemon returns `200` with the self-contained onboarding HTML

### Requirement: In-Browser WorkOS Sign-In Bound To The MCP Resource

The app SHALL authenticate the founder using WorkOS AuthKit OAuth 2.0
Authorization Code with PKCE performed entirely in the browser as a public client,
and SHALL include the MCP resource indicator (RFC 8707) on both the authorization
and token requests so the issued access token's audience matches the `/mcp`
resource. The access token SHALL be held only in the browser and sent as a Bearer
credential to same-origin `/mcp`; the platform SHALL NOT proxy, log, or persist it.

#### Scenario: Authorization request carries PKCE and the resource

- **WHEN** the founder starts sign-in
- **THEN** the browser is sent to the AuthKit authorization endpoint with a PKCE
  `code_challenge` (S256), a random `state`, and the MCP `resource` indicator

#### Scenario: Callback validates state and exchanges the code in-browser

- **WHEN** AuthKit redirects back to `/mcp/app` with a code and state
- **THEN** the app rejects a mismatched or missing state, and otherwise exchanges
  the code for an access token directly against the AuthKit token endpoint
- **AND** the code and state are removed from the visible URL

#### Scenario: The token is only ever a browser-held Bearer

- **WHEN** the app calls a `/mcp` handle after sign-in
- **THEN** it sends the access token as a same-origin `Authorization: Bearer`
  header, and the token is never sent to or stored by the daemon as the app's own

### Requirement: Server-Injected Public Config Only

The route SHALL inject into the page only public configuration — the OAuth client
id, the discovered AuthKit authorization and token endpoints, and the MCP resource
— derived from the same Protected Resource Metadata the connector advertises, so
the app's authorization server and resource cannot drift from what `/mcp` accepts.
Injected values SHALL be escaped so no value can break out of the script context,
and no secret SHALL ever be injected. When required public config is absent the
app SHALL render an honest not-configured notice rather than a broken redirect.

#### Scenario: Config is derived from the advertised metadata

- **WHEN** the page is rendered
- **THEN** its issuer and resource are taken from the connector's Protected
  Resource Metadata, not an independent source

#### Scenario: A secret never reaches the page

- **WHEN** secret environment values are present in the daemon process
- **THEN** none of them appear in the rendered page

#### Scenario: A config value cannot escape the script context

- **WHEN** an injected config value contains a script-closing sequence
- **THEN** the rendered page escapes it so the inline script is not broken

#### Scenario: Missing config degrades honestly

- **WHEN** no OAuth client id (or issuer) is configured
- **THEN** the sign-in action shows a not-configured notice instead of attempting
  a broken authorization redirect

### Requirement: Hardened Served Page

The onboarding response SHALL carry a per-request Content-Security-Policy nonce
that gates the inline script and style, with `default-src 'none'`, `connect-src`
limited to same-origin plus the AuthKit origin, and framing, base-uri, and
form-action locked down, and SHALL NOT permit `'unsafe-inline'` script. The app
SHALL render all universe- and user-derived text as text nodes, never as HTML, so
a hostile reply cannot inject script or steal the browser-held token.

#### Scenario: CSP nonce gates inline code

- **WHEN** the page is served
- **THEN** the response carries a CSP whose `script-src`/`style-src` allow only
  this request's nonce, the inline `<script>`/`<style>` carry that nonce, and no
  `'unsafe-inline'` is granted

#### Scenario: Untrusted content is rendered as text

- **WHEN** a `converse` reply or a status field contains HTML-like content
- **THEN** the app renders it as text, not as parsed HTML

### Requirement: Onboarding Funnel Over Canonical Handles Only

The app SHALL drive onboarding using only the canonical MCP handles: `converse`
for the chat (rendering the universe's first-person reply verbatim), `get_status`
for a liveness heartbeat (and, with `include_conversation`, the thread on reload),
`write_graph target=connection` for the founder's deposits and answers
(`connect_llm` for the subscription, `connect_http` for an outbound credential,
`connect_compute` for a compute node, `resolve_connection`, and `answer_request`
for a rail ask), and `read_graph target=pending_requests` for the request rail.
An answered rail ask SHALL also be relayed into the thread as the founder's own
line through `converse` (`Approved: "<title>"`, `Not now: "<title>"`, or
`Answered "<title>" — field: value`; a secret field's value is never relayed),
queued behind any turn in flight so the universe resumes without being told
again. When the universe has no engine, the app SHALL render
`converse`'s setup-required envelope honestly as the universe's own note and
surface a connect-subscription affordance, never a fabricated reply. The app SHALL
invent no backend tools and hold no universe logic, identity, or persona.

#### Scenario: A chat turn relays through converse verbatim

- **WHEN** the founder sends a message
- **THEN** the app calls `converse` and renders the returned `reply` verbatim as
  the universe's voice

#### Scenario: Setup-required is surfaced honestly

- **WHEN** `converse` returns a `held` / `setup_required` envelope
- **THEN** the app renders its note as the universe's own voice and offers to
  connect a subscription, without inventing a reply

#### Scenario: Deposit uses the canonical connect handle

- **WHEN** the founder submits a subscription credential
- **THEN** the app deposits it via `write_graph target=connection operation=connect_llm`
  and shows the connector's actual response
