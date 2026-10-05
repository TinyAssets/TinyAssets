## ADDED Requirements

### Requirement: Resident processes survive client absence under fenced supervision
The system SHALL run owner/center-bound resident processes on vendor-neutral cloud execution with durable desired state, current-generation supervisor leases, restart policy and visible health. Stop/revocation SHALL prevent restart by stale supervisors.

#### Scenario: Deployment interrupts a gateway
- **WHEN** a resident gateway loses its executor during deployment while all owner devices are off
- **THEN** a new fenced generation reconciles desired state and restores service without concurrent old-generation authority

#### Scenario: Owner stops a crashing process
- **WHEN** the process is waiting for restart backoff and the owner issues Stop
- **THEN** desired state becomes stopped and no stale retry relaunches it

### Requirement: Heartbeats reuse scheduler and owner model authority
Resident heartbeats SHALL use existing durable scheduler occurrences and owner-selected scripts or agents. Scripted heartbeats SHALL permit zero-LLM execution; agent heartbeats SHALL use only owner-connected models and emit only warranted notifications.

#### Scenario: Quiet thirty-minute heartbeat
- **WHEN** a scheduled script observes no relevant change
- **THEN** the occurrence completes without a model call or unnecessary message, with a durable receipt

#### Scenario: Budget or model unavailable
- **WHEN** a resident agent lacks its owner model binding or reaches its configured budget
- **THEN** the run visibly pauses and never falls back to host credentials or another owner
