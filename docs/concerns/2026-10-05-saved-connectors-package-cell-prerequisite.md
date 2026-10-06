---
severity: P1
title: Saved connectors require the shared package-cell launch boundary
filed: '2026-10-05'
summary: Ordinary ta extensions inherit the whole launch capability socket; L10 cannot enforce declared slots or immutable-revision activation until the foundation package-cell API exists.
---

Observed at `7af51406e7216ad9f56ce394f2c1ad7def5d8b35` in `wf-L10`.
This is missing implementation, not a requirement to deploy the foundation
before integrating it.

`tinyassets/ta_cli.py:extensions` discovers executable workspace files;
`main` runs them without an immutable-revision check. `universe_tools.py`
mounts the workspace read-write and `/tmp/ta.sock` into the entire launch.
`ta_capabilities.py:Capabilities` authorizes the launch, not an extension.
Every process can therefore reach all that launch's granted connections.
Existing broker custody and cross-owner checks still hold; this finding does
not allege that the existing jail exposes raw credentials or another tenant.

The saved-connector spec additionally requires denial when an extension asks
for another connection's slot, exact-revision activation, author/ceiling
auto-updates and second-account reuse without source-account authority.
Manifest fields and hashes alone cannot enforce those requirements.

Package publishing has exact-digest install consent, but installation copies
the files into the mutable recipient workspace. Its install pin is not a
runtime revision/slot gate. `command_center_update_policy.py` deliberately
permits presentation-only automatic updates. Legacy `authoring/service.py`
runs node/evaluator definitions and has separate effect confirmations; it is
not an ordinary `ta` package runtime and must not become a parallel connector
authority.

Handoff to `per-role-uid-split`, tasks 2.5/2.6, alongside
[the MCP stdio prerequisite](2026-10-05-mcp-stdio-owner-cell-prerequisite.md):

- Accept receiver-pinned owner/center, exact immutable package revision and
  executable arguments; use no-link immutable mounts outside the writable
  workspace and return the admitted revision.
- Bind a capability socket to only the declared local connection grants and
  incarnations. No access to the agent's broader socket, other packages or
  mutable workspace. Keep credentials in the existing broker.
- Fence new dispatch when activation or a backing connection is revoked;
  expose cancellation and a trustworthy terminal result for test receipts.
- Prove changed bytes cannot run under an earlier revision, a foreign slot
  cannot be called, and the second owner's execution cannot inherit source
  grants or private files.

L10 owns connector metadata, receipts, owner-policy integration, source/version
controls and requirement-only export on that interface. It must not create
another package launcher or approval mechanism. Raw-key injection is not
required for L10. Snapshot/status work can proceed without this API, but cannot
be presented as full connector activation or safe cross-author reuse.

Claude independently agreed with the boundary finding; its `ADAPT` verdict is
recorded in
`openspec/changes/saved-agent-connectors/readiness-review.md`. That is a
readiness review, not approval of an implementation.
