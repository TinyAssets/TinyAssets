LANE: unblock main's merge queue. Worktree: create C:\Users\Jonathan\Projects\wf-ci-main on a new branch fix/ci-queue-empty-files
from origin/main (you may create this one worktree). Two mainline CI defects block every queued PR:
1. The merge queue's aggregate required check rejects test files that are selected but report no test cases (four
   selected files have no test definitions on main). Find the selector and the aggregate check (scripts/affected_tests.py,
   scripts/ci_required_tests.py, scripts/ci_assert_junit_case.py, the merge-group workflow), and fix the root cause:
   either such files are not selected, or a selected file with no test functions is not a failure; never weaken the
   check for files that do define tests. Also delete test files on main that define no tests and serve no purpose.
2. The runsc owner-wake job ("production boxhostd startup and owner wake through runsc") fails because its test setup
   omits UNIVERSE_SERVER_URL, which main now requires. Set it in the test setup.
Failing-first where possible. Open a PR to main with a Lane line listing every file; do not post a Drain-Review
receipt. Report the head SHA.
