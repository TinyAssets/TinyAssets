# Remote MCP delivery ? 2026-10-05

Branch: `feat/mcp-connect-ladder`, stacked on `feat/per-role-uid-split`
at `688a3e91f121c5b299afe2df536e75c5b4f78a00`.

## Verified slice: typed attachment metadata

Added broker-owned version-1 HTTP MCP metadata keyed by owner, backing
connection and incarnation. Existing HTTP records and grants remain unchanged.
All reads/writes require the live owner/center grant; updates compare the prior
metadata revision. Removal marks attachment metadata revoked while preserving
the independent HTTP connection. Unknown schema versions fail without deletion.
No upstream session or credential field is accepted.

Windows and Linux oracle: 22 tests passed (attachment and broker capability
suites); Ruff passed; plugin build/import probe and 602-file mirror parity passed.
Task 1.1 remains open until the complete request/card prerequisites are proven.

## Pending

Remote protocol transport, OAuth resource discovery/registration, protected
connect activation, ta dispatch and end-to-end proof are not implemented yet.
The stack predates #4469; reconcile card and #4483 owner-session prerequisite
before wiring the user path. Do not claim a working pasted-link connection.
Task 1.4 is deferred until the remote path is complete. No deployment occurred.

## Earlier prerequisite investigation

