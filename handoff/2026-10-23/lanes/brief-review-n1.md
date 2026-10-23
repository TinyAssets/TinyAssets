Review the N1 inbound-door spec in PR #5228 (worktree C:\Users\Jonathan\Projects\wf-n1-spec, branch
spec/n1-inbound-door; `git diff origin/main...HEAD`). Read-only.
Yardstick: openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 in
openspec/changes/owner-daemon-in-box/design.md; AGENTS.md Direction (provider-agnostic, no provider-specific code,
fewer docs and process); the Shape registry row N1 in docs/architecture.md.
Check: the shared door does only verification and a positive owner->box mapping, then wake and forward (T1, T5); all
channel logic, routing and replies run in the box as connector packages (T8); channel credentials stay in broker
custody and replies go out through the egress proxy (T6); no second ingress path or second trigger mechanism beside
the C2 wake-trigger work; per-channel verification is data, not code; duplicate and replay handling; sleeping box and
unadmitted owner; the tests actually prove "one ingress route, every event lands as session.start". Flag anything
that adds owner business logic to shared code, anything over-built, and any decision left open.
Report findings with file:line and severity, then exactly one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE.
No sub-agents, no peer_agent.py. Budget about 15 minutes.
