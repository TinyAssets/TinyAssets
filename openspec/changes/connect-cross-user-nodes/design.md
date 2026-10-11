## Context

Proposed implementation, not as-built truth. Grounding and independent source-fit
review: `2026-09-09-cross-user-delivery-existing-boundaries.md (archived in git)`.
Owner intent: the 00:41-00:50 PDT app conversation on September 9, preserved in
`docs/concerns/2026-09-09-cross-user-deliverable-connections.md`.

Existing webhook intake runs a whole branch as its receiver, but a URL bearer
does not authenticate a selected sender, and a successful POST returns no run ID.
Existing handoffs are sender-owned real-world outcomes, not a two-party inbox.
Existing authoring file handles require owner AND session. Passing a foreign
handle string or running a public foreign branch is not delivery.

PLAN's Trigger + typed input + versioned graph + authority + receipts already
describes the boundary. This extends those hard ends through graph-handle actions;
it does not add a top-level tool or a preferred collaboration application. Primitive
checks for `receiver` and `deliver` returned CLEAN on September 9; source inspection
also checked webhook/source operations, handoffs and invoke_branch.

## Goals / Non-Goals

**Goals:** receiver-controlled node entry, native verified sender identity,
sender-selected output mappings, exact structured and binary deliverables,
two-sided outcome visibility, replay/revocation/crash safety, host-off execution,
and conversational management shared across providers.

**Non-Goals:** platform-defined issue/review/marketing content, shared credentials,
arbitrary cross-owner run control, caller-selected receiver authority, whole-universe
sharing, or new provider-specific logic. Legacy report removal and shared physical
capacity remain separate tracked work, not silently dropped acceptance requirements.

## Decisions

### 1. A receiving address is a constrained graph entry, not an ownership grant

Store an owner-controlled exposure for `(receiver universe, graph, selected node)`.
It carries an opaque stable ID, generation, receiver principal, immutable graph
snapshot/hash, typed input contract, allowed sender principals, and active/revoked
state. The receiver can advertise a description and contract without exposing the
private graph, prompt, other runs, credentials, sender list or other deliveries.
Default visibility is selected senders only; an empty list accepts nobody. No
wildcard is inferred from public graph visibility.

Creation/update requires the authenticated receiver's current admin authority for
the target universe AND ownership of the graph. Identity is server-derived, never
from payload fields. Revision uses compare-and-swap generation; policy changes
invalidate stale unaccepted connections until the sender inspects and relinks.
Receiver execution authority is revalidated at admission, not just at exposure.

Sender-owned links bind a permitted receiving ID/generation, sender universe,
source graph/node and explicit output-to-input mapping. Link IDs are not bearer
credentials. Sender authentication and source ownership are required on every
send. Disconnect prevents future occurrences; receiver revoke prevents new intake.
An accepted delivery is an already authorized transfer, not a remotely retractable
document. Revocation does not erase it or cancel receiver work. Owners can cancel
their own run through existing controls. Explain this before exposure, not after.

Alternative rejected: expose existing webhooks only. They cannot prove the sending
user without a new identity boundary and do not address a selected node. Keep them
for external HTTP integrations; native cross-user sends need no public callback.

### 2. Start at the chosen node in a pinned receiver graph

An exposure pins the receiver-authored graph snapshot, node ID and contract in one
generation. It can target a node that was not the original branch entry. Compile
an execution projection rooted at that node: keep its reachable downstream nodes
and edges (including reachable loops), remove predecessor-only nodes and original
START edges, and add START to the selected node. Preserve graph/node IDs and the
original snapshot hash in provenance; never edit the owner's stored workflow.

At exposure creation/update, run structural validation and the compiler's
workspace-ancestor checks against the projection and derive required input keys
with preflight (using only explicitly declared contract keys/defaults as supplied).
Do not compile or execute user code to validate a contract. Reject an exposure
whose downstream workspace relies on a removed predecessor before any sender can
connect. At acceptance repeat validation/preflight with actual supplied inputs,
and apply code authorship, provider and effect policy. Missing predecessor-produced values must
be explicit contract inputs/defaults; do not synthesize predecessor outputs or
run upstream side effects. A workspace capability cannot be replaced by a sender's
path string: require a real receiver-run ancestor or return an actionable error.
Join/conditional/loop semantics need direct projected-graph tests before use.
Do not expose an arbitrary public `start_node` execution bypass outside this
owner-created entry boundary.

Reuse receiver provider binding and queue behavior from
`api/runs.enqueue_universe_branch_run`, but factor the common validated-snapshot
enqueue so delivery is not forced to re-read a mutable whole branch. Public
invoke_branch behavior remains unchanged. No fallback to sender, host or ambient
provider credentials if the receiver lacks authority.

Alternative rejected: force every receiver to create a new one-node branch or
start the original whole branch. Both substitute a narrower end state for the
selected-node requirement.

### 3. A delivery is a durable occurrence, not a payload hash or run completion

Persist receiver exposures, sender links, delivery occurrences and artifact
bindings in the existing runs SQLite database, using additive tables and indexed
owner columns. Do not widen the existing handoff table: its accepted state emits
real-world outcome events and its ownership is one-sided.

Unique occurrence key: `(sender principal, sender universe, link ID, occurrence ID)`.
The existing declared effect chain fires a graph node's effects at most once per
run and refuses cycle revisits (`effect_already_fired`). Preserve that contract:
declared delivery effects use trusted run/node identity, not an invented iteration
counter. Per-iteration and repeated sends remain supported through the explicit
deliver RPC: the user's loop supplies a durable occurrence ID for each intended
send, retries reuse it, and the server adds authenticated run/node/link scope.
Expose this ordinary composition to the app; do not defer loop delivery or quietly
deduplicate different iterations. For a direct explicit send likewise require an
occurrence ID. Pin a request digest including
receiver generation, mapping and exact artifact content hashes. Reusing a key with
changed content is `occurrence_conflict`, not a new send. Two distinct IDs with
identical content are independent sends.

Acceptance rechecks source ownership, receiver policy/generation, contract and
real resource admission. Commit the delivery intent, exact receiver snapshot/input
and finalized artifact bindings in one transaction BEFORE attempting execution.
That delivery is the durable work intent; a current `runs.status=queued` row is
NOT a restart-surviving queue. `_execute_branch_core` submits an in-memory future;
`recover_in_flight_runs` marks queued/running rows interrupted. The earlier claim
that normal workers pick up durable run rows was incorrect and is withdrawn.

Reserve a run through a unique `(delivery_id, attempt)` key, following the existing
branch_task run-reservation/reconciliation pattern, with a transaction-aware insert
factored from create_run. Bind the projected snapshot and receiver authority to
that exact attempt. Delivery reconciliation uses the existing executor, not a
second execution engine: accepted intent without a reservation can reserve one;
an existing reservation is inspected, never replaced on a timeout or lease alone.
Atomic linkage under the unique key makes a crash between intent and scheduling
recoverable. No provider work occurs inside the acceptance transaction.

The startup/worker seam MUST distinguish an attempt proven never started from a
possibly executed interrupted attempt. Only a proven unstarted attempt may be
scheduled automatically after recovery. An interrupted/ambiguous attempt reports
that state and awaits receiver-authorized retry as a new numbered attempt; it is
not silently rerun. Normal delivery retries retain their original attempt. Worker
claims need fenced ownership so simultaneous reconcilers cannot both execute the
same reserved run. Exact claim/recovery integration is still a pre-build source
task, not a claim that the current async executor provides this behavior.

Reuse outbound receipt admission/reservation patterns at the sender effect boundary;
the two-party delivery transaction is authoritative for whether native transfer
happened. A lost sender receipt can be reconstructed from the occurrence. Retries
do not resubmit failed receiver work: receiver retry is a new numbered execution attempt
explicitly linked to the accepted delivery. Transport retry, admission retry and
workflow retry must remain distinguishable.

Receipts expose accepted/queued/running/completed/failed/cancelled or rejection,
stable delivery ID, attempt, timestamps, contract generation and safe reason codes. Accepted
does not mean processed successfully. Sender gets only the agreed receipt/outcome
projection, not the receiver's private run ID, raw errors, state, logs or outputs.
Receiver can inspect its own linked run. A receiver-defined response is a new
explicitly mapped deliverable, not ambient access to receiver output. The sender
response and sender-side global ledger MUST use delivery_id, never the receiver's
run_id; `_dispatch_run_action` currently defaults to run_id and needs an explicit
delivery-target case. Record exposure ID on receiver run provenance so projected
and whole-branch lineage are not conflated.

### 4. Copy authorized deliverables; never broaden foreign handle access

Input contracts compose existing state types and file/file_bundle manifest rules.
Support structured scalar/list/object values, text and exact binary files with
filename/media type/size/hash metadata. Unknown media types are opaque bytes;
do not require providers or platform code to know the application format. A
legitimate field named `key` or `token` is data, not grounds for automatic deletion.
Credentials belonging to the transport or runtime are never added to the payload.
Validation errors are explicit; uploads are never summarized or silently truncated.

Resolve source artifacts only through the authenticated source run/session's
existing read authority. Stage exact bytes in the managed blob store outside the
repository, hash/size-check while streaming, reserve actual storage, then bind
immutable copies to the accepted delivery and receiver run in its own scope.
There is no production run-scoped artifact-handle boundary today. Authoring handles
are session-only and authoring's draft test excludes file values from sandbox
inputs. This change must implement the missing runtime artifact binding rather
than describe it as reuse. The proposed binding is an immutable, read-only input
bundle attached to the receiver run and admitted entry, using the existing safe
descriptor/path and byte-accounting helpers. Reuse those helpers, not a fake
writable checkout lease or an exposure keyed solely to a user-supplied path.
The entry node itself and its downstream nodes need access; an ancestor-only
registry keyed to the entry would wrongly exclude the entry itself. Preserve
the existing mutable-workspace boundary and add no sibling/unrelated-run access.

Provide an exact-byte, bounded reader in the sandbox for the bound input bundle;
chunking/streaming must support files beyond a single JSON tool result. Source
files come from authenticated authoring handles or actual owned workspace output
capabilities; completed JSON run output is not magically a file handle. Small
inline base64 values remain compatible but are not the only supported file path.
No arbitrary filesystem paths, remote URL fetching,
or reuse of the sender's authoring session token. Sender handle expiry after
acceptance cannot break a successfully accepted transfer.

Use temporary staging plus atomic blob finalization before acceptance; uncommitted
staging has bounded retention and cannot be read by another owner. Failure before
commit releases reservations and permits retry. Failure after commit keeps the
accepted artifact/run binding recoverable. Content-addressed physical reuse may
avoid copies but never grants access merely from knowing a hash. Keep observable
sender staging and receiver retention attribution distinct; this does not decide
the broader unresolved platform-storage-overhead billing policy.

Alternative rejected: pass raw handles, base64-only tool text, publicize uploads,
or let the receiver read arbitrary sender files. Those either break exact useful
delivery or erase the ownership boundary.

### 5. Existing graph handles expose the complete management lifecycle

Proposed graph actions: write_graph receiver create/update/revoke and link
connect/disconnect; read_graph permitted receiver contract, owned links and
two-party delivery receipts; run_graph deliver a linked output occurrence.
Proposed internal verbs `create_receiver`, `connect_output` and `deliver_output`
passed primitive collision checks on September 9. Final read/update/revoke names
still need checks and must match router conventions before code. Delivery uses
the existing run-write admission and explicit sender-universe owner gate; receiver
policy is an additional check, never a replacement for the sender gate. No new
top-level MCP handles or provider-specific allowlists.
Expose the same validated actions through engine wrappers and tool descriptions.

A source node can use the existing declared effect/RPC boundary for the linked
output, with server-derived run provenance. It must not trust a user payload's
claimed source run/node/owner. Link selection and mapping are authored behavior;
the app agent may compose/revise workflows through ordinary authoring tools.
The operator does not alter the users' private workflows to manufacture a pass.

## Risks / Trade-offs

- Cross-owner writes amplify abuse -> selected-sender policy, real resource
  reservations, receiver-owned limits and no ambient authority; preserve unrelated
  tenants' uptime. Do not add arbitrary hidden product caps as resource protection.
- Queue insertion/replay races -> one occurrence/one committed run boundary plus
  multiprocess, restart and crash-point tests, not just mocked enqueue.
- Artifact access or instruction injection -> exact scoped copies, untrusted
  data provenance, no control-field merge or tool/provider credential transfer.
- Internal-node projection changes topology -> explicit ingress semantics and
  fixtures for joins, loops, defaulted inputs, skipped upstream effects and
  ancestor-only workspace requirements. Do not silently route whole branches.
- Model/tool-schema drift -> shared canonical dispatch/wrapper tests across all
  served providers; the receiver uses its own configured provider default.

## Migration Plan

Account deletion explicitly enumerates indirect delivery children before the
existing schema-derived satellite sweep: attempts, two-party delivery receipts,
dependent links, then receivers. Endpoint selection follows existing home-universe
scope; receipt identity fields also match either deleted principal. Surviving peer
receivers remove that exact identity from their permitted-sender list without
rewriting unrelated entries. Peer-owned runs and accepted input content remain
peer operational data, not a generic cascade target. Count each union-selected row
before deletion and commit counts only after the existing per-store transaction
succeeds. Failure rolls that store back and the account workflow records its
unfinished store phase while continuing billing/identity cleanup. Erasure support
must remain deployed even if public delivery intake is rolled back or darkened.

Additive tables only; no destructive report-record migration. Default all new
exposures to absent. Review shape before runtime edits; implement and compare
baseline/candidate on Windows and Linux for storage, workspace and process changes.
Pass exact-head independent review, CI, plugin mirror, deploy containment and
authenticated public canary with asserted handles. Then perform the two-user
conversation proof without any host-dependent execution. Rollback stops new
native intake while preserving accepted records, artifacts and receiver runs;
do not roll back into code that can duplicate accepted occurrences.

## Open Questions / Pre-build checks

The following need source-grounded closure in the shape review or first bounded
implementation task, not founder choices unless an actual authority decision arises:

1. Implementable seams found: create_run's unique branch_task reservation pattern,
   `_prepare_run`'s separate event/lineage writes, and `_execute_branch_core`'s
   in-memory submit. Specify fenced dispatch/recovery states and an existing-run
   submit seam before changing them. The assigned cloud consumer currently skips
   non-automation tasks; do not label a delivery a fake automation to get picked up.
2. No runtime artifact-handle seam exists. Specify the read-only input bundle's
   actual descriptor/sandbox interface, entry-node access and byte reservation
   lifecycle before code. Use authoring bytes and owned workspace capability
   sources; completed plain JSON alone is not an artifact reference.
3. Declared effects are once per run/node. Explicit occurrence RPC supplies loop
   semantics. Primary verbs passed collision checks; finish read/update/revoke
   names and the served-wrapper dispatch contract.
4. Establish the second independently authenticated ordinary app account for live
   proof using available authorized test identities; do not borrow another user's
   credentials or claim a same-owner pair proves the boundary.

First independent shape review returned ADAPT, with five findings captured in
`2026-09-09-cross-user-node-shape-review.md (archived in git)`. The concrete interfaces
below close the pre-build source questions; runtime tests and exact-head review
must still verify them. The authorized second app identity remains live-proof work.

## Implementation interfaces — September 9 follow-through

### September 19: reviewed RPC-first implementation contract

Bounded Fable shape APPROVE and full review: `rpc-shape-review.md`; slice boundary
and remaining file work: `next-slice-proposal.md`. This implements JSON in-node
delivery only. Files, receiver-directed retries and two-owner rendered acceptance
remain open. No private workflow or test-account manipulation is authorized.

Disposition: accept the review's explicit source-owner, exact-kwargs, cancellation
and placement requirements. Retain write settlement in this slice despite its
non-blocking review classification. Use the existing request digest's
`source_run_id` distinction for direct/node collisions, not a second identity
ledger or new prefix restriction.

- Extend the existing immutable execution context with the persisted
  `runs.owner_user_id`, populated at ordinary execution and resume. A parent-only
  frozen delivery source binds it to the compiled branch's ID, current child/run
  ID, actual scheduled graph placement, universe/actor and declared output keys.
  None is read from sandbox kwargs, payload or mutable graph state.
- The shared acceptance service takes explicit principal/base/universe. Direct
  graph calls derive these through existing request authentication; node calls
  derive them from the frozen source and recheck the matching persisted source
  run, current admin ACL and owned source/link. Scheduled universe actors never
  substitute for `owner_user_id`. Owned remixes/referenced definitions remain
  allowed under unchanged upstream code admission; original attribution is not
  a new permission check. A child invoke binds its own compiled branch/run.
- Reuse the existing author-store writer reservation before the runs-store
  transaction; provider work and dispatch occur after acceptance commits.
  Check actual source placement, compiled declared outputs and current source
  contract before accepting. Missing identity, wrong run/branch/universe, terminal
  source run, revoked authority or missing placement fails closed.
- Necessary implementation prerequisite, confirmed September 19: foreground
  `_prepare_run` currently derives owner only from an optional daemon ID, which
  ordinary graph API runs do not pass; nested invocations also omit the universe.
  Add explicit owner plumbing from authenticated foreground requests and stored
  trigger principals into run preparation, and inherit the immutable owner and
  universe on nested invocations. Do not backfill old rows, guess a founder from
  a universe string, or consult ambient identity inside worker/RPC execution.
- The `deliver_output` sandbox alias accepts exactly `link_id`, `occurrence_id`,
  `outputs`, requires its existing `tools_allowed` declaration, and checks
  `should_cancel` at handler entry and immediately before acceptance. An already
  accepted transfer is not undone by later cancellation or source failure.
- Reuse `derive_effect_key`: canonical domain/principal/universe/link tuple,
  trusted source run, canonical placement/user-occurrence fingerprint. Content
  stays in the request digest, never occurrence identity. Existing direct keys,
  including generated-looking strings, are unchanged. Direct/node collision
  conflicts through the existing source-run-bearing digest; never relabel rows.
  Receipts remain unchanged and do not invent an original occurrence field.
- After committed node acceptance call `settle_write(source_run_id)`, including
  exact replay, before receiver dispatch. Existing final write settlement cannot
  be downgraded by later effect-chain read settlement. Do not mark a declared
  effect fired or alter once-per-node effect semantics.

### Execution reservation and ownership

#### RPC dependency: private child ownership (reviewed adaptation)

The new nested-RPC regression currently fails before RPC: `_authorize_child_ref`
compares a private child's author only with `ctx.actor`. Served runs use
`universe:<id>` while owned/remixed child definitions use the human owner. Merely
persisting/inheriting owner identity does not close that mismatch. This is an
authority change, not a test-fixture repair. The focused Fable review and required
adaptation are preserved in `rpc-child-authority-review.md`.

Proposed additional case, leaving every existing case intact: only when current
provenance is `own`, permit the child if its author equals the frozen nonempty
`ctx.owner_user_id`, the actual compiled parent definition's author also equals
that owner, that principal still holds admin on `ctx.universe_id`, and
the persisted current parent run (whose ID comes from the compiler builder)
matches actor, owner and universe and is running. Requiring parent-run agreement
prevents a synthetic/mismatched context from becoming new private-read authority.
Missing owner/run/universe refuses this case. Public-foreign delegation remains
public-only even if the runner owns the private child. Do not use a random admin,
original contributor, payload field or child approval flag as authorization.
The definition-author equality closes the review's co-admin counterexample:
admin A's parent cannot use admin B's execution owner to read B's personal private
child. `definition_author` is frozen from the same compiled branch used to derive
provenance at foreground and resume construction. Owned remixes keep their new
owner as author; `fork_from` attribution does not grant or remove access.
Run reads are freshness narrowing only; they cannot fill absent context. Any
lookup error collapses to the existing uniform unavailable response before
private body deserialization. Only exact status `running` qualifies.

Tests before release must show current owner revocation, missing or spoofed
context/run owner and public-foreign provenance cannot use the new case; an
admitted owned remix can invoke its own private child, and that child's delivery
is attributed to its own run/placement. Co-admin confused-deputy and lookup-error
tests accompany explicit owner-parent/owner-child success. Implementation follows
the reviewed adaptation; independent exact-code release review remains required.

Management implementation follow-through: receiver/link records live in the
runs database, while ACLs and graph authorship remain in the canonical author
database. Management mutations acquire an author-database `BEGIN IMMEDIATE`
reservation before reading current authority, then commit the runs-database
change while retaining that reservation. This prevents ACL revocation or graph
replacement between the check and commit without copying ACL interpretation.
Lock order is author store, then runs store; delivery integration must retain
that ordering. No provider calls, user code or file transfer run in this small
management guard. SQLite contention and separate-process generation tests cover
the implemented boundary; they do not prove the future delivery executor.

Add internal delivery-attempt fields to the existing run reservation path, not a
second BranchTask type. Factor run insertion to accept the caller's runs-database
transaction; ordinary create_run remains a wrapper with unchanged semantics.
An attempt stores immutable projection/input hashes, receiver principal/universe,
run ID, state (`pending`, `executing`, terminal), and `execution_started_at`.
The UNIQUE delivery/attempt reservation and run ID are written together. Pending
events and lineage are idempotent initialization after that commit, before dispatch.

Factor `_execute_branch_core`'s submit/invoke/provider-settlement portion into an
internal existing-run submission function. Ordinary async runs still prepare then
submit. Delivery reconciliation submits only its already-reserved run. A per-attempt
OS advisory sidecar lock is held from eligibility recheck through terminal status;
reuse the cross-process Windows/POSIX lock pattern, with a same-process thread lock
and errors failing closed. Lock files are server-generated beneath the resolved
data directory and are never unlinked as cleanup. A database claim token additionally
guards attempt transitions. No TTL expiry alone establishes executor death.

Inside the acquired lock, a pending attempt with execution_started_at unset can
initialize/reinitialize pending events and be submitted. Immediately BEFORE provider
or graph execution, compare-and-swap pending to executing and persist
execution_started_at. A crash after this conservative marker reports interrupted,
even if it preceded the first actual effect; it must never auto-replay. A crash
before it is safe to resume the same reserved attempt. Competing reconcilers cannot
both acquire the lock and the durable transition. Recovery leaves executing attempts
alone while their lock is held and marks them interrupted only after acquiring it.
Never use PID reuse, timestamps or an absent in-memory Future as death proof.

Run the bounded reconciliation pass at daemon startup and the existing runtime
maintenance cadence; it schedules into the existing executor without executing
work in a database transaction. Receiver-directed retries increment attempt after
the previous attempt is terminal and no worker still owns its lock. Sender retries
only read/reconcile their original occurrence; they cannot create receiver retries.

Worker execution starts in a fresh Context, not the sender's copied ContextVars.
Use `auth.middleware.identity_context` with receiver-scoped execution authority
derived from the persisted exposure grant, after current owner/graph checks; never
construct authority from payload identity fields or borrow incoming sender scopes.
Reuse receiver-principal provider binding and foreground run receipt settlement.
The run row and BranchExecutionContext remain the source of node RPC provenance.
Tests must distinguish receiver provider identity AND receiver tool identity.

### File input boundary

Choose a parent-mediated chunk reader through the existing sandbox RPC pipe, not
an additional writable filesystem mount. Each accepted file has an opaque file ID,
delivery ID, exact size/hash and a server-generated storage key. An attempt binds
those files to its receiver run and named contract inputs. The child receives only
metadata/IDs in declared input state. `read_graph`'s artifact view and its in-node
alias resolve a file only through the authenticated owner AND trusted run/node
context; the parent injects that context and rejects user-supplied substitutes.
The selected entry and its reachable downstream nodes may read only the input
bindings actually supplied to that run. No sibling run, foreign session, naked
content hash or arbitrary path grants access. Read-only means no write/delete or
command execution operation on this bundle.

The reader accepts a nonnegative byte offset and bounded byte count, returns exact
base64 data plus next offset/EOF/hash, and charges raw bytes before returning them.
Repeated chunk reads permit files larger than any one tool result, subject to real
admission limits. A node needing command-line file processing can deliberately copy
chunks into its own existing workspace via ws.write_bytes; this never shares the
sender's checkout. The existing authoring bytes and safe workspace source-copy
helpers supply intake; workspace_fs descriptor helpers are POSIX-only and MUST NOT
be represented as Windows proof. Use the existing blob-proof stable-handle pattern
for cross-platform immutable storage integrity rather than a path-check-then-open.

Stage under server-generated names; reserve sender staging bytes before write,
verify size/hash and finalize before delivery acceptance. Acceptance transfers
retention attribution to the receiver and records bindings atomically. Rollback
releases staging reservations; committed bundles remain until receiver-owned
retention/deletion permits cleanup. Chunk transport observations are separate from
retained storage. No new price, storage-overhead payer or hidden workflow-count cap
is decided by this interface.

This is an implementation plan, not a claim these boundaries already work. The
first implementation unit is the pure selected-node projection and exposure
preflight; it neither opens a public route nor mutates users' saved definitions.
