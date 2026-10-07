## ADDED Requirements

### Requirement: Redirected downloads require explicit endpoint consent
HTTP connections SHALL default to no-follow and SHALL follow redirects only
for a bodyless GET matching an owner-approved GET-only endpoint whose validated
redirect_mode is public_https_get. Omitted and none SHALL be equivalent.

#### Scenario: Existing connection or full access without redirect opt-in
- **WHEN** an existing authorized endpoint returns a redirect without explicit redirect permission
- **THEN** no follow-up socket is opened, including on full-access connections

#### Scenario: Owner approves the displayed download permission
- **WHEN** a new redirect extension is approved through the existing owner surface
- **THEN** its preview discloses public HTTPS follow-up downloads and cross-origin credential isolation
- **AND** the write checks the displayed connection incarnation, endpoints, scopes and access mode
- **AND** it preserves existing full/exact access and refuses added authenticated hosts outside this slice

#### Scenario: Old or changed approval cannot broaden authority
- **WHEN** an approval lacks the redirect snapshot or the connection changed since the preview
- **THEN** redirect enablement refuses without changing policy
- **AND** old writers that discard the property leave no-follow and require fresh approval to re-enable it

#### Scenario: Legacy connection has no deposit identity marker
- **WHEN** ledger initialization finds an existing connection with an empty incarnation
- **THEN** it persists a fresh opaque per-row identity without changing policy, credentials, ownership, revocation or grants
- **AND** subsequent opens preserve that identity and perform no repair write when no empty identities remain
- **AND** the owner can request fresh redirect consent without redepositing a key; missing or stale redirect snapshots still refuse

### Requirement: Redirect chains remain bounded and credential isolated
The existing broker SHALL validate every redirect before its socket using the
existing canonical URL, public-address, DNS pinning, TLS and peer checks. It
SHALL enforce one deadline, aggregate body limit and at most five redirects.

#### Scenario: A signed or relative HTTPS download succeeds
- **WHEN** an opted-in GET returns a valid bounded chain of301/302/303/307/308 responses
- **THEN** the broker follows bodyless GETs and returns the final bounded text body through the existing effect
- **AND** it neither replays a mutating request nor forwards caller headers, cookies or a Referer

#### Scenario: Authentication cannot cross origins or bypass endpoint scope
- **WHEN** the next hop is not independently authorized on the original origin or any earlier hop crossed origin
- **THEN** the follow-up is anonymous, including a later return to the original origin
- **AND** permitted same-origin authentication is regenerated for the exact URL

#### Scenario: Invalid target or exhausted chain
- **WHEN** a target is unsafe, has missing or duplicate Location, loops, or exceeds time/body/hop limits
- **THEN** the broker refuses with a fixed safe reason and opens no prohibited next socket

### Requirement: Every further redirect observes current connection authority
The broker SHALL recheck the active grant, connection incarnation and approved
policy before each further socket, including after DNS. This SHALL NOT be
represented as atomic revocation of bytes already sent.

#### Scenario: Revocation or policy change between hops
- **WHEN** the child observes revocation, replacement or changed policy at its next check
- **THEN** it stops before opening the next socket without borrowing another credential

### Requirement: Download capabilities stay inside the broker
The broker SHALL reject targets containing tracked credential material before
dispatch, remove Location/Content-Location and cookie response headers, and never
add the final URL to caller results. It SHALL preserve bounded raw Location
multiplicity privately and reject intermediate/final body, reason or remaining
headers containing accumulated tracked authentication and redirect material.

The implemented echo scanner tracks complete URLs, paths, queries and query
pairs at any length; individual query values and path segments are tracked from
16 characters. It checks raw, percent-decoded and form-decoded variants. Actual
credential/authenticator values are tracked without that length cutoff. This is
not a guarantee against arbitrary short bare capabilities, multiply encoded or
otherwise transformed echoes. Evidence and errors SHALL NOT include rejected
response bytes. Dial-time authority refusal may surface as a generic broker error.

#### Scenario: Destination echoes tracked credential or signed capability
- **WHEN** a redirect response or final body/reason exposes tracked sensitive material
- **THEN** the broker returns only a safe refusal and no sensitive response data

#### Scenario: Normal downstream processing
- **WHEN** the approved download completes without sensitive echoes
- **THEN** existing code-node composition can process the full bounded decoded body
- **AND** the result makes no lossless binary-download claim or new log-specific tool requirement
