## ADDED Requirements

### Requirement: Protected key lifecycle
The system SHALL issue named owner-bound prefixed bearer secrets once, store only hashes, show last-used time and enforce committed revocations on the next request. Only a protected same-origin interactive owner session SHALL create, rename, scope or revoke keys.

#### Scenario: Issue and revoke
- **WHEN** an owner creates a key, lists keys, revokes it and calls REST again
- **THEN** only creation returns plaintext, stored/listed records contain no plaintext, and the next call returns 401

#### Scenario: Bearer cannot manage keys
- **WHEN** an agent presents a key or app bearer without the protected owner cookie
- **THEN** creation and changes are refused

### Requirement: Scoped authority persists
The system SHALL enforce independently selected read/message/control/costly levels and owned center/agent scopes at ingress and existing downstream outside-authority boundaries. Shared reads and runs MUST require all-agent scope; runs MUST also require control and costly. Keys MUST NOT approve sensitive actions or widen grants.

#### Scenario: Endpoint and level refusal
- **WHEN** a caller names a foreign center, ungranted agent, missing level or approval operation
- **THEN** no data/effect is admitted and the request is refused

#### Scenario: Queued work revocation
- **WHEN** saved key-origin work resumes after key revoke or scope revision
- **THEN** existing outside-authority checks refuse its stale authority

### Requirement: REST and MCP share implementation
The system SHALL serve the documented v1 reads/messages/runs at the canonical origin through existing handle functions and structured response projection, retaining connector untrusted fences and marking the REST data envelope untrusted. MCP SHALL remain unchanged.

#### Scenario: Same call parity
- **WHEN** REST and MCP make equivalent admitted calls
- **THEN** REST data equals the MCP structured result and preserves its nested fences

### Requirement: Durable per-key rate limit
The system SHALL atomically enforce 60 calls per minute per key across workers and return 429 with Retry-After when exceeded.

#### Scenario: Independent counters
- **WHEN** one key reaches its limit
- **THEN** it is refused until the next window while another key remains usable

### Requirement: Discoverable API and owner UI
The system SHALL publish OpenAPI at `/api/v1/openapi.json`, allowlist canonical REST paths in the Worker and expose create/name/scope/revoke and last-used in Connected apps / API keys.

#### Scenario: Programmatic connection
- **WHEN** an owner copies a newly created key to a programmatic agent
- **THEN** the agent can discover schemas and call its allowed routes with Authorization Bearer without MCP/OAuth
