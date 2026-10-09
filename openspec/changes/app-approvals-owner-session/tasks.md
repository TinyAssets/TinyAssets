## Implementation
- [x] Reproduce callback routing and cookie-isolated approval failures.
- [x] Implement owner-bound browser handoff and shared sheet return/refresh.
- [x] Verify requested suites, Linux oracle, ruff, structural guards, plugin build and hygiene.
- [x] Sync spec, commit, push, open non-draft PR and complete cross-family review.

Validation: 440 Linux owner/pending/approval/app tests, 180 follow-up Linux tests (including the Windows symlink case), 91 Worker tests, 29 rendered browser tests, 584 structural guards, Ruff, plugin import probe and hygiene passed. The broader app sweep passed 976 tests; its 45 JavaScript harness failures passed after fixes (79-test rerun), and its Windows symlink failure passed on Linux. One unrelated platform skip remains. Claude cross-family review: ADAPT on account-deletion cookie clearing; AGREE, attributes corrected and endpoint/edge assertions pass. Final additional Linux account-deletion and app-controller run pending. Public canary is blocked by missing token/1Password CLI; no post-fix live deployment or native-device acceptance claimed.
