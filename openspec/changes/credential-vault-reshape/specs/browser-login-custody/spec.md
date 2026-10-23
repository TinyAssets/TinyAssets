# browser-login-custody delta

## MODIFIED Requirements

### Requirement: Separate browser encryption keys from data backups
Browser sessions SHALL use the same broker-only owner envelope hierarchy as HTTP and native credentials. Backups SHALL contain sealed state and wrapped keys, never raw browser keys. Conversion SHALL verify old session equality before deleting its raw key. No ordinary reader SHALL fall back to the old key directory.

#### Scenario: Browser restore
- **WHEN** a sealed snapshot is verified offline with KMS
- **THEN** sessions open without an independent raw key volume
- **AND** full C3 platform recovery still clears sessions and requires sign-in to prevent stale authority resurrection
