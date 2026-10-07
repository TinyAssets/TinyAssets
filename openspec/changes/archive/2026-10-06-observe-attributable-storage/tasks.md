## 1. Shape

- [x] 1.1 Record proposal/design and independent cross-family shape/basic-safety review; resolve blocking approach findings.

## 2. Implement and verify

- [x] 2.1 Implement bounded metadata sampler with existing descriptor helpers and memo, scoped lease attribution, explicit coverage and no data writes.
- [x] 2.2 Wire additive admin-only status and test cache authorization, privacy, hard links, races, missing data and traversal bounds; rebuild plugin.
- [x] 2.3 Run focused Windows and Linux proof, compare affected outcomes, obtain exact-head independent approval and required CI.

## 3. Deliver

- [x] 3.1 Deploy with authenticated public canary/revision proof and synchronize shipped spec; report readiness only.
- [ ] 3.2 Record owner-provided rendered acceptance before closure; no autonomous app prompt or production load test, broader simplification remains open.

## 4. September 18 legacy freshness follow-up

- [x] 4.1 Add observation freshness and explicit partial accounting labels with regressions and plugin parity.
- [ ] 4.2 Obtain focused Linux proof, independent review, CI, deployment proof and spec sync; broader resource-policy closure stays open.

2026-09-08 Windows/Python 3.14: focused six-file suite (`test_storage_observations`,
`test_resource_usage_status`, `test_ttl_memo`, `test_api_status`,
`test_get_status_primitive`, `test_workspace_fs`) = 117 passed, 66 skipped.
This is portable/cache/status evidence ONLY: new traversal and existing POSIX
filesystem cases are skipped here and must pass the authorized Linux CI route.
Ruff, plugin rebuild/import probe, strict OpenSpec validation and diff check pass.

Linux/Ubuntu/Python 3.11 PR run 34306784859 passed all 27 new tests, including all
21 POSIX-specific cases, and the new current-admin cache regression. Affected
six-file result: 181 passed, two unchanged Windows-only skips. No missing tests
or regressions versus baseline 34305496977. Actual checkout 2c321ed5 has the
identical full tree to approved bf39c447. [Proof](https://github.com/Jonnyton/TinyAssets/pull/3568#issuecomment-5595397012).
PR #3568 merged as cb74e216. Deploy 34308081219 passed authenticated public
canary and protected revision containment at 2026-09-09 03:42 UTC; shipped spec
is synchronized. [Release proof](../../../docs/reviews/2026-09-08-attributable-storage-proof.md).
Task 3.2 remains unchecked: the owner owns live testing, no autonomous app prompt
is authorized, and no rendered feature-use or organic acceptance proof is claimed.
Do not archive or declare the broader resource system complete on readiness alone.
