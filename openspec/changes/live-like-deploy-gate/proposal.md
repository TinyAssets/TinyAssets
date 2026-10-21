## Why

The C6 cutover was refused six times for live host conditions that cold restore rehearsals and unit tests did not expose. Merge queue candidates need a disposable, production-shaped deployment while a read-only check catches conditions unique to the real droplet before host mutation.

## What Changes

- Run the production host deploy and its acceptance checks against a live synthetic host for every merge queue candidate, initially as a non-required check.
- Prove a clean refusal and successful retry, including absence of harness and box-store residue.
- Add a read-only production preflight for disk headroom, box-store contents, special files, and canary provisioning.
- Preserve the production deploy's current inputs, authorization, ordering, rollback, and public checks.

## Capabilities

### New Capabilities

- `live-like-deploy-proof`: A merge queue candidate is exercised through the shared deploy path on an isolated live host.

### Modified Capabilities

- `uptime-and-alarms`: Production deployment refuses known host-only blockers before writing to the host.

## Impact

Deployment workflows, `deploy/deploy_fail_safe.sh`, host installers, synthetic fixtures, and CI. The gate must contain no production data, backup, droplet credentials, or production environment access.
