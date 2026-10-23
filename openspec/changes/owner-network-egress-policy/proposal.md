## Why

N5 lets the owner restrict their box's internet access independently of agent instructions. ADR-022 P5 reserves this policy at the one relay; C7 already supplies its enforcement point.

## What Changes

- Add owner-selected `open` (default), `allowlist`, and `ask` modes to the existing proxy decision.
- Treat owner-bound credential destinations as allowed in restrictive modes; approval writes a standing entry through the sole owner policy writer. Keep authority owner-only; reuse the in-box request/approval path and expose a read-only agent projection.
- Specify the planned `egress-gate` guard without implementing it.

## Capabilities

### New Capabilities
- `owner-network-egress-policy`: owner-controlled public internet admission at the existing box boundary.

### Modified Capabilities
None. This is a post-C6 delta; the existing request lifecycle remains unchanged.

## Impact

Spec-only lane on `spec/n5-n6-egress-undo`, paired with N6 in one requested PR. Implementation is blocked on C6 close-out. Shared changes are confined to the existing egress check and its existing policy/owner-approval plumbing; owner UI and requests remain in the box. No new transport, provider adapter, shared owner-data reader, or production operation.
