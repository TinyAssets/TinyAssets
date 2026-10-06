## 1. Public copy contract
- [x] 1.1 Add selected public instruction-template export and source validation.
- [x] 1.2 Add private idempotent recipient binding installation and explicit UI reference remapping.
- [x] 1.3 Refuse unsupported nested workflow dependencies before copy and describe absent templates.
## 2. Verification and delivery
- [x] 2.1 Prove isolation, unsupported-source refusal, no-provider execution and crash replay with meaningful tests.
- [x] 2.2 Integrate frontend agent aliases, independent exact-head floor review and hosted browser proof.
- [ ] 2.3 Complete protected merge/deployment, real-user functional acceptance and spec sync.

Delivery evidence (2026-10-05, draft PR #4515): frontend `whoami().agent_refs`
and `openChat` integration was already on main. Commit `73b3ddc9e4` adds the
authenticated installed-directory handoff and extends the real Chromium proof
to file-package copies as well as screen-only copies. Linux oracle: 246 passed,
including static prompt budgets; ruff and plugin rebuild/import probe passed;
hygiene: 0 removed, 0 tampering. Claude reviewed that exact implementation head:
APPROVE, no floor/correctness findings; lead AGREE.

Hosted browser cases and the no-skip assertion passed in
[run 37406639881](https://github.com/TinyAssets/TinyAssets/actions/runs/37406639881).
The PR records the review receipt and final evidence. Directory provenance does
not authorize hooks: harness control still owns activation and safe file reads.
Task 2.3 remains deliberately unchecked; this is not a deployment claim.

CI repair (2026-10-05, PR #4515): merged `origin/main` before repair and
rechecked before push. Job `112088492334` shows the request-continuations thread
calling the test's process-wide fake `time.sleep(5)` and raising `StopLoop`.
The extracted maintenance loop now receives a private import scope for its
clock and update-maintenance dependency. All prior assertions remain; update
failure isolation, update call counts and unchanged process sleep are asserted.
No runtime or static prompt changes; Playwright imports remain test/fixture-local.
Linux oracle: 118 passed, zero skips (delivery account deletion/reservations,
update maintenance, static prompt budgets, agent templates and system Chromium
browser). Ruff and plugin rebuild/import probe passed. Claude cross-family
review: APPROVE, no floor/correctness findings; lead AGREE. Hygiene: 0 removed,
0 tampering. Reuse the existing draft PR; task 2.3 remains unchecked.
