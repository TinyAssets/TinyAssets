## MODIFIED Requirements

### Requirement: Resolve platform registrations before public-client discovery

For each connect request the platform SHALL first try its daemon-owned JSON
provider directory, then existing RFC 9728, RFC 8414/OIDC discovery and public
dynamic registration, then key paste. A directory match SHALL cover every
declared host by case-insensitive exact comparison or an explicitly declared
leading `*.` subdomain pattern at a dot boundary. An undeclared apex, suffix
lookalike or unrelated additional host SHALL NOT match. Entries lacking their client ID or
secret SHALL be inactive. Invalid optional configuration SHALL be logged with a
fixed code and its entries unavailable, without blocking engine or broker launch.
Unavailable directory/RPC lookups SHALL fall through to discovery and key paste;
unexpected errors and confidential exchange/refresh failures SHALL still fail.

The request MAY supply `oauth.scopes`, `oauth.use` (a named default scope set),
and a public `oauth.client_id`. It SHALL NOT supply endpoints, provider IDs,
secret names, client secrets or offer provenance. An active directory match
SHALL take precedence over an agent-supplied client ID.
Explicit scopes SHALL be a subset of the union of the entry's declared scope
sets; an out-of-policy request SHALL NOT use the platform registration.

#### Scenario: Registered service without discovery
- **WHEN** the declared hosts match an active directory entry
- **THEN** its configured endpoints and client ID form the sign-in offer without any discovery or registration request

#### Scenario: Inactive registration
- **WHEN** either configured credential is absent
- **THEN** public-client discovery runs as before, and key paste remains available if discovery cannot produce an offer

#### Scenario: Default scopes
- **WHEN** explicit scopes are absent
- **THEN** the named `use`, or the entry's `host_uses` mapping, selects default scope sets; ambiguous shared hosts without scopes or a selected use fall through

## ADDED Requirements

### Requirement: Directory API host coverage
The platform SHALL resolve an API connection through an active trusted directory row when every requested host matches its declared exact hosts or leading `*.` subdomain patterns and the requested scopes belong to its declared API scope sets. The agent SHALL name the API host and necessary OAuth scopes or named use.

#### Scenario: Google API host uses Google issuer
- **WHEN** a connect ask targets `www.googleapis.com` or a declared Google API subdomain with the Calendar read-only scope
- **THEN** the Google row supplies sign-in endpoints and preserves the requested scopes without destination-host discovery

#### Scenario: Uncovered host uses discovery
- **WHEN** no directory row covers the requested host
- **THEN** standard host-rooted discovery remains available

#### Scenario: A row cannot claim undeclared hosts
- **WHEN** a connection includes a host outside a row's declarations, including suffix lookalikes or the undeclared apex of a wildcard
- **THEN** that row SHALL NOT supply the connection's OAuth offer
