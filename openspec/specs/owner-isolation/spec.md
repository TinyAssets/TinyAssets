# Owner isolation

## Purpose

Define source integrity and crash-resume requirements for the owner-box cutover.

## Requirements

### Requirement: Cutover repairs only named index drift

Census SHALL run the shared SQLite integrity policy read-only and report solely
index-only failures as repairable. Stopped apply SHALL run that same policy after
the verified paired backup, REINDEX exactly the named indexes, re-check integrity
and record each repair. Every non-index integrity failure SHALL refuse.

#### Scenario: Missing index entries with intact table
- **WHEN** integrity_check reports only missing entries or wrong entry counts in named indexes
- **THEN** census remains ready with repairable indexes listed and no source writes
- **AND** apply reindexes those indexes and records verified repairs

#### Scenario: Corrupt table alongside index drift
- **WHEN** any integrity error is not an index-only entry error
- **THEN** census and apply refuse without attempting index repair

#### Scenario: Resume with protected shared storage
- **WHEN** stopped apply or its retry opens a daemon-owned source database under a root-owned sticky data directory
- **THEN** named index repair remains writable without changing source database ownership or weakening directory protection
- **AND** stopped apply takes root custody of existing single-link regular WAL/SHM files without discarding their bytes or relaxing modes, and refuses aliases before changing custody
- **AND** interruption after an owner tree or shared seeding can resume on the same host
- **AND** independent archive rehearsal trials restore the archived data-root ownership and mode as well as its contents

### Requirement: Storage scales with active owners
Owner images SHALL be sparse. The map SHALL record local availability and the
completed sleep time. After more than seven days asleep, lifecycle maintenance
SHALL evict the local image only when its latest snapshot is verified and
published, under the existing per-owner serialization. Pending snapshot or
restore work and erased admission SHALL prevent eviction. Cold wake SHALL
restore that reference and commit its generation before attach; any failure
SHALL refuse wake without creating an empty volume.

#### Scenario: Cold wake preserves the owner bytes
- **WHEN** an owner sleeps more than seven days and its verified snapshot is published
- **THEN** maintenance deletes only the local opaque image and records `local=false`
- **AND** the next wake restores byte-identical snapshot data before attach
- **AND** concurrent wake and eviction serialize, including after interruption

#### Scenario: Block store grows within the spending cap
- **WHEN** the mounted reflink-enabled XFS block store has less than the configured free-space threshold
- **THEN** the root timer requests one DigitalOcean volume growth step with its private scoped token and grows XFS online
- **AND** the default maximum is 100 GiB, a larger cap is refused, and no shrink is possible
- **AND** a step exceeding the cap or a failed API call alerts and stops without growing the filesystem
- **AND** no log contains the token
