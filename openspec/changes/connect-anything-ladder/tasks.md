Implementation backlog only. Reuse inline-connect-and-approve request authority, D5 browser substrate, D6 ta, and L3/L4 card/skill work; no duplicate implementation of those lanes.

## 1. Connections and MCP

- [ ] 1.0 Consolidate L3/L4 into one chat/settings connect card branching on auth shape and a multi-account connections list; verify account selection, preserved independent connections and shape-switch invalidation.

- [ ] 1.1 Extend existing connections with typed MCP/browser metadata, incarnation-scoped custody refs and migration; prove HTTP preservation, version refusal and rollback retaining cleanup access.
- [ ] 1.2 Consume broker-streaming-contract for HTTP MCP OAuth metadata discovery, DCR/CIMD/static registration, PKCE/resource binding, initialization, paginated discovery, streaming calls and safe session renewal; verify cross-chunk secret scanning, cancellation/backpressure, stale catalog, auth failure and uncertain-call non-replay.
- [ ] 1.3 Implement owner-activated stdio in the jail with protected secret-entry cards and broker-injected outbound auth and ta discovery/dispatch; verify raw-token-only servers fail visibly, exact configuration revision, identifier collisions, grant isolation and no host fallback.

## 2. Browser and inline continuation

- [ ] 2.1 Extend D5 with protected login capture and scoped surrogate handles; verify origin/session/expiry binding and passkey/challenge takeover without raw secrets to agent code.
- [ ] 2.2 Suppress capture-context observation and credential artifacts, then restore only structured actions/sanitized page access; prove post-login evaluation/storage/CDP/network/profile reads are unavailable and cancellation/failure leave no captured credential artifacts.
- [ ] 2.3 Integrate both shapes into existing inline requests/coordinator/continuation; verify Stop, account switching, revision changes, crash recovery, callback replay and one committed continuation result.
- [ ] 2.4 Fence revocation before durable cleanup, expire staged custody and invalidate dependents on backing connection removal; verify reconnect cannot reuse old grants or browser sessions.

## 3. Acceptance

- [ ] 3.0 Save agent-authored API/OpenAPI connectors as tested extension revisions with declared secret slots, connection-list entries and revocation; verify failed/changed tests and credential-free exports.

- [ ] 3.1 Coordinate L3/L4's generic card/skill and D10 distribution; connect an unknown MCP server and login-only site without per-platform code, including an unpowered direct-control path.
- [ ] 3.2 Run cross-user/data-loss guard mutations and affected/heavy tests on Windows and Linux 3.11 oracle; preserve test names, run touched-Python ruff, regenerate mirror for tinyassets edits and pass hygiene with 0 removed / 0 tampering.
- [ ] 3.3 Assert deployed implementation SHA, public canary and real-user inline MCP/browser connect/cancel/revoke pass; sync this capability and the parent delegation references after acceptance.
