Final fix round on PR #5237 (worktree C:\Users\Jonathan\Projects\wf-durable-send). Re-review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-rereview-send.md
All ten earlier findings are resolved. Fix only the two new findings, each with a failing-first test, no new paths:
A: a drain or stop never turns a queued (pending, not yet dispatched) send into a failure; it stays pending and the
   box's startup resume runs it exactly once. Only a send whose dispatch actually started and was interrupted becomes
   uncertain, as today.
B: consumer runs left unprojected at cutover can still be written to the transcript exactly once (through the one
   allowed writer, or a one-shot projection deleted with the other one-shot migrations), never silently dropped.
Push to the same branch. The lead checks this round; no further review.
