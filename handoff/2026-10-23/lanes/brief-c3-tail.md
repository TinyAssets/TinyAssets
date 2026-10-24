LANE: C3 tail, one model call (Shape registry S1, S13, S23 in docs/architecture.md; openspec/changes/shape-convergence
C3). Worktree C:\Users\Jonathan\Projects\wf-c3-tail, branch conv/c3-tail (from main eb582d82d08).
S1 says the second paths still live are "the MCP-sampling reviewer and one-provider authority". S13 (unverified):
platform helpers (reflexion, raptor, agentic_search, extract_learning) call call_provider; prove each carries owner
authority through the one model_call path or delete it (Direction: no platform LLM). S23: remove the S1 mocks.
Converge every model call on model_call.call(model_ref, context, parent) through the broker with a receipt, delete the
second paths, and lower or zero the S1/S13 ceilings in scripts/check_shapes.py so the registry rows read converged.
Update the registry rows. Open a draft PR.
