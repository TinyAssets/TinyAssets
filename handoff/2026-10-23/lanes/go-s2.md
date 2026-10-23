LANE: S2, one way to start an agent (registry row S2 in docs/architecture.md, ADR-014: control_plane/session.py
start(agent_ref, input, parent) is the one path; chat, wakes, owner answers, agent nodes, queued runs and `ta agent run`
call it). Worktree C:\Users\Jonathan\Projects\wf-s2-start, branch conv/s2-one-way-to-start (from main eb582d82d08).
Measure every remaining way an agent turn or run starts, converge each on session.start, delete the second paths in
the same PR, add or lower the check_shapes.py ceiling to zero, update the S2 row. Do not touch the owner message send
path internals (onboarding/app/main.js, owner_door/send.py, conversation_store.py): a parallel lane (PR #5237, one
durable send) owns them; call through their public entry points. Open a draft PR.

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
