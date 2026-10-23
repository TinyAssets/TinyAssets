Adversarial review of PR #5233 (branch conv/c10-one-extension-unit, worktree C:\Users\Jonathan\Projects\wf-c10-tail;
`git diff origin/main...HEAD`, net -12k lines). It deletes engine_mcp_server.py (tools consolidated in
universe_tools.py, registration in control_plane/registration.py), standalone node registration actions, bundled
fantasy/research domains, and api/market.py. Yardstick: T1-T10 in openspec/changes/owner-daemon-in-box/design.md,
AGENTS.md Direction and Facts 11 (doors: tinyassets.io/mcp; canonical public handles, ADR-013), registry rows S6, S7,
S12, S18 in docs/architecture.md.
Try to break: an MCP tool or canonical public handle that existed and now silently disappears or changes contract
(compare the tool lists before and after; scripts/mcp_public_canary.py --assert-handles expectations); owner/center
binding now taken from a caller-supplied value instead of the authenticated endpoint context (cross-owner); a removed
module still imported somewhere at runtime (grep; import-time failure in the box image); deleted tests that were the
only proof of an authority check; data still on disk that a deleted migration was needed to read. List every
user-visible removal you can confirm. Report findings with file:line and severity, then exactly one line:
VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE. Read-only; no sub-agents, no peer_agent.py. Budget about 30 minutes.
