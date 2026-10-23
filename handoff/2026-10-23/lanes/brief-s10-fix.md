Fix round on PR #5244 (worktree C:\Users\Jonathan\Projects\wf-s10-tables, branch conv/s10-platform-state-out). Review
(DISAGREE_EVIDENCE):
C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\result-review-s10.md
1 CRITICAL: every existing owner box would refuse its main database after deploy. The declared durable layouts must
  match what is on disk in production boxes today (built from the same history of CREATE/ALTER statements), or each
  difference gets an explicit, additive, idempotent migration that runs before the refusal check. Add a test that
  opens a database created by the previous release's code (build it from the pre-PR DDL in the test) and proves it
  opens without SchemaDrift and keeps every row.
2 HIGH: do not label S10 converged by relocation. Either move each table to its correct single home (owner state in
  the owner's box store, shared platform state in one storage/ store with one writer) and say per table where it went
  and why, or restore the S10 row to drifted with an honest probe and its true count. Never change what a probe
  measures to reach zero.
3: deleted_principals gets one home, one schema, one writer.
4-6: keep the fence debt list from growing (fence the new home or leave it in place), announce the backfill change in
  the PR body, and make the cutover helper use the current layout.
Failing-first tests; push to the same branch.
