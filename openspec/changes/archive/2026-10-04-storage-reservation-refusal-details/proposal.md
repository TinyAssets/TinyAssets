## Why

PR #4403's ordinary-write starvation fix has landed through #4418 and #4427, preserving provider recovery. Its remaining owner-facing diagnostics are still useful: active reservations currently read like retained storage. Preserve that work from the closed duplicate #4428 without its alternative admission calculation.

## What Changes

Add measured_bytes, reserved_bytes and committed_bytes to owner-visible quota refusals, derived from the existing ledger. Explain that active reservations may clear when calls finish. Preserve total usage, quota/admission/recovery decisions, schema, and the existing generic response for every non-owner.

## Capabilities

### New Capabilities
- `storage-refusal-details`: Explain existing accounting components to the affected owner without exposing them to other viewers.

### Modified Capabilities
None.

## Impact

Only storage_accounting.py and its plugin mirror, focused refusal/privacy tests, and specification artifacts. No new MCP action, database schema, permission, quota or provider policy. Owner: Codex; branch: fix/storage-reservation-refusal-details; one follow-up PR to preserve the remaining #4403/#4428 work.