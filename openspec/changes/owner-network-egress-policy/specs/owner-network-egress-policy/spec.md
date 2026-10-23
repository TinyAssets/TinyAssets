## ADDED Requirements

### Requirement: Owner policy participates in the single egress decision
Every box upstream connection SHALL pass the existing broker `destination` check for admission, public-address safety and the owner's `open`, `allowlist` or `ask` policy before opening; credentials SHALL remain broker-held and service-scoped.

#### Scenario: Every transport uses the owner policy
- **WHEN** model HTTP, connector HTTP, public HTTP/CONNECT or git opens a destination, including a redirect destination
- **THEN** the same check permits public targets in `open`, permits exact normalized host/IP and port standing entries or active credential destinations committed or widened through the protected N10 owner session in both `allowlist` and `ask`, and refuses other targets (`ask` offers owner approval)
- **AND** direct native egress cannot bypass it, and credential bindings never bypass admission or public-address safety; each redirect is checked independently without provider-specific code

#### Scenario: A permitted hostname resolves unsafely
- **WHEN** an allowed or approved destination resolves to private, loopback, link-local or metadata addresses
- **THEN** the connection refuses without an approvable ask; successful opens use validated pinned public addresses

### Requirement: Only the owner writes standing egress policy
The existing broker policy authority SHALL accept writes only through its protected owner-session path with the expected policy generation, receipt every decision and provide only a read projection to agents. The owner SHALL be the sole writer of egress permission: credential bindings SHALL satisfy restrictive modes only for scope committed or widened through the protected N10 owner session. Binding creation and every `extend`/`full` update SHALL record `committed_via` from the authenticated channel; box-committed bindings, box-widened scopes and missing owner provenance SHALL never grant restrictive-mode permission.

#### Scenario: Initialization and unauthorized mutation
- **WHEN** a new policy is provisioned
- **THEN** its explicit initial mode is `open`; later missing/corrupt policy refuses egress
- **AND** agent, box-channel, forged owner and stale-generation writes refuse without changing policy

#### Scenario: Restriction races an existing connection
- **WHEN** the owner changes egress policy while connections or approvals exist
- **THEN** opens serialize with the change, stale proposals conflict, and existing owner connections close before the change is acknowledged effective

### Requirement: Ask uses existing owner consent
The box SHALL raise a blocked `ask` connection through the existing request/approval path; only protected owner confirmation SHALL write a standing destination/port entry through the same protected policy writer.

#### Scenario: Approval and retry
- **WHEN** a blocked client, including a raw proxy client through the in-box relay, requests internet access
- **THEN** it receives refusal while one deduplicated Needs you request shows destination, port and permission for future connections until removal; no upstream is held open
- **AND** owner confirmation commits the standing entry via `PolicyStore.write` with the expected policy generation; no one-use grant store exists
- **AND** each retry rechecks current policy, admission and public addresses

#### Scenario: Approval cannot escape its scope
- **WHEN** a policy proposal is denied, forged, stale, or submitted without that owner's protected session
- **THEN** no permission is written; Clear/Deny/mute use the existing request lifecycle and never silently grant access

#### Scenario: Connected services and revocation
- **WHEN** an owner uses a bound model or connector destination in either restrictive mode
- **THEN** the generic active credential binding satisfies the restriction only for scope committed through the protected N10 owner session while secrets remain in broker custody
- **AND** revocation removes that permission unless a standing entry independently allows it; an unbound redirect needs its own permission

#### Scenario: A binding the box commits does not satisfy allowlist or ask
- **WHEN** the box commits a credential binding or widens scope through `extend` or `full`, including on an originally owner-committed binding, and no independent owner permission covers the destination
- **THEN** neither `allowlist` nor `ask` treats that binding or box-widened scope as permission; the connection refuses, with the existing owner approval path available in `ask`
- **AND** creation and each update record the authenticated committer the same way; forged or missing owner provenance cannot grant permission, and only scope committed through the protected N10 owner session satisfies the restriction
