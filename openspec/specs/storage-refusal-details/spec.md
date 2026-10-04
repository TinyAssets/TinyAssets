# storage-refusal-details Specification

## Purpose
Explain measured storage and pending writes in owner-visible quota refusals while preserving admission totals and the existing privacy boundary.
## Requirements
### Requirement: Owners can distinguish measured and pending storage

A quota refusal visible to the owning account SHALL retain existing fields and add measured_bytes, reserved_bytes and committed_bytes from the existing accounting ledger. These SHALL be described as accounting components, which can overlap until reconciliation. used_bytes SHALL remain authoritative; the added components are explanatory. Reporting SHALL NOT change the usage total, quota decision, reservation lifecycle or provider recovery policy.

#### Scenario: Active and committed writes
- **WHEN** an owner has 10 KiB measured, 60 KiB reserved and 20 KiB committed pending remeasurement and an 11 KiB write exceeds the 100 KiB quota
- **THEN** the refusal reports the three components and 90 KiB used, and says active reservations may clear when calls finish

#### Scenario: Reconciliation completes
- **WHEN** reservations are released and a later measurement reconciles committed writes
- **THEN** pending components become zero and the message no longer recommends waiting for active calls

### Requirement: Other viewers receive only the generic refusal

For account-charged quota refusals, non-owner, unknown and unauthenticated viewers SHALL receive the existing generic owner-actionable refusal without component counts, account identifiers, paths or consumer details.

#### Scenario: Another viewer
- **WHEN** the same refusal is projected for a foreign, empty or unknown viewer identity
- **THEN** the response exactly matches the pre-change generic refusal

