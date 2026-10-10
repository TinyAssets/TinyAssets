# Architecture approval

### Requirement: Protected architectural agreement
The platform SHALL accept an architecture approval request through pending requests and SHALL issue approval only through the protected owner session for the configured repository founder.

#### Scenario: Owner receives complete briefing
- **WHEN** an agent raises an architecture approval
- **THEN** the pinned app sheet displays repo, PR, head SHA, diff key, exact file list/count, changes, README Direction rationale, risks and rollback, and explicit architectural agreement wording

#### Scenario: Automated credentials cannot approve
- **WHEN** a bearer, agent, outside app, or different owner attempts approval
- **THEN** no signature is issued; a protected session of the configured founder is required

#### Scenario: Decline and retry
- **WHEN** the founder declines
- **THEN** no attestation is issued
- **WHEN** a successful approval is retried after interrupted request resolution
- **THEN** its original signature and expiry are retained

### Requirement: Broker-signed public proof
The broker SHALL keep the private Ed25519 key in broker custody and sign purpose, repo, PR, head/diff binding, count, file-list digest, owner ID, approval time and expiry; the platform SHALL expose only signed results on a read-only public endpoint.

#### Scenario: Valid owner approval
- **WHEN** the configured founder approves the pinned scope
- **THEN** a 24-hour signed attestation becomes readable at `/app/attestations/architecture/{pr}` without exposing the briefing or signing key

#### Scenario: Missing signing configuration
- **WHEN** approval is attempted without usable broker-private configuration
- **THEN** issuance fails closed and the request remains pending

### Requirement: Both proofs above the default cap
The scope guard SHALL require the existing exact-count receipt and a valid unexpired attestation above eight release-critical files, verified by one unit-tested verifier using the committed trusted-base key; every existing guard rule SHALL remain in force.

#### Scenario: Correct approval
- **WHEN** the receipt declares the exact count and the signature, repo, PR, diff key, count, file digest, owner and times match
- **THEN** the over-cap condition passes, including unchanged rebases

#### Scenario: Invalid or absent proof
- **WHEN** proof is missing, malformed, expired, future-dated, wrongly signed, bound to a different head/diff, count, file set, owner, repo or PR
- **THEN** the guard refuses and tells the preparer to raise architecture approval in the app
