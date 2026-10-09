# October 9 connection research pickup

`initial_provider: codex`; research only, not a new accepted spec or completed build task.
Evidence, source dates, tap budgets and full landing packet:
[`2026-10-09-zero-tap-connections.md`](../../../docs/design-notes/2026-10-09-zero-tap-connections.md).

Applies when touching OAuth discovery/token persistence, remote MCP/extensions,
broker auth headers/retries, inline continuation, mobile auth or browser custody.
Reuse `one-extension-unit` and the existing remote MCP/broker code; #4553 is the
keyless baseline. Do not implement the superseded second attachment model.

Next action: reconcile current main and active isolation/connection lanes, then
spec and build path/challenge discovery, CIMD and resource propagation in the
existing OAuth flow. Proposed branch `codex/mcp-oauth-discovery`, worktree
`../wf-mcp-oauth-discovery`; proposed files and acceptance are in the report.
Sources: [MCP registration](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration)
and [discovery](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization-server-discovery),
revision 2026-07-28, checked 2026-10-09.

Then finish server-owned callback/mobile return in `inline-connect-and-approve`,
current/legacy wire compatibility and URL elicitation in remote MCP, and the
owner-editable name/link resolution skill. Preserve no service-specific code;
new per-service registrations and third-party brokers are not prerequisites.
Floor review and implementation/live gates remain those of the existing lanes;
sync specs only after implementation acceptance. This docs PR targets main.

## Slice 1 implementation pickup (feat/oauth-discovery-cimd)

Implemented path/challenge discovery, exact issuer/resource checks, existing-client
then CIMD then DCR selection, issuer/callback-keyed public registration persistence,
resource propagation and typed registration/reconnect errors. Reuses the already
served /app/oauth/client-metadata.json; no new public route. The existing daemon
OAuth RPC supplies public cache reads to isolated children. No broker, outbound
storage or spawn-site edits are needed for this slice.

Proof: seven targeted regressions failed before and passed after; all six local
MCP connect-card cases (challenge/path x CIMD/DCR/existing) also failed against
original production files and passed after. OAuth/registered-client suites passed
114 tests; the first affected Linux oracle run passed 165, zero skips. Final
expanded validation and PR/review results are recorded in the implementation PR.
This is API-level card/callback and local-server proof, not a rendered-browser or
production deployment assertion. Server-owned callback/mobile return and live
acceptance remain with the existing follow-on lane; do not mark ladder 1.10 done.
