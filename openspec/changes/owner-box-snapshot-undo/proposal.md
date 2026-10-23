## Why

N6 gives an owner coherent recovery from retained sleep snapshots or an explicitly requested restore point. Reusing C1/C2 snapshots preserves the single S27 backup and restore path.

## What Changes

- Reuse automatic sleep snapshots and owner-requested checkpoints; no per-turn/per-session stop.
- Restore the whole owner volume, including files, owner DB and history, only through N10 protected owner confirmation, publishing current state first so undo is itself undoable.
- Share C2 retention and recovery pins; document free-tier cost and the planned `box-snapshot` guard.

## Capabilities

### New Capabilities
- `owner-box-snapshot-undo`: owner-confirmed whole-volume undo through the existing snapshot lifecycle.

### Modified Capabilities
None. C2/S27 remain the storage and recovery foundation.

## Impact

Spec-only lane on `spec/n5-n6-egress-undo`, paired with N5 in one requested PR. Implementation waits for C6 close-out and proven C1/C2/S27 recovery. Only the existing lifecycle call gains checkpoint/restore callers; turn scheduling, undo presentation and restored-state handling run inside the box. No product code, file-level backup engine, host content traversal, or production operation.
