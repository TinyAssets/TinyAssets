## MODIFIED Requirements

### Requirement: Access-method executors are selected by provenance with no cross-method fallback
Execution SHALL select an executor deterministically by the definition's protocol kind: an `agent:<package>` protocol selects the generic external-agent runner with that pinned package; `api_key_http` selects an HTTP protocol encoder over the SSRF-hardened outbound proxy (never a vendor SDK pointed at an arbitrary `base_url`). No executor SHALL be chosen by a vendor name. A failed external agent SHALL NOT fall back to an SDK or API using ambient credentials, nor to another source unless the caller declared it as a fallback.

#### Scenario: api_key_http never bypasses the outbound proxy
- **GIVEN** an `api_key_http` provider with a user-supplied `base_url`
- **WHEN** a turn executes on it
- **THEN** the request goes through the credential-blind outbound proxy with full SSRF enforcement (HTTPS-only, private/loopback/metadata blocked, DNS revalidated, redirects/env-proxies disabled), and the credential ref is bound to the registered endpoint so a changed `base_url` cannot redirect the key

#### Scenario: an external agent does not silently degrade to an API
- **GIVEN** an `agent:codex` source whose custody is signed out
- **WHEN** a call resolves to it
- **THEN** the call fails with `ExternalAgentUnavailable` naming the agent and `signed_out`, and is never retried as an API or SDK call on an ambient credential

## REMOVED Requirements

### Requirement: Bubblewrap readiness is a cached two-stage provider probe that selects ordinary Codex mode
**Reason**: The owner cell, and later the owner box, is the sandbox; the runner does not select a vendor sandbox mode, and the Codex executor is deleted.
**Migration**: `external-agent-packages` "The agent home is owner-private and never temporary"; packages declare their argv as data.

### Requirement: Recognized provider CLI sandbox failures are loud only after earlier quick-exit classification
**Reason**: The runner ends every failed job with a typed error and a bounded, redacted excerpt; there is no vendor sandbox to classify.
**Migration**: `external-agent-packages` "One runner serves every external agent".
