LANE: C10 tail, one extension unit and four tools (Shape registry S6, S7, S18 in docs/architecture.md; C10 in
openspec/changes/shape-convergence). Worktree C:\Users\Jonathan\Projects\wf-c10-tail, branch conv/c10-one-extension-unit
(from main eb582d82d08).
S6 second path: legacy node actions remain in api/extensions.py. S7: engine_mcp_server.py remains an owner-pinned
profile and a second server module beside universe_tools.py/ta_cli.py. S18: fantasy and research_probe domains remain
installed entry points; api/market.py keeps outcomes and attribution after paid-market retirement. Converge each on the
one path the registry names, delete the second path in the same PR (an installed entry point becomes a package a user
could install, or is deleted), lower the check_shapes.py ceilings to zero, and update the registry rows. If a deletion
removes a user-visible feature, list it in the PR body. Open a draft PR.

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

HARD CONSTRAINTS: no sub-agents (no scripts/peer_agent.py, no claude/codex subprocess). Work only in the worktree
named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.
