## Why

The live app agent cannot read an owner-approved download when its endpoint
returns HTTP302. Users need a general, credential-isolated way to authorize
redirected downloads, not service-specific code or operator workflow repairs.

## What Changes

- Add explicit GET-only endpoint permission for bounded public HTTPS redirects;
  absence remains no-follow, including existing full-access connections.
- Carry the permission through canonical policy, truthful owner preview,
  incarnation-fenced approval and storage without broadening credential access.
- Follow approved redirects inside the existing broker with per-hop network
  validation, shared limits, authority rechecks and no cross-origin credentials.
- Keep signed redirect locations and capabilities out of all caller evidence.

This is the independently designed download slice of channel-agnostic-outbound
D3. The legacy28-task umbrella remains intact; this bounded delivery does not
claim its channel migrations are complete or replace the broader active goal.
The cloud universe's projects remain its own work.

## Capabilities

### Modified Capabilities

- `external-effect-adapters`: explicit endpoint redirect permission and bounded
  credential-isolated execution through the existing HTTP effect.

## Impact

Existing outbound endpoint/ledger parsing, HTTP connection previews/approval,
pending-request normalization and the broker's HTTPS transport. No new top-level
tool, custody store, service allowlist or private-workflow edit. Legacy callers
remain no-follow; enablement requires new owner consent.

Owner: Codex, current Patches task. Branch: codex/follow-approved-downloads.
One PR will carry this slice. The model-selection branch is not changed here;
its pending extra-review authorization does not authorize another review of it.
