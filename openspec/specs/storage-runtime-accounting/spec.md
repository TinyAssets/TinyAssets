# Storage runtime accounting

## Purpose

Charge owner data while keeping protected platform runtime out of the account
quota, and distinguish storage exhaustion from launch reservation pressure.

## Requirements

### Requirement: Runtime exclusions cannot retain arbitrary uncharged writes

The account file measurement SHALL exclude root credential materialization
and inaccessible platform checkout staging.
Permanent workspaces SHALL remain measured by their own store. Persistent
runtime files (including provider-child caches, sessions, legacy CLI homes and
launch snapshots) SHALL be charged by `universe_files`. Provider home/cache writes SHALL use disposable sized mounts.
Provider launches SHALL mask the credential directory even before first use.
Nested lookalikes, arbitrary provider/cache names, and in-home database backups
SHALL remain charged. Platform sidecars outside the home are not user files.

#### Scenario: Large runtime and small user folder
- **WHEN** protected runtime exceeds the quota but owner data does not
- **THEN** the runtime does not consume the owner's pool or cause a full warning

#### Scenario: User attempts to hide data
- **WHEN** a provider writes into the credential/cache exclusion
- **THEN** the write is disposable or refused
- **AND** persistent native-session/snapshot writes count across launches

#### Scenario: Renamed runtime and recreated cache
- **WHEN** a provider renames `.runtime` and recreates `.runtime/provider-child`
- **THEN** all persistent bytes in both paths SHALL be charged, including after another launch
- **AND** retained auth and sessions SHALL NOT be deleted to repair accounting

### Requirement: Status and notices distinguish bytes from reservations

Status SHALL classify protected credential materialization files as provider runtime and
persistent writable runtime files as other user files. It SHALL explicitly say
the observed footprint is not the account quota total. A full-account warning
SHALL require measured bytes at or above the effective quota. Reservation,
committed-write reconciliation, and headroom pressure SHALL report their actual
reason while preserving the existing write allowance and volume safeguards.

#### Scenario: Genuine full account
- **WHEN** measured owner data reaches or exceeds the effective quota
- **THEN** the full-storage warning remains and recovery grace still permits cleanup

#### Scenario: Active launches reserve remaining capacity
- **WHEN** fitted admission fails while measured owner data is below quota
- **THEN** the notice identifies reservation, remeasurement, or headroom pressure
- **AND** it does not claim the owner is out of cloud storage
