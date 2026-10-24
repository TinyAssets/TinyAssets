LANE: one durable send (shape-convergence C11, Shape registry S24 in docs/architecture.md; tracker item 8.11 in
openspec/changes/owner-daemon-in-box/tasks.md). Worktree C:\Users\Jonathan\Projects\wf-durable-send, branch
conv/c11-durable-send (from main eb582d82d08).
Target: an owner message is stored once (in the box, before anything is published or dispatched), then dispatched;
the page never drops a send and never shows duplicate "Still waiting to be sent when the page reloaded" notices.
Observed live: the page's self-reload (?_ta_recover=...) brought back old waiting notices, and a send typed while a
turn was running was dropped. Find every send path (onboarding/app/main.js send_journal.js durable_send.js, the box's
turn intake), measure them, converge on one, delete the others, and add the planned `send-path` structural guard that
fails CI if a second send path appears. Spec delta first if the stored-message contract changes. Open a draft PR.
