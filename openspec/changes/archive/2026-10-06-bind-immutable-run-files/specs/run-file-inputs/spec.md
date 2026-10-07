## ADDED Requirements

### Requirement: Ordinary app users can supply exact owned bytes without internal handles
The app paperclip SHALL offer authenticated bounded raw-byte upload directly into the same immutable run-file custody, returning existing opaque references rather than requiring an authoring draft, hidden tool or operator-created file handle.
The proposed app-only upload SHALL derive owner and current home from authenticated
request context, require exact allowed origin and an anti-form custom metadata
header, reserve capacity before body ingestion, verify actual bytes/size/digest,
and recheck current home/admin/tombstone authority before commit. There SHALL be
no new artifact store, public bearer URL, workflow execution or upload charge.
Unknown capacity or incomplete home SHALL refuse before reading the body.

#### Scenario: Fresh signed-in app user attaches binary files
- **WHEN** an ordinary user selects binary and empty files in the paperclip with no prior authoring session or handle
- **THEN** successful uploads return exact file metadata and the app relays only opaque reference metadata through its existing conversation request
- **AND** their agent can author and run a declared file workflow without operator setup, byte reconstruction or private workflow edits

#### Scenario: Partial, cancelled or uncertain upload
- **WHEN** one selected upload fails, disconnects or has an unconfirmed response
- **THEN** the app does not silently send only a subset; it preserves the text draft and offers explicit retry/removal
- **AND** the same label and exact request recover committed references without copying again, while unfinished operations report recovery debt without restarting implicitly
- **AND** successful siblings remain independently owned; workflow admission still atomically validates and binds the complete submitted bundle

#### Scenario: Existing text and expired metadata
- **WHEN** a user sends a mixed text/binary attachment message or restores a queued message
- **THEN** existing accepted text attachment content is preserved exactly and opaque metadata is serialized once, retained unchanged across reconnects and scoped to the original account/home
- **AND** an expired unbound reference is explicitly unavailable, not silently reuploaded or represented as a permanent chat attachment; already bound custody remains nonexpiring until release/erasure

### Requirement: Ordinary workflow authoring preserves editable file contracts
The existing branch create/remix and patch operations SHALL preserve exact `io_manifest` declarations and validate the final staged contract through the
strict shared runtime validator. Declared file input fields SHALL match their
dict/list state types. `set_io_manifest` SHALL require an explicit member: null
clears, an object replaces, and a missing member rejects. Remix omission SHALL
inherit the published parent contract; explicit null SHALL clear it. Rejected
patch batches SHALL change neither the definition nor immutable versions.
Existing ownership, publication and scope checks SHALL remain in force on both
canonical and served handles. Previous versions and admitted runs SHALL remain
unchanged, and changed manifests SHALL conflict with reused create keys.

#### Scenario: User authors and revises a file workflow
- **WHEN** an owner creates a file workflow through ordinary graph handles,
  publishes it, then edits its contract through `set_io_manifest`
- **THEN** readback and newly published versions contain the exact new contract
- **AND** prior versions/admissions retain their original contract and can consume
  their accepted exact binary inputs without using an operator-created definition

#### Scenario: Invalid atomic contract edit
- **WHEN** a batch renames a branch then supplies a malformed or state-incompatible manifest
- **THEN** the entire batch rejects and neither the rename nor a version is saved

### Requirement: First public same-owner slice discloses its actual boundary
The first public slice SHALL expose existing graph handles rather than a new top-level tool: `write_graph target=run_file operation=capture` accepts exactly `label` and authoring `{session_id,handle_id}` sources; `operation=release` accepts exactly `file_id`. `read_graph target=run_file_limits` SHALL disclose configured capacity, finite staging retention, chunk size and unsupported intake/delivery. `read_graph target=run_file` SHALL accept an owned bound `run_id`, `file_id`, `file_offset` and bounded `file_max_bytes`, returning exact base64 bytes, the immutable reference, `next_offset` and `eof`.

`run_graph` SHALL admit declared same-owner file inputs by direct definition or alternative `branch_version_id`, refusing mixed version/definition/goal/trigger/cancel/delivery selectors. The served agent SHALL have the same capability through its existing pinned owner/universe handles. Unknown capacity SHALL report unavailable rather than claiming unlimited capture. Acceptance SHALL survive submission failure with its original run identifier and an explicit no-replacement warning. These selectors MUST NOT imply workspace capture, materialization, nested/resumed file admission or cross-owner file delivery until those separate criteria are implemented and proven.

#### Scenario: Ordinary owned capture and selected-version consumption
- **WHEN** an authenticated owner captures authoring handles and runs a readable published version with those declared references
- **THEN** capture, run binding, chosen-entry and explicitly forwarded downstream reads preserve the exact binary bytes including empty members
- **AND** public readback returns bounded exact chunks, while explicit release refuses active bindings and later removes only the selected retained file

#### Scenario: Dispatch is unavailable after durable acceptance
- **WHEN** the run and file binding commit but executor submission cannot be confirmed
- **THEN** the response retains the accepted run identifier and explains that the caller must inspect that run rather than submit a replacement
- **AND** recovery uses the same static origin registry and guarded start authority

### Requirement: Exact immutable files use one generic run-input boundary
The platform SHALL admit exact binary files and ordered multi-file bundles through one owner-scoped custody and run-binding service for direct, queued, triggered, nested, versioned, resumed and delivery-origin runs. It MUST preserve exact bytes and display metadata, enforce declared limits, and expose opaque references rather than paths, session tokens or inline whole-file payloads. A content hash alone MUST NOT grant access.

#### Scenario: Binary bundle exceeds one tool response
- **WHEN** an authorized owner admits a valid bundle whose combined bytes exceed a single RPC response limit
- **THEN** all members are captured and consumed through bounded chunks with independently verified exact hashes
- **AND** state contains bounded references, not a truncated or summarized representation

#### Scenario: A member fails verification
- **WHEN** any member violates size, count, identity or integrity requirements
- **THEN** the entire requested bundle refuses before run execution or partial receiver visibility

### Requirement: Intake and consumption derive authority from admitted context
The service SHALL resolve authoring sources through current session ownership and workspace sources through actual trusted workspace capabilities. Run binding and sandbox reads MUST use persisted owner, universe, run, compiled placement and explicit input dataflow. Existing branch publication and delegation checks MUST remain in force. Caller-supplied identity, guessed file IDs, arbitrary paths and sibling-run references MUST NOT grant file authority.

#### Scenario: Selected entry and downstream consumption
- **WHEN** a file is admitted into a selected entry's input and explicitly passed into a downstream node's declared input
- **THEN** both consumers can read it without needing an ancestor workspace belonging to the sender
- **AND** an unrelated node or sibling run cannot use the same identifier

#### Scenario: Legitimate shared definition
- **WHEN** an admitted owner runs a legitimately shared or remixed definition with their own valid file inputs
- **THEN** differing attribution alone does not deny file custody
- **AND** the service does not bypass private-branch admission rules

### Requirement: Accepted cross-owner copies have independent custody
Cross-owner admission SHALL create a complete receiver-owned immutable copy and atomically record its run bindings with run acceptance. The file's ownership and read authority MUST NOT depend on a personal delivery receipt, sender account/session or output link remaining present. Revocation before acceptance MUST refuse; later sender source expiry, revocation or erasure MUST NOT erase the receiver's accepted copy.

The accepted receiver execution envelope SHALL retain its pinned graph/input contract and durable start claim independently of personal delivery controls. Recovery MUST dispatch only a proven unstarted accepted run and MUST NOT replay an already started run's effects.

#### Scenario: Sender erases a personal receipt and source
- **WHEN** the receiver has accepted complete inputs and the sender later erases its receipt and source custody
- **THEN** the receiver can still consume its exact independent input copy
- **AND** sender personal control records are not retained as a workaround

#### Scenario: Receipt disappears before first dispatch
- **WHEN** an accepted receiver's sender receipt is erased before worker dispatch and the daemon restarts
- **THEN** the receiver-owned pinned execution envelope and complete files remain sufficient for one proven-unstarted dispatch under current receiver authority
- **AND** a previously started run is marked interrupted rather than automatically replayed

### Requirement: File movement uses byte-only conservative admission
The platform SHALL reserve bounded temporary and retained storage before file movement and account for actual transport bytes using stable operation identities. It MUST NOT invent a workspace job, effect, new charge or price for file capture/read. Retained allocation admission MUST be atomic and distinct from read-only storage observations; unknown capacity MUST refuse rather than mean unlimited. Same-owner rebinding MUST NOT duplicate retained-byte allocation.

The custody ceiling SHALL be an explicit operational subsystem setting independent of unused tier entitlements. Missing or invalid configuration MUST refuse intake; normal platform rollout MUST configure capacity globally before exposing this capability, not require per-user patches. Physical free space MUST NOT subtract retained bytes twice. Failed writes MUST preserve actual consumed transport accounting while releasing unused retained reservations only after cleanup proof.

#### Scenario: Concurrent contenders exceed remaining byte capacity
- **WHEN** two valid captures together exceed the remaining file capacity
- **THEN** no more than the available capacity is reserved
- **AND** refused work starts no copy and creates no job/effect charge

#### Scenario: Replay after settlement interruption
- **WHEN** acceptance committed but transport settlement was interrupted
- **THEN** replay resolves the original immutable operation and repairs conservative accounting debt without duplicating the file or user execution

### Requirement: Capture and consumption are streaming and platform-safe
The platform SHALL use bounded streaming with held regular-file and root identity checks, enforced byte limits and atomic publication. It MUST NOT follow unapproved symlinks/reparse points, read devices, trust display filenames as paths, or materialize into another owner's workspace. Unsupported source-consistency guarantees MUST refuse explicitly. Memory MUST remain bounded by chunks and bounded metadata rather than file size.

Workspace-produced capture SHALL occur only after demonstrated managed namespace writer quiescence, under an atomic cross-process claim excluding new acquisitions/readers/writers until capture completes. An observed sole-holder count or stat/hash comparison MUST NOT substitute for exclusion. Initial workspace capture SHALL be explicitly POSIX-only; supported authoring capture and chunk readback MUST have native Windows and Linux proof before release. No active-node capture RPC or plain process-group fallback SHALL be exposed.

#### Scenario: Consumer needs a command-line file
- **WHEN** an authorized consumer materializes a bound input into its own workspace
- **THEN** the complete exact file appears only after verified copy into that workspace
- **AND** no sender checkout, arbitrary destination or additional execution capability is exposed

#### Scenario: Source changes during capture
- **WHEN** a mutable source cannot satisfy the reviewed source-consistency rule
- **THEN** capture refuses without claiming a complete immutable source snapshot

#### Scenario: Stop during a large producer handoff
- **WHEN** a source-exclusive capture is streaming and its family is cancelled or closes
- **THEN** the short family fence remains available to persist closing promptly
- **AND** bounded chunk checks stop copying and reject stale-epoch publication, preserving cleanup debt
- **AND** a durable capture claim excludes new source acquisitions without holding a SQLite writer or family fence over the copy

#### Scenario: Unrelated sibling is still running
- **WHEN** the finished producer has a verified empty managed namespace/tree and source-exclusive lease/generation claims but another family leaf remains active
- **THEN** the sibling's existence alone does not prevent file handoff
- **AND** missing specific-producer quiescence evidence refuses rather than using whole-family emptiness as a substitute

### Requirement: Custody release and crash recovery are explicit
The platform SHALL retain accepted immutable inputs under owner-controlled bindings until authorized release or erasure, with disclosed finite unbound-staging retention. Release MUST refuse active execution bindings; account erasure MUST tombstone and cancel owned activity without indefinite blocking. Cleanup debt and byte reservations MUST survive crashes until reconciliation proves safe release. Recovery MUST NOT replay user effects, expose partial bodies, recreate a tombstoned owner or silently turn missing inputs into empty data.

Body publication SHALL hold the physical coordinator without SQLite writer locks, followed by author-store then runs-store authority/binding commit. A durable operation exclusion MUST span publication through commit; collectors MUST honor it before reclaiming unbound bodies. All delivery origins SHALL use one run-keyed start authority after explicit fencing of active legacy workers; dual independent guard authorities MUST NOT overlap during migration. Run inputs SHALL remain solely on the run row.

#### Scenario: Crash after body finalization but before acceptance
- **WHEN** an operation finalizes bytes and crashes before its run binding commits
- **THEN** no reader sees an accepted binding and recovery uses durable inventory to reclaim only proven unreferenced bytes

#### Scenario: Erasure interrupts physical cleanup
- **WHEN** owner deletion commits a tombstone but physical cleanup is interrupted
- **THEN** reads remain denied and cleanup/allocation debt remains durable until deletion is verified

#### Scenario: Later immutable reuse
- **WHEN** a permitted future run explicitly reuses a retained same-owner input
- **THEN** a fresh authorized run binding can reference the same immutable bytes without rereading a mutable sender source
- **AND** this capability alone does not schedule retries or claim idempotent user effects

#### Scenario: Live pool has not yet dequeued accepted work
- **WHEN** an accepted queued run is waiting in a live executor and its execution guard is available
- **THEN** recovery leaves it or redispatches through the same guarded conditional start, not retires it from guard availability
- **AND** a later worker cannot overwrite a cancellation, terminal state or superseding resume to start execution

#### Scenario: Start marker exists before a crash
- **WHEN** the admitted start marker exists but its row still says queued after the worker is lost
- **THEN** no provider or user effect is replayed
- **AND** retirement awaits exact owned execution/kernel evidence rather than treating queued status as never-dispatched proof

#### Scenario: Owner reads an ambiguous admitted run
- **WHEN** an authorized owner reads a nonterminal admission with an unknown, legacy or invalid origin, or a queued admission carrying either durable start-marker component
- **THEN** read-only status adds `admission_state`, `phase`, `automatic_replay=false` and `actions_may_have_occurred` without changing the persisted run status
- **AND** an unavailable origin is reported as `origin_unavailable`, while a queued started admission is `recovery_required`, never presented as proven healthy waiting work
- **AND** observation reads only admission metadata, neither private graph/input bodies nor providers, and grants no authority to retry effects
