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
