# Run File Inputs

## Purpose

Preserve exact owner-controlled binary inputs across workflow execution without
putting whole files, mutable paths or caller-asserted authority in workflow state.
This first slice covers same-owner app upload and authoring capture, direct definition or
published-version runs, declared node reads, owned export and custody lifecycle.
The broader origin/materialization proposal remains in
`openspec/changes/archive/2026-10-06-bind-immutable-run-files/`; it is not implemented by this spec.

## Requirements

### Requirement: Ordinary app users can supply exact owned bytes without internal handles

The app paperclip SHALL offer bounded raw-byte upload into the same immutable
run-file custody without an authoring session or operator-created handle.
`POST /app/files` SHALL derive owner and current home from authenticated
request context, require the allowed origin and exact custom upload metadata,
reserve capacity before reading bytes, verify size and digest, and recheck
authority before commit. Missing capacity or incomplete home SHALL refuse
without ingesting the body. Upload SHALL neither execute workflows nor create
a separate artifact store, public bearer URL or upload charge.

The app SHALL take every accepted attachment, readable text included, through
this same custody and relay its opaque reference metadata through its existing
conversation request; it SHALL carry a text attachment's decoded content inline
beside that reference only when re-encoding it reproduces the committed bytes
exactly, otherwise the reference alone, so accepted content is preserved
verbatim; and it SHALL prevent sending an unresolved subset. Same-label
exact-header replay SHALL return the original
committed references and retention metadata without requiring a second body;
unfinished attempts SHALL remain explicitly unresolved, not silently relabelled.
Unbound staging SHALL expire after the disclosed lifetime; bound custody SHALL
remain subject to explicit release or erasure, not that staging deadline.

#### Scenario: Fresh signed-in user attaches binary and empty files
- **WHEN** the user selects binary or empty files with no prior internal handle
- **THEN** successful uploads return immutable references with exact metadata
- **AND** the existing same-owner declared workflow admission can bind those references

#### Scenario: Partial or uncertain upload
- **WHEN** one upload fails or its response is unconfirmed
- **THEN** the app keeps the text draft, does not silently send only successful siblings, and offers explicit retry or removal
- **AND** checking a committed attempt recovers the original reference without duplicating custody or executing a workflow

#### Scenario: Account or home changes while callbacks are pending
- **WHEN** the signed-in account exits or the verified home changes
- **THEN** pending transfers stop and only the matching verified owner/home may restore saved upload metadata
- **AND** detached callbacks do not overwrite either account's durable recovery rows
- **AND** sign-out clears private composer text, thread and active-turn state without deleting saved recovery

#### Scenario: First session reaches chat through the connection gate
- **WHEN** the verified account connects its model and reaches chat for the first time
- **THEN** the app has already learned that account's owner/home pair and can restore only its matching history and saved attachments
- **AND** a response from an earlier login cannot replace the current identity or paint its conversation

### Requirement: Ordinary branch authoring preserves editable file contracts

Branch create/remix and patch operations SHALL preserve exact `io_manifest` declarations and validate the final staged contract through the strict shared runtime parser.
The strict parser SHALL refuse any top-level `io_manifest` key other than
`inputs`/`outputs`, naming the accepted shape and the `file`/`file_bundle`
declaration, at create/patch and again at run admission before any run row or
binding exists. Absent, empty and supported manifests SHALL remain accepted; an
already-stored invalid declaration SHALL remain readable and patchable, and a
reference-shaped value under an undeclared field remains ordinary data.
File input declarations SHALL match dict/list state fields. `set_io_manifest`
SHALL require an explicit member: null clears, an object replaces, and omission
rejects. Remix omission SHALL inherit its immutable parent's contract; explicit
null SHALL override inheritance. Top-level declarations SHALL take precedence
over the canonical nested graph form, including explicit null. Changed manifests
SHALL conflict with reused create keys. Existing ACL/scope checks SHALL remain.

#### Scenario: User creates and revises a file workflow
- **WHEN** an owner creates, publishes and then edits a file contract through ordinary graph handles
- **THEN** definition readback and new published versions preserve the exact new contract
- **AND** prior versions and admitted runs retain their original contract and can read their accepted bytes

#### Scenario: Invalid contract patch is atomic
- **WHEN** a batch renames a branch and sets an invalid or state-incompatible manifest
- **THEN** neither the rename, contract nor any new version is saved
- **AND** a valid state-field addition and matching contract can be submitted in one batch

### Requirement: Exact same-owner capture uses existing graph handles

`write_graph target=run_file operation=capture` SHALL accept exactly a label and
bounded `sources:[{session_id,handle_id}]` from the authenticated owner's existing
authoring sessions. Capture SHALL revalidate source ownership and freshness,
stream bounded exact bytes under source/root identity checks, and atomically
publish the complete requested bundle or refuse. Returned versioned references
SHALL contain display metadata, byte size and digest, never physical paths.

#### Scenario: Binary and empty members
- **WHEN** the owner captures a valid ordered bundle containing a binary member larger than one tool response and a zero-byte member
- **THEN** each member retains exact bytes and metadata through bounded reads
- **AND** a failed member prevents partial accepted bundle visibility

#### Scenario: Caller supplies authority or a path
- **WHEN** capture payload supplies an owner, universe, run, arbitrary path or URL selector
- **THEN** the request refuses without capturing bytes or granting file access

### Requirement: Capacity and supported scope are truthful

`read_graph target=run_file_limits` SHALL disclose whether capture is configured,
the custody allocation ceiling, source/count/chunk bounds and staging retention.
Missing or invalid global capacity SHALL refuse intake. Retained and pending
allocations SHALL be atomically bounded separately from transport accounting;
capture/read SHALL account actual bytes without fabricating a workspace job,
effect or new price. Same-owner run rebinding SHALL not duplicate retained bytes.

#### Scenario: Capability is not globally configured
- **WHEN** custody capacity has no valid operational value
- **THEN** limits report capture unavailable rather than unlimited capacity
- **AND** the platform requires one normal global rollout configuration, not a patch per user

#### Scenario: Unsupported transfer shape
- **WHEN** a caller asks this slice for active-workspace capture, arbitrary URL/path intake, workspace materialization or cross-owner file delivery
- **THEN** the public contract does not claim that capability exists
- **AND** file references alone do not grant those authorities

### Requirement: Admission binds immutable inputs before execution

`run_graph` SHALL accept declared same-owner file inputs for a readable branch
definition or alternative `branch_version_id`. A version selector SHALL refuse
combination with definition, goal, trigger, cancellation or delivery selectors.
The current owner/home/source authority fence SHALL precede atomic run,
immutable origin/options/snapshot and complete file binding. Private source
visibility SHALL remain enforced. No provider, initial run effects or dispatch
SHALL occur before this reservation and the common worker's guarded start CAS.

The served agent SHALL expose these operations through its existing pinned
owner/universe graph handles. This first slice SHALL refuse nested direct
provenance; other file-bearing origins remain outside its accepted contract.

The served `write_graph` description SHALL carry that compact recipe near its
start, not only in a later section.
The advertised `read_graph`, `write_graph` and `run_graph` descriptions, on the
served engine and the connector, SHALL state that an app attachment is already
an exact six-field reference which binds VERBATIM through a declared
`file`/`file_bundle` input in `run_graph` `inputs_json` without capture, that
capture exists only for authoring-session handles, and the recipe a code node
needs (`io_manifest`, matching state field, `input_keys`, `tools_allowed`
`read_run_file`, keyword `invoke_mcp_action("read_run_file", file_id=..., offset=...,
count=...)` returning `bytes_base64`/`next_offset`/`eof`). The file refusal on
`operation=deliver_output` SHALL be scoped to delivery, never stated as a
general file limitation. Descriptions SHALL NOT advertise a standalone bind
tool, public URL, inline whole-file, path or metadata-derived grant, and
SHALL keep reference metadata untrusted.

#### Scenario: Accepted submission cannot be confirmed
- **WHEN** run and file bindings commit but executor submission fails
- **THEN** the response retains the accepted run identifier and warns against submitting a replacement
- **AND** recovery nominates that same run through the single static origin registry and start authority

#### Scenario: Source or reference is foreign
- **WHEN** admission names an unreadable version, another owner's file, or changed immutable reference metadata
- **THEN** admission refuses without a partially reserved executable run

#### Scenario: Agent discovers app attachment binding from its own tool descriptions
- **WHEN** a user attaches a file in the app and asks for a result derived from its bytes
- **THEN** the registered graph handle descriptions alone name the path: build a branch with a declared file input and a `read_run_file` code node, then run it with the attachment references verbatim in `inputs_json`
- **AND** a real authenticated app upload, served `write_graph` create, `run_graph` admission, completed exact-byte processing and bounded `read_graph` export succeed with no authoring session, handle or storage-level bind step
- **AND** an unbound reference, another owner's or home's reference, and edited reference metadata remain refused with no run reserved

### Requirement: Actual node reads require trusted execution and declared dataflow

The sandbox `read_run_file` action SHALL accept only file ID, byte offset and
bounded count. Its authority SHALL derive from a live held execution-use token,
persisted run owner/universe/actor, actual compiled placement and the references
explicitly present in that node's declared incoming fields. The worker SHALL
revalidate persisted file bindings; it SHALL not invent missing bindings.
Read bounds SHALL respect each node's applicable manifest contract.

#### Scenario: Chosen entry and downstream node
- **WHEN** a selected entry receives bound files and explicitly forwards them into a downstream declared input
- **THEN** both nodes can read exact bytes within their respective declared limits
- **AND** whole-state/default visibility does not grant an undeclared node access

#### Scenario: Running row without an execution-use guard
- **WHEN** a row says running but no real guarded execution-use scope owns the read
- **THEN** file access refuses even if all caller-supplied IDs match

### Requirement: Owned export and release retain lifecycle safety

`read_graph target=run_file` SHALL read an owned bound run/file reference using
`file_offset` and `file_max_bytes`, returning exact base64 bytes, reference,
`next_offset` and EOF. `write_graph target=run_file operation=release` SHALL accept
exactly `file_id`, refuse active bindings, and revoke only the selected retained
file. Bound custody SHALL persist until explicit release or owner erasure;
unbound staging SHALL expire after the disclosed finite lifetime.

Owner deletion SHALL tombstone authority before cleanup, settle exact owned
physical custody before generic row erasure, and retain durable cleanup/allocation
debt when deletion cannot be proven. Reset and account lifecycle inventories
SHALL classify every custody table and preserve other owners' data.

#### Scenario: Selective release
- **WHEN** an owner releases one inactive retained member
- **THEN** that member becomes unreadable and its safely settled allocation is released
- **AND** sibling files remain readable

#### Scenario: Cleanup is interrupted
- **WHEN** erasure or retention cleanup cannot prove physical deletion
- **THEN** authority remains denied where revoked and durable debt survives for reconciliation
- **AND** recovery never replays user workflow effects or exposes partial bodies

### Requirement: Uncertain admission status is observational only

Owner-authorized status SHALL classify admission metadata without reading private
input/snapshot bodies or binding a provider. Unknown, legacy or invalid origin
metadata SHALL report `phase=origin_unavailable`; a queued row with either
durable start-marker component SHALL report `phase=recovery_required`.
These additive observations SHALL preserve persisted status and disclose
`admission_state`, `automatic_replay=false` and `actions_may_have_occurred`.
Observation SHALL not establish worker death or grant retry/retirement authority.

#### Scenario: Marker exists while row remains queued
- **WHEN** an owner reads a queued admission carrying a start timestamp or claim token
- **THEN** the response warns that actions may already have occurred
- **AND** it does not present the run as proven healthy unstarted work or automatically resubmit it
