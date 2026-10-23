# credential-vault delta

## ADDED Requirements

### Requirement: Broker-only owner envelope hierarchy
Every credential held by the broker SHALL be sealed under one KMS-rooted hierarchy with versioned envelope keys per owner. HTTP/OAuth, native subscription state and browser sessions SHALL share this hierarchy. Only the broker SHALL unwrap keys or open credentials. No key or plaintext credential SHALL enter an owner box. Shared code SHALL hold no owner-content path or owner business logic.

#### Scenario: Cross-owner or cross-slot substitution
- **WHEN** ciphertext is moved to a different owner or custody slot
- **THEN** authentication fails without revealing plaintext

#### Scenario: Missing root or damaged ciphertext
- **WHEN** the root is unavailable or ciphertext authentication fails
- **THEN** custody fails loudly without plaintext fallback or secret-bearing errors

#### Scenario: Native subscription execution
- **WHEN** a native source needs authorization
- **THEN** the credential stays in broker custody at rest and is leased into a per-run tmpfs HOME with refresh writeback, never persisted in the box

### Requirement: Rotation retains recoverability
Owner key rotation SHALL create a new version without invalidating retained ciphertext. Root rewrap SHALL commit atomically; failure SHALL leave all previous wraps usable. Old versions SHALL remain while live state or supported backups reference them.

#### Scenario: Interrupted rotation
- **WHEN** a root rewrap fails midway
- **THEN** no partial set of new wraps is committed

### Requirement: Sealed backup and offline cutover
Platform backups SHALL include ciphertext and wrapped keys only. Conversion SHALL verify decrypted equality before replacing plaintext, and ordinary readers SHALL reject legacy plaintext. Restore SHALL use current recovered lifecycle authority and SHALL NOT revive erased admission. Local roots SHALL exist only as explicit test dependencies.

#### Scenario: Restore credentials
- **WHEN** a sealed snapshot is opened in an offline recovery verification with its retained root
- **THEN** HTTP, native and browser state can be verified without plaintext export
- **AND** full C3 platform recovery still clears provider custody and grants, requiring reconnect so historical credentials cannot revive revoked authority

#### Scenario: Root absent in production
- **WHEN** production starts without KMS configuration or with a local root
- **THEN** credential operations fail closed
