# Owner isolation

## Purpose

Define owner admission, source integrity and crash-resume requirements for the owner-box cutover.

## Requirements

### Requirement: Owner admission has one record and one writer

Each owner SHALL have one admission record with one writer. The router, waker
and egress proxy SHALL decide on that record alone, with the authenticated
identity or bound channel selecting the box. A caller-supplied owner or box SHALL
be refused, and an absent record SHALL deny. Billing and abuse suspension SHALL be
separate reasons: clearing one SHALL NOT clear the other, and `erased` SHALL be
terminal.

#### Scenario: Retiring a center leaves the owner admitted
- **WHEN** an owner retires one of their centers
- **THEN** the owner's admission record stays `admitted`, and the broker stops the box so that its next start receives the updated center list
- **AND** an admitted owner with zero live centers can still be woken and use egress

#### Scenario: A suspended owner can still sign in
- **WHEN** a billing-suspended owner signs in
- **THEN** authentication succeeds, because only `erased` denies authentication
- **AND** the owner's work is not served while suspended

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

### Requirement: Deletion is a broker-owned durable job

Account deletion SHALL run as one resumable broker job. Its record SHALL be
written durably off-host before withdrawal. Step 1 SHALL set terminal erased
admission, serialize with wake and open, drain the owner's admitted work and
close existing ingress and egress before acknowledging. The remaining ordered
steps SHALL delete the opaque volume through boxhostd, delete off-host snapshots,
erase custody, cancel billing and expire open checkout, revoke router keys,
hooks and sessions, delete the identity-provider user, and write the receipt.
Each step SHALL be idempotent. Progress, retries and billing receipts SHALL live
only in job records. A missing required authority SHALL leave the job pending.

#### Scenario: The router hands deletion to the broker
- **WHEN** an authenticated owner sends same-origin JSON with `confirm == "DELETE"`
- **THEN** the router enqueues the broker job and returns 202 with a capability status URL only after withdrawal completes
- **AND** a durable job whose withdrawal is pending returns 503 with its status URL
- **AND** the status page remains available after the job revokes the owner's credentials

#### Scenario: An interrupted job resumes without sign-in
- **WHEN** the broker restarts between any two deletion effects
- **THEN** it resumes unfinished jobs from off-host records and repeats incomplete effects idempotently
- **AND** no snapshot can publish after erased admission

#### Scenario: Recovery loads jobs independently of its historical backup
- **WHEN** recovery calls `load_unfinished_jobs()` and passes a loaded job to the running broker
- **THEN** the job reloads its authoritative off-host record and resumes through the normal execution path
- **AND** the historical shared database cannot discard the newer job

### Requirement: Cutover completes legacy deletion obligations before publication

The one-shot cutover SHALL finish every imported legacy deletion or quarantine
obligation before publishing the new layout. It SHALL write receipts into the
off-host job records and refuse publication if any obligation cannot complete.
Census SHALL report their count. Runtime broker jobs SHALL operate only on
permanent volume bindings and SHALL never receive executable legacy owner paths.

#### Scenario: A legacy quarantine has no volume binding
- **WHEN** cutover imports a pending quarantine without an owner_volumes record
- **THEN** the one-shot migration deletes the recorded old tree and writes its job receipt before publication
- **AND** missing billing or identity authority refuses cutover rather than inferring completion
