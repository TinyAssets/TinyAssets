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


#### Scenario: Legacy authorities are converted once and removed
- **WHEN** the stopped cutover seeds owner admission
- **THEN** legacy erasures and terminal revocations become terminal erased records, and billing and abuse flags become independent suspension reasons
- **AND** the ledger, revocations, plan suspension columns, policy markers and vault tombstones are deleted before publication and are never read at runtime

#### Scenario: Account retirement writes only admission
- **WHEN** an account is retired or erased
- **THEN** only its admission record becomes terminally erased; permanent bindings and center history are retained
- **AND** clearing either suspension reason cannot clear erasure or the other source's reason

#### Scenario: No record or a substituted identity cannot admit work
- **WHEN** the admission record is absent or a caller substitutes an owner or box
- **THEN** router, proxy and waker refuse work without creating an admission record

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


### Requirement: The broker serializes each owner's lifecycle and boxhostd only executes
The broker SHALL hold a per-owner lock that serializes admission changes, wake,
channel open, snapshot publication, restore and deletion for that owner.
boxhostd SHALL execute stop, destroy and snapshot deletion as plain operations
and SHALL NOT call back into the broker to change admission. Deletion job
records SHALL live in the broker's identity database, with each change written
through to an off-host copy.

#### Scenario: Deletion erases once, in the broker
- **WHEN** an owner's deletion job runs step 1
- **THEN** the broker sets the admission record to `erased` once, under its per-owner lock, before calling boxhostd
- **AND** no boxhostd operation calls back into the broker to change admission

#### Scenario: Enqueue and status read only local job rows
- **WHEN** an owner enqueues deletion or reads its capability status page
- **THEN** the broker performs zero remote reads and writes every progress change through off-host
- **AND** enqueue executes immediately and the existing waker tick retries due unfinished local rows

#### Scenario: Wake and deletion serialize in the broker
- **WHEN** wake and deletion run concurrently for one owner
- **THEN** one broker owner lock orders them, and no wake completes under erased admission

#### Scenario: Lifecycle results have one call direction
- **WHEN** sleep, pending recovery, restore or eviction runs
- **THEN** the broker holds the owner lock and commits map transitions from executor results
- **AND** the existing waker tick collects results of already-authorized host execution without callbacks or an additional clock

### Requirement: Platform recovery is separate from owner restore
Restoring the shared layer SHALL leave owner volumes untouched. Before
admission reopens, services SHALL start with admission closed for every owner.
The running broker SHALL import current off-host job rows and resume unfinished
jobs through its normal execution path, then reconcile billing and identities.
Completed jobs newer than the backup SHALL restore terminal erasure without
repeating completed destructive steps. Stripe SHALL set or clear the billing
reason for every retained account regardless of its original writer; every
non-billing reason SHALL remain unchanged. No spend reconciliation exists. It SHALL also invalidate every
pre-restore bearer credential (sessions, API keys, hooks, identity-provider
sessions, operational credentials and box secrets), drop custody and advance
the broker replay cutoff. It SHALL install one current credential source with
no fallback to old values. Unavailable authority SHALL keep the affected
admission closed.

#### Scenario: Shared-layer restore invalidates old bearer credentials
- **WHEN** the shared layer is restored from a backup
- **THEN** no session, API key, hook token or operational credential issued before the restore is accepted
- **AND** owners reconnect their model sources

#### Scenario: Stripe decides billing while other reasons survive
- **WHEN** recovery restores billing and non-billing suspension reasons
- **THEN** current Stripe state sets or clears only billing, and every other reason remains
- **AND** unconfigured or unavailable Stripe prevents admission from opening

#### Scenario: A completed deletion cannot be resurrected
- **WHEN** a job completed after the restored platform backup
- **THEN** its current off-host receipt restores terminal erasure before any admission opens
- **AND** completed destructive steps are not executed again

#### Scenario: Recovery finishes deletions before admitting anyone
- **WHEN** the shared layer is restored and services start with admission closed
- **THEN** unfinished deletion jobs run through the normal broker path
- **AND** admission opens only after they complete and current billing and identities reconcile
