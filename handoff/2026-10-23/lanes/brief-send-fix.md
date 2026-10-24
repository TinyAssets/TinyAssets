Fix round on PR #5237 (worktree C:\Users\Jonathan\Projects\wf-durable-send, branch conv/c11-durable-send). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-send.md
Fix findings 1-10, each with a failing-first test. Priorities: 1 (a stored message is never run after a restart: the
box resumes pending stored sends on start, exactly once) and 2-3 (no caller of a deleted module: move what
mcp_events_http.py needs, update the background-authority inventory and stale templates). 4-6: stuck states resolve
(a proven no-effect send is retryable; the model sees an honest summary of non-completed rows instead of hiding them
forever; a retry conflict resolves). 7: report history_saved truthfully. 8: serialize dispatch per thread, not per
database, and never hold executor slots while waiting. 9: the send-path guard catches every writer of owner message
rows. 10: resume saved records for every agent on load. Push to the same branch.
