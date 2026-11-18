# agent-status as built (2026-11-18)

The owner already returns a receipt for the requested run. The Talk page stored its
answer against whichever run happened to be current when the request finished.
Polling, socket frames and a reconnect could also start overlapping reads of one
run. A late snapshot could replace a terminal answer and restart its poll.

The per-agent talk owns its status read and poll. It accepts an answer only while
the receipt that requested it is current. Frames or reconnects during a read request
one fresh snapshot after it, if the run is still live. An obsolete failure cannot
overwrite the page's notice. A failure of the current read still reaches the user.
Returning to the agent binds a new view to the same talk and its pending work.

A cancellation still targets the run whose button was pressed. Its receipt updates
that run while current. If a different receipt for the same live run landed in the
meantime, the app reads its status again. The owner continues to decide whether a
cancellation is pending or settled. A cancel failure is reported only while that
run is still the current one.

Three Chromium regressions use the normal Send action, navigation and the browser's
socket event. Against `f934871ad`, one returned the past run after the next one
succeeded. Three frames during one status read started three additional requests.
On this branch the newer result survives reopening the agent, and the frames cause
one subsequent read. The existing three-second fallback remains available across
view changes. A late cancellation failure for the past run also leaves the newer
run and its notice alone.

## Next dependency

Two Send actions from different renders can start before the first submit returns.
On main both requests omitted `conversation_id`. The second answer opened the
socket for its conversation, the first answer later selected a different conversation,
and the third send used that conversation while the socket still watched the other.
This precedes the status read.

The app already promises one tab conversation per agent (`TALK_NOTE`).
`limits-audit.md` records one live run per conversation. A second message waits
its turn there. The submission work must retain the first send's original request
and `client_send_id` across an uncertain reply. The owner's admission reuses
that ID for the same caller and agent, returning the original run and conversation.
The request lookup lives on the `runs` receipt. Settled receipts are pruned to
`RUNS_KEPT`, so a new send after that retention window cannot establish what
happened to the old request. Recover the conversation before sending waiting
inputs in order. Prove this with
two renders and a lost first reply, including a retry after the owner accepted it.
