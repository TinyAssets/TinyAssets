## 1. Implementation

- [x] 1.1 Extend directory host matching and Google data; add connect guidance and mirror changes.
- [x] 1.2 Prove Google asks, uncovered discovery and other-row host boundaries with regression tests.

## 2. Verification and delivery

- [ ] 2.1 Pass Linux oracle, ruff, mirror parity and test hygiene with zero removals/tampering; sync specs.
- [ ] 2.2 Open draft PR and complete one cross-family floor/correctness review.
- [ ] 2.3 After merge, assert deployed SHA and perform a real-user Calendar app pass.

Verification: Linux oracle (`MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q tests/test_platform_oauth_clients.py tests/test_generic_oauth_connections.py --basetemp /tmp/b`) passed 96 tests, zero skips. Changed-file ruff passed; mirror parity matched 602 files; strict OpenSpec validation passed. Broader repository ruff reports 55 errors in untouched files. No affected OAuth files are on the heavy-test exclusion list. Specs synced. Test hygiene and cross-family review pending.
