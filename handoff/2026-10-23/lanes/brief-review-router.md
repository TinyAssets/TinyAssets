Adversarial review of two small branches, both based on 9ab39b4b886. Read-only.
1. conv/router-503-schema-drift (worktree C:\Users\Jonathan\Projects\wf-router-drift; `git diff 9ab39b4b886..HEAD`):
   the router maps owner-box transport failures to 503 owner_box_unavailable with Retry-After; a generic SQLite layout
   reconciler rebuilds ephemeral tables with an old layout and refuses on durable ones.
2. conv/turn-reply-budget (worktree C:\Users\Jonathan\Projects\wf-turn-budget; `git diff 9ab39b4b886..HEAD`): the last
   request of a turn is a tools-off reply; a model failure proven unsent advances to another free candidate.
Yardstick: the "Target shape (binding)" table T1-T10 in openspec/changes/owner-daemon-in-box/design.md (router only
maps and forwards, T1). Try to break: the router leaking box internals or owner data in errors, a 503 masking an auth
failure (401/403 must stay), retries amplifying load, the router gaining business logic; the reconciler dropping a
durable table or user data, a table misclassified as ephemeral, a race between two openers; the turn change widening to
paid models, double-charging an attempt, an infinite candidate loop, or a final reply that can still call tools.
Report findings with file:line and severity, then exactly one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE.
No sub-agents, no peer_agent.py; run at most the test files you need. Budget about 20 minutes.
