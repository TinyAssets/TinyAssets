# Design: the owner's daemon in the owner's box

## Target shape (binding)

This section is the yardstick for every slice, builder brief and review round.
It restates `research.md`, quoted by line, and gives each rule a check that
proves it. **Where any other section of this design disagrees with this section
or with the corrections that follow it, this section wins.** Sections already
known to conflict are marked "Superseded" where they appear.

The research's test (research.md:117) is that **"the shared daemon holds zero host
paths to owner data, and every cross-owner decision reduces to a positive
identity→box mapping in three named components."** Those three are the
router/map, the credential/egress proxy and the waker (research.md:69).

| # | Research rule (research.md line) | TinyAssets design | Done check |
|---|---|---|---|
| T1 | The shared door "authenticates the caller, turns that identity into an instance ID, forwards the request … and never reads tenant data" (3). It avoids "complex business logic" and routes from "an in-memory copy of the map even when the control plane is down" (32). The box checks that its caller is the router (44). | Router: authenticate → `owner_id` → installed routing snapshot (route and per-box secret) → forward. Waking a sleeping box is a lifecycle call. It holds no lifecycle, billing, deletion or custody logic. The destination box authenticates every request with its own per-box credential and bound identity. | With the routing snapshot installed and the box awake, make every control-plane lookup and lifecycle RPC unavailable: the router still forwards. The forwarding path does authentication, route lookup and transport only (a cold start may need lifecycle). A missing, wrong-box or forged caller credential fails at the box. |
| T2 | Six shared responsibilities, each with a "must not" (52–59). Lifecycle support (provisioning, backups, fleet telemetry) belongs to the same control plane (34, 61). | Identity; owner→box map and router; billing and metering; credential and egress proxy; wake index; published data. Lifecycle support (provisioning, backup, deletion, fleet telemetry) belongs to this control plane and adds no owner-data business logic. | Every shared component, lifecycle support included, is inventoried against research.md:54–59 with its allowed inputs and stores. Shared billing uses proxy-metered or box-reported usage. No shared responsibility reads private owner contents. |
| T3 | The map holds "`owner_id → box_id`, plus the latest volume/snapshot ID" (55). "The box needs a durable volume and off-host snapshots" (61). | Each owner's data is one **opaque volume**. The map records the owner's permanent UID and volume binding, the volume generation and the latest verified off-host snapshot. The map and its permanent bindings are recoverable independently of owner volumes. A historical map never authorizes reassigning an existing owner's UID or volume. | After losing the local instance and local map, recover the durable owner→volume binding and a retained, verified off-host snapshot reference, then restore that owner without inspecting any other owner's contents. Retention preserves every reference the supported recovery point needs. |
| T4 | "Never reads tenant data" (3); "zero host paths to owner data" (117); "Tests that prove the daemon holds no host path to owner data" (113). | Shared application services have no mounted owner-content path. Lifecycle code does only opaque volume operations by UID: create, attach, detach, snapshot, restore and delete. Filesystem access for a running box is scoped to that box. The one-shot cutover migration is the only exception, and it is deleted after cutover. | Verify the deployed mount and access arrangement, and the shared call paths including subprocesses. Exercise create, start, sleep, snapshot, restore and delete with no shared content traversal. A structural test also fails on any shared open, listing, stat or tree walk under the owner mount root. |
| T5 | Cross-owner checks are "positive mappings ('derive the box from the authenticated owner'), never exclusion filters", in three components (69). Never pass a caller-supplied owner or box ID through (54). | One admission record per owner. The router, waker and proxy decide on it alone, and the authenticated identity or bound channel selects the owner's one admitted box. | For the router, waker and proxy: the authenticated identity or bound channel selects the one admitted box. A caller-supplied owner or box substitution denies, and an absent record denies. Withdrawing admission invalidates cached routing, and a wake or open running concurrently cannot complete under the withdrawn admission. |
| T6 | Credentials and egress proxy: "keys and OAuth tokens, injected into outbound requests per box; a per-box allowlist"; it must not "hand a raw secret into the box, or hold a token that reaches every box" (57). Infrastructure isolates (44). | Every outbound connection from a box (model HTTP, connector HTTP, public HTTP/CONNECT, git) passes one proxy allowlist check before the upstream is opened. That includes destinations reached by redirect. A missing allowlist denies. Provider credentials stay in proxy custody and are injected only upstream. Box-ingress credentials are per box. Infrastructure (null netns) prevents direct external connections. | Every egress path refuses a destination outside the owner's allowlist. A missing policy denies. An allowed destination succeeds. Direct native egress fails. No provider credential appears in box inputs or responses. |
| T7 | Wake index: "`(owner_id, next_wake_at)`, rebuildable by asking each box"; no "job definitions, pause state or payloads" (58). The clock is outside and the rows are inside, and delivery is at least once (36). A paused box reports "no next wake" (67). | The index holds `owner` and `next_wake_at`. The box reports its earliest locally scheduled work, or null when paused or empty. Any periodic agent wake is box-local scheduling. A missing row triggers reconstruction (one wake, the box re-reports). A reported null never causes periodic waking. | Prove reconstruction from boxes, paused and null behavior, and retry after a failed wake or report. A duplicate delivery, including a restart between execution and reporting, never runs the same scheduled occurrence twice. |
| T8 | Into the box: the agent loop, history, files, owner DB, schedules, pause, "the app and MCP logic that serves the owner's own data" and "authorization among an owner's sub-users" (63). | All of these run in the owner's box, including the owner's policy for their own file, shell, workflow and app actions. The broker keeps only credential, egress and spending policy. | Trace agent execution, private app and MCP handlers, history, files, schedules, sub-user authorization and owner action policy to the box. No shared collaborator makes these owner-local decisions. |
| T9 | A flag lives "where its reader lives, and give it one writer" (67). | Pause is written and read inside the box. Admission state has one writer, the broker's admission authority. Billing and abuse submit their suspension reasons to it. The owner stays suspended while either reason applies, and `erased` is terminal. | Prove one mutation path. Clearing a billing suspension cannot clear an abuse suspension or an erasure. |
| T10 | Published data is "copied out of the box at publish time"; never "a listing by reading private box stores" (59). | Unchanged: explicit publication copies the item to the published store via `BOX_PUBLISH`. | Explicit publication copies the selected item. The public listing works while private boxes are unavailable, reads no private store and omits unpublished items. |

**Review scope** (research.md:69, :107–112). Floor-class review covers the router/map,
the egress proxy and the waker. Every reviewer receives `research.md` and this
table, and for each finding states whether it can exist in the target shape.
A finding that can't be expressed as a defect in those three components is fixed
inside one box, never by adding a check to shared code. Any check added to shared
code must say why the path it guards still exists (research.md:119).

## Corrections to the as-built slices (2026-10-19)

The research-based audit of `d057f96fad2` returned DRIFT_BLOCKS_CUTOVER.
Each correction below replaces a shared path; none adds a guard to a path the target
shape deletes. Spec v1 (`2346ab1fc3b`) was independently reviewed against
research.md, and this v2 applies that review.

### C1. One volume per owner (T3, T4)
This replaces owner directories on a shared XFS store with project quotas and
the shared tree walks in `boxes/quota.py`, `boxes/destroy.py`,
`boxes/gvisor_runtime.py` and `restore_recovery.finish_erasures`. Target:
- **One image per owner.** Each owner gets one XFS-formatted image file,
  `<volume root>/<uid>.img`, on the box store. Its size is the owner's plan
  allowance. A downgrade never shrinks or truncates an existing filesystem: the
  smaller allowance applies to further growth and billing only.
- **Attach on start.** During box startup, the lifecycle service attaches the
  image to a loop device and mounts XFS through a per-owner host mount
  arrangement. That mount is exposed only to that owner's gVisor filesystem
  service, as `/home/owner`. Writes land in the image, never in an ephemeral overlay.
- **Detach on sleep.** A completed sleep releases every runtime reference,
  unmounts XFS without a lazy unmount and completes loop detachment.
- **Growth** is a host lifecycle operation before owner work starts: enlarge
  the image, refresh the loop capacity and grow the mounted XFS.
- **Destroy** stops the runtime, fully unmounts and detaches, and deletes the
  image. Runtime sockets and the per-box secret are removed separately.
- **Serialization:** each owner has one lifecycle operation at a time, covering
  attach, detach, snapshot, restore and destroy.
- **Deleted:** the project-quota tree logic in `quota.py`, the recursive destroy
  in `destroy.py`, the owner-tree walk in `restore_recovery.finish_erasures`, and
  the mount and symlink checks around owner trees.

**As built (b6 runtime):** broker `VolumeLifecycle` owns transitions and map
publication under the broker's owner lock. `OwnerDaemons` and `OwnerVolumes`
execute bound UID operations; their local UID lock protects execution only.
boxhostd creates a private mount namespace and gives runsc only
`attach().oci_mount()`. Owner directory initialization runs inside the box.
The broker deletion job erases admission once before `stop`, `destroy` and
`delete_snapshots`. Center retirement stops without deleting the volume.
Runtime sockets and secrets are removed separately. No host operation calls
back for lifecycle or volume state; the waker tick reconciles authorized idle
stops and pending work before subsequent wakes.
**As built (a2a migration):** stopped apply creates each owner's image at the
retained box-record allowance (or the census-derived allowance being seeded at
cutover). It copies and verifies the existing census disposition inside temporary
volume attachments. Every attachment is cleanly detached before snapshotting;
the first verified snapshot is published through `owner_volumes` before shared
publication. Snapshot IDs survive a build retry. Publication and final verification
reattach the images only within this one-shot migration exception. The old owner
directories and project-quota setup/readback in migration are deleted. The deploy
container has the loop/mount privileges and snapshot transport required by C1/C2;
the disposable venue uses a local rclone destination with networking disabled.
Service environment wiring and the complete recovery rehearsal remain pending.
The integrated a1 XFS lifecycle proof passes under the pinned runsc with shared
mount-root instrumentation. The old recovery harness still needs its paired
restore and `finish_erasures` call sites replaced.

### C2. Durability is per-owner snapshots (T3)
This replaces the paired whole-system restore, which rewound platform authority
together with owner data and produced four review rounds of carry-forward fixes.
- **Snapshot.** Close work admission, complete the box's flush and shutdown,
  release every runtime filesystem reference, unmount XFS without a lazy unmount,
  and complete loop detachment. Only then reflink the now-immutable image. A
  failure at any stage leaves the previous published snapshot authoritative.
- **When.** A snapshot is taken on every completed sleep, including the first. A
  box that stays awake asks for a snapshot stop between turns after 24 hours. A
  failed snapshot stays pending and is retried without waking the owner's agent.
- **Publish.** Compress and upload to the off-host backup destination, verify
  against the bytes read back from off-host storage, then durably publish the
  reference in the map. An incomplete or failed upload never replaces the last
  verified reference. Seven are retained, and retention never deletes a
  snapshot a supported recovery point references.
- **Restore one owner.** With the owner's admission stopped, install the verified
  image and durably commit its new volume generation before restarting. An
  interruption between installing the image and updating the map resumes the
  restore and never starts a box with a mismatched image and generation. The box
  refuses operation IDs from an earlier generation. This replay protection is
  owner-scoped only, and it is never a fleet-wide restore epoch. Platform state
  (identity, credentials, the map's admission state, billing, custody) is not
  touched, so a restore cannot revive a revoked key, an erased account or a
  spent allowance. Database consistency is validated inside the restored owner
  boundary, never by a shared scanner.
- **Deleted:** live SQLite copying and retakes (`backup-live-sqlite.py`'s owner
  retake, `backup-owner-box.sh:85–138`), the paired data and box-store archive as
  the owner recovery unit, `restore_recovery.py`'s owner-data carry, fence and
  reconciliation logic, `router_traffic.py`'s fail-open predicates, and the
  restore-day spend fence.

**As built (b6 runtime):** the broker identity database holds permanent
`owner_volumes` bindings (`uid`, `volume_generation`, nullable `snapshot_id` and
`snapshot_sha256`), pending lifecycle work, and `owner_snapshots`. Only the broker
writes them. Sleep records a retryable snapshot ID before shutdown; publication
requires the positive owner-to-box record in the publication transaction. Account
deletion sets admission to `erased` while retaining the permanent volume binding. Uploads use
`BACKUP_DEST/<uid>/<snapshot_id>.img.gz`; readback precedes publication. The broker
waker tick retries pending work and retention under the owner lock, without an agent.
After 24 hours, the box closes new work admission and drains its current turn.
Root administration enters the broker; its `restore(owner, snapshot_id)` resumes installation,
commits the new generation and recovery reference together, and leaves the box
asleep for its next ordinary wake.
The in-box operation journal keys IDs by that generation and validates owner
databases before admitting a restored generation. No fleet restore epoch enters
the box. a2a migration now publishes each initial verified snapshot and its permanent
binding before completing cutover. **As built (a2a-3 backup split):** nightly `backup.sh` calls the shared-only
`backup-platform.py`; the live owner-tree copy and SQLite retake scripts are
removed. The stopped old-layout snapshot remains solely for the one-shot cutover.
Shared SQLite stores use online backup; any copy or integrity failure aborts.
The archive and manifest are read back off-host before the manifest becomes the
published recovery point. Recovery selects only these manifests and verifies the
archive digest. Each manifest pins the latest snapshot references in its captured owner-volume
map; those references are the platform backup's supported owner recovery points. A host lock spans map capture/publication and snapshot deletion
through catalog removal, so concurrent pruning cannot invalidate a backup.
The nightly service/timer survives for shared storage only. Lane b owns deletion
jobs and erasure of the UID's remote snapshot namespace.

**As built (a2a-4 operation minting):** the bound in-box `OwnerBoxd.new_operation`
binds the current volume generation and journal epoch at creation. Universe tools
and preview writes mint through that already admitted box; journaled agent turns
mint through the process runtime, and the engine client forwards their IDs unchanged.
Missing generations are still refused, never rebound at execution. The multi-owner
test fixture now gives journal creation the same synthetic runtime as execution.
Real-runtime tests prove ordinary writes/bash, snapshot rewind with old write/bash
refusal, and fresh tools/preview after restore. Production startup already supplied
the generation; the observed bare IDs came from incomplete fixture admission and
direct test IDs. No replay check was relaxed.
The unused shared `.restore-epoch` marker and its storage inventory entry are
deleted; only the box-local generation journal remains.
**Verified (a2a-5):** the synthetic archive rehearsal restores one owner's
snapshot through the real opaque-volume lifecycle. Three earlier-generation
operations (executed write, unseen write and bash) refuse, two fresh operations
succeed, and a key revoked after the snapshot stays revoked. The native serving
fixture releases inherited attachment handles in its private mount namespace;
the production startup proof separately uses the pinned runtime and verifies
completed shutdown snapshot publication.

### C3. Platform recovery is separate, and it keeps lifecycle authority
The shared layer (identity, map, billing, broker custody, published data) has its
own backup. Restoring it never touches owner volumes.
- **Deletion jobs outlive a platform backup.** The erased admission record and
  every unfinished deletion job survive platform recovery. When a deletion job
  is created, its record is written durably off-host before admission is
  withdrawn. Platform recovery loads every job newer than the backup and resumes
  unfinished deletions before admitting any owner. It never infers a completed
  deletion from admission state. A historical platform backup cannot by itself
  re-establish current admission, reuse a UID or volume binding, or discard
  later deletion work. If current lifecycle authority can't be recovered, the
  affected admission stays closed.
- **Before admission reopens:**
  - reconcile billing, spend and suspension authority;
  - reconcile identities against the identity provider;
  - invalidate every pre-restore bearer credential: delete sessions, API keys
    and hooks, revoke identity-provider sessions, replace operational
    credentials and rotate box secrets;
  - drop custody, so owners reconnect;
  - advance the broker replay cutoff.

  Recovery installs one current credential source and removes obsolete fallbacks,
  so a missing current credential can never select an old environment value.
  Unavailable authority leaves the affected admission closed.

**As built (b5):** `backup-restore.sh` prepares and installs only the shared
layer with admission closed for every owner, including the canary. Preparation
invalidates local credentials and custody, rotates box secrets and advances the
broker replay cutoff. It releases exclusive layout locks before restarting the
services. The broker's existing waker tick runs startup reconciliation before
ordinary lifecycle work; recovery adds no execution RPC or periodic scheduler.
`resume_deletion_jobs` imports the current off-host rows. Every unfinished job
and every completed receipt newer than the backup uses the normal broker resume
path. Older completed receipts replace stale local progress without executing
again. Completion comes from job receipts, never admission state.
Stripe refreshes billing for all retained admitted or suspended owners, preserving
non-billing reasons. The identity provider confirms those owners and revokes their
sessions. A durable startup receipt lets root publish stable layout and open
admission; any missing authority leaves admission closed. The archive C3 probe
uses fake external authorities and asserts open admission, invalidated credentials
and unchanged opaque images.

**Settled for b5 (2026-10-20): which sources are "current authority".**
- **Suspension:** the only runtime writer of a suspension is billing
  (`stripe_adapter.py`, reason `billing`), and Stripe is its current source.
  Recovery refreshes every admitted owner's `billing` reason from Stripe,
  setting or clearing it, whatever wrote it first (the migration included):
  Stripe is the one source for that reason. If Stripe is unavailable or
  unconfigured, admission stays closed. Every other restored reason (`abuse`
  and any migration-carried non-billing reason) has no refreshable source, so
  recovery keeps it unchanged. No provenance column is added.
- **Spend:** no runtime spend fence or spend ledger exists (the fence was
  deleted from recovery above), so recovery has no spend authority to reconcile.
  Recovery does not fabricate one.
- **Rule for later writers:** any future control-plane suspension writer (abuse,
  spend cap) records each change in the broker's identity database and writes it
  through to the off-host copy, as deletion jobs do (C14). Recovery replays those
  rows like deletion jobs. Until such a writer exists there is nothing to replay.
- **Receipt (round 4):** the broker's startup receipt carries only facts the
  broker established (jobs resumed, billing reconciled, owners reconciled).
  Root's offline preparation facts (credential roots reset, replay cutoff
  advanced) live in root's layout document, and root checks them there.
  Recovery failures are logged as recovery failures. Recovery's resumed
  deletion jobs run as the same per-owner broker tasks as on any other tick,
  not inline on the tick, so the rule "volume work does not run on the clock"
  holds during startup too.
- **Startup order (C14, b5):** restore the shared layer → start services with
  admission closed for every owner → resume unfinished deletion jobs through the
  normal broker path → reconcile billing suspension and identities → open
  admission. `resume_deletion_jobs` and `reconcile_authority` stop raising and
  implement this order.

### C4. Cutover rollback is the old system's restore, gated by durable eligibility
Before ordinary traffic opens, rollback means stopping the new image, restoring
the pre-cutover backup (old layout) and starting the old image. Deployment
control keeps a durable cutover state. Old-layout rollback is allowed only while
ordinary writable admission has never opened, and eligibility is consumed
permanently just before admission opens. A restarted workflow cannot recreate
it. Missing or unreadable deployment state permits neither rollback nor opening
traffic. This state belongs to deployment control, not the router. **Deleted:**
the router's `traffic_opened_at` and `credentials_after` restore authority.

**As built (a2a-4):** `cutover_control.py` owns an exclusive root-owned 0700
deployment directory and 0600 state record outside shared application data.
The stopped, verified backup and old image are recorded once as `pre_traffic`;
state-file and directory fsync precede admission publication. `consumed` cannot
be reinitialized. Missing, unreadable, incorrectly owned or consumed state
refuses both rollback and opening. Rollback verifies the recorded backup,
stops the new services, installs the old layout and consumes eligibility before
starting the recorded old image. It never takes an owner volume as input.
The deploy workflow's post-acceptance step consumes this authority and publishes
ordinary admission; the router only reads that decision. The timestamp database,
router OPEN_TRAFFIC RPC, credential timestamp predicate and their writer inventory
are deleted. C3's signed WorkOS tokens must resolve to an active provider session;
revoked sessions cannot acquire authority from a refreshed token's `iat`.
Root tests prove pre-traffic restore, durable consumption ordering, restart and
missing-state refusal; deploy-shell tests pin the integration. Migration publishes
the root-owned 0644 admission decision after assigning service custody, under the
sticky root-owned shared directory. Root tests give both service UIDs directory
access and prove neither can overwrite or unlink that decision. The full archive
rehearsal passes exact pre-traffic restore, both crash-resume trials, serving and
rollback refusal after ordinary admission opens. The harness uses deployment eligibility directly
and deletes paired-restore/provider adapters. Independent crash trials validate
each opaque snapshot's digest and map binding before comparing logical map rows;
all owner-content manifests and other shared authority remain compared unchanged.

### C5. The router only routes (T1)
The router authenticates, looks the owner up in its **in-memory routing snapshot**
(owner → admission state, box socket, per-box secret), and forwards. The broker
pushes that snapshot to the router and updates it on admission changes. On a
An absent or non-admitted owner record denies. For an admitted owner whose box
is asleep, the router requests a wake from the broker's lifecycle authority. The
broker serializes the wake with admission changes and supplies boxhostd with the
operation's bound owner, UID, volume state and secret. The router forwards after
readiness, using the current installed route. Forwarding to an awake box makes
no lifecycle or control-plane lookup. The per-box secret rotates
only on platform recovery or an explicit revocation, never on every start. That
keeps the snapshot valid across sleep and wake, and the box still authenticates
every request with it.

**Deleted:**
- the account-deletion orchestration in `owner_router.py` (~228–350);
- the router-process deletion retry loop in `router_credentials.py` (~240–258);
- the per-request erasure-ledger read and `OWNER_BOX_ROUTE` call in `resolve_box`;
- rotating the secret on every start (`volume_lifecycle.py:33`, `owner_identities.py:230–236`).

`POST /app/account/delete` authenticates, enqueues the broker's deletion job and
returns 202 with a status URL.

**Withdrawal and rotation are ordered.**
- Admission and secret updates are installed atomically and in order. An older
  snapshot or wake completion can never restore a withdrawn route or credential.
- Erasure, suspension and explicit secret revocation share the broker's per-owner
  lifecycle serialization.
- Before a withdrawal is acknowledged as effective, every serving router must have
  installed it, or the old owner ingress must be made unreachable. A wake or
  channel open running at the same time cannot publish or admit work under the
  withdrawn state.
- A withdrawal that can't complete stays pending and is reported as pending.
- Secret rotation retires the box's acceptance of the old secret before the
  replacement route is admitted.
- A router that restarts needs a current snapshot before it serves.

This is not solved by per-request broker checks or by expiring healthy routes on
a timer (research.md:32).

**Secret custody.** The persistent ingress secret is unique to one owner's box and
stored in the private broker identity store. Its router projection and shared
backups keep credential access restrictions. boxhostd supplies only that box's
read-only runtime copy, kept separate from the owner volume. Neither provider
credentials nor another box's secret is ever stored in an owner volume. Recovery
and explicit revocation retire every previously accepted copy.

**As built (b4):** `broker/routing.py` publishes the complete admission map,
keyed by the admission identity digest, including terminal records whose identity
row is gone. A durable sequence orders atomic installations in
`routing_snapshot.py`; the router incarnation nonce prevents an old delivery from
initializing a restarted router. The broker retries delivery to a new incarnation;
installed healthy routes never expire. An uninitialized router refuses serving.
The router authenticates and selects its installed route; an awake request makes
no broker or lifecycle call. Cold wake and explicit revocation belong to the
broker's per-owner lifecycle authority. Wake republishes current admission and
supplies boxhostd with the bound launch record, never a replacement route in its
reply. Admission changes use that same lock. Withdrawal publishes first and
closes channels and old ingress before acknowledgement; failed delivery can
complete only after ingress stops, and failed stopping remains pending.
Revocation stops the old ingress before replacing the durable secret and
publishing it. The broker identity store owns the persistent credential;
boxhostd supplies only its one-box read-only runtime copy outside the volume.

The router resolve/release host RPCs, direct host wake helper, per-request route
and release calls, and start-triggered secret replacement are deleted. The waker
receives the broker lifecycle function explicitly. The credential listener has
a separate broker-authenticated snapshot operation, unavailable to a box's
credential requests. No shared owner-volume reader or deployment path is added.
Real broker/router and owner-process tests cover offline-control-plane forwarding,
stale publication, restart refusal, cold wake, withdrawal and rotation ordering,
and missing, forged or wrong-box ingress credentials. Separate-root connection
fixtures receive the existing broker's projection rather than starting another
broker in the router root; kernel-role host tests refuse router lifecycle
operations and admit only broker-bound wakes. Residual internal lease helpers
have no production router caller; their lifecycle/roll cleanup is outside b4.
Platform recovery ordering remains b5, and this note claims no deployment.

### C6. Admission is one record; deletion is one control-plane job (T5, T9)
**One admission record per owner,** in the broker's identity database, with one
writer (the broker's admission authority). Its state is `admitted`, or
`suspended` with a set of reasons (`billing`, `abuse`; each submitted by its
source and cleared only by that source), or terminal `erased`. The router's
snapshot, the proxy at channel admission and the waker all read only this
record. The research permits exactly one positive mapping (research.md:69).

**Admission questions, settled for lane b (lead decision, 2026-10-19; research.md:63, 67, 69; C11):**
- **Retiring a center is not an admission change.** Admission is per owner, and
  centers belong inside the box. Until lane d moves the center list into the box,
  retiring a center is a broker lifecycle action: stop the box, and its next
  start receives the updated center list. The record stays `admitted`. There is
  no "revoked but wakeable" state, and retiring a center rotates no secret.
  Expectations that a retired center blocks the owner's egress or routing tested
  the old center coupling and change accordingly.
- **Zero centers.** An admitted owner with no live centers can be woken and
  served. Lifecycle no longer requires a live center, and the one admission
  function is owner-keyed, with no per-question center eligibility.
- **Authentication is not admission.** A suspended owner can still sign in (to
  pay or to delete their account). Authentication consults only `erased`. The
  admission function answers "may this owner's work be served, woken, admitted
  to a channel or allowed out". The authentication door's `erased` read goes
  away in lane b's deletion job step, once deletion revokes credentials directly.

**It replaces these six "is this owner gone or suspended" authorities, all deleted:**
- the root erasure ledger (`erasure_ledger.py`, the whole file), its
  `erasure.sock` listener in boxhostd (`host_service.py` ~115–134, ~638–640),
  and its 32 call sites, including every authentication door
  (`router_credentials.py:31` and callers) and every broker box operation
  (`server.py` ~381–388, ~637–650, ~1336–1340);
- `owner_box_revocations` and `revoke_box` (`owner_identities.py` ~95–96,
  ~165–170, ~237–249);
- deletion of the `box_records` row as a signal;
- the suspension flags in `plans.db` (`broker/owner_reads.py` ~45–55), now
  reasons on the admission record. `plans.db` keeps tier, Stripe IDs and checkout;
- the policy `.deleted` marker and the vault tombstones as authorities;
- the open-stream erasure watcher (`public_egress.py` ~14–30, `server.py`
  ~200–213, ~244–248).

The four overlapping admission predicates (`box_route`, `owner_record`,
`resolve_owner`, `_box_scope`) become one.

**The deletion job** runs in the broker, resumable, each step idempotent:
1. Durably record the job off-host (C3). Set the record to `erased`, which
   updates the router's snapshot. Serialize with wake and open, and close the
   owner's existing ingress and egress before acknowledging.
2. boxhostd stops the box and deletes the volume (C1).
3. Delete the owner's snapshots off-host.
4. Erase custody.
5. Cancel billing and expire any open checkout.
6. Revoke router credentials: API keys, hooks and sessions.
7. Delete the identity-provider user.
8. Write the receipt.

The per-owner lifecycle serialization prevents a snapshot being published after
`erased`. Deletion history is the job's records and receipts. Billing's own
refusal of a deleted account's checkout stays.

**Platform recovery (C3)** resumes unfinished jobs from their off-host records and
reconciles the admission record's suspension reasons from their sources. If an
authority it needs is unavailable, admission stays closed. **There is no
restore-day spend fence:** it was deleted by C2, and its remaining reader goes
too (`broker/policy.py` ~420–423, `server.py` ~1261–1263).

**As built (b, spend-fence deletion only):** the remaining `PolicyStore.spend_blocked`
reader and its policy/inference call sites are deleted. The broker uses current
spending policy and reservations. The real broker RPC tests carry an obsolete,
non-timestamp restore marker while proving an allowed reservation, an over-cap
refusal and an unpriced-inference refusal with a current policy receipt.
Platform recovery still refuses unavailable current spend or suspension authority;
this deletion does not complete C3 recovery or the remaining C5/C6 work.

**As built (b1, admission readers):** the broker identity database now holds one
`owner_admission` record per owner. The router, channel and public-egress proxy,
waker and boxhostd read that record through the same admission function.
Authentication reads only terminal erasure. Billing and abuse submit independent
reasons through the sole mutation API. Center retirement stops the box without
changing admission or deleting its ingress secret; zero-center owners remain
eligible. Cutover seeds the record and discards nonterminal center revocations.
The temporary legacy-writer mirrors and equivalence matrix are marked for b2.
The overlapping ledger, plan, revocation and center-eligibility reader decisions
are removed. This completes neither the b2 authority deletion nor b3 deletion job,
b4 routing snapshot/secret persistence or b5 recovery. Those remain lane b work.

**Lane b2a implementation:** deletion is now a
broker job whose authoritative records and billing receipts are transported by
rclone to the backup destination. The router's orchestration and retry loop are
deleted. The HTTP door returns 202 only after erased admission and ingress/egress
closure; pending withdrawal returns 503 with the durable status URL. Owner work
drains inside the box before stop. Job progress is absent from the remaining
legacy ledger, which holds erased facts only and is marked for b2b.

**Legacy obligations, lead decision:** the one-shot cutover completes all legacy
deletion/quarantine obligations before publication, writes a job receipt for each,
and refuses publication if any cannot finish. Census reports pending counts.
Only this T4 exception reads old path obligations; broker jobs use volume bindings.
The recovery API loads unfinished off-host jobs and resumes them through the
running broker. Recovery locks, startup and admission-reopen ordering remain b5;
`resume_deletion_jobs` still fails closed. Routing snapshots/secret persistence
remain b4. This paragraph does not claim those lanes or deployment complete.

**Lane b2 authority deletion:** the root erasure ledger and listener, box
revocations, deletion of box-record rows as an admission signal, plan suspension
columns, policy deletion markers, vault tombstones and polling stream watcher are
removed. Account retirement writes only terminal admission. Authentication reads
only the record's erased state through b2. Cutover converts the legacy facts and
removes their stores; progress now lives in broker job rows with off-host write-through (b6). Record-only tests
replace the temporary equivalence matrix. Routing snapshot/secret persistence
and platform recovery ordering remain later lane work.

### C7. One per-box egress allowlist at the proxy (T6)
Each owner has one egress allowlist. Its default is derived from the owner's
connected sources (the endpoints of their admitted connections and model
sources), and the owner can add to it. There is no `open` mode
(`policy.py:124`). Every connection-opening path calls one proxy function that
checks the actual destination, including destinations reached by redirect,
before opening the upstream:
- model HTTP;
- connector HTTP;
- public HTTP and CONNECT, which has no allowlist today (`broker/public_egress.py:40`);
- git.

A missing allowlist denies. **Deleted:** the three separate mechanisms (policy
egress mode, per-connection endpoint allowlist and git scope matching as
independent allowlists). Credential scoping remains for every credential type: an owner-bound credential
may be injected only into its authorized service endpoint and operation. Passing
the per-box network allowlist never authorizes using an arbitrary credential at
that destination. A redirect re-evaluates credential eligibility before injection.
Git repository scope is one instance of this rule.

### C8. The owner's own action policy moves into the box (T8)
`broker/policy.py:29` decides `workspace.files`, `workspace.shell`, workflows and
private app reads and writes. That policy moves into the box, and the broker keeps
credential, egress and spending policy. `policy_rules.py:69` stops delegating
owner-local decisions to `broker/server.py:1020`.

### C9. Wake index is two columns (T7)
Drop `reported_at` and `last_woken_at` from `broker/next_wake.py:16`, and drop the
24-hour floor query at `:41–55`. The box reports its next locally scheduled
work time, or null when paused or empty. A missing index row triggers
reconstruction. Delivery stays at least once, and the box handles each
occurrence idempotently. Snapshot retries are C2's lifecycle work, never an agent wake.

### C10. Storage scales with active owners (founder decision, 2026-10-19)
The founder asked for storage that grows and shrinks with demand, with no
downtime and little wasted money. This builds on C1 and C2 and adds no new
authority:
- **Thin.** Owner images are sparse. An allowance is a ceiling, not a
  preallocation, so physical use equals the bytes written.
- **Hot and cold.** The local box store keeps only active owners' images. An
  owner asleep more than 7 days whose latest snapshot is verified and published
  has their local image evicted, under the same per-owner serialization. Their next
  wake restores from the published snapshot before attaching, which adds a few
  seconds once. That cost is founder-accepted, and it means local disk tracks
  active owners rather than total sign-ups.
- **Grow online.** The box store is an XFS filesystem on a DigitalOcean block
  volume, starting at about 25 GiB. It grows without unmounting (resize the
  volume, then `xfs_growfs`) when free space falls below a threshold. Block
  volumes can't shrink, so eviction is what keeps the needed size small.
  Spending is capped at $15/month for the volume plus Spaces, under the founder's
  approval.
- **Next foundation, not cutover scope: compute cells.** When one droplet can't
  run the awake set, add hosts. The map points each owner to a box on a host (the
  research's cell router, research.md:32). New owners are placed on the
  least-loaded host, and a sleeping owner moves by snapshot restore on another
  host. The router stays a map lookup.

**As built (a2b, C10):** sparse `OwnerVolumes.create` remains the sole creation
path. A 2 GiB XFS image uses 67,457,024 physical bytes in the pinned Linux
venue. The permanent volume binding now records `local` and `asleep_at`.
Completed snapshot publication starts the cold interval; wake clears it.
After more than seven days, the broker waker tick records eviction work under
the owner lock, asks boxhostd to remove the opaque image, and commits `local=false`. Pending
snapshot/restore work and absent admission prevent eviction. Interrupted
eviction resumes before wake, and a cold wake uses C2 restore and generation
commit before attach; restore failure never creates an empty image.

The production outer store is a reflink-enabled XFS DigitalOcean block volume.
`box-store-grow.timer` checks every five minutes; its root script uses the
pinned device/volume identity and a private token file. Below 20% free, it
requests one 25 GiB step, waits for kernel capacity and runs `xfs_growfs`
without unmounting. A configured cap at or below 100 GiB is enforced before
the API call; cap, API and device failures alert through stderr and syslog.
The [100 GiB volume price](https://www.digitalocean.com/pricing/volumes)
is $10/month, leaving $5 of the approved budget for Spaces. The timer and its
complete source closure are installed with the boxhostd bundle. Tests use only
a fake API, synthetic snapshots, loop devices and the pinned runsc.

The DR drill consumes C3 manifests and verifies a C2 single-owner restore
separately in a disposable store. Paired archive transfer/verification and
`backup-pair.py` are deleted. C3 still fails closed on unavailable current
authority. Failed-cutover guidance invokes C4 deployment control; the obsolete
paired restore instructions, owner-tree project readback and quiescence
wrapper are deleted. Production setup is the a2b row in `docs/host-actions.md`.

### Step 1 proof, before any lane is built
Run on a disposable venue that matches the deployment kernel, the outer
filesystem and the pinned runsc. Prove each of these:
- host-mounted per-owner XFS exposure, and that writes to `/home/owner` land in
  the image rather than an overlay;
- persistent writes and a clean detach, with no lazy unmount and complete loop
  detachment;
- an immutable reflink snapshot, with reflink support on the **outer** box store,
  and later writes leaving the snapshot unchanged;
- a verified Spaces round trip;
- restore from the recovered map reference;
- deletion with no content traversal.

Exercise six active boxes, concurrent lifecycle operations (no double attach and
no snapshot published after erasure), interruption of upload, installation and
map publication, growth, storage exhaustion, and the smallest plan formatting
successfully. Record usable capacity, filesystem overhead, snapshot time, upload
time and peak physical storage. If any point fails, the founder decides the
next step before any build.

### C11. The owner, not the center, is the shared layer's unit (T2, T5)
The research isolates the box. Inside the box, an owner's centers are the owner's
own data (research.md:63). Today the shared layer admits, keys custody, scopes
policy and namespaces the relay by center: `center_admissions`, `ADMIT_CENTER`
and `RETIRE_CENTER`, per-center vault directories, `_namespace`,
`TINYASSETS_ADMITTED_CENTERS`, and `BOX_CONNECT` requiring a center. Target:
- the shared layer keys admission, custody, grants, egress policy and relay
  channels by **owner**;
- centers are a table inside the box, so creating and retiring a center are box
  operations;
- the stopped cutover migration (the one-shot exception) re-keys custody:
  - it derives each credential's, connection's and grant's owner from verified
    ownership records, and checks that this agrees with the admitted
    center-to-owner mapping;
  - missing or conflicting ownership refuses the migration, and directory names
    alone never assign custody;
  - re-keying keeps credentials and connections distinct when several of one
    owner's centers use the same service, preserves revocations and credential
    scopes, and rebuilds the affected references and binding digests without
    widening any grant;
  - center-local authorization moves into the owner's box;
  - publication is restartable and exposes only the complete owner-keyed layout,
    and then the center-keyed stores and lookup paths are deleted.

**Deleted:** the shared center tables, ops, environment and per-center
directories listed above.

### C12. Deploys are image-at-wake (T2)
The research has no per-box rolling deploy. A deploy publishes the new image,
then drains awake boxes to sleep between turns, and every box starts the newly
published image on its next wake. The canary owner wakes on the new image as the
acceptance check. Rolling back an image means republishing the previous one and
draining again; it is never a per-box operation.

**Drain.**
- Deployment drain closes the box's existing work-admission gate atomically with
  request and background-turn admission, lets already-admitted work finish, then
  takes the normal flush, detach and snapshot sleep path.
- A timeout or unknown activity state never authorizes killing a turn; it is
  reported as an incomplete drain.
- Concurrent requests wait or get a retryable response; they are never replayed
  after ambiguous execution.
- Draining doesn't eagerly restart ordinary owners.

**Canary.** The canary must complete a sleep, then wake through the normal public
router on the published immutable image, prove that image's revision, and
complete a model turn before acceptance.

Republishing an older image keeps the existing layout-compatibility boundary.
The deploy drain uses the broker's ordinary sleep under the owner lock; it is not
a separate lifecycle path. **Deleted** (round-3 review, 2026-10-20; b6 moved the
roll into the broker): `RoutingAuthority.roll` and its rollback, the
`LIFECYCLE_ADMIN` roll entry, `OwnerDaemons.roll`, `Images.last`/`ran`, the
`restarting`/`roll_failed` state and its wait loops in `owner_daemons.py`, the
`wake(image=)` override in `host_service.py`, the `image` field of boxhostd's
`status` (read only by the roll), and the per-UID loop in
`scripts/roll_owner_boxes.py`. Every wake starts `images.current`. Cutover
rollback (C4) is unchanged.

**As built (lane d, C12):** the deployment driver wakes the canary through the
ordinary public router before publishing. Its one root-authenticated broker
`deploy(image)` call selects the immutable image through boxhostd and enumerates
permanent volume bindings. Awake owners use the existing `VolumeLifecycle.sleep`
under the broker owner lock. A busy or unknown drain stays pending on that same
volume state machine and reports `incomplete`; no ordinary owner is restarted.
Only a complete drain proceeds to public canary acceptance, which wakes the canary
on `images.current`, checks its baked revision and completes a model turn.
Rollback repeats publication and drain with the previous compatible image.
`RoutingAuthority.roll`, host roll/drain operations, per-box rollback, image-last
records, restart flags/waits, wake image overrides and the driver's UID loop are
deleted. The image registry retains only `current`. No owner-content path is added.

### C13. Fewer shared mechanisms (T2)
- **One call direction.** The broker calls boxhostd; boxhostd is a pure executor.
  The broker passes the state an operation needs, and boxhostd never calls back
  into the broker for lifecycle or volume state (`boxes/admission.py` ~20–32,
  ~63–84; `destroy` → `BOX_RETIRE_ADMISSION`).
  - The broker owns lifecycle orchestration, pending-work recovery and map
    publication, including sleep snapshots, restoration, eviction and restart.
  - boxhostd executes the operations it is given and returns results. It never
    selects or commits the next lifecycle transition.
  - boxhostd's maintain loop observes runtime state and moves forward only
    execution that was already authorized. It may stop a box whose process
    exited; it never selects sleep, restart, eviction or wake, and holds no
    lifecycle intent (no `wanted`/`sleeping` flags, no `resume` request). Only
    the broker commits sleep, publication, restore, eviction and wake (C15).
  - The broker collects results through its existing lifecycle coordination; no
    additional authority store or polling clock is introduced.
- **One hop for credential mints.**
  - The owner-bound relay forwards key and hook operations directly to identity's
    credential socket.
  - Identity authenticates the relay service, takes the owner only from the
    relay's fixed binding, and uses the broker-authored admission projection.
  - Minting is ordered against withdrawal and revocation without a lock (see
    "Mint ordering" below).
  - The broker forwarding operation is deleted, and identity remains the only
    writer of keys and hooks.
- **Shared clocks.** The deletions in C5, C6 and the spend fence leave two
  lifecycle clocks, boxhostd's maintain loop and the waker, plus C10's host
  storage-growth timer, which only grows the host's block store and makes no
  owner decision. No other clock. Root's platform-recovery finish is a bounded
  one-shot wait for the broker's receipt, not a clock.
- **The mint reads the installed snapshot.** Identity checks admission from the
  routing snapshot the broker already installs, not by a per-call admission IPC
  (`router_credentials.py` ~137–152 is deleted).
- **Mint ordering (2026-10-20, after lane d's repeated lock re-entry).** No
  credential lock spans snapshot installation, mint and revocation.
  - Installation takes only the snapshot's own atomic swap.
  - A mint writes its credential, then reads the installed snapshot again. If the
    owner is no longer admitted, it deletes the credential it just wrote and
    refuses.
  - Deletion step 6 revokes the owner's credentials only after the router has
    acknowledged installing a revision at or after the withdrawal revision. If
    publication is unavailable, the step does not run, and the deletion job
    retries on the existing tick.
  - Correctness: a mint whose re-check comes after the install sees the owner
    absent and removes its own credential; a mint whose re-check came before the
    install wrote before step 6, which revokes it.
  - The relay's endpoint retirement does not serialize with mints either; the
    same ordering covers relay mints.
  - **Deleted:** `router_credentials._DISPATCH` and every use of it, including in
    `routing_install`, `dispatch` and `revoke_account`, and the relay registry
    lock's serialization of accepted mints with retirement.

**As built (lane d, remaining C13):** the root relay sends the owner from its
fixed binding directly to identity's credential socket. Identity authenticates
the service UID, admits mints through its installed routing snapshot, and shares
one credential-store lock between snapshot installation, mint and revocation.
The relay's existing binding-registry lock serializes accepted mints with endpoint
retirement; broker withdrawal waits for retirement, including when publication
failed and stopping ingress is the acknowledgement path (T5/C13). The broker's
`BOX_CREDENTIAL` handler and identity's admission IPC are deleted. Broker account
revocation is a fixed authenticated identity operation, unavailable to a box;
the broker credential frame cannot mint or list credentials.
Lifecycle call direction remains broker to host; the clocks remain boxhostd's
maintain loop, the waker and C10's host-only growth timer.

### C14. The broker owns each owner's lifecycle; deletion records live in the broker (T2, T5, T9)
The round-2 structural review (2026-10-20) found that the deletion job decides in
the broker but writes `erased` because boxhostd calls back into it, and that one
deletion runs the erase mutation about four times across three callbacks. The
per-owner serialization C5 and C6 assign to the broker was in fact boxhostd's
UID flock. Target:
- **A broker-owned per-owner lock** serializes admission changes, wake, channel
  open, snapshot publication, restore and deletion for that owner. boxhostd's
  UID lock only protects its own local execution and is never the authority.
- **Deletion step 1 runs in the broker under that lock:** write the off-host job
  record, set the admission record to `erased`, close the owner's channels and
  publish the withdrawal to routing. Only then does it call boxhostd with plain
  operations (`stop <uid>`, `destroy <uid>`, `delete_snapshots <uid>`), which
  never call back.
- **Job records live in the broker's identity database.** Each change is written
  through to the off-host copy. Status pages and enqueue read the local rows.
  Platform recovery reads the off-host copy. The remote is not the only store.
- **No new clock.** A job runs when it's enqueued; retries are picked up by the
  waker's existing tick (`retry_at`). The 1-second deletion poll goes.
- **Deleted:** the boxhostd→broker erase callbacks (`BOX_ADMISSION_ERASE`,
  `BOX_RETIRE_ADMISSION`, `OwnerRegistry.erase_admission`, the
  `retire_owner` erase path); the unused owner-channel ops `RETIRE_OWNER`,
  `ERASE_ACCOUNT`, `CLOSE_OWNER_CHANNELS` and `OWNER_ADMISSION_ERASE`;
  `HostRecords` and boxhostd's `deletion_records` transport op; the 1-second
  deletion loop; the stale `account_erasure.py` orchestration wording; and the
  duplicate job-record literal in the one-shot migration (one producer).
- **Recovery (b5) startup mode:** platform recovery starts the services with
  ordinary admission closed for every owner. Unfinished deletion jobs then run
  through the normal broker path, and only then does admission open.
  `resume_deletion_jobs` is that phase, not a lock-holding offline step.

**As built (lane d, C14 recovery gap):** withdrawal reassertion also reasserts
the map's idempotent `erase` when the imported job already completed snapshots.
The existing job path owns this decision. It clears restored snapshot catalog
rows and published references without repeating a completed host operation.
The older-map/completed-job regression exercises the real broker job and map.

### C15. One lifecycle intent, one lock, one path per job (T2, T4, T9)
The round-3 structural review (2026-10-20) confirmed that C14 holds (0 callbacks,
3 clocks, one deletion job) and found the remaining lane-b structure problems.
Target:
- **One lifecycle intent.** The map's durable `running`/`work` record is the only
  lifecycle intent. The broker selects idle sleep from facts boxhostd reports in
  `status` (process running or exited, last activity) and commits it (stop,
  snapshot, publish) under the owner lock. A box whose process exited is stopped;
  the next request or scheduled wake cold-wakes it. **Deleted:** boxhostd's
  `wanted`, `sleeping`, `status.resume`, the `resume` wake in `volumes.tick`, the
  crash-restart branch, and executor-side LRU victim selection (the broker picks
  eviction victims).
- **Volume work does not run on the clock.** The waker tick only notices pending
  work and starts per-owner broker tasks; snapshot, restore and eviction never
  run serially on the tick, so scheduled wakes are never starved.
- **One writer to the volume map, one snapshot-deletion op.** Deletion's snapshot
  cleanup goes through the map's own transition (an `erase` action), not raw SQL.
  boxhostd has one snapshot-deletion op; erasure first releases the owner's pins
  through the map, so the op never bypasses a pin it was not told to release.
- **One owner lock, no bridges.** The owner lock is asyncio-native; thread work is
  scheduled from inside it. Admission mutation takes routing as an explicit
  dependency, and mutating without it fails loudly. **Deleted:** `channel_lock`'s
  holder thread, the `_authorities` registry, `transition_lock`; any remaining
  global lock beside the owner lock must name the shared resource it protects.
- **Enqueue is not execute.** Deletion enqueue runs step 1 (job record, `erased`,
  close channels, publish) under the owner lock and returns; steps 2–8 run as a
  broker task, with retries on the existing tick. The door's latency never waits
  on volume work.
- **The routing snapshot holds routes, not history.** It carries admitted and
  suspended owners only; erased owners are absent. Credentials revoked at
  deletion step 6 are the authentication fence. **Deleted:** the erased branch of
  `active_owner` and the tombstone projection in `publish`.
- **Orphans.** Delete `OWNER_BOX_REVOKE` if it has no caller. The `abuse` reason
  stays as migrated data with no new writer (C3 "Settled for b5").

**Round-4 amendments (2026-10-20, from the round-4 structural review).**
- **Capacity is a broker counter, not a status query.** The broker counts awake
  slots from the results of its own wake, sleep and stop calls. Waiting for a
  slot never holds a global lock across a host RPC.
  - **Sleep is two host calls.** The broker's sleep first calls a graceful `sleep`:
    snapshot-stop, drain, process exit and detach, refusing if work does not
    drain (C2). It then calls `snapshot`, which only copies and refuses a box
    that is not stopped and detached. `restore` has the same precondition.
  - The slot frees when `sleep` returns, so another owner can wake while the
    copy runs. That is b7's done check, now proven by call completion rather
    than a status observation.
  - `stop` stays the forced withdrawal used by deletion.
  - This moves the graceful branch out of `snapshot`/`restore` into its own
    operation; no new behaviour.
  - (Settled 2026-10-20, from b8's stop under rule 4.) Victim facts come from the
  `status` that idle-sleep selection (`recover`) already fetches. The capacity
  constants (`idle_limit`, `awake_cap`, `wake_timeout`) are broker configuration.
  boxhostd holds no cap check and exports none of them. **Deleted:** boxhostd's
  `awake_cap` check in `ensure_awake`, those three `status` fields, and the
  per-candidate `status` RPC loop under `_capacity`.
- **Notification stays on the broker loop.** Admission-change notification runs
  on the broker loop; a worker thread never calls back into the loop. The async
  caller awaits `routing.changed` after the mutation returns. **Deleted:**
  `run_coroutine_threadsafe` in `changed`.
- **Step 1 runs once.** Deletion step 1 executes once per enqueue. A resume
  re-runs it only if the record is not `erased` or its publication did not
  install. **Deleted:** the forced step-1 re-run in `_resume_owner`.
- **No publication lock.** Revision order comes from the identity database's
  `BEGIN IMMEDIATE` and the router's monotonic install. **Deleted:**
  `RoutingAuthority._publication`. (`_DISPATCH` goes with C13's one-hop mint.)
- **Names match responsibilities.** `RoutingAuthority` is split or renamed so
  each class names what it owns: publication, lifecycle and capacity, deletion
  and recovery. This is a move, not new code. Eviction candidates come from an
  explicit awake-owner query, not from `pending()`. A failed publication on the
  tick is logged, not swallowed.

### Build order and lanes
Lane a (C1–C4) and step 1 are complete. The remaining lanes run one after
another on `conv/c6-research-shape`, each after the previous lane has merged:
- **(a2b) C10 storage scaling,** plus the remnants of the paired model.
- **(b) C5, C6, C14:** the routing-only router, one admission record, the
  deletion job, then (b6) broker-owned lifecycle serialization with no callbacks,
  and (b5) recovery's startup mode, then (b7) C15, then (b8) C15's round-4
  amendments and C3's receipt rule, after lane d. b6 also takes C13's "one call direction" for
  lifecycle and volumes (sleep snapshots, pending-work recovery, cold restore,
  eviction), because one broker lock over wake, publication and restore cannot
  coexist with boxhostd calling back into the broker. The broker learns results
  from the return of its own call or from the waker's existing tick, never from a
  callback.
- **(d) C11–C13:** an owner-keyed shared layer, image-at-wake deploys, and C13's
  remaining mechanisms.
- **(c) C7–C9:** the egress allowlist, owner policy in the box, the wake index.
  It runs after d, because policy and allowlists are keyed by owner.

Lane b establishes the broker-owned cold-wake and withdrawal contract that lane d
uses. Lane d preserves that contract while removing host callbacks and center
keys. Lane c builds on the resulting owner-keyed custody and relay interfaces.

Each lane's brief cites this section and research.md, names the shared code it
deletes, and replaces the superseded as-built text and spec scenarios it touches.
Each lane gets one structural review (component, data, authority and quality,
against the research), not just a test review.

## D1. The owner daemon

- **Process:** the same image and code, with entrypoint `python -m tinyassets.owner_daemon
  --socket /run/owner/app.sock --relay /run/boxhostd/relay.sock
  --secret-file /run/boxhostd/secret`. It runs
  inside the owner's gVisor box under the owner's uid. `data_dir()` is the
  owner's root. It serves the existing ASGI app (`/app/api/*`,
  `universe_server` MCP routes, `/api` handlers) on the unix socket, not TCP.
- **Single owner:** the code paths stay as they are. With only one owner's
  rows present, the shared root databases (`.tinyassets.db`, `.runs.db`,
  `.automations.db`) become that owner's databases, and the tick sees one
  owner. No multi-tenant filter is added or needed.
- **Agent loop:** runs in-process, so tools are local filesystem and exec
  inside the box (the existing `universe_tools`). The exec bridge is deleted.
- **Outbound:** the null netns is kept. Model calls, connections and
  webhooks-out go to the broker over a socket bound into the box (one per
  box), which injects credentials. Provider CLIs run in the box, as on main.
- **What it gets from the shared layer:** the authenticated `owner_id` (the
  router sets it, and the box trusts only the router's per-box secret), the
  plan tier, and published commons reads, all through the broker socket.

### D1 decisions from the slice 1 stop (2026-10-16)

- **Bootstrap.** The per-owner relay accepts first frame `{"op":"WHOAMI"}` without a center and returns `{"op":"OWNER_IS","principal":<bound owner>,"uid":<bound uid>}` from `bind()`, with no broker round trip or request identity; this admits startup as `owner-box`, while `BOX_CONNECT` stays unchanged.
- **Mode.** Missing or refused relay admission prevents startup; only successful admission installs immutable process-local owner mode; file helpers return no session binding in that mode, and the same app factory and existing request authentication remain in use until slice 2.
- **Tools.** Local file tools call `OwnerBoxd` in-process with generation/receipt state under `<data>/.owner-boxd`; bash launches locally with bounded output/time and the existing ta receipts and extension delivery over a local socket, without `BoxExecutor` or `start_exec`.
- **Authority.** Provider-work receipts accept `owner-box` alongside `cloud`; the executor class is derived from process admission, with no request-selected identity.
- **Egress.** `owner_runtime.broker_egress` is the slice 3 seam; slice 1 tests use the existing synthetic model-wire fixture.
- **Lifespan.** The existing app lifespan starts and stops the existing owner tick and loopback engine endpoints in owner mode; it needs neither host bootstrap protection nor a second box-facing engine socket.
- **Defaults.** Admitted owner startup enables the existing app-door and engine-tool flags; sandbox status uses that admission, and storage measurement walks the local owner root.
- **Loopback.** The `bash`/`ta` engine transport uses loopback TCP inside the
  box's own netns. That is allowed. The rule is no NON-loopback connection.
- **The `owner-box` verdict is a mode marker, not authority** (slice 1
  review). It lets the owner daemon record provider work in its OWN data
  root, and nothing else. Every boundary-crossing authority stays in
  infrastructure: egress and credentials come only through the broker
  (slice 3 makes owner-mode egress broker-only), and inbound traffic reaches
  a box only through the router's admission-derived socket (slice 2). A host
  process that fakes the relay gains no egress, no credentials and no routed
  traffic. A signed "I am in a box" proof is not added: a host process can
  forge it as easily as the socket, so it would protect nothing.
- **Data root = the boxhostd owner mount** (`/home/owner`, as in the C6
  design and `gvisor_runtime`), never a CLI path. Startup refuses if that path
  isn't a mount (slice 1 review: durable, quota-bound storage only).

## D2. Shared router

- Keeps the public doors (Cloudflare tunnel → `/mcp`, `/app`, `/api`,
  `/hooks/inbound`), WorkOS sign-in, MCP OAuth and API keys.
- **Per request:** authenticate → `owner_id` → box (broker `owner_machine`)
  → `ensure_awake` → stream the request to the box socket with headers
  `X-Owner` and `X-Box-Secret`. Streaming MCP and SSE pass through unchanged.
  The router parses no owner payload.
- Reads no owner data. Commons and published listings come from its own
  published store.

### D2 decisions (slice 2)

- **Router stores never open in a box.** Every router-store opener refuses inside an owner runtime (`refuse_router_store`); box code reaches router state only through router-forwarded identity metadata or the bound credential channel.
- **Account revocation.** The shared door clears router sessions, inline/hosted/OAuth flows, approval handoffs, live API keys and hook digests in-process after box-local deletion, the durable tombstone and broker retirement. No box credential operation can revoke router account state. Refresh records carry the verified owner in authenticated public metadata, preserved through rotation so all devices and grace handles can be revoked; legacy unattributed records refuse renewal. Anonymous native login flows contain no owner and are consumed before identity is known; canary rate counters belong to the configured canary.
- **Handoff bound.** Each owner keeps at most 20 unexpired approval handoffs; insertion removes the oldest beyond the newest 19 in the same transaction.
- **One hook door:** `/hooks/inbound/<token>`; `/mcp/hooks/` is deleted and integrations re-point at cutover (named in the slice 7 report).

- Slices 2 onward change production serving, so they stay unmerged and merge together with the cutover slice as one maintenance deploy, after the `--archive` rehearsal (review of #5172).
- Secret delivery and rotation at sandbox start: slice 4 (boxhostd mints via the broker on every start and writes the read-only secret file); slices 2–7 merge together as one stack.
- **Credential channel (C13).** The fixed owner relay forwards `ROUTER_CREDENTIAL` directly to identity under `.runtime`. Identity authenticates the root relay service and checks its installed routing snapshot under the mint/publication/revocation lock. Relay retirement serializes with accepted mints. The broker forwarding operation is deleted; identity remains the only key/hook-map writer.
- **API key authority.** Owner-mode mint/list/revoke use the channel, with no box key database; grants remain local, a failed grant invalidates the newly minted router key, and a request reference never replays its secret.
- **Hook ordering.** The router mints hook tokens; the box stores only the digest and delivery configuration; all three revocation paths invalidate the router map before the local row, and failed local creation revokes the router token.
> **Superseded by C5/C6.** The authenticated door durably enqueues the control-plane deletion job and returns 202. The job withdraws admission, completes shared cleanup and exposes durable progress through the status URL. The door does not orchestrate deletion.

- **Deleted accounts never mint again.** The shared door owns `/app/account/delete`: authenticated identity, same-origin JSON and `confirm == "DELETE"`. The door first appends its digest entry to the root-owned erasure ledger under the credential dispatch lock, fencing sign-in and new mints. It then forwards to the secret-authenticated `/_box/account/delete` route for drain and local cleanup. Blocked/refused outcomes retain the fence and unfinished ledger work for automatic retry. A retry with a tombstone resumes every unfinished shared phase from the durable receipt; the tombstone refuses new work, never cleanup.
- **Retirement ends the box's secret** (slice 4): after the ledger fence and local drain and sweep, the door retires all owner center admissions through the fenced broker. The broker permanently revokes box admission and requires boxhostd to stop the box, destroy its entire volume and quota limits, and remove its relay/app socket directories and secret. The door records broker custody erasure, owner-keyed billing cancellation and plan update, router credential revocation, then external identity deletion. Billing runs even if destruction fails; identity survives until earlier cleanup completes so the owner can retry. Only a complete receipt returns success. Every failed step stays loud and retryable after the tombstone. There is no box-to-router account-revoke operation; a stale forward fails at ingress.
- **Single identity.** A valid protected session and bearer must name the same owner or the router returns 401 before resolving any box.
- **Approval handoff.** `/app/approval-handoff[/<ref>]` is sign-in navigation whose rows live in the router's owner-sessions store, so it stays at the door. It is never forwarded.
- **Bounded router rows.** Hook digests are capped per owner with an atomic count-and-insert. Revoked key rows beyond the newest 100 per owner are pruned, with their rate counters. Live keys keep the existing cap of 25.
- **Model-connection callbacks** stay at the door, and task 3c moves the whole connection flow there with broker custody. A box never holds flow state, so nothing is forwarded.

- **Transport.** One ASGI reverse proxy streams request bytes and raw response chunks over HTTP on a Unix socket, removes hop-by-hop headers, preserves MCP session headers, and closes the upstream on client disconnect; it never retries an owner request.
- **Resolution.** Fenced `OWNER_BOX_ROUTE(principal, rotate)` returns only the broker-reserved uid and secret for an owner with a live center admission; a reservation alone is not admission, and unknown/retired owners return 404 without opening a socket.
- **Socket.** The single `resolve_box` seam uses `TINYASSETS_BOXES_RUN/<broker uid>/app.sock` (default `/run/tinyassets-boxhostd`, the existing Compose mount); slice 4 adds waking there without changing route handlers.
- **Secret.** Admission mints a 256-bit per-owner secret in the broker's private identity database; explicit rotation replaces it, and owner startup requires `--secret-file`, whose value is checked with `hmac.compare_digest` on every request.
- **Identity.** The router replaces caller-supplied `X-Owner`, `X-Box-Secret` and `X-Owner-Identity`; the last carries the existing authenticated identity and capability metadata, while public bearer/cookie credentials are removed before forwarding.
- **Sessions.** Existing protected-owner cookies resolve only through the router session map; cookie-authenticated mutations retain exact-origin JSON admission, and the verified protected-view proof travels with the authenticated identity for box-local consumers.
- **Placement.** Router authentication resolves credentials only; MCP payload checks, outside capability admission and connection receipts run inside the owner daemon, and router logging never buffers owner payloads.
- **Hook map.** The router's `.hook-owners.sqlite3` contains only token digests and owners; the migration seeds existing digests; box mint/revoke requests use the fixed owner relay directly to reach the identity writer, while payload/delivery rows remain inside the box.
- **Startup.** The shared entrypoint no longer starts owner recovery, engine endpoints, ticks or storage initialization and refuses direct SSE/stdio serving; owner recovery moved into the existing owner lifespan.
- **Routes.** All owner-facing `/app/*` routes (including `/app/api/*`, home, chat, uploads and preferences), `/api` and descendants, MCP transport `/mcp[/]` and `/mcp/events`, and `/hooks/inbound/<token>` share the forwarder; discovery, sign-in/OAuth, pulse, published listing reads and billing retain router handlers.

### D3 decisions (slice 3: broker egress)

- **Model grant metering.** The broker latches `inference_required` from an HTTP connection's `model_use`/`model_discovery` capabilities or the first accepted source descriptor naming the grant. Descriptor publication and the latch commit together; platform registration publishes through the same broker operation before returning success. Backfill reads only broker-held capabilities and broker-published descriptors; it never scans private host center metadata. Missing connection rows retain the existing fail-closed default. Destinations do not classify grants. Removing capabilities or descriptors never removes metering; an unregistered HTTP grant without a model capability remains ordinary, and a latched model POST without an inference envelope refuses before credential access.
- **Send admission.** Every box stream checkpoint repeats OPEN's bound-center admission check, including after resolution/connect and before sending credentials or request bytes.
- **Shared-read admission.** Every BOX_READ rechecks the relay-bound owner's route in the broker; an established BOX_SHARED channel grants no reads after the owner's last live center retires.
- **Transport.** Existing synchronous and streaming broker clients open `BOX_CONNECT` on the startup-admitted relay; the broker derives identity, center and fence from `BOX_CHANNEL`, with no host-client fallback.
- **Models.** Reuse the HTTP model executor, source/grant queries, accounting gate and credit-bounded `OPEN` stream; publish only validated, credential-free source descriptors into the broker ledger before inference.
- **Accounting.** An allocation holds a dedicated box channel with a ten-second heartbeat; the broker holds its liveness locks and releases them on disconnect, so accounting never reads a box file or depends on a host copy of it.
- **Connections.** Expose the existing bounded ledger, catalog, capability, HTTP-connect, disconnect and usage operations on the admitted box channel; credential refresh cannot fall back to daemon-side vault access.
- **Policy.** Policy reads, receipts, classification, decisions and proposals use the channel-bound owner; protected policy writes and approval issuance remain at the shared protected door.
- **CLIs (founder, 2026-10-16).** There is one model path: the broker's. Provider CLIs (Claude Code, Codex) as a model source are retired in owner mode, with no placeholder-credential proxy and no broker endpoint built for them. The in-box CLI provider route is deleted, not left refusing. Users whose subscription was connected through a CLI reconnect it through the broker's connection flow at cutover, and the cutover report names those accounts.
- **Shared reads.** `BOX_SHARED` uses the relay-bound owner without a center and exposes only named plan and published-definition/catalog reads; plan rows live in `.broker/plans.db`, published data in `.published/`, and slice 6 seeds these shared stores at cutover.
- **Enforcement.** Install one Python socket audit hook at owner startup for direct connects, datagrams and DNS; only loopback IPs/localhost and Unix channels are allowed, while the existing null netns remains the native-process boundary.
- **Native public egress (6a parts 1f/1g).** Startup gives OwnerBoxd the admitted relay and starts one in-process HTTP/CONNECT byte forwarder on `127.0.0.1:3128` for the box lifetime. Every bounded child receives upper/lowercase HTTP, HTTPS and ALL proxy defaults and `NO_PROXY=localhost,127.0.0.1`; invocation-scoped authenticated git's local `http_proxy` still overrides that default. The listener sends centerless `BOX_PUBLIC`; the root relay supplies only its bound principal to the broker, waits for `BOX_ADMITTED`, then acknowledges `BOX_CONNECTED` and copies raw bytes. The broker admits the owner's verified box record, retaining revocation, deletion and suspension refusals, destination pinning/private-address refusal and owner/host caps. The shared router starts no listener. OwnerBoxd.close (including daemon SIGTERM for idle sleep) cancels children and closes/joins listener and active bridges; no per-command fork or output descriptor is retained.
- **Relay admission as built (6a parts 1f/1g).** Public channels use owner-keyed broker admission; connector channels keep center-keyed BOX_ADMISSION, never the deleted per-call OwnerRegistry session-grant APIs. The relay bind supplies identity; broker admission, per-stream scope/checkpoints and lifecycle retirement remain the authority. Git's binary exchange uses BrokerClient's existing connect seam so it performs BOX_CONNECT/BOX_CONNECTED before sending OPEN in owner mode, rather than writing OPEN directly to the relay socket. This keeps authenticated git on the real owner relay after backend retirement.
- **Custody.** Startup rejects the existing platform-secret inventory and provider/OAuth credential environment names; owner-mode credential-vault access and local billing-store access refuse rather than create or read a box-local secret store.

### D3c decisions (connection lifecycle at the door and the broker)

Research rules applied: secrets stay outside the instance; the shared layer
keeps identity and the credential proxy; one writer per state. The founder
rule (2026-10-16): every auth type a provider allows, any provider, with no
provider-specific code.

- **Secrets enter only at the door.** The router serves the whole connection
  flow: `/app/model-connect/*` (begin, exchange, resume, deposit, inline
  begin/poll/cancel, generic OAuth begin/exchange, source sign-in),
  `/app/model-callback/*`, `/app/oauth/client-metadata.json` and
  `/app/connections`. Flow rows (preset PKCE, generic OAuth, DCR client
  cache, inline popup) live only in router stores. A secret, code or token
  never passes through a box, not even in transit.
- **One custody writer: the broker.** The router hands a secret to the
  broker over its private socket and never persists it. The broker writes
  custody in shared `.platform` storage, which no box mounts, and commits the
  grant. The box sees grants and credential-free source descriptors only.
- **Which center.** The door accepts a center only if the broker's admission
  map names the authenticated owner for it (`admitted_owner(center) ==
  owner`). This is an identity read, not a tenant-data read.
- **Connects started in a box.** A keyless (`none`) connect goes to the
  broker's `HTTP_CONNECT` on the box channel. Any connect that needs a secret
  returns a door link, the existing tap-to-connect, and the box's MCP and
  approval actions take no secret value. A key pasted into chat is not
  accepted; the slice 7 report names this change.
- **Completion.** The box learns of a finished connect from the broker ledger
  (`HAS_MODEL_SOURCE`, `GRANTED_RESOURCE`), with no push and no shared flow
  row in the box.
- **Refresh** runs only inside the broker (`ConnectionTokens`). The box's
  `REFRESH` frame and every daemon-side vault writer are deleted.
- **Disconnect and revoke.** Disconnect at the door, or from the box as the
  credential-free `DISCONNECT` op. The broker calls the provider's RFC 7009
  `revocation_endpoint` when its directory data names one, then erases
  custody.
- **Provider-agnostic.** Auth stays data: schemes `none`, `bearer`, `basic`,
  `header`, `oauth1a`, `oauth2` and `url_secret`. OAuth uses discovery,
  RFC 7591 DCR, the client metadata document and registered-client JSON.
  Provider-specific code is deleted with the CLI stack: the OpenAI device
  flow and its four routes, `llm_deposit` (Claude/Codex), `sk-ant-` checks,
  and the CLI branches of `provider_resolver`, `credential_vault`,
  `providers/base` and `api/universe` `set_engine`. A generic RFC 8628
  device flow is not built; no remaining HTTP provider needs it, and it can
  be added later as data.
- **Plans.** Stripe webhook and checkout write `.broker/plans.db`. Account
  admission writes an explicit `free` row. A missing row is an error, never
  read as `free`.
- **Hooks** already publish through the router (slice 2).
- **Plans at cutover.** The census copies each owner's tier once into
  `.broker/plans.db` from the authoritative source: Stripe's customer and
  subscription, as recorded today in the per-center `.subscription_state.db`
  that the Stripe webhook writes. If a paid tier can't be attributed to
  exactly one owner, the census refuses and the cutover stops. It never
  defaults an account to `free`. Unpaid owners get the explicit `free` row.
- **Existing connections at cutover.** HTTP connections (vault `http`
  records under shared `.platform/credential-vaults/<center>/` plus the
  broker's grant rows) are already outside every box, in the custody store
  this section keeps. They carry over in place, with the broker as their
  writer: no copy and no reconnect. CLI `llm_subscription` records are
  deleted with the CLI stack, so those accounts reconnect through the door.
  The census verifies the vault location on the production copy.
- **One model path, so in-process local models go too.** The direct Ollama
  vision call in image ingestion is deleted. Images keep their metadata,
  and a description comes from the owner's model through the broker.
- **The slice 7 report names, per account:** CLI connections lost, keys
  pasted into chat no longer accepted, HTTP connections carried over,
  `/mcp/hooks/` integrations to re-point to `/hooks/inbound/`, and the loss
  of local image descriptions, and billing status no longer showing seat
  and storage usage (plan limits remain).

| Research rule | 3c |
|---|---|
| Secrets stay outside the instance | Entered at the door, held by the broker; the box holds descriptors |
| The door never reads tenant data | The door reads only identity (admission map) and its own flow rows |
| One writer per state | Flow rows: router. Custody and grants: broker. Plans: billing at the broker |
| Isolation is the box, not shared checks | No per-owner filter is added; the owner comes from authentication or the bound channel |

### 3c-2 as built

`ADMIT_CENTER` registers new centers on the bound box channel. `/app/connections`
and `/app/model-connect/*` run at the shared door using broker admission and catalog
state, without owner-store reads. `HTTP_CONNECT` prepares, commits and rotates
custody; `oauth_offer`, `oauth_exchange` and `preset_exchange` keep discovery and
exchanges in the broker. Box connect and rotate actions take no credential:
keyless connects use the box channel; other schemes return the existing `/app`
link with `request`, `center` and connection ID. The browser posts credentials
only to the door. Commit and rotation record `request_ref` only at the door, with broker-stamped
`committed_via` derived from transport; the box channel rejects request references.
Only door-committed grants can complete asks. Box ledger polling
through `GRANTED_RESOURCE` matches the pending request, applies its uses, resolves
it and wakes the existing continuation. There is no push or router request read.
The lifecycle tests exercise all nine auth variants from a separate box process,
then disconnect at the door or on the box channel and prove the next call fails.

### 3c-2a custody and refresh

- `HTTP_CONNECT` retains prepare/commit. Commit carries `secret` and the
  prepared revision; the broker rechecks both ledger rows, writes custody and
  commits the grant in the same operation. The prepare request has an empty
  secret. `rotate` carries destination, incarnation and secret;
  `inspect_custody` returns only a refusal code. Box channels refuse secret
  deposits and rotations; keyless connects remain available.
- Vault writers and erasers live in `broker/vault.py`. Their lock files live
  under `.platform/credential-vaults/<center>/`, never in owner trees or
  provider-assignment state. Ownership rows live in `.broker/credential-custody.db`;
  C6 deleted custody tombstones. Task 6 migrates that metadata and changes
  directory ownership; there is no runtime compatibility reader.
- `DISCONNECT` erase removes custody before deleting the fenced ledger rows.
  C14 job step 1 writes erased admission once. Custody cleanup then erases
  vaults under the same locks as deposits and refresh, without another admission
  mutation. The daemon no longer
  stages credential directories during account deletion.
- `broker/tokens.py` resolves and refreshes OAuth credentials locally, under
  broker vault admission. The REFRESH/REFRESH_ACK protocol, daemon callback
  preparation and daemon OAuth-service refresh operation are deleted.
- Registered OAuth clients use the same trusted directory at exchange and
  refresh. The existing environment installer renders only `TINYASSETS_OAUTH_*`
  assignments into `/etc/tinyassets/broker.env`, loaded by the broker's Compose
  service. Router exchange still needs its existing client configuration;
  refresh never calls the router for a secret or token.
- RFC 7009 provider revocation is task 3c-2b, not part of this slice.

### 3c-2b connection controls and revoke

`/app/connections` is standalone at the shared door. Explicit centers use
`ADMITTED_OWNER`; omitted centers use the fenced `SOLE_CENTER` identity read
and require exactly one live admission. `CONNECTION_CATALOG` includes fenced
rows on request so interrupted cleanup stays reachable without an owner store.
`DISCONNECT` revokes the current OAuth token through the trusted directory's
RFC 7009 endpoint under the custody lock, then erases locally even on provider
failure. A failure leaves a revoked, credential-free connection receipt with
`revocation_error` and `custody_erased`; `HTTP_CONNECT` can replace it with a
fresh incarnation. Model-connect placement, box actions and ledger completion
are described in the 3c-2 as-built contract above.

### 3c-3 as built

- `broker.owner_reads.Plans` is the sole shared plan writer. Its `set_plan`
  (`apply_tier_event`) keeps ordered tiers, end dates, customer/subscription
  mappings and the owner's checkout attempt in `.broker/plans.db`.
  First center admission attaches that database and writes the explicit free
  row in the identity transaction. Further admissions never repair a missing
  account plan. Missing rows raise `PlanMissing`, including over `BOX_SHARED`.
- Router billing uses fenced `BILLING_PLAN` IPC because `.broker` belongs to
  the broker UID. Status, checkout and cancel use authenticated owner IDs;
  verified subscription events use the stored customer/subscription mapping,
  and first service-signed owner claims establish that mapping. Old signed
  center references require the mapping seeded by task 6. Checkout settlement
  resolves the stored session/attempt identity, ignoring client reference hints.
  Checkout retries retain their exact creation parameters and lease identity.
- Seat/storage usage is omitted from the billing response. Plan limits remain;
  billing does not need a private usage read to sell or cancel a subscription.
  Box quota reads and update preparation use `BOX_SHARED` plan reads, and broker
  provisioning reads the shared plan directly.
- `BOX_PUBLISH` copies a validated immutable definition into `.published/`,
  taking authorship only from the relay bind. Bundle ownership and idempotency
  stay enforced by the existing commons writer. `bundle_version` is a named
  shared read for later package publications. Private bindings may retain a
  public definition copy for their local foreign key.
- Public-run and broker catalog reads open only `.published/` and do not
  initialize schemas. Published SQLite data uses rollback journaling and is
  broker-written/router-readable. The box no longer writes a shared preview;
  uncached public previews render from published content without router writes.
- Task 6's census/copy and task 6a's remaining non-owner compatibility branches
  are not part of this slice. No deployment or independent review is claimed.

## D3. Lifecycle (boxhostd)

- boxhostd supports one persistent sandbox per owner, running the owner
  daemon with its socket bind-mounted to `/run/tinyassets-boxhostd/<uid>/app.sock`.
- It wakes on a router request or on the waker. It sleeps after N minutes
  idle with no running turn, and the daemon flushes on SIGTERM.
- It restarts on a crash, with backoff.
- Memory: each box gets a cgroup cap. A cap on concurrently awake boxes
  queues wakes (8 GB host).

### D3 as built (slice 4)

- **One lifecycle writer.** `boxes/owner_daemons.py` runs in boxhostd. The
  router uses its control socket for wake/stop and the in-box activity status
  supplies idle truth; the per-call file/exec backend is deleted.
  Startup's existing private runsc census stops survivors before accepting work.
- **Native public bridge (6a parts 1f/1g).** The admitted owner startup constructs
  OwnerBoxd with its relay and one `owner_public_egress` listener at
  `127.0.0.1:3128`. Its bounded launcher supplies proxy defaults to bash,
  provisioning and probes; authenticated git uses its invocation route and
  the standard relay handshake. Public admission is owner-keyed through
  `box_admission.resolve_owner`: only `{op: BOX_PUBLIC, principal: owner}`
  reaches the broker, which verifies the permanent mapping and box record,
  refuses revoked/deleted or suspended owners, and admits boxes with zero
  centers. Center-bearing BOX_PUBLIC is invalid at the relay; center-scoped
  BOX_CONNECT still uses `resolve`. Destination pinning and caps remain
  broker-owned. SIGTERM for shutdown/idle sleep reaches
  OwnerBoxd.close, which cancels children and closes/joins all proxy streams
  and threads before exit; the next wake can immediately bind the fixed port.
- **Control socket and admission (b6).** Only broker UID 1002 sends bound wake
  records and plain UID lifecycle operations to `daemon.sock`. Router UID 1001
  can read status but cannot start, stop, restore or destroy. The broker derives
  UID, quota, centers and persistent secret from the owner's binding under its
  owner lock, reconciles pending volume work, and supplies the committed launch
  generation. Root administration enters through broker `LIFECYCLE_ADMIN`.
  Four listener workers remain reserved for broker execution and eight slots
  bound scheduled waiters. Missing bindings refuse; no executor callback exists.
- **Launch.** runsc starts `/opt/venv/bin/python -B -m tinyassets.owner_daemon`
  with `--socket /run/owner/app.sock --relay /run/boxhostd/relay.sock
  --secret-file /run/boxhostd/secret`. The only data root is `/home/owner`.
  The app socket is exported in `/run/tinyassets-boxhostd/<uid>/app.sock`;
  ephemeral secret files live in the root-private `secrets/` directory and are
  owner-readable mode 0400, mounted read-only. The owner UID/GID, empty capability
  sets, static PATH/HOME/browser environment, null network and cgroup limits are
  explicit. `--host-uds=all` allows opening the bound relay and creating the
  exported socket. No host credential environment is inherited.
- **Defaults.** Idle threshold 900 seconds (`TINYASSETS_BOX_IDLE_SECONDS`),
  awake cap 6 (`TINYASSETS_BOX_AWAKE_CAP`), memory 1 GiB per box
  (`TINYASSETS_BOX_MEMORY_BYTES`, existing supported sizes 1 or 2 GiB).
  The service's aggregate MemoryMax is 7G. Interactive wakes evict the least
  recently used idle box; scheduled wakes wait without eviction and behind
  interactive tickets. Wakes have a 60-second capacity/readiness deadline.
- **Idle and turn truth.** `owner_activity` counts HTTP requests and all turns
  through `control_plane.session.start`. Secret-authenticated local
  `GET /_box/status` reports active count, idle seconds and drain/revoke state.
  `POST /_box/drain` atomically closes admission only when no work runs and the
  requested idle threshold has passed. New requests and turns then refuse.
  Boxhostd uses this handshake before idle/LRU shutdown; unknown status never
  authorizes eviction. SIGTERM uses uvicorn's existing lifespan shutdown and
  closes the local tools. Passive MCP subscriptions do not hold the work gate;
  graceful shutdown cancels remaining subscriptions after ten seconds. A
  30-second flush timeout is loud, never treated as a successful sleep.
- **Crash clock.** The existing one-second host loop checks runtime liveness,
  sleeps idle boxes and restarts crashes. Backoff is 1, 2, 4, 8... seconds,
  capped at 60; after five consecutive failures, wakes fail immediately with
  a loud status while the host continues retrying without human intervention.
  A box surviving an idle interval resets its failure count. A failed launch
  whose cleanup cannot prove absence keeps its capacity reservation and is
  never advertised as ready.
- **Wake serialization (b6).** Broker owner locks cover wake and withdrawal.
  Router forwarding uses the installed route; the unused host resolve/release
  operations and Box lease state are deleted. Owner Activity gates still prevent
  idle shutdown or rollout from interrupting active work.
- **Deletion drain.** The authenticated internal deletion route closes the same
  activity gate used by requests and turns before waiting on their completion condition for up to twenty seconds
  for active work. It admits only deletion/control calls while draining. A bound
  timeout reports `drain` unfinished; a retry may finish quiescence. No sweep
  starts while a turn can still write.
- **Deletion (b6).** The broker identity database holds job progress and billing
  receipts, written through off-host before effects. Enqueue executes the job;
  the existing waker tick retries due local rows. Erasure, channel closure and
  withdrawal publication precede all host calls. Plain `stop`, `destroy` and
  `delete_snapshots` never mutate admission. Status and enqueue never read the
  remote. Platform recovery imports the off-host job through the same broker
  path; closed-startup sequencing remains b5.

- **Linux proof.** The oracle has no runsc. Lifecycle/OCI tests use the existing
  runtime-double pattern; the router process fixture now launches actual owner
  daemons through boxhostd, including wake, HTTP serving, SIGTERM flush, secret
  rotation and retirement. The real control-socket proof runs as root with an
  unprivileged router client. Native gVisor/XFS/cgroup integration still needs
  the deployment venue; unit/protocol results are not that proof.

## D4. Waker

- At the end of every tick the box reports its `next_wake_at` to the
  broker. The shared waker wakes boxes when it comes due.
- Suspended accounts (billing or abuse) are never woken.
- Delivery is at least once, so the tick is idempotent, as today.

### D4 as built (slice 5)

- **Bound report.** Every owner `AssignedQueueConsumer.poll_once` finishes by
  computing the local minimum and sending `NEXT_WAKE {"at": epoch_seconds|null}`
  over `BOX_SHARED`. The relay's bind supplies the owner. The broker accepts
  exactly `op` and `at`, rejects non-finite or non-numeric times, and rechecks
  live admission on every report. No request can select an owner or UID.
- **Rows remain inside.** The projection reuses automation due rules (including
  cron zones, one-shot retries, retirement and pause), proactive trigger cadence
  and active hours, pending agent wakes, bound-result continuations and answer
  delivery retry timestamps. It reads only this box's stores. An empty or paused
  schedule reports null. A report failure is a failed tick with a logged error;
  it never clears the last broker value.
> **Superseded by T7/C9.** The index is `(owner, next_wake_at)`. The box reports null when paused or empty, a missing row triggers reconstruction, and there is no daily floor. Duplicate delivery and retry behavior is retained.

- **Index.** The broker is the only writer of `next_wakes` in
  `.broker/state/next-wake.db`, beside its identity database. Its columns are
  `owner`, `next_wake_at`, `reported_at`, `last_woken_at`. Reports replace only
  the reported time; successful wake acknowledgements replace only
  `last_woken_at`. Due delivery never consumes the report, including across
  broker restart or a report racing a wake acknowledgement.
- **Host process and defaults.** The waker runs in `broker.process`'s existing
  asyncio loop after boxhostd registration. That process already owns the
  index and authenticated boxhostd control calls; it needs no polling RPC or
  second index writer. It scans immediately, then every 30 seconds
  (`--wake-poll-seconds`). It considers every live admitted owner whose report
  is due or whose last successful wake is at least 86,400 seconds old. A never
  reported/never woken owner is eligible immediately. Retired centers and
  permanently deleted owners are excluded through the admission map.
- **Capacity.** Every dispatch uses `ensure_awake(owner, scheduled=True)`.
  The broker UID may request only scheduled wakes. At most two control calls
  are in flight, leaving two of boxhostd's four broker workers for retirement;
  the existing scheduled queue remains behind interactive requests. Slow
  control calls do not block the scan clock. Failed calls remain due and are
  logged, and attempt ordering prevents a failing owner starving later owners.
- **Suspension source.** `.broker/plans.db` is the single source. The existing
  free/paid schema had no suspension state; it now also holds independent
  `billing_suspended` and `abuse_suspended` booleans, initially false on explicit
  admission. The shared door can change them through fenced `BILLING_PLAN`
  `set_suspension`; boxes cannot. Tier updates preserve both flags. Missing
  plan rows raise `PlanMissing`, never imply unsuspended. Both the waker and
  boxhostd's broker launch admission check suspension, including after a
  capacity wait. Production passes the same plan store to identity admission,
  billing and the waker, avoiding a second plan file under `.broker/state`.
- **Linux proof.** The slice 4 launcher runs a real owner daemon process with
  the runtime double, a file clock and the existing automation `_execute` seam.
  The box reports a future time, boxhostd drains and stops it, the shared waker
  starts it without an incoming request, and the real tick claims and settles
  the automation and reports its next occurrence. A duplicate restart at the
  same instant leaves one execution and one durable attempt. Fake-clock tests
  cover the daily floor, null/future reports, missing plans, both suspension
  reasons, retirement, retry after failed delivery and bounded concurrent wakes.

## D5. Billing

- Stripe webhooks land at the router. Plan state is kept in the shared
  billing store, keyed by `owner_id`.
- The box reads its tier from the broker. It never holds a Stripe secret.

## D6. Migration (one cutover, after a verified full backup)

The one migration builds each admitted owner's box root inside its own attached
volume, copies center trees and per-center platform files, and extracts that owner's
rows from shared root databases with their table/index/trigger schemas. It
seeds the router, broker and published stores, regenerates platform-local state,
verifies attribution totals and SQLite integrity, detaches the volumes, publishes
their verified initial snapshot references, removes host owner trees, and publishes
the final owner-daemon marker.

Every row and file must be attributed to its destination, explicitly dropped,
or explicitly regenerated. Anything else refuses the cutover. The `--archive` rehearsal on the private production copy must:

- show the per-owner row counts summing to the original totals;
- show each owner daemon serving that owner's history, pending requests and
  pause after the move;
- prove a restore of the paired backup returns today's layout.

### D6 as built (slice 6; synthetic harness accepted)

The lead's archive command is:
`python scripts/rehearse_box_move.py --archive <absolute-tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz> --image tinyassets-c6-rehearsal:s6-<head>`.
The archive must retain the `tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz`
filename. The image must already be built from the checked-out head; setup
refuses with `image not found: <tag>` before creating any volume otherwise. Every
container runs without a network. Development uses generated archives only.
The synthetic root end-to-end run passes every step, including both crash
boundaries, reconnect confirmation, the post-write restore and report privacy.
The harness creates disposable Docker volumes and a real XFS loop mount with
project quotas. It calls deploy's `box_migration_container` directly through
the script's explicit disposable runner, sharing caps, mounts, tmpfs and memory
limits. Both paths invoke read-only preflight, refusing census, ordinary apply
and final manifest/quota/marker verification. Apply verifies before sealing the
final owner-daemon marker; the extra verify command reads that sealed output.

The venue bind mount becomes private before mounting XFS, preventing propagation.
Cleanup removes containers, then discovers every mount of the venue loop device
and every venue bind mount in the engine namespace. It unmounts deepest first,
using lazy unmount only after ordinary unmount fails, detaches the loops and
asserts no venue mount or loop remains before removing any volume. A failed
assertion retains the volumes and fails loudly, including after partial setup.

Admitted centers' LanceDB directories and knowledge SQLite stores (including
sidecars) are owner data copied byte-for-byte, never regenerated. Only runtime,
consumer-liveness and workspace-staging artifacts are regenerated. The legacy
root `knowledge.db` moves whole only when every row in its known knowledge
tables resolves through its memory-scope center/user columns to one admitted
owner. Unknown/unscoped or competing ownership is a counted `no_owner_key` or
`conflicting_owners` drop; no default owner is inferred. Knowledge SQLite sidecars follow that disposition. Root `lancedb/` is always
dropped and counted as `unattributed_root_vectors`; it never inherits a knowledge owner. Census reports knowledge files and bytes per
owner and reason-coded dropped files and bytes. C12 receives the originals.

An uninterrupted generation establishes manifests, then the real paired
restore validates the original source bytes, converts through the same forward
cutover, and verifies the restored owner-box generation. Two independent disposable
source fixtures then crash at `after_owner_tree:1` and
`after_seeding`. Each resumes to the uninterrupted shared and owner manifests.
The test-only `CUTOVER_CRASH_AT` hook calls `os._exit(137)` inside apply; the
former self-SIGKILL was ignored because Python was namespace PID 1. Deploy
and restore entry points refuse the variable before host actions and override
image defaults with an empty value. Only the explicit disposable runner can
forward it, after checking the generated volume family and mountpoints.
Serving starts the real owner lifecycle manager, apps, router and broker with
kernel UID peers. This process venue uses private mount namespaces and the
fixed owner mount; gVisor isolation remains separate acceptance. Every admitted
center reads history and pending requests, pauses/resumes, reads its broker plan
and writes history. Reconnect confirmation uses the real shared model-connect
door and model-use reconciliation on a disposable shared-store clone: a new
connection changes shared custody authority. The original serving generation is
then disaster-restored with both acknowledgements after a normal restore has
refused the live traffic marker. Reports expose only fixed step names, numeric
counts and PASS/FAIL, one line per reached step:
`<step> PASS [<counter>=<nonnegative integer> ...]` or `<step> FAIL`.
The eleven steps are setup, preflight, census, apply, verify,
restore_before_write, crash_resume, serving, normal_restore_after_traffic_refuses,
disaster_restore_after_traffic and cleanup.
The synthetic run records 2 owners, 4 centers, 27 census rows, 3 manifests,
2 interruptions and 2 resume comparisons; serving records 4 history reads,
4 history turns, 4 pending reads, 4 saved requests, 8 controls, 8 plan reads,
4 verified writes and 2 reconnect confirmations. Normal rollback compares the
exact original pair. Disaster recovery rejects old write/bash identities under
fresh handles, the old broker outbound ULID and all three stale API-key, session
and hook credentials. It also proves complete owner erasure. Tests scan stdout
and the report for identifiers, paths and content, enforce numeric counters,
and check the redacted failure report.

Synthetic proof venue:
`python scripts/linux_oracle.py --as-root --docker-engine -- -q --no-header -p no:cacheprovider tests/test_box_move_rehearsal.py`.
The Docker-engine opt-in is root-only and leaves ordinary oracle invocations
without an engine socket. Task 6 remains open for the lead's archive run and
the remaining acceptance, regardless of synthetic results.

`box_layout_migration` is the sole cutover module.
Host admission reads the canonical root-owned erasure ledger by principal digest.
Broker custody digests provide local enforcement; they never select retries or override ledger authority.
Seeding creates the broker-owned credential directory even when no HTTP
credentials survive, so a source-free owner can reconnect after cutover.
Provider definitions remain private owner files (0600); registration already
publishes the credential-free descriptor through the broker channel. An owner
process never changes a definition file to the retired broker-reader group.
Inference source validation runs only in the broker's accounting operation and
reads only that published descriptor plus the live grant, regardless of the
accounting row's liveness-token prefix. No broker owner-file reader remains.
Cutover quota planning converts the legacy tier from the read-only census with
`usage_policy.account_type`; the runtime account-type API reads the admitted
box's broker plan and is not a migration reader.

The census reports six classes:
owner, router, broker, published, dropped and regenerated, with per-table totals,
per-owner counts and report-local account labels. Policy account paths are also
pseudonymous. The broker admission map is authoritative; creation/home bindings
only corroborate it. An unknown table, conflicting ownership, unclassified file,
unattributable paid tier or HTTP slot without its owner's connection refuses.
The census materializes attribution columns, never history/checkpoint payloads.
An admitted center attributes its rows to its owner even when their named
principal is unadmitted, an earlier identity or a platform actor. This applies
through reviewed parent joins as well as direct center keys; another admitted
owner still conflicts and a missing parent still drops as `orphan_reference`.
`universe:<center>` actors resolve through live admission. Branch versions use
the admitted definition author, otherwise their admitted publisher, otherwise
drop; their own snapshot survives an absent definition. Schedules resolve
through their definition or version. Competing admitted owners still refuse.
Interchange receipts/links join their stage and receipt, and
published stages join their created definition's author. Idempotency uses its
operation to select stage, receipt or definition. Checkpoints can join several
runs only when every run resolves to the same owner.
The apply streams payload rows and rechecks their census attribution.

Dropped state includes the listed pre-ownership runs and dependent records,
never-admitted centers without owner bindings, unowned rows in the reviewed
small owner-table sets, retired CLI records/homes and the stale database copy.
The stale copy is never opened and remains only in the paired snapshot.
Owner leases, fences, accounting counters and branch-version migration records
are recreated empty in owner stores. Center-keyed event/delivery/lease records
use the admitted center or are explicitly counted as dropped. Each unresolved
row gets exactly one reason, counted per table. Precedence is
`conflicting_owners`, `orphan_reference`, `unadmitted_principal`,
`unknown_center`, `no_owner_key`. Conflicts refuse; the other four explicitly
drop. A known center without admission reports `unadmitted_principal`;
an absent center reports `unknown_center`. Missing-parent and parent-owner
failures propagate to dependents. An admitted center, including one reached
through a parent, clears unadmitted-principal, unknown-center and
ownerless-parent evidence. An unadmitted actor alone cannot invent an owner.
Dropped versions and schedules
appear under their candidate owner's `workflow history` with separate version
and schedule counts. Unresolved usage rows retain their drops, described as
`usage reservation history` and `provider bindings`.
Candidate owners survive other failures so a broken key cannot mask conflict.
Permanent identities alone do not admit a principal. Binding/admission integrity
checks and unknown-table/schema-review refusals remain. Foreign-key violations
on explicitly dropped rows are accounted losses; violations on retained rows
still refuse. No owner is invented.

File placement follows exact code-backed locations: broker identity, plan,
policy, outbound, custody and operation databases; broker fence/deletion
markers; legacy hashed browser keys beside either outbound ledger; router
provenance/release markers; regenerated SQLite sidecars, custody/refresh locks,
PID/liveness/heartbeat state and interrupted atomic-publication files. Real
SQLite files retain table-by-table schema review. Unknown files still refuse
where required and expose only `<file>.<ext>` (or `<file>` with no conventional
extension), with code-constant filenames allowed verbatim. No unknown platform
vault index or key is assumed safe merely from its extension. The round-6
identifications classify all 16 supplied files in synthetic proof; only the
lead's next authorized census establishes the real-data result.

Broker egress audit JSONL files and architecture signing state remain broker
files. The three door handshake files remain private router state; neither
their names nor contents appear in reports, and they never enter boxes.
The engine route table, reset barrier and blob-root lock regenerate. Stale
backup WAL/SHM files drop without opening them. Executor configuration and
external-agent refresh markers drop under `CLI login state`.
Run-file bodies resolve through `run_file_objects.storage_key + '.body'`,
matching the reader and cleanup code; they move or drop with that row and
their losses are counted. Objects also resolve their operation parent.

The transaction splits `.scheduler_fires.jsonl` by admitted `universe_id`
and `ledger.json` by admitted actor, falling back to admitted target. Each
owner gets the original relative filename at its box data root, preserving
entry order and the JSONL/list shapes. `api.engine_helpers._append_ledger`
and `api.branches._append_global_ledger` use this root ledger path. The retired
scheduler has no current reader; its same-path file preserves owner history.
Unknown/non-admitted entries drop with explicit counts. `history_splits`
reports `scheduled-run history` and `activity ledger`, per-account entries,
original totals and drops; `workflow_history` reports carried version/schedule
counts. `loss_summary` separately lists dropped history entries.
Both splits read back counts and content and require per-owner totals plus
drops to equal the original count before publication. Unpublished files rebuild
on resume; `after_scheduled_history_split` exercises a real exit between them.
Only verified publication removes the source histories and custody bodies.
Reports also list all five accepted user costs from the founder's decisions.

The transaction holds the exclusive layout lock and verifies a fresh paired
snapshot before its first write. A root-private `.owner-cutover.json` records
build and publish phases, the snapshot digests, owner plan and file manifests.
Build leaves the original stores intact. Each owner gets its centers,
`.agent-sessions`, `.universe-sidecars`, attributed transcripts and extracted
root databases. Shared staging contains router identity/keys/OAuth/sessions,
hook digests, broker admissions/box records/policy/outbound/custody, explicit
free or paid plans, published records and an empty next-wake index. Legacy
principal tombstones become the broker's digest-only schema; restore authority
checks read that final custody store. HTTP vault
slots stay in their existing custody location, with broker ownership; CLI slots
are filtered out. Regenerated rows are not copied.

After row reconciliation, SQLite integrity checks, rsync checksums and durable
file manifests, publication uses atomic replacement on the destination
filesystem. A crash in either phase resumes against the same snapshot pair;
a changed verified output or different pair refuses. Only verified publication
removes the host trees and writes `owner_daemon=1`, `box_layout=owner-box` and
`state=stable`. Completed application is idempotent. The deploy's step 3d keeps
its canary, idle-turn, mount, preflight and fresh-paired-snapshot gates and uses
this transaction once on a forward deploy. An intermediate owner-box marker
without the final owner-daemon version now refuses.

Deleted P2b paths: credential copying into host custody, CLI agent-home copying,
and the second activity-conversion path and its helpers. Their whole obsolete
tests were retired; the independent retired-workflow guard remains.

Synthetic Linux proof covers two owners with two centers each, every census
class, legacy runs/checkpoints/transcripts, a never-admitted center, plans,
custody, shared key/hook authentication, crash/resume and paired restoration.
The restore uses the real shell/SQLite/tar implementation; only host service,
mount/quota and absent sandbox-runtime queries are modeled. Normal restore returns the original pair exactly before traffic. After traffic,
only acknowledged disaster recovery may reset credentials, fence operations and
reconcile erasures. Services remain stopped on return.
This is not real XFS quota, sandbox, deployment or production-data proof.

The process rehearsal starts two real owner apps against the migrated roots
using the existing process launcher. Production HTTP routes return each of four
centers' conversation history and pending requests. An authenticated test probe
calls the real chat-history and pending-request writers and pause/status/resume
operation, and reads each owner's migrated
plan through the real relay and broker. Foreign-center history is absent. The
rig substitutes the fixed mount and broker peer UID, not these application
operations; it does not prove native sandbox isolation.

The platform-wide `outside_switch(id, enabled)` is router state:
`OutsideClientAuthority.set_enabled` has no owner argument, and `admit` reads
id 1 before checking any owner's grant. It is never replicated into boxes.
Device registrations, authentication receipts and UI preferences have no
session/connection-to-owner foreign key: only their recorded authenticated
subject can carry them. Reviewed rows without any real ownership key are
counted drops, including the retired public-reader marker; no default owner.

Legacy center-local `.credential-vault.json` files are counted drops and never
opened or copied by census. The old `credentials_outside_broker_custody` count
was files, not slots. Retained HTTP slots stay in the platform vault, with
broker UID ownership and custody metadata. CLI records/homes are counted drops.
Census emits an explicit all-accounts reconnect line when carried HTTP is zero,
and points to door model-connect plus model-use confirmation.

Every enumerated file has a placement (including refused/unattributed), a
masked directory pattern and count. `file_pattern_kinds` pairs each pattern
and placement with counts by `admitted_center`, `unadmitted_center`,
`unknown_dir`, `account_hash`, `broker_account`, `platform_store`, or `root_file`.
Registered shared roots keep their code-defined placement. Other unknown
and never-admitted directory contents drop; an account hash is never an owner
default. Unrecognized root, account, broker and platform files still refuse
with their kind. Identity/plan databases are extracted by their reviewed table
rules, policy deletion markers and the broker fence stay broker-owned, and
custody/refresh locks regenerate. Browser-vault keys belong outside this data
root; an arbitrary key file here refuses.
`loss_summary` groups reason and description by pseudonymous account, with
explicit dropped class and row/file counts: e.g. `CLI login state` and
`orphaned budget reservations`. The separate unowned bucket never invents an
account. Existing `losses` retains aggregate tables and masked patterns only.
Sessions and consent/seed sidecars follow
their center's admission. Center LanceDB files are preserved; provider runtime, process locks,
PID/liveness files and checkout staging are regenerated; legacy scratch and
root activity logs are dropped. Arbitrary owner filenames remain verbatim.
Unclassified root files refuse. Classification also drives output exclusion and
source cleanup, so regenerated/dropped files cannot silently survive the move.

Task 6 remains unchecked pending the lead's real-data census, real-mount
rehearsal and CI shards. Synthetic proof cannot assert the remaining real-data
refusal counts without that rerun.

## D7. Guard and deletions

- Guard test: the router package imports no owner store module and opens no
  path under `data_dir()` other than its own shared stores. The D7 AST scan in
  `tests/test_router_store_guard.py` permits runtime-mode predicates only at
  the 29 reviewed file/function role boundaries, each with its reason. It
  resolves import aliases, captured runtime/mode variables and delegated
  broker predicates/store refusals. Branchless owner identity/tool reads and
  the runtime module's own lifecycle are not alternative execution paths.
  Temporary module-copy mutation tests prove new forks are detected.
- Deleted: per-center host dirs; `box_tools` exec bridge; `file_binding`
  owner routing; daemon-side `_call_writer` placement; cross-owner listing
  filters; multi-owner tick loop.

## D8. Shared stores stay shared, owner stores move (review gap 1)

Published workflow definitions retain their authoritative row in the admitted
author's box and a published copy in commons. This matches runtime publication:
`publish_requests._flip_if_unchanged` updates the local definition, then
`_publish_snapshot` sends the frozen publication through the broker. Census counts these
local published definitions per owner separately from exclusive source placement.
Apply verifies local workflow references before publishing its completed marker.

> **Superseded.** Owner recovery follows C2, platform recovery follows C3 and old-layout cutover rollback follows C4.

Restore follows **Restore: pre-traffic rollback, post-traffic disaster** above.
Normal cutover rollback restores the original pair while unioning the erasure ledger. Disaster recovery
resets credentials and fences operations before any box starts.

The migration classifies every table of every shared root database:

- **Router store:** identity and WorkOS mapping, API keys and their owner,
  MCP OAuth clients and tokens, inbound hook token → owner, the canary
  account, published commons records.
- **Broker store:** `owner_identities`, `center_admissions`, `box_records`,
  credential custody, billing and plan state.
- **Owner box:** every table whose rows belong to one owner, split by owner
  principal or center id.

The census reports router, broker, owner, published, dropped and regenerated
classes with per-table counts. Dropped legacy state and never-admitted center
state are excluded from the move; platform leases, fences and accounting are
regenerated. Unknown tables, conflicting ownership and any item outside those
explicit classes refuse. Shared tables are not split.

## D9. Router state, the per-box secret and the socket (review gaps 2, 3)

- `/hooks/inbound` and `/api` keys resolve to an owner from the router's
  own maps. That is router state.
> **Superseded by T1/C5.** The router forwards from its installed routing snapshot. Control-plane admission publishes routes.

- The router resolves owner → box socket only through broker admission
  (`owner_machine`), never from a request field.
- The broker mints the per-box secret when the box is admitted and rotates
  it on every box start. boxhostd passes it into the box as a read-only file.
  A restored box gets a fresh secret on its first start.

## D10. Waker floor and the interactive priority (review gaps 4, 5)

> **Superseded by T7/C9.** There is no 24-hour floor. A paused or empty box reports null and is not woken periodically.

- The waker keeps the last reported `next_wake_at` until it is replaced, and
  wakes every admitted, unsuspended owner at least once every 24 hours. A box
  that dies before reporting is still woken.
- At the awake cap, an interactive request evicts the least recently used
  idle box. Scheduled wakes queue behind interactive requests.

## D11. Deploys, canary and rollback

C12 is implemented by `scripts/roll_owner_boxes.py`: public canary wake, immutable
image extraction, one broker deployment call, ordinary sleep, then public canary
acceptance. Extraction never runs the image or mounts owner data. The broker
selects the image and serializes each awake owner's ordinary sleep with wake,
restore and withdrawal. The host executes the bound operation. New work receives
a retryable response after the in-box Activity gate closes; existing request and
background turns finish before flush, detach and snapshot. Unknown activity or a
busy turn reports an incomplete drain and remains pending on the normal lifecycle
path. An incomplete result fails acceptance. Healthy ordinary owners remain
asleep until a request or schedule wakes them on the current image.

The static image's `ACTIVE_BOX_LAYOUT` declaration still rejects pre-cutover
images. Compatible rollback means republishing the previous image and draining
again. `images.json` contains one current image; there is no per-owner image
history, rollout or automatic rollback. Root calls the broker; only the broker
can select the host's image. No host callback, new clock or authority store is
introduced.

Canary preflight reaches `/app/me` through the authenticated public router, so
the canary participates in the ordinary drain. After successful control, canary
acceptance signs in again, checks its configured owner/home, bound UID and baked
build revision, and completes a nonce model turn through public MCP. Missing
evidence fails. This uses no canary fixture endpoint. The driver writes counts of
`slept`, `asleep`, `incomplete` and control/canary outcomes; it records no owner ID,
private error, credentials or model reply. Existing extraction, SSH and socket
bounds remain. No workflow has been enabled, dispatched or run.

C4's deployment-owned pre-traffic rollback and C3's shared-platform recovery are
unchanged. The router holds neither rollback eligibility nor recovery authority.

## Billing and deletion at cutover (review round, 2026-10-18)

Billing remains shared-layer state. The census derives each paid owner's verified
customer/subscription mapping from retained billing records and carries it into
the shared plan. Missing or conflicting authority refuses with counted reason
codes on the pseudonymous account report; a paid plan is never seeded without
its mapping. Every legacy attempt with a session ID becomes a permanent
`checkout_settlements` mapping, including expired leases and settled sessions.
The one active lease remains in `plans.attempt` for checkout resumption. Only
sessionless attempts drop, with `dropped_sessionless_attempts` counted per account.
Webhooks resolve the permanent session mapping; deletion queries every retained
mapping before declaring billing absent. Settlement never deletes the mapping.
Malformed or conflicting mappings refuse census.

The erasure ledger fences checkout creation, serialized with in-flight checkout
and webhook processing. Billing cleanup expires outstanding sessions through the
Stripe adapter, confirms any resulting subscription cancelled, and records a
distinct expired-attempt outcome. A subscription delivery for a tombstoned owner
immediately cancels and records that subscription. Deletion succeeds only after
no payable checkout or live subscription remains; uncertain outcomes stay retryable.

> **Superseded by C1–C3/C6.** Owner restore leaves platform lifecycle authority untouched. Platform recovery restores durable admission and resumes unfinished deletion jobs. Cleanup addresses opaque volumes and snapshot objects, never owner-tree paths.

Both restores union ledger events into staging. Post-publication cleanup and
boxhost startup retry all recorded homes and quarantines. Permanent erased UID
reservations are carried even without live identity rows; conflicting reservations
refuse restore. Forward migration records new obligations for copied erased homes.
Lifecycle maintenance contains suspension, refusal and authentication failures per
owner, reports an explicit suspended outcome and stops/refuses only that box.
Deletion addresses only a live box and permanently latches its ingress against new work.
Sleeping and suspended owners go directly to confirmed full destruction without a box start.
Background execution entry points hold the same activity gate as turns, so drain
observes running work and queued work cannot start after quiescence. Background
work holds this drain gate without renewing the user-idle clock: a periodic poll
cannot keep an otherwise idle box awake forever, and active work still prevents
sleep. No owner-store reader or compatibility path is added to the door.

## Founder decisions (2026-10-16)

- Build what the research says to build.
- A co-admin of a shared center is refused, as the research recommends by default. Sharing is by publishing.
- Features not ported into the owner daemon at cutover are dropped. The list is reported to the founder before the cutover deploy.

### 3c review corrections

- A credential-free grant records its commit transport (`door` or `box`), chosen
  by the broker. Only `door` plus the matching request reference completes an
  existing connection ask; a keyless agent connection cannot approve model use.
- Model receipts continue in the browser to the protected owner approval endpoint
  in the box. It verifies the live door grant, prepares only credential-free
  model metadata and raises the existing `bind_model_access` ask. The owner then
  approves that ask. Existing automatic selection ranks all accepted sources;
  the first source powers the default agent. No secret or flow enters the box.
- Failed provider revocation still erases custody, but returns
  `removed_locally_revoke_failed` and a durable provider recovery message.
  Since the token is erased, recovery is manual revocation at the provider,
  named from directory data; retrying removal retains that outcome.

## Founder decisions (2026-10-17)

- **Fresh backup for the final rehearsal: approved.** The lead pulls a fresh backup of today's production data read-only onto its own machine, never shown to an external model, runs the final rehearsal network-less, and reports only redacted pass/fail and counts.
- **Unattributable legacy data is dropped**, and the cutover report lists it with counts. That covers pre-ownership runs (Aug 5–19), never-admitted centers, unknown-center runs, orphaned usage records and unknown-directory files. Nothing that has an attributable owner is dropped silently.
- **The cutover's user costs are accepted.**
  - Every account reconnects a model source; CLI connections are retired.
  - Keys pasted into chat are no longer accepted (tap-to-connect).
  - `/mcp/hooks/` integrations re-point to `/hooks/inbound/`.
  - Local image descriptions are dropped.
  - Billing status no longer shows usage figures.
- **The cutover deploy goes ahead when every gate passes**: a clean census, the real-data rehearsal passing on a fresh backup, the lead's receipt on every slice, green CI, and the report delivered. The founder's condition: "Just make sure the architecture isn't sacrificed on review rounds or hard parts." No weakened shape, compatibility path or skipped gate gets a slice through.

## Founder decisions (2026-10-19)

- **Build the researched shape before cutover.** The founder's go covers the whole
  way to deploy; builders and reviewers must keep the architecture clean.
- **Every approval routes through the lead's session.** Approval-gated steps
  (spending, droplet changes, the deploy) are executed there.
- **Storage spending:** up to $15/month for the block-volume box store and Spaces
  snapshots, plus the C10 scaling design.
- **User-visible changes accepted:**
  - account deletion completes in the background, with a status page;
  - an owner asleep more than 7 days gets a few seconds of cold restore on their first request;
  - a box awake more than 24 hours pauses briefly between turns for its snapshot;
  - everyone signs in again and reconnects after a platform disaster recovery.
- **Design questions** that research.md and this design answer are decided by the
  lead and reported afterwards. New user-facing questions go to the founder.
- **The deploy may run at any hour** once every gate passes.
- **The old on-server box store copy** is deleted only after every C6 fix is done
  and 24 hours pass with no new related issue.

## Index-only corruption at cutover

`sqlite_integrity.check` is shared by read-only census and stopped apply (including
the deploy and rehearsal apply container). Census reports named repairable indexes
without opening the source SQLite files. After the paired backup is verified,
apply repairs exactly those indexes and verifies integrity again, recording
`reindexed: <db>/<index>` in its durable report. Any other integrity error refuses;
no table repair or blanket REINDEX is permitted.


Stopped apply must preserve committed WAL/SHM bytes and directory protection while repairing named indexes; rehearsal reset must restore archived root metadata.
