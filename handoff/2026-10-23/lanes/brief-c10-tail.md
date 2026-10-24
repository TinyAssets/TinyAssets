LANE: C10 tail, one extension unit and four tools (Shape registry S6, S7, S18 in docs/architecture.md; C10 in
openspec/changes/shape-convergence). Worktree C:\Users\Jonathan\Projects\wf-c10-tail, branch conv/c10-one-extension-unit
(from main eb582d82d08).
S6 second path: legacy node actions remain in api/extensions.py. S7: engine_mcp_server.py remains an owner-pinned
profile and a second server module beside universe_tools.py/ta_cli.py. S18: fantasy and research_probe domains remain
installed entry points; api/market.py keeps outcomes and attribution after paid-market retirement. Converge each on the
one path the registry names, delete the second path in the same PR (an installed entry point becomes a package a user
could install, or is deleted), lower the check_shapes.py ceilings to zero, and update the registry rows. If a deletion
removes a user-visible feature, list it in the PR body. Open a draft PR.
