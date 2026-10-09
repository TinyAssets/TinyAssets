---
severity: P2
title: The workspace effector's credentialed git and dependency provisioning are retired at the owner split
filed: '2026-10-08'
summary: The cutover retires three daemon-uid children no owner cell can carry, so workspace checkout/push through the git worker and workspace provisioning now refuse visibly.
---

# Workspace checkout and provisioning are retired

**Filed:** 2026-10-08, per-owner isolation cutover (builder C, `iso/cutover-c`).

## What changed

The cutover rule says a spawn class that cannot be celled is retired and never
left unconfined. Three children of the workspace effector ran as the daemon's
uid:

- `workspace_worker.execute_workspace_operation`: a `multiprocessing` child
  that read the vault and ran git with the token and the container network.
- `workspace_provision_process.run_provision_stage`: dependency acquisition
  and installation in bubblewrap.
- `workspace_registry_process.RegistryBrokerProcess`: the package-registry
  broker beside it.

No owner cell holds a credential, and none has a writable checkout together
with registry egress. Now:

- `execute_workspace_operation` returns `RETIRED_ANSWER`;
- `execute_provision` returns `provisioning_retired`, charging nothing;
- the provisioning, registry process, registry and registry-proxy modules are
  deleted.

As a result, the effector's checkout, push and push-intent reconciliation
through the worker refuse with a visible error. Agents keep authenticated git
from bash through the broker's `git_egress` route, and they install
dependencies with bash in their own tool cell.

## Resolution

There are two ways to resolve this:

1. Delete the effector paths that can no longer succeed, together with the
   worker's operation handlers (dead after the spawn went). This is the
   direction's default: users build from bash and packages.
2. Or bring them back through cells:
   - checkout/push via a `workspace-git` cell plus the broker git route;
   - provisioning via a node cell with egress.

Either way, delete this file when it lands.
