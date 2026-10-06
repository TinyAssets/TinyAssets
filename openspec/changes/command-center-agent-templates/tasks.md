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
