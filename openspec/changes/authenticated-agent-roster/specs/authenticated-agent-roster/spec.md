## ADDED Requirements

### Requirement: Authenticated external roster
The owner agents projection SHALL show external MCP/API connections only after successful authentication and SHALL preserve the separate installed-agent roster.

#### Scenario: Unused and refused credentials
- **WHEN** a key is prepared but unused, invalid, or denied by its grant
- **THEN** it is absent from the external roster.

#### Scenario: Successful connection
- **WHEN** an external MCP connection or API key authenticates successfully
- **THEN** the owner sees a connection without a separate add-agent step, with actual name provenance or an honest unknown name.

#### Scenario: Owner and center isolation
- **WHEN** an owner reads another owner's or an unauthorized center's roster
- **THEN** no external connection metadata is disclosed.

### Requirement: Evidence is not liveness
The roster SHALL distinguish authentication history from current liveness or message delivery and SHALL never infer a brand, readiness, or authority from a name.

#### Scenario: Idle or revoked connection
- **WHEN** a connection is idle
- **THEN** its last authentication is displayed without an online claim; a locally revoked grant is excluded from the available roster and existing messages remain unchanged.

#### Scenario: Empty roster
- **WHEN** there are no authenticated external connections
- **THEN** the app says “Tell your agents to connect to your TinyAssets MCP or API.” and lists no seeded external agents.

#### Scenario: Navigation during refresh
- **WHEN** the owner signs out or changes account or center while a roster read is pending
- **THEN** the old response cannot repopulate the visible roster.

### Requirement: Credentials remain secret
Observations SHALL contain no bearer, secret, request body or new authority and SHALL be erased with their owner.

#### Scenario: Authentication observation
- **WHEN** a connection is recorded
- **THEN** only bounded identity/provenance and authentication-time metadata is persisted; existing grant and revocation enforcement remains unchanged.
