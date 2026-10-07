## MODIFIED Requirements

### Requirement: Keep transport credentials outside the adapter contract
The adapter SHALL invoke a server-owned transport callback with only the authorized destination
and response body; it SHALL accept no caller-supplied credential, URL, token, or provider
override. The server-owned Slack transport that satisfies this callback SHALL be realized as an
instance of the general `authenticated_external_call` primitive: it SHALL resolve a per-universe
named `slack` connection under authority and dispatch the call through the credential-blind
broker child, rather than owning a bespoke token lookup and HTTP POST. This SHALL NOT change the
adapter's credential-blind contract — the credential remains outside the adapter, resolved only
inside the broker child as a typed bundle whose bot token stays distinct from the app-level
token — and it SHALL preserve the existing property that the reply body must never round-trip
back through the boundary.

#### Scenario: Exact Slack destination reaches the server-owned transport
- **WHEN** a valid authorization targets Slack and the body digest matches
- **THEN** the callback receives that exact destination and body once
- **AND** the returned receipt contains no body or credential material

#### Scenario: The Slack transport resolves a named connection, not a bespoke token lookup
- **WHEN** the server-owned Slack transport delivers an authorized reply
- **THEN** it resolves a per-universe named `slack` connection under authority and dispatches an `authenticated_external_call` through the credential-blind broker child, and the Slack bot token is present only inside that broker child
