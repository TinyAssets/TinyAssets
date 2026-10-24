Adversarial review of PR #5237 (branch conv/c11-durable-send, worktree C:\Users\Jonathan\Projects\wf-durable-send;
`git diff origin/main...HEAD`): one durable send (registry S24, C11). It unifies box intake, removes steering,
destructive claims and batch resend paths, adds browser recovery records, reply identities and a send-path CI guard.
Yardstick: T1-T10 in openspec/changes/owner-daemon-in-box/design.md (owner messages are stored in the box) and Facts 8
(fail loudly). This is data-loss class: try to break it. A message accepted but never stored; stored twice and run
twice (page reload, retry, two tabs, crash between store and dispatch, deploy restart); a send dropped while a turn is
running; recovery records replaying another account's or another agent's message after sign-out/sign-in; a removed
path that a caller still uses (grep); the guard missing a second path. Report findings with file:line and severity,
then exactly one line: VERDICT: AGREE or VERDICT: DISAGREE_EVIDENCE. Read-only; no sub-agents, no peer_agent.py.
Budget about 30 minutes.
