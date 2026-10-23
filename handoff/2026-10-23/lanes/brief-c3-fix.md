Fix round on PR #5232 (worktree C:\Users\Jonathan\Projects\wf-c3-tail, branch conv/c3-tail). Review:
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-c3.md
Fix every finding with a failing-first test, without adding a second path:
1 HIGH: a workflow node's llm_policy.fallback_chain must not create a second fallback path when an invocation carrier or
  pre-bound served authority is present; the run's own candidate fallback is the only fallback.
2 MEDIUM: fallback only when every attempt is proven unsent (side_effect_state none) with a known failure class, and
  cool the abandoned source, exactly as the chat and work paths do; reuse that code, do not copy it.
3: correct the S1 registry text to what is true per path.
4: make the S13 ceiling catch the async bridge form too; keep it at zero only if nothing matches.
5: delete the dead _get_shared_router.
Push to the same branch.
