# HTTP connections and outbound authority

## Purpose

How a universe owner builds an outbound channel to any HTTPS API with no
per-service platform code: what a connection declares, how its credential is
applied, and what the egress boundary admits.

**As-built and PARTIAL by construction.** This file grows as each change
touching the capability is proven. It carries the owner-configured review
contract (2026-10-04) and capability-URL requirements (change
`capability-url-connections`, landed 2026-09-30, PR #4115).
The `exact` / `full` access-mode requirements live in the still-open
`openspec/changes/full-channel-access/` delta and join here when that change
archives.

## Requirements

### Requirement: HTTP connection grants have no fixed request cap

New HTTP connection grants SHALL be created without an unprompted action cap.
Opening the ledger SHALL clear legacy caps named exactly `http_requests`, while
preserving other caps (including `one_pull_request`), other grant fields, and
malformed cap rows. Connection listings SHALL return `null` for the absent cap.
Owner rules, destination consent, and outbound authority checks still apply.

#### Scenario: a new HTTP connection is provisioned
- **WHEN** provisioning creates an HTTP grant
- **THEN** its stored cap is absent immediately, without a ledger reopen, and listings return `null`

#### Scenario: a legacy HTTP request cap is migrated
- **WHEN** the ledger opens with a grant carrying the `http_requests` cap
- **THEN** that cap is cleared and other caps and grant fields remain unchanged
- **AND** concurrent opens safely converge on the same uncapped state
- **AND** lock failure leaves the cap intact for a later retry

#### Scenario: an initialized ledger reopens during another writer's transaction
- **WHEN** no legacy HTTP request caps or other pending migrations remain
- **THEN** opening the ledger performs no data write and does not require a writer lock

### Requirement: Owner connection writes follow owner rules without mandatory model review

An authenticated external call through the owner's connection SHALL proceed
when the owner's declared rules, active connection grant, allowed operations
and destination consent allow it, subject to the existing deterministic egress
and isolation checks. The platform SHALL NOT require a model review by default,
including for money, security or access action classes. Owner hand-off and
ask-first rules SHALL continue to hold actions before review or sending.

#### Scenario: a subscription model cannot perform text-only review
- **WHEN** the owner's rule allows a GitHub-style POST on their granted connection, consent is active, and the owner has not enabled review for that action class
- **THEN** the effector sends through the scoped connection proxy without invoking a reviewer or returning `auto_review_unavailable`, including when the run uses only a subscription provider

#### Scenario: owner rules deny an otherwise granted write
- **WHEN** a matching owner rule asks first or hands the action off
- **THEN** the effector holds the action without spending a review call or sending the request

### Requirement: Only explicit owner review choices require a review

The owner SHALL be able to enable review per agent and consequential action class through
the authenticated owner rules surface. Enabled choices SHALL persist in
`review_on` in the owner-controlled rules store outside the agent workspace.
The Rules UI SHALL show these explicit choices and allow changing each class.
Legacy `review_off` rows SHALL remain readable; absence from that old table
SHALL NOT be treated as opt-in, because the old storage did not distinguish
explicit enabling from its platform default. Disabling a review SHALL still
explain the consequence and require the owner's confirmation.

Configured reviews SHALL use the run's own model with the existing text-only,
owner/run-bound admission, finite budget and attempt checks. A review SHALL
only tighten the owner's rule, never confer connection or cross-user authority.
An unavailable model, unsupported text-only provider, unreadable review setting
or unclear verdict SHALL hold with a clear cause. No platform model or provider
substitution SHALL be introduced to obtain a verdict.

#### Scenario: an owner-configured review cannot run
- **WHEN** the owner explicitly enables review and the selected subscription provider cannot enforce text-only execution
- **THEN** the action is held with `auto_review_unavailable` naming the text-only restriction, and no external request is sent

#### Scenario: review choices are local to the owner and agent
- **WHEN** an owner enables review for one agent's action class
- **THEN** that choice persists for that agent alone and does not enable review for another agent or owner

### Requirement: Cross-user and shared-host boundaries remain independent of review

Every external call SHALL still require a live grant bound to the executing
universe and named connection. The scoped proxy SHALL enforce the authenticated
principal, grant and connection owners, revocation, operation scope and endpoint
allowlist. Destination consent and soul denials SHALL still apply. Credential
custody, SSRF restrictions and shared-host isolation SHALL NOT depend on an
optional model review or be bypassable by an owner rule or review verdict.

#### Scenario: another universe's grant is named in an allowed write
- **WHEN** an owner rule allows the action but the packet names a grant bound to another universe
- **THEN** the effector refuses before opening the proxy, regardless of review settings

#### Scenario: consent or connection authority is missing
- **WHEN** the owner has not enabled review but destination consent is absent or revoked, or the grant or operation scope does not allow the request
- **THEN** the existing consent or grant check refuses the call before external egress

### Requirement: A capability URL's secret is a path segment in the vault
 An `http` connection MAY declare `auth_scheme: "url_secret"`, whose credential is a path segment rather than a header. Its endpoints SHALL each carry exactly one reserved placeholder — `{secret}` for a single segment or `{secret+}` for the final tail of one or more segments — and the vault SHALL hold the segment text alone.

The reserved placeholder SHALL NOT accept a caller-declared value pattern. The
platform SHALL declare it as the anchored literal token, so a stored
`/mcp/hooks/{secret}` endpoint matches the concrete path `/mcp/hooks/{secret}`
and nothing else.

The reserved placeholder SHALL be the ONLY placeholder in a `url_secret`
endpoint's template: a capability URL is a fixed path plus a secret. A
`url_secret` connection SHALL NOT be `full`, because a full connection admits
any path on a matching host, so the placeholder would never be enforced.

#### Scenario: a webhook connection is deposited
- **WHEN** an owner deposits `auth_scheme: "url_secret"` with one endpoint whose `path_template` is `/mcp/hooks/{secret}` and `methods: ["POST"]`
- **THEN** the connection is created, the vault holds only the secret segment, and the stored `allowed_endpoints` hold `{secret}`

#### Scenario: a caller tries to pattern the reserved placeholder
- **WHEN** an endpoint declares `param_patterns: {"secret": ".*"}`
- **THEN** the endpoint is refused

#### Scenario: another placeholder beside the secret
- **WHEN** a `url_secret` endpoint declares `/hooks/{room}/{secret}` or `/hooks/{secret}/{tail+}`
- **THEN** it is refused when authored, rather than accepting the ask and then rejecting the owner's correct link

#### Scenario: full access on a capability URL
- **WHEN** a `url_secret` deposit asks for `access: "full"`
- **THEN** it is refused

### Requirement: The scheme and the placeholder imply each other

A connection whose `auth_scheme` is `url_secret` SHALL carry a reserved
placeholder on every endpoint, and a connection carrying a reserved
placeholder on any endpoint SHALL have `auth_scheme` `url_secret`. Both
directions SHALL be enforced at the deposit door, at the storage boundary, and
again at dispatch against the row as re-read — so a row mutated after a proxy
opened is refused before any credential is resolved.

#### Scenario: a header scheme with a secret placeholder
- **WHEN** a `bearer` deposit declares `path_template: "/mcp/hooks/{secret}"`
- **THEN** it is refused, and the error names `url_secret`

#### Scenario: url_secret with no placeholder
- **WHEN** a `url_secret` deposit declares `path_template: "/mcp/hooks/x"`
- **THEN** it is refused

#### Scenario: the row is mutated after deposit
- **WHEN** a stored `url_secret` connection's `auth_scheme` is changed to `bearer` and a call is dispatched
- **THEN** the dispatch is refused before a credential is resolved, and no request is sent

### Requirement: The owner pastes the link and the platform extracts the secret

A `url_secret` deposit SHALL accept either the whole capability URL or the bare
secret segment. Given a URL, the platform SHALL require `https`, no userinfo,
no query and no fragment, and SHALL require the URL's host and path to match
exactly one declared endpoint whose template carries the reserved placeholder;
it SHALL then extract the captured segment(s) as the credential.

A refusal SHALL NOT echo any part of the pasted value. It SHALL name the
declared template instead.

#### Scenario: the whole link is pasted
- **WHEN** the owner answers a `url_secret` ask with `https://tinyassets.io/mcp/hooks/<token>`
- **THEN** the vault holds `<token>` and nothing holds the full URL

#### Scenario: a link for the wrong host
- **WHEN** the pasted URL's host is not the endpoint's host
- **THEN** the deposit is refused, the error names the declared template, and the error contains no part of the pasted URL

#### Scenario: a link on another port
- **WHEN** the pasted URL carries an explicit port
- **THEN** the deposit is refused rather than retargeted at the endpoint's own origin

#### Scenario: a link the URL parser cannot read
- **WHEN** the pasted URL's authority changes under NFKC normalization, or its port is not numeric
- **THEN** the deposit is refused with fixed text, and neither the refusal nor any exception it carries contains the pasted value

#### Scenario: a link for an undeclared path
- **WHEN** the pasted URL's path does not match the declared template
- **THEN** the deposit is refused and nothing is written

#### Scenario: an ambiguous link
- **WHEN** the pasted URL matches two declared endpoints
- **THEN** the deposit is refused rather than guessing which holds the secret

### Requirement: The secret enters the URL after the egress boundary

The outbound driver SHALL evaluate the canonical-HTTPS parse and the
per-connection endpoint allowlist against the URL **as the caller supplied it**
— carrying the literal placeholder token — and SHALL substitute the vault
segment into the path only after the allowlist has admitted the request. DNS
resolution and the globally-routable-address check SHALL run after
substitution, unchanged, on the same host.

The substitution position SHALL be derived from the **template of the endpoint
that admitted the request**, never by searching the concrete path. A request
that no declared endpoint admitted, or that was admitted on a host match alone
(`full`), SHALL be refused rather than substituted.

A reserved token appearing anywhere OTHER than the slot the matched endpoint's
template declares SHALL refuse the request. That check SHALL cover the query
string as well as the path, and SHALL consider the percent-decoded form as well
as the raw one, and SHALL match a token embedded in a larger segment as well as
one occupying a whole segment. On any other `auth_scheme`, no occurrence is
legitimate and any SHALL refuse the request.

The stored segment SHALL be re-validated against the segment grammar
immediately before substitution, and a segment that fails SHALL refuse the
request.

#### Scenario: the substituted URL reaches the wire
- **WHEN** a node emits a packet whose path is `/mcp/hooks/{secret}` on a `url_secret` connection
- **THEN** the request on the wire is `https://<host>/mcp/hooks/<segment>`

#### Scenario: a node supplies its own value in the secret's position
- **WHEN** a node emits a packet whose path is `/mcp/hooks/<anything-but-the-token>`
- **THEN** the allowlist refuses the request and nothing is sent

#### Scenario: a reserved token in a position the template did not reserve
- **WHEN** an endpoint declares `/hooks/{secret}/{tail+}` with a permissive `tail` pattern and a node requests `/hooks/{secret}/echo/{secret+}`
- **THEN** the request is refused, and the credential is never placed in the tail

#### Scenario: a reserved token in the query, encoded, or embedded
- **WHEN** a request carries `?q={secret}`, a `%7Bsecret%7D` path segment, or a `prefix{secret}` segment outside the declared slot
- **THEN** the request is refused

#### Scenario: a corrupted stored segment
- **WHEN** the stored segment contains a `/`, a dot-segment, a control byte, or is shorter than 8 characters
- **THEN** the request is refused before a socket is opened

### Requirement: No output channel carries a capability URL's secret

The secret SHALL exist only inside the broker child. The node packet, the graph
as read back, the run state, the effect evidence, the effector's returned `url`
on success and on failure, the structured failure rows, the grant policy, the
projected connection view, the grant sentence and a remixed connector artifact
SHALL all carry the placeholder form. A destination response that echoes the
secret SHALL refuse the call rather than return it.

A response echoing the secret SHALL be recognised whether it echoes the whole
credential or any one of its path segments that is long enough to be a
credential rather than an address. A segment shorter than that SHALL NOT be
scanned on its own: the leading segments of a real capability URL are public
ids, and failing a clean response that happens to quote one would trade a leak
for an outage.

#### Scenario: a failing call
- **WHEN** a `url_secret` call returns HTTP 500 with the request URL in its body
- **THEN** the run's `external_write_errors` row and the effect evidence contain no part of the secret

#### Scenario: a response echoing one segment of a multi-segment secret
- **WHEN** a `{secret+}` connection's destination returns a body containing only the token segment of the credential
- **THEN** the call fails closed and that segment is not returned, persisted or quoted

#### Scenario: a response quoting a public id from the same capability URL
- **WHEN** a `{secret+}` connection's destination returns a clean 200 whose body contains a short leading segment of the credential (a workspace or channel id)
- **THEN** the response is returned as ordinary evidence

#### Scenario: the graph is read back
- **WHEN** the owner reads the branch that posts to the webhook
- **THEN** the node's packet holds `{secret}`

### Requirement: A literal secret in a path template is refused when authored
 Every authoring door for an endpoint allowlist — the pending-request ask, the deposit, and the extension — SHALL refuse a fixed (non-placeholder) path segment that reads as a credential or an opaque identifier, and SHALL name both repairs: the `url_secret` scheme with `{secret}` for a credential, and a `{param}` with a `param_patterns` regex for a public identifier.

Reading a **stored** template SHALL NOT apply this check, so a connection
deposited before it existed stays readable and removable.

#### Scenario: the live failure
- **WHEN** an ask declares `path_template: "/mcp/hooks/MRL8k3nZ-Ht9xWq2vB7pC4dF6gJ0sYaT1u"`
- **THEN** the ask is refused and the error names `url_secret` and `{secret}`

#### Scenario: an ordinary API path
- **WHEN** an ask declares `path_template: "/v1/chat/completions"` or `/repos/o/r/contents/{path+}`
- **THEN** it is accepted

#### Scenario: an existing connection with such a template
- **WHEN** a stored connection whose template holds such a segment is read, listed or removed
- **THEN** it succeeds
