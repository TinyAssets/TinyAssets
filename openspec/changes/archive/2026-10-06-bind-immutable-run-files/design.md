## Context

Approved implementation shape, inspected on Windows on 2026-09-19. Fable session 80912 returned ADAPT; the lead accepted the corrections and qualifications recorded in `review-disposition.md` before implementation. This is the generic foundation for the existing `connect-cross-user-nodes` file requirement, not a replacement for remaining file, retry and two-owner acceptance. PR #3881 is merged; the release lead owns deployed verification. No live-account changes are authorized here.

PLAN was read in full. Custody here means the already chosen private universe's governed daemon data store (cloud or local), not a mandate that all future user data reside on a platform service. Files remain exportable exact bytes. No external object store, new price, provider integration or user-specific workflow is required.

### Verified primitive inventory

Paths below are relative to this proposal worktree. Function names are the durable lookup keys; line numbers describe the inspected tree, not release assertions.

| Existing primitive | Reuse and missing part |
| --- | --- |
| `authoring/io.py:50` `IODeclaration`, `:110` `parse_manifest`, `:389` `read_handle_bytes`; `authoring/store.py` `get_file_handle` | File/file_bundle metadata and owner/session/expiry/revocation checks exist. Reads buffer whole files; handles are session-scoped, not durable run custody. Current ceiling is 8 MiB (`io.py:44`). |
| `branches.py:896` `BranchDefinition`; `authoring/models.py:565` `_validate_node`; `graph_compiler.py:633` `_resolve_field_type` | Executable branch currently has no `io_manifest`; authoring graph validation drops it. State types alone do not enforce files and unknown types become `Any`. A lossless declaration bridge is necessary, not already shipped. |
| `storage/deliveries.py` `accept_in_transaction`, `api/deliveries.py:20` `_structured_inputs`, `delivery_runtime.py` `_execution_subject` | Run/attempt acceptance is transactional and file values currently explicitly refuse. General bindings must commit with the admitted run, not hang off a receipt that account deletion removes. |
| `runs.py:943` `initialize_runs_db`; `delivery_runtime.py:46` `_execution_subject` | The ordinary run row has inputs but no pinned projected graph. Delivery currently loads its pinned snapshot and starts/reconciles through personal delivery rows. Preserving file bytes alone would not preserve an accepted-but-not-started run after receipt erasure. |
| `execution_authority/blob_proof.py:324` `_HeldRegularFile`, `:523` `coordinated`, `:678` `_read_regular_file`, `:892` `put_blob` | Installed verified integrity capability with physical-root coordination and held-file checks. `put_blob` takes whole bytes; extend/extract its bounded stream support. Hash/integrity proof is not read authority. |
| `workspace_fs.py:602` `copy_regular_file_beneath`; `effectors/workspace.py:1334` `_acquired`; `node_sandbox.py:1077` `ws.read_bytes` | Real workspace capability and regular-file descriptor access, not caller path authority. Source-copy helper is POSIX-only. Existing base64 helpers buffer entire bounded files; repeated `write_bytes` replaces rather than appends a file, so they are not a streaming materializer. |
| `workspace_pool.py:816` `reserve_operation_bytes`, `:890` `reconcile_operation_bytes` | Durable idempotent byte reservations exist, but current helper ALSO adds one workspace job. Extract byte-only reservation/settlement using the same ledger; do not call that helper unchanged or add a hidden job/effect charge. |
| `storage/usage_ledger.py`; `usage_policy.py:54,113` | Stable-key reservation pattern and configured storage limits exist. The usage ledger is not a retained-byte allocation ledger; current broad storage observations are incomplete. Do not enable unrelated dormant effect policy or claim exact total universe storage. |
| `account_deletion.py:582,627,911`; `storage/current_home.py` `check_principal_not_deleted` | Tombstone admission fence and delivery-control erasure exist. Current receipt deletion can remove `inputs_json`. A surviving run row alone does not preserve usable inputs. Reuse author-store fence, not the provider-specific lock that can recreate a deleted home. |
| `storage/cloud_automation_inputs.py:40` `stage_accepted_spec` | Atomic exact text staging, max 2 MiB, no generic ownership/read/bundle authority. Not an artifact registry. |

Inventory commands: `rg` over these modules and `openspec/specs`; `python scripts/check_primitive_exists.py action capture_run_file`, `read_run_file`, `release_run_file` each returned CLEAN against origin/main. This does not mean the new actions are approved. OpenSpec resolved this change to the local `openspec/changes/bind-immutable-run-files` directory; `check-change ... --provider codex` returned ALLOWED. Old scaffold context mentioning STATUS is superseded by AGENTS's typed homes.

## Goals / Non-Goals

**One intent:** supply exact immutable binary/multi-file inputs to any admitted run and its authorized consumers, independent of the intake origin.

In scope: authoring upload capture; post-exit declared workspace-output handoff; generic owned input admission; selected entry and explicit downstream consumption; receiver-owned copies; lossless metadata/bytes; retention/erasure/accounting; immutable reuse seams. Success is actual files larger than a single RPC response, not a base64 string masquerading as delivery.

Out of scope: receiver retry scheduling, arbitrary URL downloads, archives unpacked automatically, executable mount permissions, mutable shared workspaces, global content-addressed deduplication, changing publication authority, new storage billing, providers, user goals or their private workflows. Capturing a file never grants code/network capability.

## Decisions

### Proposed app byte-intake completion (not implemented)

Read `app-byte-intake-amendment.md` for the minimal authenticated paperclip
upload, same-custody streaming and existing-message reference relay. This is a
proposed amendment, not approval to expose legacy authoring actions, modify user
workflows or claim a tool-only binary producer. It preserves existing rollback:
unset capacity stops only new bytes; existing-file use requires a targeted
hotfix if it must be stopped. The previous runtime review did not cover this
missing fresh-user intake entrypoint. No PLAN principle or canonical as-built
spec changes are proposed.

### 1. One declaration and reference contract, not a second file schema

Persist optional `io_manifest` unchanged in `BranchDefinition` round-trips, compiled/versioned snapshots and authoring conversion. Reuse `IODeclaration`/manifest validation rather than introduce incompatible `state_schema` file types. File fields remain JSON dictionaries (bundle fields ordered lists of dictionaries) in thin state; the manifest adds file authority and size/count/media constraints. Validate matching field names and dict/list shape; refuse contradictory declarations. Legacy branches without a manifest keep existing scalar behavior. Do not reinterpret arbitrary dictionaries as capabilities.

The public reference is versioned metadata: opaque `file_id`, exact `size_bytes`, `sha256`, original display filename and declared media type. No storage path, session token or bearer URL. Display filename is preserved as data, never used as a path. Unknown media types stay opaque; no MIME sniffing grants permissions. Internal server rows, not the supplied hash or metadata, decide authority. Nested/bundle order is stable. Receiving a reference validates every field against the immutable record; altered metadata refuses rather than silently relabels.

The v1 exact reference key set is `version` (integer 1), `file_id`, `size_bytes`, `sha256`, `filename`, `media_type`; boolean integers, extra path/credential keys and relabeled metadata refuse. Bounded metadata admits at most 4096 display-filename characters and 256 media-type characters, refusing over-limit values rather than truncating. The display name may contain separators/Unicode as data and is never used for storage or materialization destinations. The strict shared parser takes explicit operational byte/count ceilings, rejects lossy counts/booleans, duplicate names and contradictory bounds, and does not silently clamp runtime declarations to authoring's 8 MiB ceiling. Existing authoring's default parser behavior remains unchanged unless it explicitly opts in. Declared file fields require existing `dict` state, bundle fields `list`; unknown `any` types do not grant file authority.

The current authoring parser silently clamps to 8 MiB and loosely coerces count values. Extract a strict shared file-declaration validator with a caller-supplied technical ceiling; preserve existing authoring limits unless that surface deliberately opts in. Runtime reports its effective limits before intake and rejects invalid/over-limit declarations explicitly. Large runtime files must not be secretly clamped to an authoring limit. Default/cap choice is a pre-build question below, not an unreviewed pricing decision.

### 2. Minimal public and sandbox boundary

#### Public authoring completion amendment (lead approved 2026-09-20)

Actual `write_graph target=branch operation=create` must carry `io_manifest`
through its shared staged conversion, not merely through storage fixtures.
Create/remix and the existing ordered patch language reuse the strict manifest
parser with the runtime technical limits (SQLite-safe bytes, 32 files), without
coercion or truncation. File input declarations must match dict/list state fields.
Validate the final staged model, permitting state and manifest changes in one
atomic patch. Rejected batches save neither metadata nor new versions.

`set_io_manifest` requires an explicit `io_manifest` member: omission rejects;
null clears; an object replaces the complete declaration. Create omission means
no manifest, while remix omission inherits the immutable parent's declaration;
explicit null overrides that inheritance. The canonical nested graph input uses
the existing top-level precedence, including explicit null. Unrelated edits keep
the declaration. Prior immutable versions and admitted input envelopes retain
their original contracts. Build idempotency compares the manifest as executable
content. Existing ACL, private ownership, publication and scope policies are
unchanged; the served sanitizer allowlists only this explicit metadata operation,
not arbitrary fields or new auth grants. Tests use actual canonical and served
WorkOS-mode authoring, publishing, capture, execution and readback.

Use existing graph action dispatch/permission mapping, no additional top-level MCP handle:

- `write_graph action=capture_run_file`: promote an authenticated, still-readable authoring session handle to owner-scoped immutable custody. Accept source session/handle and a bounded caller idempotency label; owner/universe are authenticated, not payload authority. A retry with changed source bytes/metadata conflicts. This works before a direct run exists.
- Workspace producers return a declared output field identifying a safe relative file. The parent captures only after demonstrated managed PID-namespace writer quiescence, before releasing the existing workspace capability or scheduling consumers. There is no active-node capture RPC. An atomic cross-process sole-holder capture claim excludes every new workspace acquisition/reader/writer until copy ends; a count observation is insufficient. Failure to prove exclusivity/quiescence refuses. Only POSIX managed workspace capture is supported initially; no plain subprocess fallback. The actual trusted capability and declared output field supply authority, not JSON owner/run/path roots.
- `run_graph` keeps ordinary `inputs`; approved opaque file references in manifest fields are resolved by generic admission. Queued/triggered/nested/versioned/resume paths call the same service with persisted admitted principals. Ambient actor names never substitute for missing owner evidence.
- `read_graph action=read_run_file`: owned run + bound file + bounded nonnegative offset/count returns exact base64 chunk, next offset, EOF and immutable metadata. This is the ordinary download/export seam; no new public unauthenticated download URL. A trusted sandbox adapter supplies the run/node context and checks input-field binding, not a caller-selected run. Zero-byte files return valid EOF.
- A workspace materialization adapter copies a bound file into the consumer's **own existing authorized workspace**, to a safe relative destination, through the same parent-mediated stream and existing workspace limits. It stages privately and publishes the destination only after final integrity check. It does not call repeated whole-file `ws.write_bytes`, create a fake checkout lease, or expose the sender's workspace.
- `write_graph action=release_run_file`: owner-scoped explicit custody release. Refuse while any active execution binding needs the file; terminal bindings become explicitly unavailable for later reuse. Account deletion is the stronger tombstoning/cancellation path and must not be blocked forever by an active run.

Graph/action metadata, discovery, tool policy, read/write classification and plugin mirrors must agree. Read/download and materialization debit actual transport bytes through byte-only accounting, not effect quota. None of these actions may read another owner's staging by guessing an ID.

### 3. Generic binding service and provenance

Place storage mechanics in `tinyassets/storage/`, the admission/consumption service in the runtime bounded context, and dispatch adapters in `tinyassets/api/`; do not add a delivery-global singleton with hidden ambient identity.

Admission receives a trusted origin envelope already authorized by its caller: persisted owner, physical universe, requested run ID, pinned branch/manifest and admitted entry, plus validated source references. It rechecks current owner/universe authority and tombstones before publication. The service does not itself grant branch visibility, private-child execution, or provider authority. Approved shared/remixed definitions are fine: definition attribution is not custody ownership. Existing publication/delegation checks still run before this service.

Same-owner admission adds bindings to existing owned immutable custody without copying physical bytes or charging retained size twice. A nested child needs explicit mapped references and a new child binding; merely knowing a parent's run/file ID is insufficient. Resume uses saved immutable bindings, not a mutable authoring handle. Missing legacy owner evidence refuses file intake only, not unrelated scalar execution.

For cross-owner delivery, the sender's current authorized source is copied into **new receiver-owned custody** after current receiver/link/contract checks. Binding/run/receipt acceptance is one existing runs-store transaction after byte verification. A retry of the same delivery occurrence returns the existing immutable acceptance; changed source identity/bytes conflict. The sender may subsequently revoke/delete its source; receiver's previously accepted copy remains. Revocation before acceptance aborts. Acceptance is not permission to download any other sender file.

The selected entry can read its bound input immediately. A downstream node can read only bindings carried into its declared `input_keys` through the accepted graph's actual dataflow; an unrelated branch/run or a node merely listing an ID does not gain access. Node-produced capture binds to its producer occurrence and can be passed only along declared outputs/dataflow. Every RPC rechecks persisted running owner/context and cancellation, following PR #3881's trusted context seam. No expansion of the separately reviewed private-child rule is part of this proposal.

### 4. Necessary durable rows and physical custody

Extend the existing runs store with a generic storage module (names below are proposed, not existing tables):

- `run_file_objects`: opaque ID, custody owner/universe, server-generated storage key, exact digest/size, filename/media metadata, lifecycle state, creation time. **No foreign key to delivery receipt, sender account/session, or output link.** Cross-owner copies are independently owned objects, not shared ref-counts across accounts.
- `run_file_bindings`: run ID, file ID, named field and stable bundle ordinal, admitted entry/producer provenance. Unique `(run_id, field, ordinal)`; bound references validated against the stored run owner. Run deletion releases its binding but cannot delete a peer copy. This is the durable input authority when personal delivery controls are erased.
- `run_input_admissions`: receiver/owner-owned execution envelope keyed by run, with admitted owner/universe, immutable branch-version reference or exact selected-entry projection snapshot and digest, input manifest and proven start state. Inputs remain solely in `runs.inputs_json`. Reuse a retained immutable branch version where available; the delivery projection needs exact snapshot storage because it is not currently a standalone version. This record contains no sender personal authority and has no receipt foreign key. One run-keyed start/recovery guard replaces receipt-only dispatch for ALL delivery origins, not only file-bound runs. Explicit migration fences active legacy workers before transferring start authority; never run competing old/new guards. Personal receipt deletion cannot lose a queued accepted receiver run. This is a necessary storage/authority dependency; do not broaden authorization to execute a changed/private graph.
- `run_file_operations`: owner/universe, immutable operation identity/request digest, reservation limit, state, staging inventory and final object IDs. Same-key changed requests conflict. Captures use trusted occurrence derivation; foreground intake uses an authenticated namespaced label. This is a recoverable operation journal, not a second workflow queue.
- `run_file_allocations`: byte-only pending/retained/releasing allocation tied to object/operation and physical storage root identity; exact counters updated in the same runs-store transaction. A durable deletion outbox records physical cleanup debt until verified. Prefer reuse of the existing cleanup-outbox pattern, not cascading deletion of the only inventory of bytes still on disk.

Bodies live under a resolved private data-root subtree, outside repository and mutable universe-home directories; storage keys are random server IDs. Owner access is row authority, not directory names or content hashes. Extract existing verified blob root/file integrity primitives with a streaming interface; do not add run objects to the size-bounded whole-file blob JSON index. No cross-owner dedup in v1: it complicates erasure and independent custody for little initial value. A future backend/dedup can preserve the reference/binding contract.

Copy-before-accept is necessary because SQLite cannot atomically write a filesystem. A complete immutable body may precede committed visibility, but a visible binding must never precede complete verified bytes. After safe same-filesystem finalization and directory durability where supported, commit object readiness and run bindings atomically. Abort never exposes a partial bundle or starts user code. If any member fails, none of the receiver bundle becomes visible.

### 5. Byte reservations and global lock order

Separate three native measures: temporary copy reservation, retained owned bytes, and bytes actually transported/read. Existing workspace hourly byte ledger remains the shared transfer limiter; extract `reserve_bytes`/reconcile internals that do not insert `KIND_JOBS`. Existing workspace callers retain their established job behavior. Stable operation keys prevent retries from double charging; unknown/crashed transfer remains conservatively reserved until recovery can prove actual movement. Do not turn on the unrelated effect-reservation policy.

Retained and pending file bytes require new exact allocation rows; the read-only storage sampler and effect ledger cannot provide atomic admission. Account for both source temporary storage and a new receiver copy before allocating either. Same-owner rebinding uses references, not another retained allocation. Never count platform images/provider installs as user files. Reject unknown storage availability; do not interpret unavailable/zero as free capacity. Under one short capacity transaction, compare configured file-custody capacity and the verified destination filesystem's free bytes against outstanding reservations and safety headroom. Live free space already excludes written retained bytes: do not subtract retained bytes from it twice. Pending reservations may conservatively over-count bytes already staged until settlement. Capacity guards bound this subsystem; unrelated host writers can still exhaust the volume, so ENOSPC must abort safely.

The operational subsystem ceiling is `TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES`, independent of tier/owner pricing. Unset or invalid means intake refuses; ordinary platform rollout configures it globally before advertising file support, never by per-user setup patches. Headroom uses a separately disclosed operational byte value. Do not read unused `TierLimits.storage_bytes`. Admission checks retained+pending+request against the subsystem ceiling and free-pending-headroom against request, at the verified physical root. ENOSPC releases unused retained allocation only after cleanup proof; actual transported bytes remain counted.

Standalone authoring capture intentionally uses `run_id=""` in the existing byte
ledger, because no run exists yet. It requires an actual string (not None/bool),
authenticated universe and namespaced immutable operation; it creates no job or
fake run. Changing standalone/actual-run scope under the same operation refuses.
The free-space floor is `TINYASSETS_RUN_FILE_HEADROOM_BYTES` (default 64 MiB,
nonnegative strict integer). Fresh filesystem free-space measurement occurs in
the short allocation transaction so a concurrently retained copy cannot make a
pre-transaction sample overstate available space. This is technical admission,
not a new tier or price.

Internal `capture_authoring_files` copies up to 32 immutable authoring handles as
one atomic custody bundle before any public adapter is enabled. Operation identity
namespaces the caller label by authenticated owner/universe; immutable request
identity includes ordered handle/session IDs. A committed retry returns current
owned custody even after source revocation, never rereads expired sender data.
Before initial acceptance, all source byte/display identities must still match
under one source-store bundle fence. Interrupted operations retain cleanup debt
and refuse implicit re-copy. Only logical copy bytes enter the existing transfer
ledger; internal integrity hashing does not fabricate another transfer/job.

Physical publication never calls an authority/cancellation predicate while holding
the root coordinator, because those predicates may read metadata stores. Bounded
checks occur before and after that short physical section, with final authority
again checked before object visibility. Cancellation while waiting can leave an
invisible inventoried body for cleanup, never an authorized ready object.

Approved order, including erasure/collector operations:

1. Acquire the immutable operation's cross-process exclusion before recovery/copy; no TTL lease or unlink/recreate of a live lock file. Never await user/provider code under it.
2. Short transaction: author-store admission/tombstone fence, then runs-store allocation reservation. Release both. Acquire/update workspace byte-ledger reservation in a separate transaction, never while holding author/runs locks. Failure leaves only conservative, recoverable reservations; no bytes copied yet.
3. Stream while holding only the actual source capability/stable file handles and operation exclusion. No SQLite writer lock over file I/O. Periodically check cancellation; source expiry/revocation is checked again at final authority fence.
4. Publish durable body names under the physical-root coordinator ALONE, with no SQLite writer locks, then release it. While still holding the immutable operation exclusion, take author-store fence then runs-store transaction; recheck tombstones and current grants, commit ready objects/bindings/run acceptance. A finalized body with no binding is inventoried orphan debt, never an available file. Collector must acquire that same operation exclusion and prove no live publisher/committer before reclaiming it.
5. Settle transfer observations separately, keyed by the durable operation. A crash between commit and settlement is visible repairable debt, not crash-atomic accounting. Recovery must never reexecute user effects.

Collector and erasure take the same operation/object exclusion. They tombstone under author -> runs first, release database locks, then physically clean under the coordinator alone from a durable outbox. Never wait on a blocking physical-root lock under SQLite writers, and never acquire DB locks from inside the coordinator. Retain the operation exclusion across publication and binding commit. Final publication must not reacquire workspace capabilities while holding author/runs locks.

### 6. Cross-platform stream safety

Authoring sources use owned immutable handles, periodic revocation/expiry checks,
and a final short source-store metadata fence after physical publication. Lock
order is operation exclusion -> canonical author/tombstone -> source authoring
writer -> runs binding writer. Never copy/hash or acquire the physical root
coordinator under either metadata writer. The final fence checks the same owned
session/handle and exact original byte/display identity; revocation either wins
before binding or is serialized after it. Bounded refusal is preferable to an
unbounded source writer wait. Existing authoring transactions do not acquire the
canonical author/runs store; account deletion tombstones before satellite phases.

Extend the verified held-file approach instead of `Path.resolve()`/check/`open()`. Reject symlink/reparse/device/FIFO inputs, escaped ancestors and unsupported guarantees before acceptance. Hold a stable regular-file handle and verified parent/root identity; source size/hash must agree at the end. Workspace capture additionally requires post-exit managed PID-namespace quiescence and cross-process acquisition exclusion through copy. Stat/hash checks alone cannot prove an atomic mutable-file snapshot, and plain process-group termination cannot prove escaped-descendant quiescence.

Use bounded read/write chunks, incremental SHA-256, checked integer limits and a byte limit enforced before every write. Memory must scale with chunk size, not file size or bundle size. Keep only bounded metadata. Files remain non-executable data. Destination display names never affect storage paths. POSIX descriptors and Windows stable handles/reparse checks need separate tested implementations; the POSIX-only workspace helper is not Windows evidence. If a source cannot be safely frozen on a platform, explicitly refuse that source operation rather than accept a racy path. Byte-exact authoring input and readback still need native Windows proof, not skips counted as success.

### 7. Retention, tombstones and crash recovery

#### Shared workspace lifecycle and prompt cancellation

The cloud containment lane owns family root/epoch/closing and lease/lock/outbox lifecycle. This lane owns additive physical lease/generation use and capture claims, using that lane's `FamilyFence` and `FamilyAdmission`; family membership never grants file authority. A held fence is only cross-process cooperation, not evidence that physical writers have exited.

Every managed workspace acquisition, including a second node of an existing family member, claims use under the same short family fence. Post-node handoff requires a typed receipt attesting the specific finished producer's managed namespace/process tree (and its leaf when wired), exact mount and lease generation, plus no other writer claims. Whole-family emptiness is only final family release evidence: unrelated authorized siblings may remain running during this handoff. An unavailable producer receipt refuses capture, never substitutes an empty parent process group or an unrelated family receipt.

Under a short family fence, the still-running producer run upgrades its sole use claim to a durable exclusive capture claim keyed by operation, physical lease/generation, family root and epoch. A competing live use claim refuses/backs off; capture never waits for its release while holding the fence. IO then runs outside the family fence and all SQLite writer transactions. All new writer acquisitions consult this claim under that same fence. Between bounded chunks, capture freshly checks closing reason, exact epoch and operation claim; Stop/OOM can persist closing promptly without waiting for the entire stream. Final publication/binding must reject closing or a stale epoch and may never retarget a resumed family. Independent byte/placement/owner authority is still required.

Nonexpiring operation exclusion spans this protocol through final binding, and crashed-claim recovery requires actual lock ownership plus producer/IO completion evidence, never a TTL takeover. Collector and erasure preserve owned cleanup debt until exact physical cleanup. The producer node may be exited while its run is still running; do not weaken `FamilyAdmission` with a terminal-run status bypass. Tests must prove prompt stream-versus-close cancellation, exclusion of new writers and crashed-claim recovery before the adapter is enabled.

- Unbound captures/staging have a disclosed finite deadline, initially reusing the authoring one-hour staging lifetime. Deadline alone never permits collection while a valid operation lock is held. Interrupted operations retain cleanup inventory and reservation debt until a recovery worker owns their lock.
  The first adapter records `created_at` and `unbound_expires_at` in the existing operation row; staging gets one hour from reservation and successful publication starts the full one-hour unbound lifetime. Existing rows with unknown zero timestamps are never guessed old. Binding serializes with the collector in the existing runs writer and refuses expired unbound references; an already bound ready object keeps ordinary explicit reuse authority. An existing five-minute maintenance tick visits at most 32 operations and rotates an in-process cursor, so a busy or corrupt early operation cannot starve later debt. The cursor is scan fairness only, not a second durable queue or authority. Cleanup remains exact-journal and nonblocking-lock governed.
- Accepted inputs are retained with the owner's run bindings, including interrupted/terminal runs needed for inspect/resume and future explicit retry. Do not silently expire active or accepted data just because a sender session expired. Retention is bounded by exact allocation admission, with explicit owner release/account erasure; no unannounced terminal TTL in v1.
- Accepted-but-unstarted execution uses its receiver-owned `run_input_admissions` envelope even if personal controls were erased. The same durable start claim and non-expiring OS guard semantics prove whether initial dispatch is safe; a started-but-unfinished run becomes interrupted, not automatically replayed. Later resume/retry still requires its ordinary fresh execution authority. Source receipt deletion alone neither cancels a receiver-owned accepted run nor grants it new authority.
- Explicit release marks custody unavailable before physical deletion, refuses active bindings, and reports that future reuse/resume will fail. Export is available beforehand through the exact reader. Dangling bindings return a stable released/missing-input refusal, never empty bytes.
  A failed single-file cleanup does not revoke ready siblings. Both binding and reading require the requested ready object's server storage identity to be outside the validated cleanup subset of the original operation inventory. Missing, malformed or inconsistent journals refuse rather than accepting the operation's coarse `cleanup` state as permission.
- Account tombstone wins against intake/finalization/read. Erasure cancels owned execution, removes owned bindings/custody and enqueues deletion; future operations cannot recreate the home. Read chunks recheck owner deletion before returning. Bytes already delivered to an authorized reader are not recalled. Another owner's accepted independent copy is preserved; personal delivery controls/attribution are still erased by the existing rule.
  The first adapter preserves the existing account-deletion active-work refusal; it does not add a bypass or claim a new cancellation mechanism. After the existing tombstone, the runs-store phase settles exact principal-owned custody under operation exclusion before deleting FK-ordered rows. A busy or failed physical cleanup leaves that store phase unfinished, with inventory/allocation debt retained; other existing deletion phases still run. Existing maintenance recognizes tombstoned operation owners and can finish physical debt without waiting for the one failed account request. Generic row deletion rechecks released/zero-allocation/no-cleanup/no-ready-object state in its own transaction, then deletes owned bindings, objects, allocations/outbox and operations in FK order, never by a mutable home match. Root custody tables are classified as preserved run history for scoped reset; that does not authorize reset to erase accepted bytes.
- Crash before body finalization: recover lock, remove inventoried staging, reconcile reservation. Crash after finalization before DB commit: retain inventory and collect an unreferenced object after proving no ready binding. Crash after acceptance: recover existing binding/operation and settlement debt, never copy under a different identity or rerun accepted effects. Crash during erasure: tombstone denies read and outbox persists until physical deletion verified, then allocation is released. Unknown/corrupt journal/index fails closed and is reported; do not recursively scan/delete guessed paths.

### 8. File-first implementation and future retry seam

#### Shared execution lifetime guard

The lead approved a common `storage/run_execution_lock.py` primitive for every managed execution origin, including ordinary runs with no file/input-admission record. `try_run_execution_lock(base_path, run_id=...)` yields a nonblocking current OS guard or no guard. It binds the canonical runs database and exact run identity, checks acquiring PID/thread/lifetime, and never unlinks its stable sidecar. Callers retain it throughout actual execution; absence of a local Future is not worker-death evidence. Recovery/retirement holds the same acquired guard while checking current family epoch/kernel evidence and mutating, never probe-release-then-act. Lock order is execution guard, short family fence, then short database transactions.

Stop/OOM is deliberately outside that lifetime guard: it acquires only the short family fence, persists monotonic closing for the exact current epoch, releases the fence and signals exact owned processes. Later cleanup acquires the execution guard without holding the family fence while waiting. The run-input envelope's `execution_started_at` and `claim_token` distinguish admitted/unstarted from started; the OS guard does not itself authorize a new run or replay. Consumer intent stores and file bindings reference the same run ID rather than adding another start claim/queue. All delivery legacy workers must still be explicitly fenced before switching dispatch authority. Cross-lane integration needs a bounded independent review before activation; the primitive alone does not change ordinary execution/recovery behavior.

Fable integration review at `f68a9db8` returned ADAPT; the lead accepted the precise queued-handoff correction on 2026-09-19 (`execution-guard-review.md` is the full result). A healthy pool can hold an unstarted queued job without its lifetime guard yet. Therefore a free guard plus queued status, or the existing run insertion timestamp `started_at`, is NOT orphan evidence. Recovery leaves proven-unstarted work alone or redispatches through the SAME guard and conditional start; it never retires queued work on guard availability alone. Under the guard, start and terminal transitions compare the expected prior status/claim, and zero affected rows mean exit without user execution or overwriting the winner. Ordinary managed invocation and request-thread provider refusal require the same rule. A late pool worker must not turn cancellation, retirement or a superseding resume back into running.

The common guard is host-local, matching the SQLite storage assumption, and non-reentrant. Inner invocation receives the already-held guard and revalidates it rather than reacquiring. File lane owns admitted start-marker CAS and prepared worker; cloud owns ordinary start/terminal CAS, Stop, and exact managed namespace recovery. A durable started marker even with queued status is ambiguous after worker loss: keep recovery debt without replay until the managed epoch/kernel evidence resolves it. Pre-invocation failures in the currently guarded attempt can use cloud's conditional unstarted-terminal seam, never an unconditional SQL/status fallback.

New common admission starts only a queued row with no start marker, no claim token and no cancellation intent, under current owner/universe and expected-state CAS. Interrupted/resumed/other terminal states are never auto-restored by a late worker even if the marker is absent. Legacy delivery's old startup-restoration rule belongs only in the explicit old-worker fencing/migration operation, not generic dispatch. A zero-row claim starts nothing. A durable-started queued/running row remains no-replay recovery debt until exact managed evidence resolves it.

The internal shared worker API is `run_input_runtime.dispatch_admitted_run(base, run_id=..., prepare=...)`. It submits the same reserved run to the existing executor in a fresh context. Its trusted origin callback `prepare(base, envelope, *, author_conn, runs_conn)` uses the already-held authority/run transactions and returns `PreparedRunExecution(identity, actor, bind_provider=None, recursion_limit=...)`; it may not open nested writers or call providers. Source/branch/publication checks remain origin-specific current checks. The worker reloads the pinned graph and sole run-row inputs after the callback, commits the irreversible marker, releases DB locks, then optionally calls `bind_provider(base, envelope, branch)` under the fresh owner identity. Cloud's `_invoke_prepared_branch(..., _execution_guard=guard)` keeps that same guard through provider settlement. Cloud's `terminalize_unstarted_run` performs conditional pre-invocation terminalization and cancellation wins; ambiguous prior-start recovery may not use it. These cloud seams are explicit integration dependencies, not compatibility fallbacks. No adapter is activated before they and legacy-origin migration are ready.

Before dispatch and again before worker admission, compatible invocation/terminal
hooks are verified without durable marker/events/provider side effects. The
worker acquires the existing scoped-reset shared maintenance barrier outermost
(bounded five seconds), checks clean recovery state, then enters its run guard
and authority transactions. It retains that shared barrier through settlement,
never invokes root-creating/recovering service initialization, and never takes a
maintenance barrier from inside an origin callback's writers. An independent
reset either wins before execution or waits for the existing writer boundary.
Current-attempt pre-invocation failures may terminalize only queued status;
interrupted/running/resumed and terminal winners stay untouched. A marker found
after restart is not equivalent to this same-attempt knowledge.

Current helper integration proves actual graph/cancel/settlement, but no public
origin is activated yet. Nested synchronous adapters require trusted execution
depth and existing child-executor parity before activation; they must never
schedule a waiting child onto its saturated parent pool or accept client-authored
depth as authority. There is no new pool/queue. The complete origin matrix and
immutable file consumption remain acceptance requirements, not claims derived
from storage tests.

Public status adapters must display durable-started/queued ambiguity as held or
uncertain, with existing action-may-have-occurred semantics. They may not present
it as indefinitely healthy queued work, manufacture terminal success/failure, or
auto-replay it. Reuse prepared-state classification, not a second recovery queue.

Lead-approved direct-run parity (2026-09-19): the trusted `PreparedRunExecution`
carrier retains recursion limit and adds optional concurrency override and node
status callback, passed unchanged into the existing invocation. These are server
adapter values, not sandbox-authored identity/depth or a different executor.
The common dispatcher also accepts a trusted optional `on_settled(base, run_id)`
observation hook after submitted worker scopes unwind, including failure and
pool cancellation. A callback is not evidence of a terminal outcome: consumers
must read canonical current status and apply only their existing idempotent,
owner/tombstone-authorized terminal projection. No provider call, effect replay or
restart is authorized. Callback errors are logged without rewriting execution;
existing durable projection repair remains required, not replaced by notification.
Published pins omit the presentation name by design. Common execution applies
the existing `_load_branch_version` display-name fallback only to a detached
runtime copy after verifying the persisted pin; stored snapshot/hash stay exact.

Durable restart dispatch still needs the narrow pending
[`origin-recovery-amendment.md`](origin-recovery-amendment.md): explicit immutable
origin kind/version and typed direct options in the existing envelope. No schema
or recovery implementation is authorized by this link alone; independent shape
review precedes it. File bindings never identify an origin. Indefinitely pending
after restart is not a durable-dispatch pass.

Build the generic admission/custody service and actual sandbox consumption first; adapt foreground, queue/trigger, nested/versioned/resume and delivery paths to it. Do not substitute receiver retry for files. Later retry can bind the same receiver-owned immutable object IDs to a new run after fresh branch/link/resource/owner checks; no mutable sender handle must be reread. This proposal does not promise idempotent user effects or implement scheduling/replay. Keep `connect-cross-user-nodes` retry tasks open.

## Risks / Trade-offs

### First independently usable release boundary (lead approved 2026-09-19)

Ship single-owner authoring capture -> direct/version-pinned input binding ->
selected entry and explicitly declared downstream bounded chunk reads -> owned
export/release. Reuse existing graph action routing and trusted node/dataflow
context; no additional top-level handle or arbitrary path/URL download. This is
the first releasable origin, not a narrowing or completion of the full proposal.

Before that release, its custody rows require finite unbound retention, exact
inventoried crash collection under the operation lock, account-tombstone erasure,
and explicit scoped-reset classification. Readers own the same operation guard
against physical collection and recheck deletion/current scope before returning
bytes. Same-owner direct admission validates exact reference metadata and binds
only declared fields under its existing admitted principal before dispatch;
attribution is not authority. Actual sandbox tests must prove chosen-entry and
downstream binary/multi-file reads while unrelated node/run IDs refuse.

Lead disposition for bundle release (2026-09-19): public per-file release revokes
only the requested owned object, never silent sibling deletion. Derive cleanup
subset from exact server-owned original inventory, not caller paths. Under the
same operation guard, retain all ready sibling bytes plus the failed/unknown
subset's capacity as debt until exact physical absence is proved. Only then
recompute retained size from surviving ready siblings and restore committed
operation state. A different sibling release while debt is unsettled refuses
explicitly; same-object release retries may finish that debt. Same-label capture
replay may not recreate or resurrect released members. Interrupted full-copy
cleanup still uses the entire original inventory. No new table or pricing.

The five release gates are: reader and trusted RPC; direct/versioned bindings;
public routing/policy/discovery parity; collector/erasure/reset lifecycle; and
complete platform/regression proof plus exact-head review/CI/deploy/rendered app
acceptance. Producer-workspace quiescence/capture, nested/resume/trigger adapters,
cross-owner copies/erasure and materialization remain explicitly OPEN until their
own integrated proof. Do not tick their broader task boxes for this first release.

- New storage/authority boundary -> independent shape review and exact-code review, including two-owner erasure races, before deployment.
- Cross-owner physical copies use more space -> exact visible per-owner accounting; no early global dedup complexity.
- File capture from mutable workspaces lacks a general snapshot primitive -> quiesced source capability or verified stable-handle refusal, never silently accept a mixed snapshot; review required below.
- Host disk telemetry is not a global host scheduler -> fail-safe reservations plus ENOSPC handling, explicit limitation and no total-storage claim.
- Existing scalar paths span several run origins -> adapter matrix tests; do not regress scalar workflows or widen private execution to make file tests pass.
- Retaining terminal inputs consumes capacity -> explicit export/release; proposed fixed terminal expiry would be a separate user-visible policy change.

## Migration Plan

Additive schema with legacy scalar/JSON rows unchanged; no bulk backfill from expired handles. Existing file-shaped delivery payloads keep refusing until both custody and consuming runtime are available. Review the exact activation mechanism before release; runtime capability discovery must not advertise a half-installed route. No production flag/host changes in this proposal.

Deploy integrated schema, safe storage helper and all admission/consumption adapters together. Verify schema recovery, physical-root identity, limits and mirrors before exposing file intake. Unsetting the custody ceiling stops only new byte capture and reports capture unavailable. Existing retained references remain bindable, executable and exportable without that configuration fence; bound files do not expire. Preserve accepted objects/bindings and compatible read/export/cleanup. Stopping existing-file binding or execution requires a targeted hotfix redeploy, never an old binary that silently discards declarations/references or downgrades them to JSON. Destructive migration rollback is forbidden.

Release lead owns CI, merged/deployed SHA proof, canonical canary and ordinary live-app acceptance. Root must explicitly reopen the second-user test lane after the separate general onboarding/removal fixes and authorize that test; this proposal grants no account/browser access.

## Approved shape and remaining proof

The four review corrections and six lead qualifications are settled in `review-disposition.md`. No additional broad shape round is required absent a newly discovered irreversible boundary. Implementation must demonstrate cross-process source exclusion and namespace quiescence, orphan-collector fencing, byte-only accounting, and legacy-worker guard migration rather than treat review prose as proof. Native Windows authoring intake/chunk readback is a release gate for claimed Windows support; workspace-produced intake remains explicitly POSIX-only. Runtime technical limits must be exposed, and global capacity configuration must be part of normal rollout before users encounter the feature. No private-user setup workaround is permitted.

## Test and ordinary-user acceptance plan

Red-first tests cover binary non-UTF8 and zero-byte members, ordered multi-file bundles larger than a single RPC response, filename preservation without traversal, exact independent hashes, bounded memory, and no partial accepted bundle. Exercise authoring capture and real sandbox workspace capture, entry read, downstream read and own-workspace materialization. At least one file exceeds the authoring-only 8 MiB ceiling if the reviewed runtime limit permits it; report actual limits rather than claim unlimited files.

Authority matrix: forged IDs/hashes/metadata/context; foreign staging; sibling run/placement; authorized remix vs foreign private branch; owner revocation/deletion before capture, during copy, at commit and before chunk return; child mapping vs guessed parent handle. Both owners and both universes are disposable fixtures, never the blocked real second user.

Erase the sender's receipt/account specifically **after acceptance but before initial worker dispatch**, then restart the daemon: the independently owned receiver envelope and files must dispatch once if proven unstarted. Repeat after a recorded start: it must become interrupted without effect replay. Testing only file downloads after a completed run would miss the existing receipt-backed execution dependency.

Concurrency/fault matrix: byte reservation races, same-key replay/changed source, cancellation at each phase, ENOSPC, short writes, source mutation, symlink/reparse/root swap, receipt deletion after acceptance, owner release during active read, crash on either side of body/DB publication and accounting settlement, tombstone/collector races. Assert no user-code execution before all inputs commit and no invented recovery success. Test native Windows and Linux oracle; distinguish unsupported source semantics and skips. Run existing scalar, delivery, authoring, nested-run and account-erasure regressions, mirrors, lint and required CI.

Live acceptance after deployed proof: an ordinary owner asks their app agent to create a workflow consuming a binary file and a multi-file bundle, then an authorized second owner connects a receiver whose selected entry and downstream node consume exact independent copies and return hashes/derived output. The agents—not platform operators—author/configure those workflows. Sender receipt/source deletion must leave accepted receiver inputs readable; receiver release must make later reads explicitly unavailable without affecting sender data. Confirm no host machine is online. Capture rendered conversations, exact sizes/hashes, deployed identity and permissions; then request the agent's checklist response. Receiver retry and the umbrella's remaining two-owner criteria stay open until separately proven.
