# Foundation verification — not complete file capability

2026-09-19, isolated `codex/run-input-file-custody-proposal` worktree. Corrected
design/disposition committed before runtime code at `1000bd83`; current origin/main
integrated non-destructively at `9f29213c` (including merged PR #3881). No public
capture/read route, scheduling migration, user workflow/account or production
setting changed. Required CI and exact-code review have not run for this slice.

## Red-first and implemented core

- `python -m pytest -q tests/test_run_file_foundation.py`: initially 9 expected
  failures/1 pass (missing manifest preservation and byte-only API). Intermediate
  run caught the separate immutable-version snapshot dropping its manifest.
- `test_run_file_store.py` and `test_run_input_admissions.py` each initially failed
  collection because their planned storage module did not exist. After implementation,
  the 3 new files pass 27 tests on native Windows and Linux.
- Implemented optional lossless `io_manifest` through branch model, stored graph,
  update/fork, immutable version and authoring validation; absent legacy manifest
  remains absent so existing snapshot hashes do not change.
- Extracted `reserve_transfer_bytes` without adding workspace jobs; existing
  `reserve_operation_bytes` retains its job behavior. Scope mismatch refuses;
  existing byte settlement records actual consumed transport after failed copies.
- Added internal exact allocation/operation journal, prewrite key inventory,
  immutable owned object/binding and cleanup-debt transactions. Tests cover capacity
  races, changed replay, bundle validation before insertion, explicit config,
  owner/run binding, same-owner reuse and no receipt dependency.
- Added internal run-owned execution envelope preserving pinned projected snapshot
  or version/hash reference, not a second `inputs_json` copy. Tests cover erasure
  independence, version integrity, owner check before private deserialization,
  changed acceptance and transaction requirements. It deliberately has no unguarded
  start API; worker/migration/start-guard wiring is not yet implemented.

These are internal storage seams, not physical streaming proof. Callers still
must implement the reviewed held-operation, tombstone/current authority,
publication/collector and actual physical deletion proof before invoking them.

## Focused regression proof

Command suite (same 14 files on each OS):

```text
python -m pytest -q -rs tests/test_run_file_foundation.py tests/test_run_file_store.py tests/test_run_input_admissions.py tests/test_workspace_pool.py tests/test_branch_definitions_db.py tests/test_branches.py tests/test_authoring_file_io.py tests/test_authoring_sessions.py tests/test_delivery_public.py tests/test_delivery_runtime.py tests/test_delivery_node_rpc.py tests/test_delivery_account_deletion.py tests/test_run_branch_version.py tests/test_branch_versions_rollback_columns.py
```

- Native Windows, Python 3.14: **371 passed, 2 skipped** in 30.13s. Skips are
  `test_workspace_pool.py:1380` POSIX fork and `test_delivery_node_rpc.py:302`
  Linux bubblewrap. New core tests have no skips.
- Linux, Python 3.11: **373 passed, 0 skipped/0 deselected** in 92.39s.
  Canonical `scripts/linux_oracle.py` could not connect to Docker Desktop's
  `dockerDesktopLinuxEngine` pipe. Used the established WSL Ubuntu Docker fallback:
  `tinyassets-workspace-browser-probe:e2d3edcc`, immutable image ID
  `sha256:1b69d8536490285c7c7a13f1efe53ebe847696a99ae567dbbd2b9761ee0c530a`.
  Read-only current worktree mounted `/src`, workdir `/src`, network none,
  memory 2g, pids 1024, seccomp unconfined; `PYTHONDONTWRITEBYTECODE=1`,
  `TMPDIR=/tmp`, `TINYASSETS_DATA_DIR=/tmp/tinyassets-test-data` and
  `-p no:cacheprovider`. No host/user data mounted.
- `python packaging/claude-plugin/build_plugin.py`: **469 runtime files,
  import probe OK**. All new/changed canonical runtime files mirrored.
- Ruff passes changed modules/tests except the pre-existing 8 E501 decorative
  mojibake separator lines in `daemon_server.py`; same eight found at the pinned
  integrated HEAD via `git show HEAD:tinyassets/daemon_server.py | python -m ruff
  check --stdin-filename tinyassets/daemon_server.py - --output-format concise`.
  No unrelated cleanup performed. `git diff --check` and strict OpenSpec pass.

## Next integrated work and shared ownership

File lane owns custody/run envelope, byte-only extraction, physical lease/generation
use/capture claims and compiler/effector adapters. Cloud lane owns FamilyFence,
root/epoch/closing and lease/lock/outbox lifecycle. Agreed API dependency is cloud
core `1b99c8c4`: `try_family_fence`, `FamilyAdmission.require`, and the planned
`BranchExecutionContext.workspace_family` snapshot. The context is not authority;
fresh fence/row validation and separate byte/placement authority remain required.
Legacy `None` is not inferred from lineage. The cloud commit is not yet integrated
here; no duplicate lock manager was created.

Still open: strict runtime manifest/reference admission, authoring service/native
Windows adapter proof, producer namespace-empty/exclusion claims, all origin/read/
materialization adapters, all-delivery active legacy-worker fencing and run-keyed
start migration, account tombstone/cleanup service, rollout config, exact-code
review, CI/deployment and ordinary two-owner app proof. Receiver retry stays queued.

## Physical stream checkpoint (2026-09-19)

`test_run_file_streams.py` was red first: 9 expected missing-method failures.
The existing held-file/root traversal is extended by an internal bounded stream
helper; no public read authority, authoring adapter, producer snapshot or account
service is enabled. Stage iteration and incremental digest checks hold neither
SQLite nor root coordinator; only final same-parent no-clobber link/unlink
publication holds the physical coordinator. Caller must inventory both names and
hold operation exclusion through binding; interrupted two-name publication is
cleanup debt, not a second allocation. Unsupported hard-link filesystems refuse.

- Native Windows `python -m pytest -q tests/test_run_file_streams.py
  tests/test_execution_blob_proof.py`: **40 passed, 1 skipped** (POSIX FIFO/symlink
  fixture). Exact 10 MiB non-UTF8 stream, ordered binary/empty members, bounded
  memory (<3 MiB traced stage peak), chunk readback, no-clobber, cancellation,
  short writes and ENOSPC preserve exact actual read/write diagnostics. These are
  native physical primitive tests, not the remaining authoring adapter gate.
- Linux fallback container described above, same two tests plus the three core
  tests: **66 passed, 2 skipped** (existing Windows-only blob-proof fixtures).
  All new stream tests execute on Linux. No live data or network used.
- Root coordinator stays available while stream source is paused; cancellation
  prevents the next write. This is not yet the separate family-close/claim test.

The shared lifecycle protocol now explicitly uses short family-fence transitions
and durable exclusive physical capture claims. Stop/OOM need not wait behind file
IO. Required quiescence is the particular producer namespace/tree plus exact
mount/generation and source exclusivity, not an entire otherwise-running family.

## Shared execution-guard checkpoint (2026-09-19)

`test_run_execution_lock.py` first failed collection because the proposed common
module was absent. After implementation, **8 passed on native Windows** and
**8 passed, zero skips on Linux** (same disposable fallback environment above).
Proof covers independent spawned-process contention, crash release without
sidecar replacement, same-process thread contention, database/run scoping,
expired/constructed/PID-mismatched guard refusal and ordinary no-admission runs.
Command: `python -m pytest -q tests/test_run_execution_lock.py` (Linux adds
`-p no:cacheprovider`). Ruff, mirror build/import probe and diff check pass.

This adds no runtime acquisition/recovery caller yet. Cloud owns ordinary managed
execution/epoch/cleanup integration; file lane owns admitted/prepared dispatch.
Both consume this exact common lock. Stop never waits for it. Cross-lane shape
review and integrated Stop/remote-worker recovery tests remain required before
activation; no claim of managed-worker protection from an unused primitive.

## Prepared admission/worker checkpoint (2026-09-19)

Fable 94507 exact guard-checkpoint review and the lead's accepted queued-handoff
ADAPT are retained in `execution-guard-review.md` and its disposition. The later
prepared worker is NOT included in that review approval.

- Start tests were red first (7 absent-API failures), and worker tests first
  failed collection for the absent runtime module. New storage start state uses
  common guard plus expected status/owner/universe/claim CAS and cancellation
  intent checks. Only queued/unstarted may start; interrupted/resumed rows never
  auto-restore. Zero-row claim means no execution. Already-started queued state
  remains no-replay debt pending exact managed recovery, not invented kernel proof.
- Shared origin callback receives existing `author_conn` and `runs_conn`, so
  source authorization does not reenter a writer. Provider binding occurs only
  after committed start and outside both writers, under fresh owner identity.
  Graph/inputs are reloaded from the immutable envelope/run row, not a callback
  replacement. Same run, same guard, same existing executor, no second queue.
- Focused 9-file cohort: native Windows **100 passed, 1 POSIX-only stream skip**;
  Linux **101 passed, zero skips** in the recorded disposable fallback environment.
  Files: `test_run_file_foundation`, `test_run_file_store`, `test_run_file_streams`,
  `test_run_input_admissions`, `test_run_execution_lock`, `test_run_input_start`,
  `test_run_input_runtime`, `test_delivery_runtime`, `test_delivery_attempts`.
  Tests cover saturated pool/free guard, five terminal/resume late-worker winners,
  immutable input/target reuse, no local-context leakage, actual SQLite writer
  availability during provider bind, marker replay refusal and foreign owner refusal.
- These worker tests use a traced invocation boundary and one traced terminal
  seam. Real graph integration REQUIRES the cloud lane's `_execution_guard`
  invocation seam and `terminalize_unstarted_run(base, *, run_id, execution_guard,
  status, error)` CAS. There is deliberately no old-runtime fallback. Native
  Windows worker tests pass after aligning that exact keyword-only contract.

No public origin adapter or legacy dispatch migration is enabled. Complete
ordinary managed lifecycle, exact namespace recovery, source custody service,
all file adapters, exact-head review/deployment and real two-owner proof remain open.

## Strict file contract checkpoint (2026-09-19)

New `test_run_file_contract.py` was red first (missing module). Opt-in strict
parsing reuses authoring `IODeclaration`/`Manifest` vocabulary and keeps legacy
default behavior unchanged. Exact references preserve display metadata as data,
reject path/extra keys and bool-as-int, require immutable owned-row equality,
and apply bounded file/bundle/state contracts without granting ownership.

Native Windows and Linux each: **48 passed, zero skips**, command
`python -m pytest -q tests/test_run_file_contract.py tests/test_authoring_file_io.py
tests/test_run_file_foundation.py` (Linux uses documented fallback). This includes
explicit >8 MiB runtime declaration versus unchanged legacy clamp, 13 malformed
declaration variants, duplicate names, exact reference metadata, zero-byte and
optional empty bundles, and required dict/list state shape. No public route is
enabled by these pure helpers. Mirrors/import and lint pass.

## Authoring source and acceptance-fence checkpoint (2026-09-19)

Internal handle adapter streams exact owned server-private immutable bodies,
rechecking expiry/revocation between bounded chunks. Final short metadata fence
serializes revocation with binding; it holds no writer during copy/hash or physical
publication. Lock order is operation, canonical author/tombstone, source-authoring,
runs. Reverse-order audit: AuthoringStore transactions touch only their own store;
service publication returns from that transaction before other work; account
deletion commits tombstone first and processes satellite stores in separate phases.

Red-first missing adapter/fence tests now pass on native Windows and Linux:
`python -m pytest -q tests/test_run_file_authoring_source.py
tests/test_authoring_file_io.py tests/test_authoring_sessions.py`: **65 passed,
zero skips on each OS**, using the recorded disposable Linux fallback. Includes
3 MiB non-UTF8 input, original metadata, foreign/refused scope, revocation and
expiry between chunks, bounded lock contention, and independent spawned-process
revocation blocked until actual custody binding commits. The fixture creates a
real internal authoring handle; this is not public upload/whole acceptance proof.
Canonical authority, operation exclusion, service orchestration and all public
file adapters remain open. Prepared worker review/disposition retained separately;
its requested integration corrections are not yet claimed complete.

## Real prepared-run integration checkpoint (2026-09-19)

Cloud `af3156ba` and its queued-only terminal CAS correction `f13224a4` merged
non-destructively after the lead dispositioned Fable 38182. Source-authoring
checkpoint is `6a1ffd8a`. Review corrections now implemented: pre-marker seam
compatibility refusal, exact queued-only local terminal predicate, true
`request_cancel` intent-before-dispatch, and actual marker-agnostic same-attempt
pre-invocation terminal settlement. Callback actor is captured before preparation;
secondary settlement failures are logged. Restart markers remain no-replay debt.

New integration suite first reproduced four failures: both missing/incompatible
hook cases, independent reset entering during settlement, and pending recovery
starting execution. The existing shared maintenance barrier plus clean-state
check now encloses run guard through provider settlement; no service root/recovery
initializer is called. New tests use the actual graph invocation and terminal
hooks, not traced substitutes. Provider callbacks are deterministic local fixtures,
not live provider/account evidence.

`python -m pytest -q tests/test_run_input_integration.py
tests/test_run_input_runtime.py tests/test_run_input_start.py
tests/test_workspace_family_execution_guard.py`: native Windows **49 passed,
zero skips**; Linux **49 passed, zero skips**, recorded disposable fallback.
Includes independent-process reset exclusion through settlement, reset-first
waiting before run guard/marker, nested existing service/worker shared leases,
actual graph success/Stop, null-marker cancellation and post-marker binder failure
with cancellation/resumed winners. Older traced worker tests remain useful unit
contracts but are no longer the sole integration evidence.

No public origin, legacy-worker migration or file reader/materializer is activated.
Nested synchronous common dispatch still needs trusted depth/child-pool parity;
all-origin file capability, exact-head review, CI/deploy and two-owner proof remain
open. These tests do not prove cloud producer namespace emptiness or file custody
service orchestration.

## Legacy recovery exclusion correction (2026-09-19)

Root's storage-review integration finding reproduced in four red cases: an aged
admitted run with no family assignment was interrupted by another process's read
or startup sweep, both before and after the durable start marker. Current ordinary
`create_run(...queue_universe_id=...)` assigns family metadata immediately; the
fixture deliberately models an existing reservation whose universe is assigned
after insertion. Common admission must protect both forms, not depend on timing.

Both legacy paths now exclude `run_input_admissions` using their existing runs
connection. The status-write transaction covers schema/predicate lookup and the
conditional rewrite so concurrent admission cannot slip between probe and retire.
Read-time rewrite also rechecks family-null predicates. Absent legacy table is
supported without schema creation; a malformed present table/view fails closed.
No new writer connection, authority grant, recovery queue, marker reset or replay.

Native Windows/Linux: **48/48 passed, zero skips** for
`test_run_input_recovery.py test_run_input_integration.py
test_workspace_family_execution_guard.py test_workspace_family_transitions.py`.
After adding corrupt-schema tests, the final recovery file alone passed **12/12
on each OS**. Same recorded commands/environment. Prepared started debt still
requires exact guarded recovery; exclusion is not a claim that cleanup is complete.

## Standalone binary custody service checkpoint (2026-09-19)

New `test_run_file_capture.py` first failed collection for its absent service.
Internal standalone capture now connects existing immutable authoring handles,
bounded streams, per-operation OS exclusion, tombstone/current admin fences,
prewrite inventory, short serialized capacity measurement, exact ready-object
transaction and separate existing transfer ledger settlement. No API is exposed.
The new operation guard uses the existing kernel-lock primitive, not a second
workspace-family manager or TTL lease. Both publisher and future collector must
own this same stable sidecar through their operation.

Native Windows **67 passed, 1 skipped** (POSIX FIFO/symlink fixture); Linux **68
passed, zero skips** for `test_run_file_capture.py test_run_file_lock.py
test_run_file_streams.py test_run_file_authoring_source.py
test_run_file_foundation.py test_authoring_file_io.py`. Same recorded disposable
Linux environment. Exact 3 MiB binary plus zero-byte bundle, same-label replay
after source revoke, changed-order conflict, foreign/revoked grants, revocation
during copy, actual partial ENOSPC, publication-before-DB fault and cross-process
operation exclusion/crash release are covered. Failure retains exact inventoried
cleanup debt and conservative retained allocation; transport records actual
logical copy bytes, including consumed bytes on ENOSPC. No fake run/job rows.

One integration test reproduced metadata callback execution inside the physical
root coordinator. Corrected publication checks authority/cancellation before and
after the short physical section, never under it; final ready-object commit
still revalidates source/grant/tombstone. Cancellation while awaiting publication
can leave an invisible inventoried body, not a ready reference. The regression is
included in the final cohort above. No user/provider code executes inside fences.

Remaining independently releasable single-owner path: public capture/read/release
adapters and policy/discovery, direct/versioned binding, actual selected-entry and
declared downstream chunk RPC, finite unbound retention/collector, account erase
and reset classification, final exact review/CI/deploy/app evidence. This checkpoint
does not implement those, or claim workspace producer snapshot, cross-owner file
delivery, all origins, reader/download, materialization, cleanup or completed
file capability. Full broader tasks remain open.

## Shared envelope erasure/reset correction (2026-09-19)

Consumer integration supplied two red failures; reproduced independently here:
even an empty `run_input_admissions` table was unclassified by existing root
run-history reset inspection, and real account erasure after a home rebind left
the private envelope in the old universe, blocking deletion of its parent run
with a foreign-key failure. Added only the envelope to known run history and
owner-keyed personal erasure despite a universe column. No widening of reset
scope, source attribution deletion or blind file custody cleanup.

`python -m pytest -q tests/test_run_input_erasure.py
tests/test_account_deletion.py tests/test_scoped_reset_mutation_proof.py`:
native Windows **62 passed**, Linux **62 passed**, zero skips. Two-owner test
uses real `delete_account` after home rebinding and verifies peer envelope/run
remain. External billing/identity callbacks are local fixtures. File custody's
separate physical cleanup/reset classification remains a release gate.

## Reader and exact per-file cleanup checkpoint (2026-09-19)

Reader/collector tests were red first for absent modules; subset cleanup was red
for the missing selective-release contract. Internal owned run-bound reader now
returns exact <=1 MiB base64 chunks/metadata, rechecks current authority before
return, and holds operation exclusion against collection. No arbitrary custody
ID alone grants a read. Known read bytes settle through the byte-only ledger;
unmeasured failures conservatively retain their bounded reservation.

Collector consumes only existing revoked cleanup debt under the same operation
guard, verifies physical-root identity and server inventory, removes exactly its
`.part`/`.body` names, proves absence and only then releases/reconciles capacity.
No recursive scan, pathname payload, TTL takeover, ready-object deletion or SQL
writer across physical cleanup. Per-file release refuses active bindings and
preserves bundle siblings after successful cleanup. Root inspection subsequently
found sibling reads incorrectly refused while a subset cleanup remained pending;
the correction and added proof are recorded below. Unknown deletion keeps capacity debt and an explicit
cleanup-pending result; retry cannot resurrect a released object or replay copy.

`python -m pytest -q tests/test_run_file_reader.py tests/test_run_file_cleanup.py
tests/test_run_file_store.py`: native Windows **27 passed**, Linux **27 passed**,
zero skips. Includes binary ranges/zero-byte EOF, foreign scope/invalid ranges,
revoke-before-return, active-binding refusal, sibling read after selective release,
repeated release, failed cleanup/retry capacity, changed capture replay, corrupt
inventory/path refusal, live operation exclusion and unchanged transport charge
after cleanup. Earlier full reader/capture/cleanup integration: **19/19** on each
platform. These are internal services, not public reader/RPC, scheduled retention,
account physical erasure or completed end-to-end file capability.

## Finite retention and pending-cleanup sibling correction (2026-09-19)

Nine new retention cases started red. Operation timestamps now give staging and
successfully published unbound inputs one hour each at their respective start;
unknown migrated deadlines remain zero and cannot authorize deletion. The existing
five-minute daemon maintenance loop scans a rotating bounded batch, rechecks
expiry under the operation guard and runs writer, and revokes only unbound ready
members. Terminal bound inputs stay retained. New bindings cannot race expiry
into accepting an expired unbound object. No SQL writer spans physical cleanup,
no TTL steals a live operation lock, and cleanup never refunds transport bytes.

Root reproduced an a2003223 isolation error: ready sibling binding succeeded but
reading failed while another object's cleanup was pending. The regression now
reads that sibling both through its terminal binding and a new queued-run binding
before cleanup can succeed. The shared visibility rule verifies the ready object
is outside an exact valid cleanup inventory; four journal corruption/membership
cases fail closed for both read and binding. Pending deletion capacity remains
unchanged until exact absence proof.

`python -m pytest -q tests/test_run_file_retention.py tests/test_run_file_store.py
tests/test_run_file_reader.py tests/test_run_file_cleanup.py`: native Windows
**40 passed**, Linux **40 passed**, zero skips, using the established read-only
WSL Docker fallback command/image above. Additional
cases cover unknown migration ages, bounded rotation past a live operation,
failed-deletion debt/retry, no database creation when subsystem is absent,
preservation of terminal bindings and expiration of only their unbound siblings.
Account physical cleanup and all public/run-node adapters remain open.
Ruff, plugin mirror/import probe, `py_compile` of the existing maintenance entry
point and `git diff --check` pass. This is not a deployed scheduling proof.

## Account physical custody and reset classification (2026-09-19)

Four tests first reproduced real account deletion failing its runs phase on file
FKs, missing physical cleanup, and unclassified root file tables. Added exact
tombstone-authorized owned-operation settlement before generic runs-store row
deletion. A busy lock or failed physical step keeps the store phase visibly
unfinished; inventory/debt remain until existing maintenance or an explicit
deletion retry can settle them. An existing pending subset is settled before
remaining siblings are erased. Current zero allocation/released state is checked
again before FK-ordered row deletion. Owner identity, not the current home, scopes
every file row. Root reset classifies and preserves this run history; it does not
gain authority to discard accepted file contents.

`python -m pytest -q tests/test_run_file_erasure.py tests/test_run_input_erasure.py
tests/test_account_deletion.py tests/test_scoped_reset_mutation_proof.py
tests/test_run_file_retention.py tests/test_run_file_reader.py
tests/test_run_file_cleanup.py tests/test_run_file_store.py`: native Windows
**107 passed**, Linux **107 passed**, zero skips (same explicit WSL read-only
fallback). The two-owner fixtures use identical bytes in independent physical
copies, bind terminal runs, rebind the deleted owner's home, invoke real
`delete_account`, and verify the peer's bytes/rows/bindings survive. Also checks
busy operation fencing, retained deletion debt through failure, maintenance
recovery, retry and refusal without a real tombstone. Existing active-work
deletion refusal is unchanged; this is not a new Stop/cancellation implementation.
Ruff and plugin mirror/import probe pass. Public adapters and rendered acceptance
remain OPEN; no production/user data was touched.

## Shared prepared carrier and post-unwind notification (2026-09-19)

Lead approved additive trusted concurrency/status-callback fields for direct
file-run parity and the consumer's optional post-unwind `on_settled` notification.
No new executor, depth authority, queue or start marker. New tests first failed
on the absent API, then prove exact option propagation, notification after the
run guard unlocks, one notification after a pre-start authority refusal,
exception isolation/visible logging, and pool cancellation leaving canonical
queued/unstarted truth untouched. Callback does not prove terminal state.

`python -m pytest -q tests/test_run_input_runtime.py tests/test_run_input_integration.py
tests/test_run_input_start.py tests/test_run_input_recovery.py
tests/test_run_input_admissions.py`: Windows **55 passed**, Linux **55 passed**,
zero skips before the additional published-version regression (10 existing
LangGraph Python-3.14 deprecation warnings on Windows). These
include actual graph/settlement/reset integration, not only traced invocation.
This shared seam is not public file adapter completion.

Consumer's real published-run integration exposed that canonical executable
snapshots omit `name`; the common worker rejected the valid pin during runtime
validation. Reproduced red using real `publish_branch_version` and actual graph
execution. Applied the established `_load_branch_version` presentation fallback
to a detached copy only, after envelope/hash validation; no persisted bytes or
content digest change. The regression asserts exact before/after stored
snapshot/hash and completed graph result. Final cohort: Windows **56 passed**
(12 existing LangGraph warnings), Linux **56 passed**, zero skips. The first
post-fix attempt exposed a test-provider fixture accepting keyword arguments
only; corrected that fixture to the existing positional provider contract and
reran the full cohort on both platforms. Ruff, mirror/import and diff checks pass.

## Execution-use integration and declared binding checkpoint (2026-09-19)

Non-destructively merged exact cloud execution-use checkpoint `3f98a648`; merge
retains source provenance and does not import later dirty kernel/compiler
activation. Read the full retained ADAPT and implementation receipt. Prepared
worker signatures remain compatible. Added transaction-local whole-declaration
metadata/owner validation before file binding and a worker revalidation seam
which refuses missing bindings rather than creating them. This is not yet public
direct admission or actual file RPC wiring.

`python -m pytest -q tests/test_run_file_binding.py tests/test_run_execution_use.py
tests/test_run_execution_lock.py tests/test_run_input_runtime.py
tests/test_run_input_integration.py tests/test_workspace_execution_use_lifecycle.py`:
Windows **53 passed, 1 skipped** (the fork-only identity case), Linux **54 passed,
zero skips**, same established read-only WSL fallback. Includes actual late RPC
lifetime and shared prepared graph tests plus ordered multi-file metadata,
foreign-owner/released-object refusal and missing-admission binding checks.

## Trusted file-node RPC checkpoint (2026-09-19)

Resumed the preserved binding/node helpers at `c66fa7f1`. Seven actual sandbox
tests first failed because `read_run_file` was not routed. The compiler now
supplies a trusted run/owner/placement source, intersects explicit node input
keys with immutable file declarations, and freezes that incoming view separately
for each invocation. Child RPC accepts only file ID, offset and count. Reads
require the pinned execution-use lifetime, persisted running identity, no cancel
or family closing, original run binding, exact reference metadata and the
downstream declaration's count/media/byte constraints. Whole-state/default
visibility never substitutes for explicit file input declaration.

Actual graph proof consumes the same captured 3 MiB non-text file plus zero-byte
member at a non-default graph placement and again after explicitly forwarding
the bundle. It checks exact SHA-256 results and refuses forged run/owner/node/
source/incoming selectors and undeclared state access. A narrower downstream
byte declaration refuses before its read. Its first assertion expected the
internal size reason; corrected to the existing public ManifestViolation code
(`manifest.invalid_reference@forwarded`), not a runtime behavior change.

Final `python -m pytest -q tests/test_run_file_node_rpc.py
tests/test_run_file_node.py tests/test_run_file_binding.py`: native Windows
Python 3.14 **20 passed**, Linux Python 3.11 **20 passed**, zero skips. Before the
last downstream-declaration check, the Linux combined file/delivery/enqueue/use
cohort passed **103 tests, zero skips**; native Windows corresponding non-file
cohort passed **83 tests, one Linux-bubblewrap skip**. Commands used
`tests/test_delivery_node_rpc.py tests/test_node_enqueue_verb.py
tests/test_node_enqueue_concurrency.py tests/test_workspace_execution_use_lifecycle.py`.
The unchanged Docker Desktop pipe remained unavailable; Linux used the same
immutable WSL oracle image and read-only/current-tree isolation recorded above.
Ruff and diff checks pass; plugin mirror/import probe passes (486 runtime files).

This is a compiler/RPC integration milestone, not public capture/direct dispatch
or deployment. Origin recovery awaits the lead's actual cross-family review
disposition. The common foundation also requires the cloud lane's correction for
ordinary non-managed runs incorrectly entering family-only recovery; no release
may strand those rows. Experimental cgroup/bootstrap fixtures are not a file
reader dependency and must not be wholesale shipped from branch ancestry.

## Explicit admitted-origin recovery checkpoint (2026-09-20)

Full independent Fable96232 ADAPT is retained in
`docs/reviews/2026-09-20-admitted-origin-shape-review.md`; root accepted the
corrections recorded in `origin-recovery-amendment.md` before implementation.
Added only the reviewed origin kind/version/options fields with additive legacy
defaults and exact immutable replay comparison. Direct v1 keeps original typed
recursion/concurrency values; canonical consumer v1 keeps `{}` and resolves its
separate exact correlation. Unknown/legacy/options/correlation disagreement is
held, not guessed from files or terminalized.

One static `dispatch_initial_run` registry entry is used by independent bounded
admission nomination in the existing boot/five-minute maintenance loop. The scan
has its own run cursor, includes runs without file operations and excludes all
durably started rows. Worker guard/CAS and empty-context execution remain common.
Direct preparation reads explicit persisted owner/universe/actor and current
source/home authority, revalidates file bindings, then constructs its provider
session outside writers. Valid depth-zero self-root family provenance is not
mistaken for child-pool provenance; malformed/foreign roots refuse.

Inspection found an exception path that could rewrite a queued row with corrupt
prior start markers to failed. Settlement now distinguishes a marker committed
by this guarded attempt from prior/ambiguous marker evidence; the latter stays
held unchanged. Regressions cover either lone marker component and correlation
conflict, and prove no new start marker. An actual direct sandbox graph completes
through the registry, and subsequent recovery does not replay it. Original
runtime recursion7/concurrency3 survive changing the ambient default to999.

`python -m pytest -q tests/test_run_input_origin.py tests/test_run_input_origins.py
tests/test_run_input_runtime.py tests/test_run_input_admissions.py
tests/test_run_input_integration.py tests/test_run_input_recovery.py
tests/test_run_input_start.py`: Windows **85 passed** (14 existing LangGraph
deprecation warnings), Linux **85 passed**, zero skips. Same pinned WSL oracle,
read-only current-tree mount, network disabled and previous bounded test limits.
Ruff, diff check and plugin mirror/import proof pass (489 runtime files).

Still unconnected: public direct reserve-only intake, public capture/read/release,
consumer acceptance stamp/registry switch (consumer owns its static exports),
and full shared foundation activation correction. This checkpoint is an internal
shared dependency, not an independently deployable file capability or a final
cross-family code approval. Existing wider origin/live acceptance tasks stay open.

## Reserve-only direct file intake checkpoint (2026-09-20)

Added internal `reserve_direct_run`: current owner/home/source authority under
the author fence, then run+origin snapshot/options+whole file binding in one runs
transaction under the existing reset barrier. It performs no initial events,
lineage, provider binding or submission. Only the common registry worker starts
the result. Version-pinned intake derives its file contract from the admitted
version, not a caller's alternate mutable Branch. Strict exact-JSON input
validation precedes insertion; failed bundle metadata rolls back every run,
envelope and binding. Nonzero/bool depth and any nested parent refuse.

`python -m pytest -q tests/test_run_file_direct.py tests/test_run_input_origins.py`:
Windows **22 passed** (10 existing LangGraph warnings), Linux **22 passed**, zero
skips. Both direct and real published-version paths reserve without lineage/start
effects, then execute actual chosen-entry and downstream sandbox reads of the
3 MiB binary plus empty member with exact hashes. Current operational family
activation remains cloud-owned; no public route is installed by this helper.

## Public same-owner slice candidate (2026-09-20)

Canonical and pinned served graph handles now expose owned authoring capture,
disclosed capacity/retention/chunk limits, exact bound readback and selective
release. Direct definition and alternative immutable `branch_version_id`
intake both reserve the run/envelope/bindings before the common registry starts
execution. Mixed selectors and unreadable private versions refuse. Existing
scalar execution remains on its prior executor; admitted files do not construct
the request-context provider before the durable start CAS. Submission failure
returns the original accepted run ID and a no-replacement warning, never implies
that a failed response means no run was accepted.

`59ca33f6` supplies the pure shared metadata classifier also consumed by the
consumer release. Owner-only public status reads five admission metadata columns
without graph/input decoding. Queued prior start markers report uncertainty and
no automatic replay; unknown/corrupt/legacy origins report unavailable. The row
status remains unchanged. This projection is not execution-death evidence and
cannot authorize retirement or retry.

Commands: `python -m pytest -q -rs tests/test_run_file_public.py
tests/test_run_branch_version.py tests/test_engine_mcp_server.py
tests/test_engine_mcp_hardening.py tests/test_engine_mcp_routes.py
tests/test_engine_mcp_write_graph_patch.py tests/test_run_input_observation.py`:
Windows Python3.14 **211 passed, 3 skipped** (Windows symlink creation); Linux
Python3.11 **214 passed, zero skipped**. Actual public-route proof captures a
3 MiB binary plus empty member, executes chosen entry/downstream in the sandbox
for both direct and published-version starts, and exports exact chunks.

The separate file/lifecycle cohort (contract/store/streams/capture/authoring-source/
cleanup/retention/erasure/reader/node/node-rpc/binding/direct/input-erasure plus
required-input-preflight) initially returned Windows **141 passed, 1 skipped,
1 failed**, Linux **142 passed, 1 failed**. The sole failure was the old preflight
unit fixture mocking only the final version executor while providing no version
snapshot, now resolved before selecting the scalar/file adapter. The fixture now
supplies that same branch contract; the complete preflight module rerun passed
**22 Windows / 22 Linux, zero skips**, without runtime changes to mask the issue.
Other cohort results remain valid at identical runtime bytes. The remaining
Windows skip is the POSIX symlink/FIFO fixture, exercised on Linux.

Final combined rerun of both cohorts at the frozen candidate, after correcting
that test fixture: **353 passed, 4 skipped** on Windows (56 existing LangGraph
deprecations); **357 passed, zero skips** on Linux. This supersedes the earlier
split reruns as exact-current-tree suite evidence, not as deployed proof.

Ruff, diff check and plugin mirror/import proof pass (490 runtime files). Linux
used the recorded immutable WSL oracle image, read-only current-tree mount,
network disabled, 2GiB memory and 1024 process limit; Docker Desktop remains
unavailable. No live user/account/workflow was changed or tested by this builder.

Release still requires isolated assembly atop the corrected shared foundation,
independent exact-head review, configured global custody capacity, required CI,
deployed SHA/canary and ordinary rendered app-agent acceptance. This is not a
claim that wider nested/resume/workspace/cross-owner custody criteria are done.

## Isolated assembly in progress (2026-09-20)

The isolated file release starts at consumer `f9b91ed7`, preserving corrected
managed-root activation and its common origin/runtime/model bridge. The source
candidate `4dd99ba4` is not merged wholesale. See `release-inventory.md`.

Initial integrated public-file/direct/origin + consumer scope/model/public-turn
and delivery-erasure cohort: **93 passed**, plus the already-known consumer
maintenance AST fixture failure at that base. Consumer's docs/test-only successor
`d3fa5f91` corrects the enclosing cursor scope; the file layer must also bind its
new file cursor in that fixture. No production-loop change is required.

Expanded 48-file Windows cohort: **832 passed, 6 skipped, 3 failed**. The three
failures are lower-level file node/RPC fixtures that relied on the superseded
implicit managed-family enrollment to create an execution-use guard. On the
corrected foundation these manually prepared runs have no guard unless explicitly
provided. Actual reserve/common-dispatch/public direct/version tests pass. The
fixture correction must provide a real held guard, retain no-guard denial and
never re-enable auto enrollment or weaken file authority. Linux counterpart is
still running at this checkpoint. This assembly is not yet frozen for review.

Broader Ruff checking found eight pre-existing E501 warnings on mojibake divider
comments in `daemon_server.py`; `git show f9b91ed7:tinyassets/daemon_server.py |
python -m ruff check --stdin-filename tinyassets/daemon_server.py --output-format
concise -` produces the same eight, with only downstream line shifts. File hunks
do not touch those comments; no unrelated cleanup is included.

### Guarded fixture and maintenance correction

The Linux expanded cohort completed **838 passed, 3 failed, zero skips**; its
failure set exactly matches Windows. Source inspection confirmed that
`runs._managed_execution_scope(provided=None)` no longer issues execution use
for ordinary non-managed rows, while the common admission worker passes its
real held guard. Only the lower-level tests changed: acquire
`try_run_execution_lock`, pass `provided` / `_execution_guard`, and retain
no-use denial. A new negative proves a running unmanaged row by itself grants
no file read. No production authority, enrollment or validation was weakened.

Consumer successors were imported as `456d9368`, `82145d50` and `7e17f751`,
preserving their runtime unchanged. The maintenance factory now binds both real
closure cursor names and tests three ticks with either delivery, admission or
file cleanup failure; all other lanes advance and the failed lane's cursor is
retained. Actual hosted `main` startup coverage remains intact.

`python -m pytest -q -rs tests/test_run_file_node.py
tests/test_run_file_node_rpc.py tests/test_delivery_account_deletion.py
tests/test_consumer_startup.py tests/test_run_file_public.py`: **42 passed** on
Windows and **42 passed** on Linux, zero skips. Ruff for all changed fixture
files passes. The bounded canonical `openspec/specs/run-file-inputs/spec.md`
now describes only implemented same-owner behavior; wider proposal tasks remain
open and deployment/app acceptance have not been claimed.

### Historical isolated full cohort and actual-authoring gap

At `478f6ecc`, the expanded 50-file cohort completed **843 passed, 6 skipped**
on Windows and **849 passed, zero skips** on the Linux oracle. Commands were
`python -m pytest -q -rs` and the same test list through the recorded immutable
WSL container. These are historical pre-authoring-fix results, not evidence for
later changed bytes. The six native skips require POSIX bubblewrap, symlink/FIFO
or fork behavior and are exercised by the Linux run.

Subsequent source inspection found a real public authoring omission:
`api.branches._staged_branch_from_spec` dropped `io_manifest`. Earlier public
runtime tests saved their fixture definition directly and therefore did NOT
establish that a user could build the workflow through ordinary graph handles.
The release claim and proposed `478f6ecc` review were held. Red tests exercised
actual branch create through canonical and served handles; the correction now
preserves declarations in shared staging and readback's existing `graph` shape.
The lead approved the bounded existing-language `set_io_manifest` amendment,
including strict final-model validation, null/omission rules and immutable pins.

The served fixture initially used legacy exact OAuth scope semantics while the
actual engine binds coarse effect grants. Tests now exercise production WorkOS
resolve-always mode, leaving real action-scope and owner ACL checks active; the
foreign-actor edit refusal is tested. No runtime auth grants changed.

The first new authoring cohort passed **21 Windows tests**. Final expanded
canonical/served capture-run-export, nested graph input, matched Linux and wider
regression results must be recorded before freezing. Three pre-existing E501
findings in `api/branches.py` were reproduced at `478f6ecc` using
`git show 478f6ecc:tinyassets/api/branches.py | python -m ruff check
--stdin-filename tinyassets/api/branches.py --output-format concise -`; no
unrelated cleanup is included.

### Completed public authoring correction (2026-09-20, Windows/Linux)

New actual authoring coverage now passes **23 Windows tests**: ordinary canonical
and served WorkOS-mode create, strict final-model edit validation, atomic malformed
or missing setter refusal, foreign-owner refusal, null clear, combined state-field
and contract edit, idempotency conflict, canonical remix inheritance/overrides and
nested-input top-level precedence. The end-to-end cases publish an original pin,
edit and publish a conflicting new contract, then run the OLD pin through the
actual canonical/served capture/run/read handles and verify independent exact
binary hashes and 33-byte export. Publishing/remix permissions remain unchanged;
served agents do not gain publication/fork rights from the metadata setter.

The expanded **56-file cohort passed 971 tests / 6 POSIX skips on Windows**
(260.97 seconds, 212 existing LangGraph deprecation warnings), and **977 tests /
zero skips on Linux** (419.63 seconds). Exact native command:

```text
python -m pytest -q -rs tests/test_account_deletion.py tests/test_app_consumer_controls.py tests/test_app_consumer_turn.py tests/test_authoring_file_io.py tests/test_authoring_sessions.py tests/test_branch_definitions_db.py tests/test_consumer_origins.py tests/test_consumer_prepared_scope.py tests/test_consumer_public_turn.py tests/test_consumer_reason_actions.py tests/test_consumer_run_envelope.py tests/test_consumer_selection.py tests/test_consumer_startup.py tests/test_delivery_account_deletion.py tests/test_delivery_node_rpc.py tests/test_engine_mcp_hardening.py tests/test_engine_mcp_routes.py tests/test_engine_mcp_server.py tests/test_engine_mcp_write_graph_patch.py tests/test_node_enqueue_concurrency.py tests/test_node_enqueue_verb.py tests/test_required_run_input_preflight.py tests/test_run_branch_version.py tests/test_run_file_authoring_source.py tests/test_run_file_binding.py tests/test_run_file_capture.py tests/test_run_file_cleanup.py tests/test_run_file_contract.py tests/test_run_file_direct.py tests/test_run_file_erasure.py tests/test_run_file_foundation.py tests/test_run_file_lock.py tests/test_run_file_node.py tests/test_run_file_node_rpc.py tests/test_run_file_public.py tests/test_run_file_reader.py tests/test_run_file_retention.py tests/test_run_file_store.py tests/test_run_file_streams.py tests/test_run_input_admissions.py tests/test_run_input_erasure.py tests/test_run_input_observation.py tests/test_run_input_origin.py tests/test_run_input_origins.py tests/test_run_input_recovery.py tests/test_run_input_runtime.py tests/test_scoped_reset_mutation_proof.py tests/test_work_consumer_model_bridge.py tests/test_workspace_execution_use_lifecycle.py tests/test_workspace_pool.py tests/test_run_file_public_authoring.py tests/test_branch_authoring_actions.py tests/test_branch_mutation_authority.py tests/test_branch_read_authority.py tests/test_branch_versions_rollback_columns.py tests/test_invoke_branch_authoring.py
```

The identical test list ran on Linux with `python -m pytest -p no:cacheprovider
-q -rs` inside the immutable WSL oracle image
`sha256:1b69d8536490285c7c7a13f1efe53ebe847696a99ae567dbbd2b9761ee0c530a`,
network disabled, read-only `/src` mount, Python 3.11, 2 GiB RAM, 1024 PID limit,
`seccomp=unconfined`, `PYTHONDONTWRITEBYTECODE=1`, `TMPDIR=/tmp` and
`TINYASSETS_DATA_DIR=/tmp/tinyassets-test-data`. Native Python is 3.14; no test
temp roots were inside the worktree. No source changed during either full run.

Afterward only the built-in guide changed: remove the obsolete creation-not-
exposed sentence and document exact file declaration/edit/capture/node-read
vocabulary. `python -m pytest -q tests/test_node_reuse_discovery.py
tests/test_run_file_public_authoring.py` passed **36 Windows / 36 Linux, no
skips**, against that successor. All 494 plugin mirrors rebuilt with successful
import probe. New test/engine/server Ruff, diff check and strict OpenSpec change
and canonical spec validation pass; the three exact-base API lint findings above
remain attributed, not relabeled as new or hidden.

This closes the discovered authoring omission locally, NOT independent review,
hosted CI, global deployment configuration, deployed SHA/canary or rendered app
acceptance. No live user workflow, account or production setting was changed.

### Reviewed foundation fixture successor

Imported root-reviewed foundation test-only commit `f1f56021` by ordinary
cherry-pick as `2a500b5d`, after committing authored runtime/docs at `3299bee7`.
The four-file matching check initially found one additional file-layer stub
mismatch on BOTH platforms: recursion override's MagicMock returned another
mock from `to_dict()`, not the real scalar branch dictionary. Windows returned
93 passed / 24 skipped / 1 failed, Linux 117 passed / 1 failed. The lead approved
setting that fixture's `to_dict.return_value` to its existing scalar `dummy_src`;
no runtime validation was weakened and no skip was added.

`python -m pytest -q -rs tests/test_effects_at_node_time.py
tests/test_resource_usage_status.py tests/test_run_recursion_limit.py
tests/test_storage_observations.py` now passes **94 Windows / 24 POSIX skips**
(9.34 seconds) and **118 Linux / zero skips** (30.49 seconds), using the same
oracle invocation above. Ruff passes for all four files. The imported commit
changes no runtime bytes; previous 56-file and final 36-test proof therefore
remain evidence for the exact current runtime. Freeze for root's independent
review; no push, merge or deployment performed by this builder.

## App byte-intake backend slice (2026-09-20, isolated builder)

Builder: Claude Fable, worktree `wf-file-upload-backend`, branch
`codex/claude-file-upload-backend` on top of `7d7b779c`. No subagents, peer
subprocesses, pushes, PRs, merges, production, provider or browser calls.
This is the BACKEND half only; `onboarding/app.html` and UI tests belong to
the sibling `wf-file-upload-ui` lane and are not integrated here.

### What is implemented locally (not deployed)

- `tinyassets/run_file_upload.py`: exact `X-TinyAssets-Upload` metadata
  parser (base64url UTF-8 JSON, exact seven keys, `version:1`, label 16-128,
  header <= 8192 ASCII, non-bool nonnegative `size_bytes` <= 8 MiB, 64
  lowercase hex SHA-256; refuse, never truncate), a two-slot `StreamBridge`
  (ASGI frames resliced to `CHUNK_BYTES`, 10 s idle / 120 s total deadlines
  enforced on BOTH sides, `fail` wakes both), and `upload_app_file`, which
  reuses `_capture_files` unchanged: same barrier, operation guard,
  reservation, inventory, stage/publish, commit fence, cleanup debt and byte
  settlement. Operation ids are namespaced `file:upload:`; request digests
  use source kind `app-upload-v1`. Content-Length equality is checked inside
  `metadata_provider`, i.e. only for a NEW copy and before reservation, so a
  lying length refuses 400 without consuming the label; a committed
  same-label replay never reads the body. `replay_result` wraps references
  with `unbound_retention_seconds` and the operation row's
  `unbound_expires_at` (null when bound; expired unbound refuses).
- `tinyassets/onboarding/file_upload.py`: `POST /mcp/app/files` behind the
  existing identity middleware. Order: 404 dark flag, 401 identity, 403
  origin (existing Host-or-resource set, exact scheme/authority, no
  path/query/fragment, `application/octet-stream`, `null`/missing refused;
  `_same_origin_json` untouched), 403 missing custom header, 400/413
  metadata, 400 malformed Content-Length, 409 no home / expected-home
  mismatch, 503 four-slot per-process semaphore, THEN the worker. The worker
  runs on the Starlette threadpool with `require_current_home=True`, so
  admin/tombstone/current-home are rechecked at every chunk checkpoint and
  inside the final commit fence. The producer reads raw `request.receive()`
  frames only after `ready_to_copy` (post-reservation). Refusal, disconnect,
  timeout and worker failure all fail the bridge; the task group cancels the
  producer and `run_in_threadpool` joins the worker before the response.
- `tinyassets/api/run_files.py` `file_limits`: `app_upload_available`
  (true only when the onboarding app flag mounts the route),
  `app_upload_max_bytes`, and `app_upload` in `supported_intake` when live.
- `tinyassets/universe_server.py` `read_graph` guide: copy app attachment
  references verbatim; `unbound_expires_at` is wrapper metadata; a sent
  message is not a binding.
- `onboarding_routes()` registers the route; `tests/test_onboarding_app.py`
  route table updated. Plugin mirror regenerated
  (`python packaging/claude-plugin/build_plugin.py`, 496 files);
  `python scripts/check_mirror_parity.py` -> all 496 mirror-matched.

### Evidence (native Windows 11, Python 3.14.3, pytest 9.0.2; Linux oracle
container Python 3.11.16 / git 2.47.3 / bwrap 0.12.0 via WSL Docker 29.1.3)

- Red first: with the route line removed,
  `python -m pytest tests/test_app_file_upload.py -q -x` failed at the first
  origin case (404 instead of 403); restored, the file passes 12/12 (5.9 s).
- `tests/test_app_file_upload.py` drives the REAL
  `AuthContextMiddleware(Starlette(onboarding_routes()))` with scripted ASGI
  `receive`/`send` (not TestClient, which collapses bodies to one frame):
  22 identity/origin/header/metadata/home refusals with zero body reads and
  zero rows; 6 MiB non-UTF-8 body in three uneven frames (one 3 MiB+17
  frame proves reslicing); exact six-field ref, wrapper expiry equals the
  operation row; committed replay with zero reads; metadata-only POST
  (`Content-Length: 0`) observes the committed result; changed metadata
  409; empty file; independent sibling labels and a second owner; length
  lie 400 with no row; digest mismatch 400 -> label held 409 with cleanup
  inventory; overflow 413; disconnect mid-stream (cleanup, allocation debt
  kept, no active guard); idle and total deadlines 408; slow-worker
  buffering high-water <= 2 chunks; founder-home rebind mid-copy 409 inside
  the fence; capacity unset 503 and full semaphore 503 before bytes;
  expired-unbound 409, bound replay `unbound_expires_at: null`, release
  then 409, and `account_deletion.delete_account` erasing the uploaded
  custody through the existing path; discovery truthfulness.
  `AuthoringStore.put_file_handle` is patched to raise for the whole module.
- Linux oracle (`scripts/linux_oracle.py` invoked through a tiny WSL wrapper
  because WSL git cannot resolve this worktree's Windows `gitdir`):
  `tests/test_app_file_upload.py tests/test_run_file_capture.py
  tests/test_run_file_public_authoring.py tests/test_run_file_erasure.py`
  -> first run 44 passed / 2 failed. Both failures were ORDER-DEPENDENT and
  caused by my discovery test: it imported `tinyassets.api.run_files` for
  the first time under a monkeypatched `helpers._base_path`, so the module
  permanently bound the test lambda and later served captures read the wrong
  data dir (`run_file_access_denied`). The same leak, in the other direction,
  failed only that discovery test on Windows when the public suites ran
  first. Confirmed by bisect (onboarding suites: 161 passed; public/direct
  suites: reproduced; remaining custody suites: 107 passed) and by an
  isolated oracle run of the two tests on the base tree `7d7b779c` (2
  passed) and on the working tree (2 passed). Fix: the test imports the
  module at collection time and patches ITS `_base_path`, `_principal` and
  `_request_universe`; no runtime code changed for this.
- Windows regression before the fix (19 files incl. all `test_run_file_*`,
  `test_delivery_account_deletion`, `test_onboarding_app`,
  `test_onboarding_model_preferences`): 325 passed, the 1 failure above.
- Ruff clean on every touched file; `scripts/check_context_budget.py` OK.
- After the fix, Linux oracle on `tests/test_app_file_upload.py
  tests/test_run_file_capture.py tests/test_run_file_public_authoring.py
  tests/test_run_file_erasure.py tests/test_run_file_public.py
  tests/test_onboarding_app.py`: **192 passed, 0 failed** (51.33 s, exit 0).

### Root independent completion of the stopped backend checkpoint

September20,2026 ~07:55UTC. Fable process55493 ended exit0 after1393s,
but its final message still described a pending Windows rerun and no commit.
Root verified no corresponding pytest/oracle process remained and preserved all
changes. No runtime code was changed by root. Author: Claude Fable; independent
source/test reviewer: root Codex, no extra ChatGPT agent.

- Windows command: `$uploadTestFiles = @(rg --files tests -g 'test_run_file_*.py');
  python -m pytest -q tests/test_app_file_upload.py
  tests/test_delivery_account_deletion.py tests/test_onboarding_app.py
  tests/test_onboarding_model_preferences.py $uploadTestFiles`:
  **355 passed,1 skipped**,143.65s,exit0. Sole skip is the POSIX symlink/fifo
  fixture in test_run_file_streams.py:232; this is not an all-platform claim.
- Independent Linux command: `wsl -d Ubuntu --exec python3
  /mnt/c/Users/Jonathan/.codex/worktrees/0a7f/TinyAssets/output/root-upload-linux-oracle.py
  -- -q -rs tests/test_app_file_upload.py tests/test_run_file_capture.py
  tests/test_run_file_public_authoring.py tests/test_run_file_erasure.py
  tests/test_run_file_public.py tests/test_onboarding_app.py`:
  **192 passed,zero skips**,48.44s,exit0. Helper imports this worktree's canonical
  scripts/linux_oracle.py, overriding only `_repo_root` with its exact WSL path
  because its .git file points to a Windows path. Canonical image/readonly-copy/
  test execution unchanged. Python3.11.16,git2.47.3,bwrap0.12.0. Tar reported
  `.: file changed as we read it` while the Windows suite was running; no source
  edits occurred after the builder exited. Root inspected unchanged source diff.
- Scoped Ruff passed on both new modules, modified API/onboarding/guide and
  tests; mirror parity496 passed; diff-check passed. No gate or ledger change.
- Root read the whole new route/bridge/adapter and executable ASGI suite,
  authority and shared capture extraction. Authentication precedes body reads;
  expected home is equality, not authority; current-home/admin/tombstone fencing
  surrounds publication; replay keeps label/reference/expiry and skips body;
  cancellation/timeouts retain same custody cleanup debt. No new store or queue.
  The factored AuthoringStore constructor is side-effect-free; the original
  source reads and write fence remain inside shared capture ownership.

This freezes the backend checkpoint, not a usable release. UI candidate's
queued-account-switch bug is independently reproduced and held in its own lane.
Combined exact-head review, CI, rollout configuration, public gates and ordinary
app file intake + downstream use still required. Do not count seeded custody or
the fake transport as live user acceptance.
