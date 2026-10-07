## Why

The user's simpler resource system needs attributable retained-storage evidence.
Current status honestly reports total storage unavailable; transfer reservations
and the host-wide storage report cannot substitute for an owner's footprint.

## What Changes

- September 18 additive follow-up (`codex/storage-observation-freshness`, Codex):
  label legacy `storage_utilization` with observation time, elapsed age, cache
  reuse TTL, filesystem denominator and partial enumerated-subsystem scope.
  Preserve prior fields, authority and scans; no new paths or owner identities.

- Add bounded metadata-only storage observations to existing admin-only status.
- Separate permanent workspaces, provider runtime, other universe files and
  lease-attributed shared scratch/quarantine; report partial and excluded scope.
- Reuse existing paths, lease rows, SQLite read-only reader and TTL/single-flight.
- Keep total attributed storage unknown, all quotas unchanged, and owner-held
  rendered acceptance open. No ledger, migration, pricing or workflow changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `live-mcp-connector-surface`: scoped retained-file observations and coverage.

## Impact

`tinyassets/api/resource_usage.py`, a bounded observation helper, focused tests,
generated plugin mirror. Owner: Codex/Patches; branch: `codex/attributable-storage`.
The prior correction is deployed and awaiting owner acceptance; this separately
authorized enabling slice does not redefine or prematurely close that acceptance.
