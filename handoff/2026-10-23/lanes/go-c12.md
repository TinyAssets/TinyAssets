SPEC LANE: C12 / S17, knowledge is files, one export (ADR-017, ADR-018; registry row S17 in docs/architecture.md:
"OKF bundle canonical, indexes derived; one export action"; second paths memory/, retrieval/, knowledge/,
daemon_brain.py canonical; bundle seed-only; okf_export.py unwired). Worktree C:\Users\Jonathan\Projects\wf-c12-spec,
branch spec/c12-knowledge-files (from main eb582d82d08). Closed PR #4903 attempted this pre-C6 and extended the
one-shot migration; do not reuse that approach (read it for scope only: gh pr diff 4903 --name-only). Write the spec
for the post-C6 shape: the owner's knowledge is files in the owner's box, indexes are derived and rebuildable inside
the box, one export action produces the bundle, and the second canonical stores are deleted in slices. Name each slice,
what it deletes, and the guard that makes S17 read converged. Spec only: proposal, design, spec delta, tasks; no
product code. Keep it short. Open a PR.

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
