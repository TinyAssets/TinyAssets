## 1. Build

- [x] 1.1 Add the six-step XCUITest and generated-project installer.
- [x] 1.2 Reuse signing and build/package both Simulator and Device Farm runners.
- [x] 1.3 Add bounded AWS orchestration, credential injection and encrypted evidence collection.
- [x] 1.4 Replace manual founder instructions with project IAM/secrets/pricing setup.

## 2. Verify and deliver

- [x] 2.1 Run focused tests, ruff, actionlint and offline dry-run checks.
- [ ] 2.2 Run the production journey on the GitHub macOS Simulator and record the actual result.
- [x] 2.3 Open an infra-change PR, complete one cross-family review and sync the spec.

PR: https://github.com/TinyAssets/TinyAssets/pull/4604 (infra-change, non-draft).
26 focused tests, Ruff, actionlint and positive/negative splash video checks pass.
The selected heavy files yielded 245 passes, 10 skips and 22 failures in unchanged
handoff/host-installer code on Windows (missing flock, symlink semantics and one
in-flight reservation). No fixes to those unrelated modules are included.

Both XCUITest targets compiled, the production signed-out Simulator XCUITest
passed with its named screenshot, and the native splash was verified in the
actual recording in [run 38101326847](https://github.com/TinyAssets/TinyAssets/actions/runs/38101326847).
This proves the launch and evidence harness only. The preceding dependency-install
failure was resolved by selecting CI Python before pip installs.
Full signed/credentialed proof was rejected before
execution in run 38100020133: app-store environment permits main only. Founder
must dispatch credentialed proof from main after merge; no environment
protection was changed and no AWS calls were made. Do not call the six-step
Simulator journey or physical recording proven until those runs succeed.

Security fix round: independent X25519 recipient encryption, owner-only local
keygen, fail-closed placeholder, explicit IAM, main-only dispatch, secret-free
dependency installs and next-physical-run cancellation sweep implemented.
92 focused Windows tests and 11 Linux-oracle review tests pass; Ruff and
actionlint pass. The affected heavy-file Linux run has 155 passes and 30 failures,
all in test_deploy_prod_workflow.py; that file and deploy-prod.yml are byte-identical
to origin/main. Cross-family review: APPROVE; AGREE (no floor/correctness findings).
The lead must commit the generated public key before main dispatch can proceed.
