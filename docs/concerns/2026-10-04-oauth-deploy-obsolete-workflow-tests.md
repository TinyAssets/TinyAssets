# OAuth deploy tests still assert the pre-review contract

PR #4443's review fixes move OAuth validation to a runner prerequisite, warn and
skip incomplete pairs, and require target/rollback filtering before writing an
atomic pair. Eleven existing cases in `tests/test_deploy_prod_workflow.py` still
assert the previous single-step contract (including failure on half-configured
credentials) and do not supply the new action/previous-image step inputs.

The founder explicitly required only added tests, with no changes or renames to
existing tests. Those eleven cases are therefore unchanged. New executable
coverage lives in `tests/test_oauth_deploy_hardening.py`.

Evidence on 2026-10-04: the focused Linux oracle run reports 53 failures, all in
the legacy workflow file. Running that unchanged file against the pre-fix HEAD
workflow through a temporary, external baseline fixture gives 42 failures and
67 passes; the additional eleven failures are the obsolete OAuth cases. No
production branch files were replaced for the baseline comparison. The workflow
test file is already listed in `.github/heavy-test-files.txt`.

Resolve when authorized to update the eleven existing OAuth cases to the new
runner/host split. The 42 pre-existing workflow failures need their own scope;
this change neither suppresses them nor reports them as passing.
