# MCP stdio needs the isolation foundation's package cell

Task `connect-anything-ladder` 1.4 is blocked on implementation of the owner-cell
launch boundary, not on remote MCP or task 1.10 deployment. Observed on foundation
`71df3620de`, merged into `feat/mcp-connect-ladder` as `611fb20c30`.

Evidence:

- `deploy/role_launcher.py:291` routes every SPAWN to `_decoder`; lines 310-314
  accept only `image-decoder`. Its fixed exec at lines 357-360 cannot launch an
  owner package, executable/argv/cwd revision, or stdio MCP server.
- `deploy/role_decoder.py` provides a data-free image cell. It has no owner
  package view, scoped egress relay or connection-incarnation launch grant.
- `tinyassets/providers/provider_jail.py` confines descendants but does not
  provide a separate raw-key server user. Running the MCP child alongside the
  agent in that jail would expose its environment and proc entries to that code.
- Foundation tasks 2.5/2.6 still own package cells and engine egress routing.
  D53-D55 add account erasure, snapshot modes and relay socket ownership; they
  do not add this missing launcher kind.
- The planned engine identity is shared UID 1003. The MCP spec explicitly asks
  for a separate user identity for an opted-in raw-key server. The foundation
  needs to select and prove that identity/mapping; a separate process alone
  does not implement this requirement. Shared UID alone is not evidence of a
  namespace escape, but it does not satisfy the stated distinct-user contract.

Handoff to `per-role-uid-split`: provide an admitted package-cell launch API with
receiver-pinned owner/center scope, no-link immutable revision mounts, mediated
stdio/cancellation, scoped proxy handles pinned to connection incarnations, and
the explicit separate-user raw-key variant. Prove actual processes cannot inspect
foreign/agent/server proc, environment, arguments, files or inherited descriptors.
Then this lane can bind that API to protected exact-revision consent, named-own-key
custody, MCP protocol/ta and incremental stdout/stderr scanning.

The foundation's production entrypoint and complete acceptance matrix remain its
own tasks. They are not a demand to deploy during the MCP implementation run:
isolated test admission is enough to begin integration once the API exists.
Do not count image-decoder or remote HTTP proofs as stdio acceptance.

Claude's independent read-only assessment agreed that no usable path was missed:
`openspec/changes/connect-anything-ladder/review-stdio-readiness.md`.
Its APPROVE verdict applies only to this readiness assessment, not task 1.4.
