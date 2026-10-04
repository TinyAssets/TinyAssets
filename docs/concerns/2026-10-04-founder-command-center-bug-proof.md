# Founder command-center reports, 2026-10-04

Branch: `fix/app-ui-run-and-code-checks`. Push only, no PR or deployment requested.

## Failed run reads

`app_ui.js` treated a snapshot's execution `error` as a read refusal. The bridge
now checks the returned run identity; server scope/visibility checks remain in
`api/runs.py`. The owner door already preserves error-as-data.

Red: `test_failed_run_details_and_partial_output_remain_readable` failed with
"that run is not one of yours" before the fix. Green: the bridge/live-state
tests (11 tests), scoped listing (2), and real owner-door tests (21, including
failed status/error/partial output and denial to another account). Mirror rebuilt.

Pending: production deployment and real-user verification remain outside this
push-only request. Delete this finding once those are proved.
