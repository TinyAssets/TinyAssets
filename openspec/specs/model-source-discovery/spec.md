# Model source records

## Purpose
Connect a model source from one address using the same credential-free record for editable declarations and detected providers.

## Requirements

### Requirement: Bare-domain API discovery
For a bare domain the detector SHALL also try its `api.` subdomain, and SHALL
probe `/v1`, `/openai/v1` and `/api/v1` on candidate origins through the same
bounded hardened transport. The connect screen SHALL use its address field
without a separate "Connect any OpenAI-compatible endpoint" prompt.

#### Scenario: Provider website address
- **WHEN** an owner enters a website domain whose model API is on its `api.` subdomain
- **THEN** discovery can find its model catalogue on the common API paths

### Requirement: One source record
Every source SHALL be a SourceRecord with a JSON schema: identity and origin, exact address aliases, data-only suggested/free flags, one sign-in mechanism, one invocation mechanism, exact egress hosts, and credential state location. HTTP credentials SHALL remain in the broker; CLI login state SHALL remain in the owner volume. Consumers SHALL select behavior by mechanism, never provider identity.

#### Scenario: Declared address
- **WHEN** the owner types an exact address alias from an installed or owner-authored record
- **THEN** the record supplies the connection mechanism without probing a different provider

#### Scenario: Owner declaration
- **WHEN** an owner or their agent adds a valid record in `.ta-cli/sources/<id>.json`
- **THEN** that owner's address resolver uses the same validation and connection mechanisms
- **AND** no other owner acquires that declaration or its credentials

### Requirement: Address-based model connection
The connect dialog SHALL contain one address field and one Connect button, initially filled from the sole suggested free source. It SHALL show no source list, categories, or alternate-source sections. Sign-in SHALL open directly in the same web tab or the native external browser surface. Native HTTP authorization SHALL use an owner-bound, browser-bound return and one-shot broker exchange. CLI login SHALL resume the owner-bound flow after returning to the page.

#### Scenario: Unfamiliar OAuth model source
- **WHEN** an owner supplies an unfamiliar provider with published OAuth and a compatible model API
- **THEN** discovery produces a detected record and PKCE authorization creates the ordinary HTTP source, retaining existing model-use authority checks

#### Scenario: Missing sign-in
- **WHEN** a detected model API has no supported sign-in
- **THEN** the same dialog offers one key field and a provider help link

#### Scenario: Failed address
- **WHEN** connection discovery cannot find a usable source
- **THEN** the page keeps the address and displays one plain sentence for retry

### Requirement: Frozen exact destinations
The flow SHALL use HTTPS and exact model hosts and paths, refuse redirects, cache DCR by issuer and callback, and freeze authorization metadata before exchange. A protected catalogue SHALL be authenticated and validated before publishing model use; a blanket authentication response alone SHALL NOT establish a model catalogue.

#### Scenario: Host changes during authorization
- **WHEN** metadata changes the token host after authorization begins
- **THEN** exchange still uses the frozen endpoint

#### Scenario: Insecure address
- **WHEN** a submitted address uses HTTP
- **THEN** discovery refuses it before network access

### Requirement: Expired approval recovery
An expired or wrong-account approval handoff (409 `handoff_expired_or_wrong_account`) SHALL remove only the approval query parameter and continue normal app startup without an error notice. Other errors SHALL retain their existing handling.

#### Scenario: Stale link
- **WHEN** an owner opens a stale approval URL
- **THEN** the app retains unrelated query parameters and fragment and loads normally

### Requirement: Named source model groups
The model menu SHALL show Automatic first and selected unless an explicit preference exists. Models SHALL start collapsed beneath their source's preset display name or API host, never an internal identifier, including unavailable models with their reasons.

#### Scenario: Expand one source
- **WHEN** the owner opens a source group
- **THEN** only that source's models expand and the current selection remains indicated

### Requirement: Bounded and conservative discovery
Discovery SHALL have a 35-second overall budget, shorten each transport timeout to the remaining budget, and run outside the broker fence reader lock on a dedicated two-worker pool with no pending queue. Admission and fencing SHALL be checked before and after probing. Authorization metadata and endpoints SHALL share the model API's registrable site, including private public suffix boundaries. Discovered models SHALL not be assumed flat-rate or free: only explicit zero-price catalogue entries may be offered as free, pending owner acceptance.

#### Scenario: Slow or untrusted address
- **WHEN** discovery is slow, saturated, or advertises another site's authorization server
- **THEN** it fails within its budget or refuses that sign-in without blocking other broker work
