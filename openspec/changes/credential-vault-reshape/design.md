# S28 design: authority before encryption

## Binding shape
Read owner-daemon-in-box/research.md and design.md T1-T10. T6 permits credential custody and upstream injection in the broker; T8 keeps owner business logic in the box. The hierarchy never opens an owner volume or accepts a box-supplied owner identity. Broker admission continues to own identity, revocation and erasure.

## Native subscription custody
ADR-022 P2b and C6 tracker 8.1 keep native subscription CLIs (`subscription_lease.py`) in broker custody at rest. A run leases that state into a per-run tmpfs HOME and writes refresh back (`docs/concerns/2026-10-15-cli-auth-in-guest-tmp.md`). That lease is the custody path. S28 (tracker 8.14) seals the broker's at-rest custody stores under the KMS key hierarchy.

## Storage
Use a single broker-owned SQLite envelope ledger: owner digest, key version, root provider/reference and wrapped AES-256 owner key. Each stored document uses AES-GCM with a random 96-bit nonce; authenticated context binds format, owner digest, store, slot and key version. HTTP records, native state and browser sessions share the same key service. Existing custody locks retain write serialization. The authenticated owner digest selects keys; no command-center key or host owner path is needed after C6.

The root interface generates, wraps and unwraps owner keys. Production requires explicit KMS configuration and fails closed when unavailable. A local root is injectable only in tests and is refused in production. Errors carry no secret payload. No provider-specific model code is introduced.

## Rotation and recovery
Owner rotation creates a new active key version; previous wrapped versions remain until all live records and supported backups stop referencing them. Root rotation rewraps every retained version transactionally. Failed rotation retains the previous complete state. Backups contain sealed documents plus wrapped versions, never root material or raw browser keys. A sealed snapshot can be opened with its root without plaintext export. Full C3 platform recovery deliberately clears provider credentials and outbound grants, requiring reconnect: current admission alone cannot prove that a historical credential was not revoked after the snapshot. Preserve that reset until durable credential revocation authority exists; do not revive credentials merely because KMS can decrypt them. Recovery must never restore owner-volume contents or revive erased admission. Account erasure removes all owner wrapped versions after custody removal. Historical backup access still requires current lifecycle authority; cryptographic destruction of historical backups is complete only after retention expires.

## Cutover
Offline, broker-only conversion accepts the current shared custody layout. Seal and reopen/compare before replacing any live plaintext document. No production dual reader or plaintext fallback. Before deployment the founder provisions KMS, verifies a full backup, runs conversion in a maintenance window, verifies a sealed restore and rewrites retained legacy backups before deleting originals. This lane neither accesses production nor provisions/spends. Founder KMS install, the maintenance conversion and the retained-backup rewrite stay host actions.

## Verification
Targeted linux_oracle tests cover all three stores, cross-owner/slot substitution, tampering, unavailable roots, rotation failure/retention, backup restore and erasure. Ruff checks touched Python. No full suite, no delegation.
