## Why

Users can exchange structured node outputs, but cannot yet give an admitted run durable binary or multi-file inputs that its entry node and downstream nodes can consume. Authoring-session handles expire and are not runtime custody; wrapping bytes in JSON does not close this gap.

## What Changes

- Proposed-only fresh-user completion: add authenticated raw-byte intake behind
  the existing app paperclip, directly into the SAME immutable run-file custody.
  Return existing opaque file references to the conversation, not fabricated
  authoring sessions or a new artifact store. The exact app-only boundary and
  acceptance are in `app-byte-intake-amendment.md`; independent shape review is
  required before implementation. Current reviewed custody code alone does not
  close the missing fresh-user binary-upload route.

- Add one generic immutable run-file custody boundary, shared by direct, queued, nested, resumed and delivery-origin execution. A delivery adapter copies approved files into the receiver's independently owned custody; it does not introduce another artifact framework.
- Preserve existing `io_manifest` file/file-bundle declarations through executable branch serialization and snapshots. Bind opaque, authorized references into existing input state, with bounded file materialization available to declared consumers.
- Preserve and validate declarations in ordinary branch creation/remix, and expose `set_io_manifest` through the existing transactional branch patch language. Missing declarations inherit on remix; explicit null clears. Existing immutable versions/admissions never change.
- Extend existing graph handles with narrowly scoped authoring capture/read actions and sandbox read/materialization. Capture declared workspace outputs only after managed producer exit under cross-process source exclusion, not active-node RPC. No new top-level MCP tool, arbitrary path access, remote URL fetch or external storage service.
- Reserve actual byte capacity before copying, stream exact bytes through held regular-file handles, and atomically bind complete immutable bundles to run admission. Expose truthful retained/pending byte use without adding prices or a fake workspace-job charge.
- Keep receiver inputs independent of sender receipts and authoring-session lifetime. Add owner erasure, explicit release/retention, tombstones and conservative crash reconciliation; do not claim cross-database or filesystem transactions are atomic.
- Anticipate reuse of these immutable inputs by a later explicit receiver-retry action, but do not implement retry or replay user effects in this slice.

## Capabilities

### New Capabilities

- `run-file-inputs`: Exact binary/multi-file capture, run binding, authority-scoped consumption, independent custody, accounting and lifecycle.

### Modified Capabilities

- `graph-execution-substrate`: Executable file declarations and entry/downstream consumption survive ordinary branch reuse and all admitted run origins.
- `account-deletion`: Erase an owner's file custody without erasing a surviving recipient's accepted independent copy; do not preserve erased delivery-control metadata as file authority.

## Impact

Likely implementation seams: `tinyassets/authoring/io.py`, `branches.py`, run admission/compiler/sandbox adapters, `api/` graph dispatch, generic `storage/` modules, verified blob/file helpers, existing workspace byte ledger and account deletion. New durable custody/allocation/run-binding rows are necessary; existing receipt rows cannot own them. Exact schema and lock sequence are in `design.md` and require independent pre-build review.

The corrected shape was approved for implementation after Fable 80912 ADAPT and lead disposition; it is not shipped behavior. Trusted owner/context propagation from merged PR #3881 is a delivery dependency; root owns deployed verification. The larger `connect-cross-user-nodes` change remains open for full files, receiver retry and ordinary two-owner live proof. An explicit tier-independent operational capacity ceiling must be configured in ordinary global rollout, never a per-user patch. No user workflows/accounts, pricing or PLAN principles change here.

Owner: Codex delivery lane. Branch: `codex/run-input-file-custody-proposal`. One implementation PR after approved shape; no implementation PR yet.
