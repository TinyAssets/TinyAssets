## ADDED Requirements

### Requirement: One durable owner send intake (S24)
The box SHALL commit a send identity and its verbatim owner message before
publication or dispatch. App, voice and connector sends SHALL use `converse`.
The browser SHALL retain each unacknowledged identity independently and retry
that identity after reload; it SHALL NOT claim/delete server messages to resend
them, batch distinct identities, or infer delivery from matching text.
Dispatch in a thread SHALL serialize inside the box. Selected conversation runs
SHALL attach their transcript result to the same admitted owner row.

#### Scenario: Send while working and reload
- **WHEN** a second message arrives during a turn and the page reloads
- **THEN** both identities remain independently recoverable
- **AND** retrying either returns its receipt without a second execution or owner row
- **AND** the page displays no legacy waiting notice for an acknowledged identity

#### Scenario: Interrupted dispatch
- **WHEN** a box stops after committing a message but before starting dispatch
- **THEN** replay of that identity resumes the pending dispatch
- **WHEN** it stops after dispatch starts without a saved result
- **THEN** replay reports uncertain execution and never repeats its effects

#### Scenario: Intake fails
- **WHEN** the message cannot be committed
- **THEN** nothing is published or dispatched and the browser retains the request

#### Scenario: Connection setup is incomplete
- **WHEN** dispatch proves no effects occurred and requires a model connection
- **THEN** the send remains held under its original identity
- **AND** completing setup resumes that identity without another owner row

#### Scenario: Legacy browser draft has no receipt identity
- **WHEN** a previous version's queued draft is imported
- **THEN** one stable recovery entry offers send or discard
- **AND** it is not automatically replayed based on matching message text

#### Scenario: Structural regression
- **WHEN** another browser send transport or steering/claim intake is introduced
- **THEN** the `send-path` CI guard fails

### Requirement: Owner boxes know the platform public URL
The env renderer SHALL derive the origin (scheme and authority) from the existing
`WORKOS_MCP_RESOURCE` and write it as `UNIVERSE_SERVER_URL` in `boxhostd.env`.
The preflight installer SHALL render and require that value before installing
boxhostd code or starting/restarting it; changes to the rendered env SHALL also
trigger a restart. No additional source variable or secret is required.
The production gVisor launcher SHALL pass that public value from its own
environment into every box and SHALL refuse startup if it is absent or empty.
Owner app origin checks SHALL use that configured public resource even when
the internal transport uses HTTP and an internal Host.

#### Scenario: Browser writes through the internal socket
- **WHEN** the public URL is `https://tinyassets.io` and the box receives an HTTP request
- **THEN** preferences and uploads accept that public Origin and reject a foreign Origin
- **AND** the router retains its public Host rewrite during rollout

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

### Requirement: Owner data is one opaque volume per owner
Each owner's data SHALL live in one volume, sized to the owner's plan allowance,
that is never shrunk. Shared application services SHALL have no mounted
owner-content path. Lifecycle code SHALL handle owner data only as whole volumes
identified by owner UID: create, attach, detach, snapshot, restore and delete. It
SHALL NOT open, list, stat or walk a path inside a volume. A running box's volume
SHALL be exposed only to that owner's sandbox. Each owner SHALL have one
lifecycle operation at a time. The one-shot cutover migration is the only
exception to these rules, and it SHALL be deleted after cutover.

#### Scenario: Destroying an owner deletes the volume without a walk
- **WHEN** an owner's deletion job reaches the destroy step
- **THEN** the runtime stops, the volume is fully unmounted and detached, and the image is deleted
- **AND** no shared code lists, stats or opens a path inside it

#### Scenario: Lifecycle never traverses owner content
- **WHEN** create, start, sleep, snapshot, restore and delete run for an owner
- **THEN** no shared process or subprocess opens, lists or walks owner content
- **AND** writes to `/home/owner` land in that owner's volume

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

### Requirement: Owner durability is off-host snapshots referenced in the map
The map SHALL record, per owner, the permanent UID and volume binding, the volume
generation and the latest verified off-host snapshot. The map and its bindings
SHALL be recoverable independently of owner volumes, and a historical map SHALL
NOT authorize reassigning an existing owner's UID or volume. A snapshot SHALL be
taken only after work admission closes, the box flushes, every filesystem
reference is released, XFS is unmounted without a lazy unmount and loop
detachment completes. A completed sleep SHALL be snapshotted at once, except an
idle stop, which SHALL be snapshotted once the box stays stopped for fifteen
minutes, or at once when the owner has no verified snapshot or its newest is a
day old; a wake before then supersedes it. Failures SHALL be retried. A snapshot reference SHALL be published only after
the bytes read back from off-host storage are verified. Restoring an owner SHALL
replace only that owner's volume and SHALL commit its new volume generation
before the box starts. It SHALL NOT modify identity, credentials, admission
state, billing or custody.

#### Scenario: Restoring an owner cannot revive platform authority
- **WHEN** an owner's API key is revoked after their latest snapshot, and the owner is restored from that snapshot
- **THEN** the key stays revoked
- **AND** the box refuses operation IDs from the earlier volume generation

#### Scenario: Only verified snapshots are published
- **WHEN** a snapshot upload is interrupted or its read-back verification fails
- **THEN** the previous verified reference stays authoritative
- **AND** the snapshot stays pending and is retried without waking the owner's agent

#### Scenario: A pause between turns is not an off-host copy
- **WHEN** a box stops because it is idle and wakes again within fifteen minutes, and its newest verified snapshot is under a day old
- **THEN** no snapshot is copied, and the stop leaves deploy readiness nothing to wait on
- **AND** a box that stays stopped for fifteen minutes is snapshotted on the next lifecycle pass
- **AND** an explicit, deploy, deadline or crash stop is snapshotted at once

#### Scenario: An interrupted restore never starts a mismatched box
- **WHEN** a restore is interrupted between installing the image and committing the generation
- **THEN** the restore resumes, and the box does not start until image and generation match

### Requirement: The router only authenticates, maps and forwards
The router SHALL authenticate the caller, select the owner's box from its
installed routing snapshot and forward with the per-box secret. It SHALL hold no
billing, deletion, erasure or custody logic. Forwarding to a box that is already
running SHALL need no synchronous control-plane lookup. The box SHALL
authenticate every incoming request with its own per-box credential. The router
SHALL answer a request itself only when its one registry of trusted handlers
names it (MCP by JSON-RPC method, tool and target; app by path and method), from
its verified identity (ADR-030). It SHALL forward every other request with its
original bytes, and `tools/list` SHALL stay the box's.

#### Scenario: Only registered requests are answered on the trusted side
- **WHEN** a POST to `/mcp` names no registered method, tool and target
- **THEN** the owner's box receives the original bytes, including a body over the router's read bound or one that is not JSON; only a batch holding a registered request is refused

#### Scenario: Routing survives a control-plane outage
- **WHEN** the routing snapshot is installed, the owner's box is awake, and control-plane lookups and lifecycle calls are unavailable
- **THEN** the router still forwards the owner's request

#### Scenario: A forged caller is refused at the box
- **WHEN** a request reaches a box with a missing, wrong-box or forged caller credential
- **THEN** the box refuses it

#### Scenario: Neither door forwards a non-HTTP scope
- **WHEN** a websocket, or any ASGI scope other than HTTP and lifespan, reaches the router or a box's ingress
- **THEN** the door closes the websocket, or fails loudly on any other type, before routing, admission or any handler runs; lifespan still starts the app

### Requirement: Admission is one record and deletion is one resumable job
Each owner SHALL have one admission record with one writer. The router, waker
and egress proxy SHALL decide on that record alone, with the authenticated
identity or bound channel selecting the box. A caller-supplied owner or box SHALL
be refused, and an absent record SHALL deny. Billing and abuse suspension SHALL be
separate reasons: clearing one SHALL NOT clear the other, and `erased` SHALL be
terminal. Account deletion SHALL be one resumable control-plane job. It SHALL
first record the job durably off-host, then withdraw admission, serialized with
wake and open, and close the owner's existing ingress and egress before erasure
is acknowledged. It then deletes the volume and snapshots, erases custody,
cancels billing, revokes router credentials, deletes the identity and writes a
receipt. Snapshot publication, restore and deletion SHALL be serialized per owner.
Progress, retries and billing receipts SHALL live in the broker identity database,
with every change written through to the off-host copy (C14).
Missing required authority SHALL leave the job pending.


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

#### Scenario: Erasure takes effect before acknowledgement
- **WHEN** the deletion job withdraws an owner's admission
- **THEN** cached routes are invalidated, and concurrent wake or open operations cannot complete
- **AND** existing ingress and egress for the owner are closed before erasure is acknowledged

#### Scenario: A deletion survives an older platform backup
- **WHEN** the shared layer is restored from a backup taken before an unfinished deletion job
- **THEN** recovery loads the job from its off-host record and resumes it before admitting any owner
- **AND** the owner's admission stays withdrawn

#### Scenario: The router hands deletion to the broker
- **WHEN** an authenticated owner sends same-origin JSON with `confirm == "DELETE"`
- **THEN** the router enqueues the broker job and returns 202 with a capability status URL only after withdrawal completes
- **AND** a durable job whose withdrawal is pending returns 503 with its status URL
- **AND** the status page remains available after the job revokes the owner's credentials

#### Scenario: An interrupted job resumes without sign-in
- **WHEN** the broker restarts between any two deletion effects
- **THEN** the existing waker tick resumes unfinished local job rows and repeats incomplete effects idempotently
- **AND** no snapshot can publish after erased admission

#### Scenario: Recovery loads jobs independently of its historical backup
- **WHEN** recovery calls `load_unfinished_jobs()` and passes a loaded job to the running broker
- **THEN** the broker imports that authoritative off-host record into its local job rows and resumes through the normal execution path
- **AND** the historical shared database cannot discard the newer job

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

### Requirement: Owner action policy is held by the broker and enforced at OPEN
The broker SHALL hold each owner's action rules, action classifications,
standing grants and one-use approvals in its per-owner policy store, beside
spending and egress policy. Only the router's owner channel, with a live
protected owner session, SHALL widen them. A box OPEN SHALL meet the owner's
rules at the broker whatever the box asserts; the box's own checks are advisory.

#### Scenario: The box cannot write its own rules
- **WHEN** a box sends a policy write or a one-use approval on its channel, with or without a session proof
- **THEN** the broker refuses it and the owner's policy generation is unchanged

#### Scenario: An ask-first call needs the owner's exact approval
- **WHEN** a box OPEN is classified under an ask-first rule
- **THEN** the broker sends nothing unless the OPEN carries an unspent one-use approval of exactly that request at the current generation, or a live standing grant matches

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
- **AND** router-local durable per-owner not-before times reject older bearer `iat` values without listing WorkOS sessions; deletion permanently refuses the owner
- **AND** upstream refresh revocation remains required, legacy raw refresh cookies cannot bypass the local fence, and fresh sign-ins and sealed refresh handles work after restore
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

### Requirement: Cutover rollback requires durable pre-traffic eligibility
Old-layout rollback SHALL be possible only while ordinary writable admission has
never opened. Deployment control SHALL consume rollback eligibility durably and
permanently before opening admission. Missing or unreadable deployment state
SHALL permit neither rollback nor opening traffic.

#### Scenario: A restarted workflow cannot roll back after traffic opened
- **WHEN** a deploy workflow is re-run after ordinary admission has opened
- **THEN** old-layout rollback is refused

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

#### Scenario: Recovery finishes deletions before admitting anyone
- **WHEN** the shared layer is restored and services start with admission closed
- **THEN** unfinished deletion jobs run through the normal broker path
- **AND** admission opens only after they complete

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

### Requirement: The router forwards from an in-memory routing snapshot
The broker SHALL push each owner's admission state, box socket and per-box secret
to the router. An absent or non-admitted record SHALL deny. Forwarding to an
awake box SHALL make no control-plane call. A sleeping admitted owner's wake
SHALL be requested from the broker's lifecycle authority. Admission and secret
updates SHALL be installed atomically and in order, so an older snapshot can
never restore a withdrawn route. A restarted router SHALL hold a current snapshot
before serving. The per-box secret SHALL rotate only on platform recovery or
explicit revocation, and rotation SHALL retire the box's acceptance of the old
secret before the new route is admitted.

#### Scenario: A box wakes without invalidating the route
- **WHEN** a sleeping owner's box is woken
- **THEN** the router forwards with the same per-box secret from its snapshot

### Requirement: Cutover preserves shared directory custody
Staging SHALL record the live UID, GID and permission bits of every existing
shared directory it stages. Publication and crash resume SHALL restore those
recorded values exactly. New directories SHALL have explicit role custody:
broker-private directories use 1002:1002 0700; published directories use
1002:1102 0750; connection secrets and runtime children use 1001:1001 0700;
the runtime socket parent uses 1001:1102 0710; the platform parent uses
1001:1001 0711, with broker-private children. Other router directories use
1001:1001 0750. Staged directory metadata SHALL be verified alongside the file
manifest before publication; absent or changed metadata SHALL refuse publication.

#### Scenario: A live runtime survives cutover
- **WHEN** cutover stages a live `.runtime` owned by 1001:1102 with mode 0710
- **THEN** publication preserves that custody and every other existing shared directory's custody
- **AND** the daemon serving credentials as UID 1001 with groups 1100, 1101, 1102 permits the UID 1002 broker with group 1102 to connect

### Requirement: Cutover preserves knowledge bytes and removes rehearsal residue
The migration SHALL copy admitted centers' vector directories and knowledge
SQLite files verbatim into their owner's box. Census SHALL report knowledge
file and byte counts per owner. Legacy root knowledge SHALL move whole when
its recorded scopes resolve every row to one admitted owner; otherwise it
SHALL be dropped with a counted reason, never treated as regenerated. Root
`lancedb/` SHALL always be dropped with `unattributed_root_vectors`, never
attributed by inference from another store.

#### Scenario: Center vectors survive cutover
- **WHEN** an admitted center contains a LanceDB directory and knowledge databases
- **THEN** apply and verify preserve their original bytes in that owner's box
- **AND** no runtime rebuild or second read path is required

#### Scenario: Disposable cleanup has propagated loop mounts
- **WHEN** rehearsal cleanup encounters multiple mount paths for its loop device
- **THEN** it unmounts all paths deepest first and detaches the loop
- **AND** it asserts no venue mount or loop remains before removing any volume
- **AND** setup makes its mount private and refuses a missing image before creating volumes

### Requirement: Published workflows remain locally authoritative
Migration SHALL retain an admitted author's published definition in that owner's
box and its published snapshot in commons, matching runtime publishing. Census
SHALL count published definitions copied locally per owner. Apply SHALL verify
that migrated workflow references resolve locally before declaring completion.

#### Scenario: Existing published workflow survives cutover
- **WHEN** an admitted owner's published workflow is migrated
- **THEN** its definition resolves from that owner's database
- **AND** its published snapshot remains readable through the published read
- **AND** a missing local definition causes apply verification to fail loudly

### Requirement: Shared billing and deletion authority survive cutover

The cutover SHALL preserve verified paid subscription mappings and unexpired
checkout leases in shared plans and every session-bearing attempt as a permanent settlement mapping. Census SHALL refuse missing paid mappings and
malformed or ambiguous attempts with counted reason codes and pseudonymous account
evidence. Deletion SHALL fence new checkout creation, expire payable sessions and
cancel live subscriptions before success. Restore SHALL preserve deletion authority.

#### Scenario: Paid legacy account has cancellation authority
- **WHEN** a legacy paid account is censused
- **THEN** its verified owner-to-customer-and-subscription mapping is carried into the shared plan
- **AND** missing or conflicting mappings refuse census with a named, counted reason; no unmapped paid plan is seeded

#### Scenario: Checkout webhook follows an expired lease
- **WHEN** a migrated completed checkout receives its webhook after the attempt lease expires
- **THEN** the permanent session mapping resolves its owner
- **AND** deletion consults every retained session mapping and cancels resulting subscriptions
- **AND** only attempts without a session are dropped and counted

#### Scenario: Open checkout survives publication
- **WHEN** a legacy account has an unexpired `checkout_attempt_v1`
- **THEN** the shared plan retains its identity, session, frozen parameters and expiry and subsequent checkout resumes it
- **AND** malformed, conflicting or unattributable attempts refuse census with a named, counted reason

#### Scenario: Account deletion closes payable billing
- **WHEN** the door tombstones an owner with an outstanding checkout attempt
- **THEN** new checkout creation is refused and cleanup confirms session expiry or cancellation of its resulting subscription through the Stripe adapter
- **AND** cleanup reports a distinct outcome for an expired attempt; uncertainty prevents deletion success
- **AND** a subsequent subscription webhook for the tombstoned owner cancels that subscription immediately and records the cancellation

### Requirement: Lifecycle admission failures are isolated per owner

Maintenance SHALL contain admission failures per owner and retain explicit suspended
status. Only the authenticated door deletion flow SHALL admit a suspended owner for
deletion-only cleanup under the existing drain and tombstone rules. Background execution SHALL
hold the activity gate without renewing the user-idle clock; active work SHALL
prevent sleep, and deletion-only startup SHALL start no background dispatchers.

#### Scenario: One suspension does not stop the fleet
- **WHEN** one loaded owner is suspended while two other owners run work
- **THEN** maintenance stops or refuses that owner's box with an explicit suspended outcome
- **AND** both other owners continue work and the maintenance loop continues

#### Scenario: Suspended owner can delete without ordinary wake authority
- **WHEN** an authenticated suspended owner confirms account deletion at the door
- **THEN** the off-host job is durable before erased admission and deletion-only cleanup
- **AND** the outer lifespan starts no scheduler, delivery, consumer or maintenance loop
- **AND** every background execution holds the activity gate; pending delivery stays unexecuted and deletion drain reports zero work

#### Scenario: Periodic checks leave an idle box eligible for sleep
- **WHEN** background work enters an owner whose user-idle interval has elapsed
- **THEN** drain refuses while that work is active
- **AND** after it finishes the box can sleep without a fresh user-idle interval

### Requirement: Cutover accounts for loss without guessing ownership

The census SHALL resolve reviewed parent keys before dropping legacy state,
retain conflicting ownership as a refusal, and report masked directory patterns
and pseudonymous per-account carried sources and losses. A report with zero
carried sources SHALL say "every account reconnects a model source".
Center-local credential vaults SHALL be counted as dropped legacy custody and
never copied to an owner tree. Carried credentials SHALL exist only in broker
custody and its platform vault directory.

#### Scenario: Every unresolved owner row has one reason
- **WHEN** reviewed ownership keys cannot place a row
- **THEN** the census counts exactly one reason per table: `conflicting_owners`, `orphan_reference`, `unadmitted_principal`, `unknown_center`, or `no_owner_key`, in that precedence order
- **AND** conflicting owners refuse; the other four reasons explicitly drop the row
- **AND** parent failures propagate, while retired non-account actor markers may still follow a real parent
- **AND** a row with an admitted center, directly or through a reviewed parent, belongs to that center's owner even if its principal is unadmitted; conflicting admitted owners still refuse and missing parents still drop
- **AND** dropped branch versions and schedules report `workflow history` with version/schedule counts on the candidate owner's line; unresolved budget reservations and provider bindings retain their drops as `usage reservation history` and `provider bindings`
- **AND** versions use their admitted definition author, otherwise admitted publisher, otherwise drop; a missing definition does not discard the version's own snapshot, schedules resolve through definition or version, and competing admitted owners still refuse
- **AND** per-account losses report reason, class and count without identities or path lists

#### Scenario: File directory kinds explain placement
- **WHEN** the census enumerates files
- **THEN** each masked pattern reports counts by `admitted_center`, `unadmitted_center`, `unknown_dir`, `account_hash`, `broker_account`, `platform_store`, or `root_file`
- **AND** unadmitted-center and unknown-directory contents drop, except registered stores retain their reviewed placement rules
- **AND** account hashes alone never confer ownership; unclassified account, broker, platform and root files refuse with their kind
- **AND** known identity, plan, key and lock files follow their creator-backed broker, router or regenerated placement
- **AND** unclassified filenames expose only `<file>.<ext>` or `<file>`; code-constant filenames may be printed verbatim, while unknown platform vault indexes and keys still refuse
- **AND** retired credential directories are summarized as CLI login state per account

#### Scenario: Round-6 files and owner histories publish atomically
- **WHEN** the cutover classifies the identified platform files
- **THEN** egress audit and architecture signing files remain broker state, door handshake secrets remain private router state with no report names or content, and neither enters a box
- **AND** route tables, reset barriers and blob-root locks regenerate; stale backup sidecars drop unopened; retired executor configuration and refresh markers count as `CLI login state`
- **AND** custody bodies follow their `run_file_objects` row via `storage_key + '.body'`, including counted drops
- **AND** scheduled-run JSONL entries split by admitted center and activity-ledger list entries split by admitted actor, falling back to admitted target, at the original relative filename in each owner's box root
- **AND** reports list per-account `scheduled-run history` and `activity ledger` entry counts, explicit unowned drops and all five accepted user costs
- **AND** both splits read back content and per-owner counts and require retained plus dropped entries to equal the original total before publication
- **AND** a hard exit after the scheduled history split resumes both splits without duplication or source loss

#### Scenario: The archive rehearsal uses the deployment transaction
- **WHEN** the operator runs the archive harness with a candidate image
- **THEN** disposable network-less containers on a real XFS project-quota loop mount use the deployment container builder for read-only preflight, ready census, resumable apply and final manifest/quota/marker verification
- **AND** kills between owner trees and after seeding resume to the uninterrupted generation's manifests
- **AND** every migrated owner serves history, pending requests, pause/resume and broker plan reads through owner processes and router forwarding
- **AND** an owner without a model source can complete shared-door connect and model-use confirmation on a disposable clone without changing the restore generation's shared authority
- **AND** pre-traffic rollback verifies the original layout; owner restore verifies isolated data recovery without rewinding platform authority; platform recovery verifies credential invalidation and preservation of deletion and admission authority
- **AND** reports contain fixed step names, counts and PASS/FAIL without identities, paths or content
- **AND** a generated two-owner, four-center archive with rows in all six census classes and all non-conflicting drop reasons exercises the entire harness in the root Linux oracle venue


### Requirement: Owner work runs in the owner's box

Everything that reads or writes an owner's data SHALL run in that owner's
box, as the owner's daemon process: the agent loop, history, schedules,
pause, owner stores, and the app and MCP handlers for that owner. The shared
process SHALL NOT hold a path to any owner's data.

#### Scenario: The shared router reads no owner data
- **WHEN** an authenticated request for owner A arrives
- **THEN** the router forwards it to A's box socket, with A's per-box secret
- **AND** the router opens no owner file or owner database

#### Scenario: Another owner's box is unreachable
- **WHEN** owner B's authenticated request names owner A's center
- **THEN** it is forwarded only to B's box, which has no such center
- **AND** B receives not-found

### Requirement: Owner egress uses the admitted broker channel

The owner daemon SHALL send model calls, streaming replies and authenticated
external calls through the per-owner relay to the broker. The broker SHALL
derive identity from channel admission, resolve credentials and enforce its
existing model accounting, policy and connection grants. The box SHALL hold
no provider, OAuth or Stripe credential and SHALL refuse direct non-loopback
outbound connections at its socket seam.

#### Scenario: A model turn streams through the broker
- **WHEN** an owner chats using a connected HTTP model source
- **THEN** the model executor uses the existing broker OPEN stream and usage gate
- **AND** the broker injects credentials outside the box
- **AND** loss of the allocation's channel closes its broker-held liveness lease

#### Scenario: Model registration permanently latches metering
- **WHEN** the broker accepts a source descriptor naming an HTTP grant, or installs a model capability on its connection
- **THEN** it latches the grant's inference requirement in the same operation
- **AND** later removal or disabling of metadata cannot permit a raw model POST
- **AND** platform registration publishes to the broker before succeeding

#### Scenario: Inference source validation uses only published descriptors
- **WHEN** the owner daemon requests an inference dispatch reference through accounting IPC
- **THEN** the broker validates the source against its accepted descriptor and live grant
- **AND** every accounting liveness token uses that same descriptor path
- **AND** an owner-private provider definition file cannot substitute for a missing publication or change validation

#### Scenario: Existing sources adopt metering without classifying ordinary HTTP
- **WHEN** the broker upgrades existing grants
- **THEN** broker-held capability rows and accepted descriptors latch their referenced grants
- **AND** an unknown connection retains its existing fail-closed metering default
- **AND** an unregistered grant without model capabilities remains ordinary regardless of its destination name

#### Scenario: The relay carries many streams and one owner cannot take them all
- **WHEN** owners hold many concurrent relay streams, such as one per model call
- **THEN** boxhostd serves them on one event loop rather than a thread each, admitting at least 128 per owner and 2,048 in total
- **AND** a stream beyond its owner's bound is refused while other owners' streams are still admitted

#### Scenario: Missing channel and direct egress fail
- **WHEN** a model call has no broker channel, or an SDK or raw HTTP call attempts direct outbound access
- **THEN** it fails without a direct provider or credential-store fallback
- **AND** `api_key_http` is the only model access method; provider CLI executors and their connection actions are absent
- **AND** users reconnect through the broker HTTP connection flow at cutover

#### Scenario: An authenticated effector uses its grant
- **WHEN** the owner invokes an authenticated external call
- **THEN** its action policy decision runs in the box and its connection grant and HTTP dispatch use the admitted box channel
- **AND** revoking the grant prevents dispatch

#### Scenario: Native commands use the persistent public bridge
- **WHEN** OwnerBoxd starts in the admitted owner daemon
- **THEN** it owns one loopback HTTP/CONNECT listener for the box lifetime and supplies proxy defaults to every command, including provisioning and wake probes
- **AND** each connection uses centerless BOX_PUBLIC on the owner-bound relay; the broker admits the owner's verified box record even with zero centers, refuses revoked, deleted or suspended owners, and retains its public-address floor and owner/host caps
- **AND** a center or principal supplied to the relay's BOX_PUBLIC request is invalid
- **AND** authenticated git's invocation-scoped local routes take precedence over the public proxy default
- **AND** closing the daemon for shutdown or idle sleep closes the listener and active bridges without retaining command output pipes

### Requirement: Shared reads contain no box-local shared authority

The daemon SHALL read its plan and published commons through named broker
operations. The broker SHALL select plan state using the relay-bound owner,
and SHALL read published data independently of owner-private roots.

#### Scenario: Plan and catalog reads
- **WHEN** the daemon requests its plan or a published definition
- **THEN** BOX_SHARED returns the shared billing or published catalog result
- **AND** no shared database or Stripe credential is placed in the box

#### Scenario: Explicit account admission and missing plan
- **WHEN** the broker admits an owner's first center
- **THEN** its explicit free row and center admission commit in one transaction
- **AND** an existing paid row is preserved
- **AND** a later missing row raises PlanMissing through BOX_SHARED rather than becoming free

#### Scenario: Billing at the shared door
- **WHEN** an authenticated owner requests billing status, checkout or cancellation
- **THEN** the router uses fenced BILLING_PLAN operations keyed by that owner
- **AND** the broker keeps tier, end date, processor mapping and checkout attempt in .broker/plans.db
- **AND** neither home resolution nor an owner store is read at the router
- **AND** status includes plan limits and omits private seat/storage usage

#### Scenario: Verified billing event reaches the box
- **WHEN** a signed, mode-matching subscription event arrives at the router
- **THEN** its service claim and stored customer/subscription mapping select the owner
- **AND** ordered tier application preserves de-escalation on equal timestamps
- **AND** checkout settlement matches its stored session/attempt rather than a client reference hint
- **AND** the owner's next BOX_SHARED plan read observes the shared row

#### Scenario: A box publishes to the shared commons
- **WHEN** the box submits a definition through BOX_PUBLISH
- **THEN** the broker validates and writes it in .published with the bound owner as author
- **AND** a request cannot select another author or append to another owner's bundle
- **AND** public-run and catalog reads use only the published copy
- **AND** neither the box nor the public router writes a shared preview cache

### Requirement: Boxes sleep and wake without a person

A box SHALL sleep when idle, and SHALL wake on an inbound request or at its
reported next-wake time.

#### Scenario: An interactive request wakes its persistent daemon
- **WHEN** the authenticated router resolves an admitted owner
- **THEN** it asks boxhostd to ensure that owner's broker-derived UID is awake
- **AND** boxhostd starts one gVisor sandbox with the quota-bound owner mount, relay, read-only freshly rotated secret, static environment, null network and memory cap
- **AND** it waits for the authenticated app socket to respond before acknowledging and atomically leases the box against eviction until forwarding is admitted or a ten-second bound expires
- **AND** startup or capacity timeout returns a loud 503 at the router

#### Scenario: Idle sleep cannot race a turn
- **WHEN** no request or turn has run in a box for the ten-second wake lease, and no sign-in is pending (ADR-026: no idle timer)
- **THEN** boxhostd asks its app socket to atomically drain only if no request or turn is active
- **AND** a drained box admits no new work and flushes on SIGTERM
- **AND** unknown status or a running turn prevents idle eviction

#### Scenario: Crash restart and capacity
- **WHEN** a wanted daemon crashes
- **THEN** boxhostd restarts it with exponential backoff capped at 60 seconds and rotates its secret
- **AND** five consecutive failures produce a loud status while automatic retries continue
- **WHEN** a wake's thin reservation (256 MiB) would take the awake and reserved boxes past the memory budget (6 GiB by default), each charged its last reported cgroup memory and never less than thin (ADR-026)
- **THEN** an interactive wake may evict the least recently used idle box
- **AND** scheduled wakes queue without eviction and behind interactive wakes

#### Scenario: Retirement fences stale forwards
- **WHEN** a center is retired
- **THEN** the broker requires boxhostd to stop the box, preserving the owner's admission and ingress secret
- **AND** a forward resolved beforehand fails at the stopped socket, and the next start receives the updated center list
- **WHEN** the shared door retires an account after persisting its deletion tombstone
- **THEN** the owner's admission is terminally erased and the box stops before router credentials are cleared
- **AND** the deleted account can never wake or admit again

#### Scenario: The door hands account deletion to the control plane
- **WHEN** an authenticated owner submits same-origin JSON with `confirm == "DELETE"` to `/app/account/delete`
- **THEN** the door durably enqueues the control-plane deletion job and returns 202 with a status URL
- **AND** the job withdraws admission first, then completes volume, snapshot, custody, billing, credential and identity cleanup, exposing durable progress through the status URL
- **AND** every step is idempotent, and an interrupted job resumes after a restart or platform recovery
- **AND** the box exposes no operation that revokes router account state

#### Scenario: A scheduled run in a sleeping box
- **WHEN** a sleeping box's next-wake time arrives
- **THEN** the waker wakes it and the owner's tick runs the due work
- **AND** the tick reports its next local schedule time through bound NEXT_WAKE
- **AND** duplicate wake delivery does not duplicate the same automation occurrence

#### Scenario: Only the bound owner can report a wake
- **WHEN** an admitted box sends NEXT_WAKE on BOX_SHARED
- **THEN** the broker writes only that relay-bound owner's timestamp
- **AND** owner, principal and UID request fields are refused
- **AND** schedule definitions, pending work and retry rows stay inside the box

#### Scenario: Paused or idle boxes are not woken periodically
- **WHEN** a box reports null because it is paused or has no scheduled work
- **THEN** the waker does not wake it
- **AND** a missing index row triggers one reconstruction wake, after which the box reports again
- **AND** a failed wake or report is retried, and duplicate delivery never runs the same occurrence twice

#### Scenario: Suspension and retirement prevent scheduled wakes
- **WHEN** an owner's admission record is suspended, erased or absent
- **THEN** a due report does not wake it
- **AND** boxhostd rechecks the same admission record after capacity waits
- **AND** missing launch-plan quota input is a loud launch error, never a default allowance
- **AND** plan suspension is written only through the broker's fenced shared-door operation


### Requirement: Credential custody and refresh have one broker writer

Only the broker SHALL persist, rotate or erase connection credential material.
The router SHALL hand key deposits or authorization codes and PKCE verifiers
to HTTP_CONNECT over its private broker channel. Token-endpoint requests SHALL
run only in the broker, using broker-owned registered-client configuration.
The broker SHALL own its custody locks, ownership metadata and deletion tombstones independently of daemon and owner stores.

#### Scenario: The door deposits a credential
- **WHEN** the door deposits a key or the broker exchanges an authorization code
- **THEN** the broker rechecks the revision before writing custody and committing its grant
- **AND** a refused deposit leaves no new grant
- **AND** the router performs no vault write

#### Scenario: An OAuth token expires during a box call
- **WHEN** an admitted model or effector call needs a refreshed OAuth token
- **THEN** the broker refreshes and durably saves the replacement before using it
- **AND** registered-client credentials are resolved inside the broker
- **AND** REFRESH and REFRESH_ACK are unknown box-channel operations

#### Scenario: Disconnect erases custody
- **WHEN** DISCONNECT erases a fenced connection with the expected incarnation
- **THEN** the broker calls the directory's RFC 7009 endpoint under the custody lock, using the current refresh token or otherwise the access token and the registered client authentication
- **AND** it erases custody and grants even when provider revocation fails
- **AND** a provider failure leaves a credential-free, revoked connection row with `revocation_error` and `custody_erased`; a fresh connect may replace that receipt
- **AND** a failed erase leaves the connection fenced and retryable

#### Scenario: Connection controls need no owner store at the router
- **WHEN** the authenticated owner opens `/app/connections` or disconnects a listed connection
- **THEN** an explicit center must name that owner in broker admission, or the broker must find exactly one live center for that owner; otherwise the door returns 404
- **AND** the router uses CONNECTION_CATALOG and DISCONNECT without reading a home, owner database, pending request or provider assignment
- **AND** the catalog can include fenced connections whose local custody cleanup needs retry

#### Scenario: Model connection flows need no owner store at the door
- **WHEN** an authenticated owner starts preset PKCE, inline popup, generic OAuth, source sign-in or a key deposit at `/app/model-connect/*`
- **THEN** the door selects the center only through broker admission, using SOLE_CENTER when omitted and returning 404 for a foreign, missing or ambiguous center
- **AND** source and connection definitions come from data or the broker catalog; request_ref is opaque and no home, pending request, universe file or owner database is read
- **AND** the router stores only bounded flow state and public definition metadata, with inline verifier and callback code sealed

#### Scenario: Broker exchanges and commits a grant
- **WHEN** the door consumes an OAuth or preset PKCE flow
- **THEN** it sends code, verifier, redirect_uri and request_ref to the broker
- **AND** the broker prepares the policy, exchanges at the selected endpoint, writes custody and commits the grant with request_ref on its credential-free connection row
- **AND** registered client authentication is resolved only in the broker
- **AND** the consumed router flow cannot be replayed and the broker response contains no credential
- **AND** box channels cannot invoke these exchanges

#### Scenario: Box connect actions carry no secret
- **WHEN** a box invokes a connection action or answers a connect approval
- **THEN** its accepted fields contain no secret, token, key, password or capability URL value
- **AND** a keyless connection uses HTTP_CONNECT on the bound box channel
- **AND** a secret or sign-in connection returns a door link carrying request, center and source or connection ID
- **AND** browser credential submissions go directly to the shared door

#### Scenario: A grant completes the requesting box's pending connection
- **WHEN** the box sees a live GRANTED_RESOURCE committed through the door with the pending connection's request_ref and matching policy
- **THEN** it applies the requested uses and resolves the pending request through the existing answer and continuation path
- **AND** a different request_ref leaves it pending and repeated polling does not deliver a second answer
- **AND** a completion-storage or model-acceptance failure leaves the request pending with an explicit failure
- **AND** completion needs neither push delivery nor a router read of a pending request

#### Scenario: Every supported authentication type completes its lifecycle
- **WHEN** none, bearer, basic, header, oauth1a, url_secret, DCR, registered-client or preset authentication is connected
- **THEN** a model or authenticated effector call from a separate box process succeeds through the broker
- **AND** disconnect through the shared door or box DISCONNECT makes the next box call fail

#### Scenario: Agent connections cannot approve requests
- **WHEN** a box channel submits a request_ref in HTTP_CONNECT
- **THEN** the broker refuses it; only the door can store a request reference
- **AND** reconciliation ignores a box-committed grant even if its reference matches an ask

#### Scenario: First model connection continues to owner confirmation
- **WHEN** a door flow commits a model source
- **THEN** the browser continues with its credential-free grant to the protected owner approval endpoint
- **AND** the box prepares a candidate and raises the existing model-access ask
- **AND** owner approval powers the default agent through the broker, using automatic selection across accepted sources

#### Scenario: Provider revocation fails after local erasure
- **WHEN** RFC 7009 revocation fails
- **THEN** removal returns removed_locally_revoke_failed with a message naming the provider and its recovery URL
- **AND** custody is erased and retrying removal retains the manual-revocation instruction

### Requirement: Production boxhostd delegates before starting either layout

The sole host entry point SHALL move itself from its systemd service cgroup
into a supervisor leaf and enable cpu, memory and pids on the now-empty service
cgroup before layout discovery, subprocess creation or listener startup. Idle
and live entry scripts SHALL converge on this entry point. Missing controllers,
remaining internal processes or failed delegation SHALL abort startup. Owner
cgroups SHALL be siblings of the supervisor under the discovered service path.
The root-only entry point MAY accept an explicit absolute data mount instead of
discovering the Docker volume; both use the same marker checks and service.

#### Scenario: First wake after cutover restart
- **WHEN** systemd starts boxhostd in a populated service cgroup with Delegate=yes
- **THEN** production startup alone evacuates and delegates that cgroup
- **AND** a UID1001 control request wakes an owner under the pinned real runsc
- **AND** the authenticated app becomes ready with verified memory, pids and CPU limits
- **AND** the launch proof supplies no pre-enabled service controllers, owner mounts,
  app sockets, relay sockets or owner-process environment outside production setup

### Requirement: Private MCP messages retain authenticated provider authority

Provider authorization SHALL read authenticated router identity only from the
exact current MCP POST request scope, after OwnerIngress authentication. Ambient
session identity and raw forwarding headers SHALL NOT grant model authority.

#### Scenario: Converse through the private owner route
- **WHEN** OwnerIngress authenticates the router on the current MCP POST
- **THEN** provider authorization uses that exact request's authenticated identity
- **AND** it requires neither a forwarded public bearer nor ambient session identity
- **AND** raw forwarding headers, GET requests and non-MCP paths grant no model authority

### Requirement: Cutover repairs only named index drift

Census SHALL run the shared SQLite integrity policy read-only and report solely
index-only failures as repairable. Stopped apply SHALL run that same policy after
the verified paired backup, REINDEX exactly the named indexes, re-check integrity
and record each repair. Every non-index integrity failure SHALL refuse. The shared verdict SHALL inspect
all errors using SQLite's maximum error limit. Legacy seeding may read table bytes
through index-only drift; pre-cutover rollback SHALL refuse unrepaired drift
before publishing the staged database.

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
