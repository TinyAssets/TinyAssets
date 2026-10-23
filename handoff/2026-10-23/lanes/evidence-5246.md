Lead approval of the merge-queue CI fix (gate files, checked line by line by the lead).
- scripts/affected_tests.py: a selected file counts as a required test file only if it can expose pytest cases (test functions, Test classes, test_ aliases or assignments; a parse error stays selected so pytest reports it). Helper-only modules are still dependencies, so tests importing them are still selected; the aggregate check stays strict for files that define tests. Selector regression failed before the fix and passes after.
- Deletes three test files that define zero tests on main (emptied by #5182): test_app_chat_cloud.py, test_cloud_only_admission_regressions.py, test_converse_turn_cost.py, plus the dead structural-guard hint for the last.
- Sets UNIVERSE_SERVER_URL in the synthetic runsc host setup that main now requires.
- 48 targeted workflow tests on the Linux oracle; ruff.
