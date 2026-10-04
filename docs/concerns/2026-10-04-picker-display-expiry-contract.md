# Picker display expiry expectation conflicts with PR #4431 retention

The unchanged test
`tests/test_native_model_options_api.py::test_native_public_picker_rechecks_after_metadata_io[expired-discovered]`
expects all options and ordering to disappear after aging prepared snapshots by
six minutes. PR #4431 retains advisory display catalogues without an upper age
limit and leaves execution freshness checks in place, so the assertion fails.

Reproduced on the pulled PR commit `dc42d8748e8c009c5cb200bd0d89c84b94a978cf`
in an external archive checkout, as well as after the P1 history-retention fix.
This predates that fix. The main-branch test was left unchanged, per the review
fix request; changing its expectation needs a separate contract decision.
