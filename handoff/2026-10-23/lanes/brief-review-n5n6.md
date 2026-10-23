Review the N5 (owner egress gate) and N6 (undo by box snapshot) specs in PR #5229 (worktree
C:\Users\Jonathan\Projects\wf-n5n6-spec, branch spec/n5-n6-egress-undo; `git diff origin/main...HEAD`). Read-only.
Yardstick: openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 in
openspec/changes/owner-daemon-in-box/design.md; AGENTS.md Direction (provider-agnostic, fewer docs and process); the
Shape registry rows N5 and N6 in docs/architecture.md.
Check: N5 is evaluated by the ONE existing egress check at the broker (design.md C7), not a second check, and the
policy has one writer, the owner (T9, N10); "ask" uses the existing approval path; the agent can read but never write
it. N6 reuses the one per-owner snapshot path (design.md C1-C2, S27), fits the 5 GiB free tier, is consistent with a
running turn, and the owner confirms any restore. No owner business logic in shared code, nothing over-built, no
decision left open.
Report findings with file:line and severity, then exactly one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE.
No sub-agents, no peer_agent.py. Budget about 15 minutes.
