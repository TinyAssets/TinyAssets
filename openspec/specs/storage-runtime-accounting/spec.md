# Storage runtime accounting

## Purpose

Charge owner data while keeping protected platform runtime out of the account
quota, and enforce account totals across concurrent and nested jail calls.

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

### Requirement: Jails enforce total account storage without per-call capacity

Tool and provider jails SHALL share the owner's total-storage quota. A launch
SHALL NOT reserve unused headroom or receive a fixed growth or file-size cap.
Ordinary gated-write reservations SHALL continue to count toward account usage.
Writable runtime and workspaces across all owned homes SHALL remain charged.
The shared-volume free-byte and free-inode floors SHALL remain aggregate
storage safeguards. Supervision is polling-based and can overshoot between
checks; it SHALL NOT be represented as a synchronous filesystem quota.

Status SHALL classify protected credential materialization as provider runtime
and persistent writable runtime as owner data. A full-data warning SHALL require
measured bytes at or above quota, rather than capacity held by active calls.
A full account SHALL be allowed to run cleanup without increasing its initial
total; the allowed total SHALL ratchet downward as cleanup makes room. No new
per-call recovery allowance SHALL be granted. Accounting errors SHALL fail closed.
Concurrent supervisors SHALL coalesce account scans; admission and exit SHALL
force a current check. A breach detected after exit SHALL say writes landed,
rather than claiming the process was killed.

#### Scenario: Complete clone with an active provider
- **WHEN** a tool clones a complete repository larger than 16 MiB while a provider is active
- **AND** the owner's total usage fits the quota and shared-volume floors hold
- **THEN** the clone succeeds without sparse or shallow fallback
- **AND** neither active launch reserves unused headroom away from the other

#### Scenario: Total quota across concurrent homes
- **WHEN** writes across the owner's homes and pending ordinary writes exceed quota
- **THEN** the next total-storage check reports the violation and stops a still-running jail
- **AND** other owners' data does not consume this owner's quota

#### Scenario: Genuine full account and cleanup
- **WHEN** measured owner data reaches or exceeds quota
- **THEN** the full-storage warning remains and deleting files can succeed
- **AND** repeated calls do not receive fresh growth allowances

#### Scenario: Fast completed write exceeds quota
- **WHEN** a command exits before the periodic watcher observes its excess writes
- **THEN** the final check reports that it finished over quota and writes landed
