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

RESUME NOTE: a previous run of this round was interrupted (system memory). Its partial edits are committed on the branch as "WIP: interrupted lane". Review that commit, keep what is right, finish the round, and replace the WIP commit message by squashing it into proper commits before pushing.

SHAPE: read openspec/changes/owner-daemon-in-box/research.md and the "Target shape (binding)" table T1-T10 at the top of
openspec/changes/owner-daemon-in-box/design.md before designing anything. The shared layer (router, broker/egress
proxy, waker) holds no owner business logic and no host path to owner data; owner logic runs in the owner's box;
provider credentials stay in broker custody. Before patching any failing test or finding, ask "can this class exist in
the target shape?" If not, move the work and delete the shared path; never add a guard to shared code. AGENTS.md
Direction: provider-agnostic (no provider-specific code), fewer docs and less process.

HARD CONSTRAINTS: no sub-agents (no scripts/peer_agent.py, no claude/codex subprocess). Work only in the worktree
named above unless this brief says otherwise. No production access, no secrets. Never touch git config. Never use
--no-verify. No full suite: run targeted tests via python scripts/linux_oracle.py -- <files>, plus ruff (and
node --check for JS). No model identifiers in commits or PR text. Commit trailer:
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
PR descriptions end with:
Generated with Claude Code - https://claude.ai/code/session_01VbXHMwY2gHVgPSN8E7CHgJ
Report: head SHA, what changed, tests run and their result, anything left undone and why.
