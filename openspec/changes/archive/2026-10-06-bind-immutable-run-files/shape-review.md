I'll read the proposal, design, and the cited source seams, then give a per-question disposition and a terminal verdict.

Now targeted source reads on the cited seams, all in parallel.

Reads are done. Here is the disposition.

**Ground truth I verified (2026-09-19, this worktree)**

- `usage_policy.TierLimits.storage_bytes` has zero consumers under `tinyassets/`. The tier storage numbers are configured, never enforced. The proposal's premise for question 2 holds.
- `blob_proof.coordinated_transaction` has no callers. The documented order there is coordinator first, then SQLite transaction, but nothing depends on it yet.
- `_PhysicalRootCoordinator.acquire` takes a blocking `fcntl.flock(LOCK_EX)` (`blob_proof.py:132`) and a process RLock.
- `reserve_operation_bytes` inserts a `KIND_JOBS` row unconditionally (`workspace_pool.py:869-874`).
- `node_sandbox` runs nodes under bwrap `--unshare-all --die-with-parent --new-session` and kills the process group on exit (`node_sandbox.py:1760-1762`, `:2319`). Process exit therefore ends every in-sandbox writer.
- Workspace capability is POSIX-only by existing precedent: push refuses on non-POSIX (`effectors/workspace.py:1341-1347`), and `copy_regular_file_beneath` needs `dir_fd`.
- `_execution_subject` needs `receiver_branch_id`, `snapshot_json`, `snapshot_sha256` from the delivery row (`delivery_runtime.py:46-56`). Sender erasure deletes that row and the attempt row (`account_deletion.py:639-649`). The run row survives with `inputs_json` intact (`runs.py:951`), so only the pinned snapshot and start state are lost, not the scalar inputs.
- `try_attempt_lock` is keyed `{delivery_id}-{attempt}` and `dispatch_accepted_delivery` only ever passes `attempt=1`.

## Question 1: producer handoff

**DISAGREE_CONCERN** with the design as written. The design's §2 keeps a sandbox `capture_run_file` RPC "using the trusted running context" while the node is alive. That is the active-node capture the prompt warns about. Nothing in `_acquired` or the chain excludes a background process the node spawned inside the mount.

Smallest enforceable seam: post-exit declared output handoff, no in-node capture RPC in v1.

- The manifest already names output fields. A node writes the file at a declared relative path and returns its JSON output naming the field. Nothing else is needed from untrusted code.
- The daemon collects after `proc.wait()` returns for that node's bwrap child. With `--unshare-all` the PID namespace dies with its init, and `_kill_process_tree` covers the non-bwrap path. At that point no in-sandbox writer exists. This is enforceable because it is a kernel property, not a stat comparison.
- The copy runs under `_acquired` so a racing discard cannot close the descriptor, and through `copy_regular_file_beneath` so a rename cannot repoint the path.
- One remaining writer class: a concurrently running sibling node holding the same workspace as an ancestor mount. The chain must expose a sole-holder check at collection time. If it cannot prove the collecting node is the only live holder, refuse with a stable code. Do not weaken this to a hash recheck.
- Source support in v1: authoring uploads on both platforms, node-produced files on POSIX only. Windows workspace capture refuses explicitly, matching push.

This is smaller than the RPC design: it deletes a sandbox surface rather than adding one, and the boundary is the process exit the daemon already observes.

## Question 2: capacity

**AGREE** with the preferred correction. There is no enforced retained-byte primitive to reuse. Concrete contract:

- Extract `reserve_bytes` and `reconcile_bytes` from `workspace_pool` that write only the `KIND_BYTES` row keyed by `operation_id`. Existing push and provisioning callers keep calling the wrapper that also inserts the job row.
- New env var, one subsystem ceiling for all owners, no tier branch: `TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES`. Unset means file intake refuses with a clear code. Do not read `TierLimits.storage_bytes`.
- Admission under one short runs-store transaction: `retained + pending + request <= ceiling`, and `shutil.disk_usage(root).free - pending - headroom >= request`. Retained bytes are already absent from free space; do not subtract them there.
- Headroom is a second env var with a fixed default. Physical root identity is verified before measurement and unknown or unresolvable root refuses.
- ENOSPC during stream unlinks the staged file by inode compare, settles the reservation to zero and records the refusal.

Hardening, not blocker: exposing retained and pending totals per owner in status.

## Question 3: stream primitive

**DISAGREE_EVIDENCE** on the proposed lock order step 4. The design says author fence, then runs transaction, then physical-root coordinator. `acquire` blocks on `fcntl.flock(LOCK_EX)` at `blob_proof.py:132` while both `BEGIN IMMEDIATE` transactions are held. That stalls every other runs-store and author-store writer for as long as any other holder keeps the root lock. It also inverts the primitive's own documented contract at `blob_proof.py:524`.

Correction: publish the body first, then commit the binding.

1. Stream to a private staging name under the same physical root with no coordinator and no DB lock held.
2. Take the coordinator alone for the short rename plus fsync of the parent, then release it.
3. Author-store tombstone fence, then runs-store transaction: recheck grants, commit object ready plus bindings plus admission.

A crash between 2 and 3 leaves an orphan body the operation journal already inventories, which the collector reclaims. A crash in the reverse order would leave a visible binding with no body, which the design forbids. The reorder is strictly safer and removes the OS lock from under SQLite.

Minimal shared interface, extracted from `blob_proof`:

- `stage_stream(relative_path, chunks, *, max_bytes) -> (staged_name, sha256, size)` reusing `_secure_parent` and the fsync logic of `_atomic_replace`, with incremental digest and a byte bound before every write.
- `publish_staged(staged_name, final_name)` under `coordinated()`.
- `open_held(ref)` returning a `_HeldRegularFile` variant without the `content` buffer, plus `read_range(offset, count)`. Today `_ensure_exact_content` compares against `self.content`, so a digest-only mode is a required change, not optional.

Do not put run-file objects in the blob JSON index. It is a whole-file rewrite per publication under the root lock, and `_load_index` is size-bounded. The `run_file_objects` row is the authority.

Platform staging: Windows already has `msvcrt.locking`, `_windows_open_directory` and reparse checks in `blob_proof`, so authoring intake and readback can be proven on both platforms. Workspace source capture is POSIX-only per question 1. Skips are not proof; the Windows suite must run the authoring path natively.

## Question 4: accepted execution envelope

**AGREE** that a run-owned row is the right home, with two corrections.

- Do not copy `inputs_json` into `run_input_admissions`. The run row already holds it and survives sender erasure. The admission row carries owner, universe, `branch_def_id`, either a `branch_versions` reference or the exact snapshot plus sha256, the manifest, and start state. One definition per fact.
- The start guard keyed by `run_id` should replace the attempt-keyed guard for every delivery-origin run in this slice, not only file-bound ones. Attempt is always 1 today, so this is a key rename, and running two guards for one run during a migration is the two-definitions class. `graph_delivery_attempts` becomes a personal-control view with no start state.

No better existing home: `run_lineage` pins a version integer, not a projection, and `graph_receivers.snapshot_json` is regenerated per generation. `branch_versions` exists and should be referenced when the run came from a version.

Same-owner retry binding to immutable objects stays out. Receiver tombstone check via the author-store fence before dispatch is correct.

## Blockers before build versus later hardening

Pre-build blockers, all bounded:

1. Replace the active-node capture RPC with post-exit declared handoff plus a sole-holder refusal.
2. Reorder publication: coordinator alone for rename, then author then runs. Keep run-file objects out of the blob index.
3. Byte-only reservation extraction and the explicit custody ceiling env var, unset means refuse.
4. Run-keyed start guard for all delivery-origin runs, inputs stay on the run row.

Later hardening: per-owner usage reporting, Windows native proof of chunk readback, collector timing under load.

Also confirm the design's claim that the `io_manifest` bridge is necessary. `branches.py` has no `io_manifest` field and the only readers are in `authoring/`, so authoring file declarations are not runtime support today.

VERDICT: ADAPT

This review's brief is read-only and bounded: no edits, no dispatches, no other worktrees, one terminal verdict. That verdict is delivered above. The outstanding dispatches the hook lists belong to other lanes. The two finished files sit in the cross-user-delivery worktree, which this brief forbids me from acting in. The running entry is this review's own output file. Nothing else here is mine to advance without breaking the constraints root set.

Recap for a reader who sees only this message. The proposal's shape is right in three of four places and needs four bounded corrections before build:

1. Replace the active-node `capture_run_file` sandbox RPC with post-exit declared output handoff. Process exit under bwrap `--unshare-all` is the enforceable writer boundary. Add a chain sole-holder check and refuse when it cannot be proven. Workspace capture is POSIX-only, authoring upload works on both platforms.
2. Extract byte-only `reserve_bytes` and `reconcile_bytes` from `workspace_pool`. Add one subsystem ceiling env var with unset meaning refuse. Do not read the unenforced tier storage values.
3. Reorder publication. Stream to staging with no locks, rename under the root coordinator alone, then author fence, then runs commit. The proposed order nests a blocking flock under two SQLite write locks. Keep run-file objects out of the blob JSON index.
4. Add `run_input_admissions` without duplicating `inputs_json`, and key the start guard by run ID for every delivery-origin run. Attempt is always 1 today, so this is a rename, not a second queue.

VERDICT: ADAPT
