Fix round on PR #5232 (worktree C:\Users\Jonathan\Projects\wf-c3-tail, branch conv/c3-tail). Review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-c3.md
Fix every finding with a failing-first test, without adding a second path:
1 HIGH: a workflow node's llm_policy.fallback_chain must not create a second fallback path when an invocation carrier or
  pre-bound served authority is present; the run's own candidate fallback is the only fallback.
2 MEDIUM: fallback only when every attempt is proven unsent (side_effect_state none) with a known failure class, and
  cool the abandoned source, exactly as the chat and work paths do; reuse that code, do not copy it.
3: correct the S1 registry text to what is true per path.
4: make the S13 ceiling catch the async bridge form too; keep it at zero only if nothing matches.
5: delete the dead _get_shared_router.
Push to the same branch.

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
