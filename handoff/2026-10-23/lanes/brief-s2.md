LANE: S2, one way to start an agent (registry row S2 in docs/architecture.md, ADR-014: control_plane/session.py
start(agent_ref, input, parent) is the one path; chat, wakes, owner answers, agent nodes, queued runs and `ta agent run`
call it). Worktree C:\Users\Jonathan\Projects\wf-s2-start, branch conv/s2-one-way-to-start (from main eb582d82d08).
Measure every remaining way an agent turn or run starts, converge each on session.start, delete the second paths in
the same PR, add or lower the check_shapes.py ceiling to zero, update the S2 row. Do not touch the owner message send
path internals (onboarding/app/main.js, owner_door/send.py, conversation_store.py): a parallel lane (PR #5237, one
durable send) owns them; call through their public entry points. Open a draft PR.
