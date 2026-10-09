---
severity: P2
title: Workspace dependency provisioning is still retired after the owner split
filed: '2026-10-08'
summary: The effector's checkout, push and push-intent reconciliation are back through an owner cell; dependency provisioning is not, and refuses visibly.
---

# Workspace dependency provisioning is retired

**Filed:** 2026-10-08, per-owner isolation cutover. Narrowed from
`2026-10-08-workspace-checkout-and-provisioning-retired.md`, whose other two
claims are resolved.

## What is back

Credentialed git works again as the OWNER, in an owner cell. The
`workspace-remote` cell (`deploy/role_git.py`, mapper kind in
`deploy/role_owner_launcher.py`) is bound to the owner's command center and
that center's checking egress socket; the daemon opens an ephemeral broker
route (`tinyassets/git_egress.py`) and hands the cell the URL rewrites as git
options, so the token still only ever exists inside the broker. Checkout,
push, push-intent reconciliation and the empty-workspace `create` all run
there (`tinyassets/workspace_remote_cell.py`,
`tinyassets/role_remote_git.py`, `tinyassets/workspace_worker.py`).

## What is not

`workspace_provision_execution.execute_provision` still returns
`provisioning_retired`, charging nothing, and the registry-broker modules
stay deleted. A `provision` block on a checkout packet therefore comes back
as `workspace_provision_refused`, visibly, with the checkout itself
unaffected.

Why it did not come back with the rest: provisioning is not one more git
operation. It needs dependency acquisition and installation for a package
manager in the owner's node cell WITH package-registry egress, and the
restriction that used to be a dedicated registry broker has to become the
checking egress proxy's business instead. That is a different cell (node, not
`workspace-remote`), a different authority (no connection grant is involved
at all), and a bounded-install contract -- transfer bound, storage bound,
digest, cancellation -- that `execute_provision`'s callers already expect and
nothing currently satisfies. Half of it would be worse than none: an install
that runs but is not bounded is a quota hole, and one that is bounded but not
restricted is an arbitrary outbound fetch under the owner's identity.

## Resolution

Either:

1. Run acquisition and installation in the owner's node cell with egress,
   keeping `execute_provision`'s existing contract (manifests in, bytes to
   charge and a failure class out), with the registry restriction enforced by
   the command center's checking proxy; or
2. Delete `execute_provision` and the `provision` packet field, and say in
   the packet grammar that dependencies are installed with bash in the
   agent's own tool cell -- which is the direction's default.

Either way, delete this file when it lands.
