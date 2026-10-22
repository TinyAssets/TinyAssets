# Owner isolation

## Purpose

Define owner admission, source integrity and crash-resume requirements for the owner-box cutover.

## Requirements

### Requirement: Every egress path passes one proxy check, and credentials stay scoped
Every outbound connection from a box SHALL pass one proxy check before the
upstream is opened: the owner SHALL be admitted and the destination SHALL be a
public address. That covers model HTTP, connector HTTP, public HTTP and CONNECT,
git and destinations reached by redirect. Any public destination MAY be reached
without a credential. An owner's credential SHALL be injected only into its own
authorized service endpoint and operation, re-evaluated after every redirect.
Provider credentials SHALL stay in proxy custody, and direct external
connections from a box SHALL be impossible.

#### Scenario: A private destination is refused
- **WHEN** an admitted box issues CONNECT to a private, loopback, link-local or metadata address
- **THEN** the proxy refuses before opening an upstream connection

#### Scenario: A credential never leaves its service
- **WHEN** a request carrying an owner's connection is redirected to another host
- **THEN** the proxy does not inject the credential at the new host

#### Scenario: Public access needs no credential authority
- **WHEN** a public request has no matching credential operation or its credential record is absent
- **THEN** the proxy sends the request with no credential
- **AND** no adapter or discovery reader independently refuses the destination or operation

### Requirement: Owner action policy belongs to the box
The owner box SHALL store and decide workspace file, shell and workflow rules,
private app reads and writes, action classifications and action approvals. The
broker SHALL retain credential scope and spending controls only. A local action
policy request SHALL NOT require a shared policy decision. The stopped cutover
SHALL move retained account action rules into the owner volume, retain spending
rules in broker custody, and remove the egress section and shared action approvals.

#### Scenario: Action rules do not require shared authority
- **WHEN** the owner changes a workspace shell rule through the protected owner session
- **THEN** the box stores and applies the rule without contacting the broker policy service
- **AND** the broker policy document contains no owner-local action rule

#### Scenario: A cutover separates policy ownership
- **WHEN** the stopped cutover retains an account policy
- **THEN** action rules move into the box and monetary rules and spend limits remain in the broker
- **AND** neither policy has an egress mode or destination allowlist

### Requirement: The wake index holds only the next owner wake
The shared wake index SHALL have exactly two columns, `owner` and `next_wake_at`.
Schedule definitions, pause state and occurrence execution SHALL live in the box.
Only a box report SHALL set its next wake, or null when paused or empty. The waker
SHALL reconstruct a missing row by waking the admitted box to report again.
Failed wake or report SHALL retry on the existing tick. A reported null SHALL
never cause a periodic wake. Snapshot retries SHALL remain lifecycle work.

#### Scenario: Missing rows reconstruct without periodic null wakes
- **WHEN** an admitted owner's row is missing
- **THEN** the waker wakes that box to reconstruct its next wake
- **AND** a null report stays asleep beyond 24 hours, including after restart

#### Scenario: Failed delivery preserves the report
- **WHEN** wake or reporting fails
- **THEN** the existing due or missing row remains eligible for retry
- **AND** duplicate delivery, including restart between execution and reporting, never runs the same scheduled occurrence twice

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
- **AND** reassertion removes restored snapshot catalog rows and published references through the map erase transition

#### Scenario: Recovery receipt attests only the broker's work
- **WHEN** the broker completes startup reconciliation
- **THEN** its receipt records jobs resumed, billing reconciled and owners reconciled
- **AND** root checks credential reset and replay cutoff preparation in its own layout document, never in the broker receipt
- **AND** resumed deletion jobs run as ordinary per-owner broker tasks, never inline on the tick
- **AND** recovery failures are labelled as recovery failures

### Requirement: The broker holds the only lifecycle intent
The broker SHALL be the only component that selects or commits sleep, restore,
eviction and wake, from the map's durable record and facts boxhostd reports.
boxhostd SHALL hold no lifecycle intent and SHALL NOT request a wake. Deletion
enqueue SHALL return after its first step; later steps SHALL run as broker tasks.

#### Scenario: An idle box sleeps by the broker's decision
- **WHEN** boxhostd reports a box idle
- **THEN** the broker stops, snapshots and publishes it under the owner lock
- **AND** boxhostd holds no flag that could restart it on its own

#### Scenario: Deletion enqueue returns before volume work
- **WHEN** an owner's deletion is enqueued
- **THEN** the call returns after the owner is erased, channels are closed and routing is withdrawn
- **AND** destroy and snapshot deletion run afterwards as a broker task

#### Scenario: Capacity is independent of slow host probes
- **WHEN** one owner's host status is slow while another owner requests wake
- **THEN** the second wake does not queue behind that probe under a global lock
- **AND** the broker counts slots, selects victims from recover's facts and an explicit awake-owner query, and owns the capacity limits
- **AND** boxhostd has no capacity decision or exported capacity constants

#### Scenario: A stopped slot is free before copying finishes
- **WHEN** graceful sleep completes drain, process exit and detachment
- **THEN** its slot is released before the separate snapshot copy completes
- **AND** snapshot and restore refuse a box that is still running or attached
- **AND** forced withdrawal remains the deletion stop operation

#### Scenario: Crash recovery copies are durability debt
- **WHEN** boxhostd has stopped a sandbox but its map row is still running, or its sleep copy is pending
- **THEN** a wake arriving before the copy starts the local image without waiting for snapshot upload
- **AND** the last verified published snapshot remains the restore point until a later completed sleep is copied
- **AND** the tick pays pending copies outside the broker owner lock under the executor UID lock, publishing only the matching pending sleep
- **AND** local eviction refuses any owner whose copy is owed

#### Scenario: Deployment completes before durability copies
- **WHEN** every awake box has completed graceful sleep on a deployment drain
- **THEN** the drain is complete and the tick pays the copies as ordinary per-owner work

#### Scenario: Host registry mutation never spans owner execution
- **WHEN** one owner's launch, readiness poll, drain or status probe is slow
- **THEN** another owner's wake and sleep proceed independently
- **AND** the executor UID lock serializes owner execution while the registry lock covers only registry access

#### Scenario: Broker lifecycle work has one configured concurrency bound
- **WHEN** lifecycle connections reach the broker-configured concurrency bound
- **THEN** the broker starts no additional host operation until capacity is released
- **AND** boxhostd explicitly refuses an excess connection instead of silently closing it
- **AND** the waker derives its pending bound from that same broker configuration

#### Scenario: Erased and installed deletion resumes after step one
- **WHEN** a deletion resumes with erased admission and its withdrawal publication installed
- **THEN** it does not repeat step 1
- **AND** a missing installation or non-erased record repeats that step through the same job

#### Scenario: Notifications and publications need no global bridge
- **WHEN** the admission mutation completes
- **THEN** its async caller awaits the change notification on the broker loop
- **AND** database revisions and monotonic router installation order publication without a publication lock
- **AND** a failed tick publication is logged

### Requirement: Deploys start each box on the published image at wake
A deploy SHALL publish the new image and drain awake boxes to sleep between turns.
Each box SHALL start the published image on its next wake. Rolling back an image
SHALL be republishing the previous image. There SHALL be no per-box roll or
per-box rollback.

#### Scenario: Drain never kills a turn
- **WHEN** a deploy drains an awake box with a turn in progress
- **THEN** new work admission closes atomically, and the turn finishes before the box sleeps
- **AND** a timeout is reported as an incomplete drain, never a killed turn

#### Scenario: A sleeping box wakes on the new image
- **WHEN** a new image is published and a sleeping owner's box next wakes
- **THEN** the box starts on the new image, and the canary acceptance runs on that path


#### Scenario: Canary proves sleep and public wake on the image
- **WHEN** deployment acceptance runs
- **THEN** public preflight wakes the configured canary, and the normal deployment drain completes its sleep
- **AND** only a complete drain proceeds to a public router wake on the published immutable image, revision proof and a successful model turn

### Requirement: Credential mints go directly from bound relay to identity
The owner-bound relay SHALL forward key and hook operations directly to identity.
Identity SHALL authenticate the relay service, take the owner only from its fixed
binding and read admission from its installed routing snapshot. Missing admission
SHALL deny. A mint SHALL re-check installed admission after its write and delete
its own credential and refuse if the owner is absent. Snapshot installation SHALL
take only its atomic swap; no credential or relay lock SHALL serialize mints
with withdrawal or revocation. The broker
SHALL NOT forward owner mints and identity SHALL NOT query admission per mint. The broker
credential operation SHALL only revoke an account, never mint or list credentials.

#### Scenario: Broker credential authority only revokes an account
- **WHEN** the authenticated broker requests a key or hook mint or lists keys
- **THEN** identity refuses the operation
- **AND** the broker's fixed account-revocation call still completes

#### Scenario: An admitted owner mints with broker lookups unavailable
- **WHEN** the owner's routing snapshot is installed and all broker lookups are unavailable
- **THEN** the bound relay mints through identity without contacting the broker
- **AND** a substituted owner, missing record or non-service peer cannot mint

#### Scenario: Withdrawal orders a mint already in flight
- **WHEN** a mint and owner withdrawal run concurrently
- **THEN** a mint whose post-write check sees withdrawal deletes its own credential and refuses
- **AND** deletion step 6 revokes only after the router acknowledges installing the withdrawal revision or a later revision
- **AND** a mint checked before installation is revoked by step 6; no credential survives either order
- **AND** unavailable publication keeps step 6 pending on the existing tick, and relay retirement never waits for a mint

### Requirement: The shared layer is keyed by owner, not center
The shared layer SHALL key admission, credential custody, grants, credential scope
and relay channels by owner. An owner's centers SHALL be a table inside
the owner's box, and creating or retiring a center SHALL be a box operation. The
shared layer SHALL hold no center admission table and no per-center store.

#### Scenario: The cutover drops center-keyed custody
- **WHEN** the cutover migration runs
- **THEN** each owner's center table is written into that owner's box from the admitted center-to-owner mapping
- **AND** every center-keyed credential, connection, grant and policy scope is dropped and counted, and no custody is carried into the owner-keyed stores
- **AND** owner-keyed policy keeps monetary account rules and spend controls, moves account action rules into the box, and drops egress, with a recomputed digest, next generation and migration receipt
- **AND** owners reconnect through the door's ordinary connection flow

#### Scenario: Creating a center touches no shared store
- **WHEN** an owner creates a new center
- **THEN** the center is recorded inside the owner's box
- **AND** no shared admission, custody or policy row is created for the center

#### Scenario: Retiring a center touches no shared store
- **WHEN** an owner retires a center
- **THEN** only the box-local center table changes
- **AND** shared databases and directories remain unchanged

#### Scenario: The owner channel needs no center admission
- **WHEN** an admitted owner's box has zero or several centers
- **THEN** the bound owner channel serves without a shared center list
- **AND** substituted owner or box identifiers cannot select another account

#### Scenario: Same-service connections keep distinct handles
- **WHEN** an owner creates two connections to the same service
- **THEN** each connection's handle selects its own credential and grant
- **AND** another owner cannot read, dispatch or revoke either connection

#### Scenario: Sign-in flows bind only the authenticated owner
- **WHEN** an owner starts and completes a connection sign-in
- **THEN** the shared flow record is bound to that owner and an opaque flow handle, with no center column or comparison
- **AND** a caller-supplied center field is refused, and foreign owners cannot consume the flow
- **AND** PKCE, interactive-session proof and one-shot consumption remain required


### Requirement: Cutover rollback requires durable pre-traffic eligibility
Old-layout rollback SHALL be possible only while ordinary writable admission has
never opened. Deployment control SHALL consume rollback eligibility durably and
permanently before opening admission. Missing or unreadable deployment state
SHALL permit neither rollback nor opening traffic.

#### Scenario: Retry after an old manual rollback left consumed state
- **WHEN** deployment finds a consumed cutover receipt before preflight
- **THEN** it verifies the recorded backup, stable OLD marker, recorded old image,
  and absence of registered or running owner boxes under the lifecycle lock
- **AND** it removes only that attempt's owner images, locks, shared cutover tree
  and interrupted store staging, then archives the receipt with a timestamp
- **AND** unknown artifacts, image mismatch, unstable or new layout, attached
  images or registered/running boxes refuse without deleting anything
- **AND** cleanup writes neither the data volume nor backups; automatic and manual
  rollback perform this same finalization after the old image is accepted
- **AND** a fresh attempt creates new eligibility rather than replaying consumption

#### Scenario: Deploy after successful ordinary admission
- **WHEN** ordinary admission opens after eligibility is durably consumed
- **THEN** deployment archives the consumed receipt under the same lock
- **AND** if archiving was interrupted, the next deploy recognizes the consumed
  receipt, stable owner-box layout and open admission, and archives only the receipt
- **AND** later deploys proceed without rollback cleanup of committed owner stores
- **AND** a later refusal restarts the daemon unless this deploy owns an in-flight cutover

#### Scenario: A restarted workflow cannot roll back after traffic opened
- **WHEN** a deploy workflow is re-run after ordinary admission has opened
- **THEN** old-layout rollback is refused

#### Scenario: A failed apply restores the paired store on its own device
- **WHEN** this deploy entered apply and fails before acceptance with unused eligibility
- **THEN** deployment control restores the verified stopped pair under the host lock
- **AND** store extraction and rename remain on the store filesystem, preserving numeric ownership and hardlinks
- **AND** old-image acceptance returns rollback status 2; explicit bundle restore returns 0

#### Scenario: Initialization fails before apply
- **WHEN** this deploy created eligibility but never invoked apply
- **THEN** the EXIT handler retires that attempt's eligibility and restarts the old image without restoring data
- **AND** refusal does not start a writable daemon before the handler owns recovery
- **AND** an existing attempt's directory is never adopted by a new run

#### Scenario: An accepted committed deploy cannot clear its dirty marker
- **WHEN** an accepted deploy reports marker_clear_failed
- **THEN** automatic rollback is disarmed and the committed data and running service remain intact

#### Scenario: Rollback fault injection is inadmissible on production volumes
- **WHEN** CUTOVER_ROLLBACK_FAULT is set without the exact synthetic volume label
- **THEN** initialization refuses before publishing any eligibility
