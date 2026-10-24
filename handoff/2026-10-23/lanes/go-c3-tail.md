LANE: C3 tail, one model call (Shape registry S1, S13, S23 in docs/architecture.md; openspec/changes/shape-convergence
C3). Worktree C:\Users\Jonathan\Projects\wf-c3-tail, branch conv/c3-tail (from main eb582d82d08).
S1 says the second paths still live are "the MCP-sampling reviewer and one-provider authority". S13 (unverified):
platform helpers (reflexion, raptor, agentic_search, extract_learning) call call_provider; prove each carries owner
authority through the one model_call path or delete it (Direction: no platform LLM). S23: remove the S1 mocks.
Converge every model call on model_call.call(model_ref, context, parent) through the broker with a receipt, delete the
second paths, and lower or zero the S1/S13 ceilings in scripts/check_shapes.py so the registry rows read converged.
Update the registry rows. Open a draft PR.

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
