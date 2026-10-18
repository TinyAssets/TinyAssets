## ADDED Requirements

### Requirement: Restore: pre-traffic rollback, post-traffic disaster
Normal restore SHALL be limited to pre-traffic cutover rollback. It SHALL restore the verified backup pair except for the monotonic erasure ledger, whose live and staged events are unioned. Its admission proof remains root-owned `.layout.json` and `.router-traffic.sqlite3`; no owner-writable state is restore authority.
Any set `traffic_opened_at` (including one newer than the backup) or `credentials_after` refuses with exit 4 and directs the operator to disaster recovery; a missing or unreadable marker refuses unless readable layout proves an unfinished pre-traffic cutover, and missing or unreadable layout too always refuses.
After traffic, only acknowledged disaster recovery is allowed, and its operation fences live in platform-owned state exposed read-only to boxes, never in owner-writable journals or receipt databases.
The router SHALL durably record its backed-up traffic marker before admitting
ordinary traffic after deploy canary acceptance. Closed admission SHALL also
refuse anonymous writable callbacks; only immutable app probes and the separately
authenticated canary sign-in may bypass it.
Disaster recovery SHALL require both existing acknowledgement flags, select the
newest verified pair, reset every bearer credential root or delete unkeyed digest
rows, drop external credential custody and outbound grants/vaults, fence every
operation/receipt authority, and block remaining daily spend until the next day.
WorkOS SHALL revoke all surviving owners' sessions and refuse uncertain results;
the router SHALL compare original session creation time resolved by signed `sid`
to the restore epoch, including cookie refresh. Broker cutoffs SHALL exceed the
restore time plus the shared maximum future skew. Billing reconciliation SHALL
use the webhook resolver for signed legacy universe subscription mappings.
It SHALL reconcile identity erasure through WorkOS and billing through Stripe
adapter seams, report unavailable billing/suspension sources, loss window and
classes reverting to backup time, and exit 6 on completed recovery.
One root-owned append-only `.erasure-ledger.sqlite3` on the shared data root
SHALL be erasure authority, keyed by sha256(principal). Deletion SHALL append its
entry first and record each phase completion. The shared root SHALL be root-owned
with sticky group-write access for the router, preventing replacement of root
ledger files; all ledger SQLite sidecars SHALL be root-private. Cutover SHALL seed all router
tombstones, custody digests, boxhost owner_destroys and broker revocations, then
remove the router tombstone store and shared host journal. Plaintext required for phases SHALL
remain only in root-owned state. Local custody fences are not retry authority.
Both restore paths SHALL union readable live and staged ledger events; owner-box
restores SHALL NOT read legacy erasure stores or diagnostic reports as authority.
Cutover and NORMAL rollback to a pre-cutover pair SHALL import legacy facts,
including report-only quarantine obligations, before retiring those formats. Every new
restored home SHALL create new work; prior completion receipts cannot hide it.
Post-publication cleanup and every start SHALL retry all unfinished home and
quarantine targets across any number of restores. A quarantine destination SHALL
be recorded before rename and home_removed SHALL remain false until all targets
are destroyed. Reports SHALL carry no authoritative cleanup queue.
Authentication SHALL refuse any principal whose digest is in the ledger, even
without an identity mapping. Automatic retries SHALL select every ledger entry
with unfinished phases, without a restore_pending or identity-row filter.
An erased box SHALL never start; deletion of sleeping or suspended owners SHALL
destroy the complete volume, quota project, sockets and secret without launching a box. Deletion SHALL be iterative, descriptor-relative,
and never follow symlinks or cross mounts.
Identities found only in live platform state and absent from the backup SHALL be
erased with their permanent UID reservations retained from the ledger even if
live identity rows are unavailable; conflicting reservations SHALL refuse restore.
Forward migration SHALL record new cleanup obligations for copied erased homes.
Retained live quota clearing
SHALL not require backup registry membership.
Pre-cutover pairs SHALL be refused as disaster sources with an explicit explanation
that forward migration is required; disaster restore requires an owner-box pair.
Unsafe platform paths, invalid pairs, uncertain
identity reconciliation and preparation failures SHALL preserve original volumes.
The DR drill SHALL fetch, verify and transfer both archives and their manifest.
Rehearsal provider injection SHALL require an explicit harness argument and a
fixed mounted module absent from production images; no environment selector.
Rehearsal SHALL prove survivor reconciliation, provider-only erasure before box
startup, and unreachable-provider exit 4 without changing either live volume.

#### Scenario: Exact maintenance-window rollback
- **WHEN** a verified pair is restored before ordinary traffic has opened
- **THEN** normal restore preserves the backup pair and unions erasure events created after it
- **AND** no live credential, policy or operation row is carried forward

#### Scenario: Normal restore after traffic
- **WHEN** the live router marker is newer than the backup pair
- **THEN** normal restore refuses before swapping either volume

#### Scenario: Missing pre-traffic proof
- **WHEN** marker and layout are both missing or unreadable
- **THEN** normal restore refuses with exit 4 before publication

#### Scenario: Owner content is not recovery authority
- **WHEN** an owner plants a corrupt database anywhere in its writable tree
- **THEN** neither normal preflight nor disaster fencing opens it
- **AND** disaster recovery still fences that owner's pre-restore operation IDs

#### Scenario: Acknowledged disaster recovery on a fresh droplet
- **WHEN** both disaster acknowledgement flags are supplied with the newest verified pair
- **THEN** recovery completes with status 6 and rotation/fencing evidence
- **AND** pre-restore API keys, sessions, hook tokens and external bearer credentials fail
- **AND** pre-restore write, bash, broker outbound and receipt replays refuse
- **AND** non-ULID IDs are fenced by epoch, never by lexical timestamp comparison
- **AND** every owner's remaining daily cap is zero until the next day
- **AND** the report names the backup-to-restore loss window and reverted classes

#### Scenario: Erasure survives loss of live stores
- **WHEN** a readable erasure ledger or WorkOS proves an owner deleted
- **THEN** disaster restore unions that erasure and destroys the restored owner tree
- **AND** an uncertain identity lookup refuses publication

#### Scenario: Disaster preparation failure
- **WHEN** credential reset, fencing, erasure quarantine or reconciliation cannot complete
- **THEN** neither staged volume is published and original state remains intact

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
- **THEN** the ledger entry is durable before deletion-only cleanup and shared phase execution
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
- **AND** paired restores validate original source bytes and verify normalized owner-box state; the after-write restore proves key revocation, owner erasure and write/bash replay refusals
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

#### Scenario: Missing channel and direct egress fail
- **WHEN** a model call has no broker channel, or an SDK or raw HTTP call attempts direct outbound access
- **THEN** it fails without a direct provider or credential-store fallback
- **AND** `api_key_http` is the only model access method; provider CLI executors and their connection actions are absent
- **AND** users reconnect through the broker HTTP connection flow at cutover

#### Scenario: An authenticated effector uses its grant
- **WHEN** the owner invokes an authenticated external call
- **THEN** its policy decision, connection grant and HTTP dispatch use the admitted box channel
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
- **WHEN** a box is idle for the configured interval (900 seconds by default)
- **THEN** boxhostd asks its app socket to atomically drain only if no request or turn is active
- **AND** a drained box admits no new work and flushes on SIGTERM
- **AND** unknown status or a running turn prevents idle eviction

#### Scenario: Crash restart and capacity
- **WHEN** a wanted daemon crashes
- **THEN** boxhostd restarts it with exponential backoff capped at 60 seconds and rotates its secret
- **AND** five consecutive failures produce a loud status while automatic retries continue
- **WHEN** the awake cap (six by default) is reached
- **THEN** an interactive wake may evict the least recently used idle box
- **AND** scheduled wakes queue without eviction and behind interactive wakes

#### Scenario: Retirement fences stale forwards
- **WHEN** a center is retired or the shared door retires an account after persisting its deletion tombstone
- **THEN** the broker durably revokes its box secret and requires boxhostd to stop the box before router credentials are cleared
- **AND** a forward resolved beforehand fails at revoked ingress or the stopped socket
- **AND** another retained center starts with a fresh secret; a deleted account can never wake or admit again

#### Scenario: The shared door orchestrates account deletion
- **WHEN** an authenticated owner submits same-origin JSON with `confirm == "DELETE"` to `/app/account/delete`
- **THEN** the router first appends the ledger entry, then forwards deletion to the owner's secret-authenticated internal route for local cleanup
- **AND** a blocked or refused deletion returns 409 with the fence and unfinished ledger work retained; automatic retries need no sign-in
- **AND** ingress drains new work and waits at most twenty seconds for active turns before the local sweep; a timeout reports drain unfinished
- **AND** the router first persists a root-owned ledger entry, then retires every owner admission, requires confirmed volume/quota/socket/secret destruction, erases broker custody, cancels shared owner billing, clears router credentials and deletes the external identity before returning 200
- **AND** every failed step is recorded as unfinished; a tombstoned retry completes destruction, custody, billing, credentials and identity from the saved receipt
- **AND** billing cancellation runs despite destruction failure, and identity survives until earlier phases complete while automatic ledger retries continue
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

#### Scenario: The daily floor survives absent or stale reports
- **WHEN** an admitted owner has never been woken or its last successful wake is 24 hours old
- **THEN** the shared waker offers a scheduled wake even if the owner never reported or reported null
- **AND** reports remain stored until replaced, including after failed or duplicate delivery
- **AND** the broker's 30-second scan remains responsive while bounded control calls wait for capacity

#### Scenario: Suspension and retirement prevent scheduled wakes
- **WHEN** an owner is billing-suspended, abuse-suspended or has no live admission
- **THEN** neither a due report nor the daily floor wakes it
- **AND** boxhostd rechecks broker plan suspension and admission after capacity waits
- **AND** a missing plan is a loud error, never an unsuspended default
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
