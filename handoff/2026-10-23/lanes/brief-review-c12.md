Review the C12 / S17 knowledge-as-files spec (branch spec/c12-knowledge-files, worktree
C:\Users\Jonathan\Projects\wf-c12-spec; `git diff origin/main...HEAD`). Read-only.
Yardstick: openspec/changes/owner-daemon-in-box/research.md and T1-T10 in its design.md; ADR-017 (knowledge is files),
ADR-018 (export); AGENTS.md Direction 3 and 6; registry row S17 in docs/architecture.md.
Check: the owner's knowledge is files in the owner's box and those files are canonical; every index is derived and
rebuildable in the box; exactly one export action; the second canonical stores (memory/, retrieval/, knowledge/,
daemon_brain.py) are deleted in named slices, each with what it deletes; no shared component reads owner knowledge;
published items are copied out at publish time only (T10); no migration that silently drops data; the guard that makes
S17 read converged is concrete. Flag anything over-built or left open. Report findings with file:line and severity,
then exactly one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE. No sub-agents, no peer_agent.py. Budget about
15 minutes.
