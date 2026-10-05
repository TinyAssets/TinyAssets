# Remote MCP delivery - 2026-10-05

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

## Verified slice: remote transport

Added Streamable HTTP protocol negotiation (2025-06-18 and 2025-03-26),
paginated discovery, JSON/SSE parsing and tool calls exclusively through
AsyncBrokerClient. Sessions remain instance-local. Discovery renews expired
sessions; tool calls never retry an uncertain outcome. Catalog hashes and
argument schemas fence stale calls. Broker send guards bind endpoint,
incarnation and metadata revision; attachment revocation serializes with sends.

Linux oracle: 70 passed across attachment, remote protocol, real broker socket,
broker server and upstream stream suites. Windows protocol/storage tests: 23
passed. Ruff passed. The real broker test proves initialize/list/call and
attachment revocation, but is not a live remote OAuth/user-app proof.

## Verified slice: OAuth primitives

Added path-aware protected-resource metadata discovery, exact resource matching,
authorization-server metadata, public DCR followed by advertised CIMD followed
by configured static ID. CIMD reuses the existing TinyAssets-hosted document.
Generic authorization and token exchange/refresh carry the resource. The broker
refuses another resource and disables redirect expansion for resource-bound
tokens. The optional provider directory is unchanged.

Local fake OAuth server covers DCR, real PKCE code exchange, refresh and resource
parameters. Existing OAuth regression tests passed (88 tests before the final
resource-confinement test); the final focused run passed 21 tests. Combined Linux oracle: 169 passed, no skips; plugin import probe and 606-file
mirror parity passed. This is not a
server-held PKCE/owner-session or end-to-end MCP card proof.

## Remaining by task

- 1.1: metadata is implemented; full prerequisite and rollback-cleanup acceptance remains.
- 1.2: discovery/registration/resource primitives are implemented; protected
  server-held generic PKCE and initiating owner-session integration remain.
- 1.3: broker transport core is implemented; production client wiring,
  MCP cancellation notification and durable operation reconciliation remain.
- 1.6: ta catalog/dispatch, annotation-to-owner-policy integration, consolidated
  card activation and durable continuation are not implemented.
- 1.4 remains deferred. 1.5 and 1.7-1.10 are not complete.

The full paste -> card -> sign-in -> tools/list -> tools/call test does not exist.
No browser app proof, deployment, deployed-SHA assertion or as-built spec sync
has occurred; no task checkbox is marked complete.
The stack predates #4469; reconcile card and #4483 owner-session prerequisite
before wiring the user path. Do not claim a working pasted-link connection.

Dependency evidence on 2026-10-05: #4469 was merged to main as
99fa48fd6d837ea22a06a3ced6f31138453cc4ef but is not an ancestor of this stack.
#4483 remains OPEN on fix/consent-asks-owner-session. Both own the request/card
surfaces this lane must consume. The isolation foundation concurrently advanced
through dc34dd2b10, including HTTP deposit and owner-metadata broker routing.
Keep the PR draft and reconcile dependencies before editing their overlapping
request/approval integration surfaces. Do not add a parallel approval mechanism.
