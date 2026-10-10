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

PR #4598 fix round merges origin/main. AGREE with all six supplied cross-family findings: configuration is advisory; model/external observations cannot authorize rollback; candidate-only failures must reproduce against the previous image; unique scratch paths and restart retries prevent probe races; rate alarms require multiple owners; secrets are step-scoped and installs pinned; PR jail/browser proofs and affected-test jail execution are restored. No review receipt is requested or generated. No deployment is claimed.

- [x] Re-run touched/heavy proofs, structural guards, Ruff, actionlint and hygiene.
Pushed fix-round head `8faf9beb0f5`; final hosted results and full restored-workload timings are maintained in PR #4598. Local proof: 601 structural, 141 workflow, 99 capability/deploy and 87 Linux cases (zero Linux skips); Ruff, actionlint, mirror/import build and hygiene pass. GitHub API and Pushover failure injections warn and exit normally. Hosted release-contract coverage found the new deploy dependencies missing from the image-build prefilter; the prefilter now includes the canary action and scripts.

Restart continuation: merged current origin/main (8996a96d2a1), preserving both owner-cell spec additions. Corrected hosted callback fixture and build-image input coverage, updated workflow/draft/budget contracts, and made model-dependent report evidence warning-only. Focused Linux proofs: 156 capability/workflow/tool cases plus 25 starter cases, zero skips. Full hosted re-measurement on the final pushed head is recorded in PR #4598; the earlier reduced-workload timing is explicitly superseded. No review receipt or deployment is claimed.

Final local verification: 605 structural guards passed; production Docker image passed all fourteen capabilities; release contracts passed after matching the dynamic script closure in the build prefilter. Removed the remaining PR duration/execution filters (retaining only full structural deduplication and the required heavy-file exclusion); 52 selector/workflow contracts passed. Ruff and actionlint pass. Hosted timings must include this restored selection, not merely the dedicated proof jobs.
