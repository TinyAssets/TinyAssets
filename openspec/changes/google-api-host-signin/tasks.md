## 1. Implementation

- [x] 1.1 Extend directory host matching and Google data; add connect guidance and mirror changes.
- [x] 1.2 Prove Google asks, uncovered discovery and other-row host boundaries with regression tests.

## 2. Verification and delivery

- [x] 2.1 Pass Linux oracle, ruff, mirror parity and test hygiene with zero removals/tampering; sync specs.
- [x] 2.2 Open draft PR and complete one cross-family floor/correctness review.
- [ ] 2.3 After merge, assert deployed SHA and perform a real-user Calendar app pass.

Verification: Linux oracle (`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q tests/test_platform_oauth_clients.py tests/test_generic_oauth_connections.py --basetemp /tmp/b`) passed 96 tests, zero skips. Changed-file ruff passed; mirror parity matched 602 files; strict OpenSpec validation passed. Broader repository ruff reports 55 errors in untouched files. No affected OAuth files are on the heavy-test exclusion list. Specs synced. Test hygiene: 4 tests added, 0 removed, 0 tampering. Draft PR: https://github.com/TinyAssets/TinyAssets/pull/4495. Cross-family review: Claude via peer-agents returned APPROVE, no floor/correctness findings; its independent platform OAuth file passed 67 tests. Disposition: AGREE. Remaining work is post-merge deployment assertion and real-user Calendar proof; this PR remains draft.
