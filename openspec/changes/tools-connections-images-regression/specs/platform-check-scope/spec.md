## ADDED Requirements

### Requirement: Connection calls use connection authority
Ordinary HTTP connection calls SHALL require their active connection grant and SHALL NOT require an inference usage reservation or broker access to owner files. Model inference SHALL retain its accounting requirements, classified from trusted connection authority.

#### Scenario: Ordinary service POST
- **WHEN** an agent or workflow node posts through an active ordinary HTTP connection
- **THEN** the broker dispatches using the connection grant without inference accounting

#### Scenario: Model POST without usage
- **WHEN** an HTTP model connection is called without a valid inference reservation
- **THEN** the broker refuses before sending

#### Scenario: Revoked service grant
- **WHEN** a service connection grant is revoked
- **THEN** the broker refuses the service call before sending

#### Scenario: Production-copy acceptance
- **WHEN** the default production-copy acceptance runs on a restored and migrated backup
- **THEN** real converse turns execute read, write, edit, bash, an HTTP service call and image decoding, and real node/workflow calls pass through the broker to a local endpoint

- **AND** the acceptance never replaces broker, cell, launcher or relay seams; a structural guard enforces this
- **AND** a changed starter bundle is installed as a new immutable release, preserving owner-customized files
