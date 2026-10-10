# Platform Check Scope

## Purpose

Keep request links usable and provider checks isolated to their owner while preserving inference accounting.

## Requirements

### Requirement: Usable request-card links
The request validator SHALL accept safe HTTPS links through 8192 characters without shortening them and SHALL retain scheme and safety validation.

#### Scenario: Tap-to-post link
- **WHEN** an owner request includes a safe 430-character link
- **THEN** validation preserves the complete link

### Requirement: Cooldowns are owner scoped
Provider cooldowns and reasons SHALL be isolated by owner and provider, including deferred cooldowns after a turn or run.

#### Scenario: Shared executor name
- **WHEN** owner A cools claude-code
- **THEN** owner B can still attempt claude-code
- **AND** A remains in cooldown

### Requirement: Actionable inference accounting refusal
Missing HTTP inference accounting SHALL fail closed with a fixed actionable diagnostic across broker transports, without exposing credentials or suggesting that owner consent bypasses accounting.

#### Scenario: Unaccounted direct inference POST
- **WHEN** a model connection receives an inference POST without its parent usage reference
- **THEN** no request is dispatched and the caller is directed to the accounted model invocation path


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
