## ADDED Requirements

### Requirement: Owner policy participates in the single egress decision
Every box upstream connection SHALL pass the existing broker `destination` check for admission, public-address safety and the owner's `open`, `allowlist` or `ask` policy before opening; credentials SHALL remain broker-held and service-scoped.

#### Scenario: Every transport uses the owner policy
- **WHEN** model HTTP, connector HTTP, public HTTP/CONNECT or git opens a destination, including a redirect destination
- **THEN** the same check permits public targets in `open`, permits only exact normalized host/IP and port entries in `allowlist`, and requires a current one-use approval in `ask`
- **AND** direct native egress cannot bypass it, and a credential never grants egress permission

#### Scenario: A permitted hostname resolves unsafely
- **WHEN** an allowed or approved destination resolves to private, loopback, link-local or metadata addresses
- **THEN** the connection refuses without an approvable ask; successful opens use validated pinned public addresses

### Requirement: Only the owner writes standing egress policy
The existing broker policy authority SHALL accept writes only through its protected owner-session path with the expected policy generation, receipt every decision and provide only a read projection to agents.

#### Scenario: Initialization and unauthorized mutation
- **WHEN** a new policy is provisioned
- **THEN** its explicit initial mode is `open`; later missing/corrupt policy refuses egress
- **AND** agent, box-channel, forged owner and stale-generation writes refuse without changing policy

#### Scenario: Restriction races an existing connection
- **WHEN** the owner changes egress policy while connections or approvals exist
- **THEN** opens serialize with the change, prior approvals become invalid, and existing owner connections close before the change is acknowledged effective

### Requirement: Ask uses existing owner consent
The box SHALL raise a blocked `ask` connection through the existing request/approval path; only protected owner confirmation SHALL authorize one retry, without changing standing policy.

#### Scenario: Approval and retry
- **WHEN** a blocked client, including a raw proxy client through the in-box relay, requests internet access
- **THEN** it receives refusal while one deduplicated Needs you request shows destination, port and connection-wide scope; no upstream is held open
- **AND** owner confirmation authorizes one retry for that owner, challenge, destination/port, policy and volume generation for five minutes
- **AND** the proxy atomically consumes that authorization and rechecks admission and public addresses

#### Scenario: Approval cannot escape its scope
- **WHEN** an authorization is reused, expired, denied, forged, from an earlier generation, or applied to another owner or destination
- **THEN** no upstream opens; Clear/Deny/mute use the existing request lifecycle and never silently grant access
