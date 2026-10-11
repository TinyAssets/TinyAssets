## 0. Measure before replacing coverage

- [x] 0.1 Replay the seven merged cutover fixes and three current regressions on isolated scratch revisions; retain baseline, failing node IDs, counts and timings. Delete or fold redundant seam-mocked capability tests only after replacement proof; reduce net test count and PR time.

## 1. Capability contract and runtime evidence

- [x] 1.1 Add the canonical capability catalogue and fail-closed report validation.
- [x] 1.2 Instrument stable runtime failure codes and denominators; expose protected rates and test spike detection.

## 2. Production image acceptance

- [x] 2.1 Seed synthetic owner authority, model connection and HTTP grant; boot the real production image and exercise every catalogue capability.
- [x] 2.2 Require image acceptance through the existing required-tests aggregate and add a structural guard against execution seam mocks.
- [x] 2.3 Demonstrate historical/reintroduced cutover failures and record exact evidence and limits in this change.

## 3. Live assurance

- [x] 3.1 Implement dedicated-owner live checks and systemic-rate checks with bounded effects and exact failure attribution.
- [x] 3.2 Wire deploy failure/rollback and five-minute hosted execution to labelled issues and existing Pushover paging; document one-time provisioning.

## 4. Verification and delivery

- [x] 4.1 Run affected tests, Ruff, structural guards, plugin build, hygiene and Linux/image acceptance; resolve the cross-family floor review.
- [x] 4.2 Sync the implemented spec, commit explicit paths with attribution, push and open the non-draft infra-change PR.

Round 3 supersedes the earlier comparison design. AGREE with all four supplied findings: restore the pre-PR public MCP/app blocking, deploy-failed issue and rollback path, and the blocking CF Access gate; retain only the new capability canary as advisory. Delete the redundant image comparison entirely, so unprovisioned configuration cannot pull or run either image. Restore draft skips for jail/browser proofs and replace the production build's scripts wildcard with explicit live-canary inputs. Sync the assurance spec to this behavior.

Cross-family floor review: AGREE with the stale timeout-budget finding; corrected the bound to 25 minutes of deploy/probe work plus 55 minutes for in-flight turns, and removed the deleted comparison from the budget test. Public gate and rollback semantics match origin/main. Linux oracle: 102 passed, zero skips. Actionlint and Ruff pass. No deployment is claimed.
