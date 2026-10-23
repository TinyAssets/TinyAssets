## Why

N6 gives an owner a coherent recovery point before agent work changes their box. Reusing C1/C2 snapshots preserves the single S27 backup and restore path.

## What Changes

- Checkpoint once before each agent turn's execution, including scheduled turns, through the existing lifecycle snapshot call.
- Restore the whole owner volume, including files, owner DB and history, only after owner confirmation in the app.
- Share C2 retention and recovery pins; document free-tier cost and the planned `box-snapshot` guard.

## Capabilities

### New Capabilities
- `owner-box-snapshot-undo`: owner-confirmed whole-volume undo through the existing snapshot lifecycle.

### Modified Capabilities
None. C2/S27 remain the storage and recovery foundation.

## Impact

Spec-only lane on `spec/n5-n6-egress-undo`, paired with N5 in one requested PR. Implementation waits for C6 close-out and proven C1/C2/S27 recovery. Only the existing lifecycle call gains checkpoint/restore callers; turn scheduling, undo presentation and restored-state handling run inside the box. No product code, file-level backup engine, host content traversal, or production operation.
