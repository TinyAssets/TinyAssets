## ADDED Requirements

### Requirement: Shared disk refusal has a stable failure code
Tool jail refusals caused by the shared disk floor SHALL be logged and counted as shared_disk_full by capability rates.

#### Scenario: Disk prevents bash admission
- **WHEN** bash is refused because shared disk space is below the floor
- **THEN** the capability rate snapshot and failure log carry shared_disk_full
