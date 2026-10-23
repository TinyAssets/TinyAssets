Fix round on two branches. Review (DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-router.md
Each fix gets one test that fails first; commit each separately; push both branches.

A) conv/router-503-schema-drift (worktree C:\Users\Jonathan\Projects\wf-router-drift):
- R1 HIGH: map ONLY transport failures (connect refused/failed, a failed wake, an HTTP error before any response
  started) to 503 owner_box_unavailable. A response the box sent passes through unchanged, status, headers and body,
  including its own 503s and Retry-After. Fix tests/test_owner_router_unavailable.py accordingly.
- R2: no Retry-After when the request may have been sent (error after the body started).
- R3: one-line note at the owner_sessions declaration that ephemeral means a layout change signs everyone out.
- R4: a failed ROLLBACK TO must not mask the original error.
B) conv/turn-reply-budget (worktree C:\Users\Jonathan\Projects\wf-turn-budget):
- B1 MEDIUM: if the final tools-off reply still contains tool calls, drop the tool calls and keep the text; never
  execute them; never raise away a paid reply.
- B2: decide the last attempt from the whole turn (all eligible free sources, the deadline and the failure limit), not
  the current source's free limit.
