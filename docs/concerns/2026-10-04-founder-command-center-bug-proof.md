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

## Source guards

`node_sandbox.NodeSandbox.validate_source` and the bid producer/executor scanned
raw source for `open(` and other patterns; the compiler's narrower list had the
same prose false positives. Shared AST
inspection now examines calls and module/name references, preserving each list.
Comments, literals and docstrings pass; real calls (including spaced,
parenthesized, attribute and f-string-expression calls) remain blocked. The
universe path-I/O test was already AST-based and needed no change.

Red: `test_source_guard_syntax.py` had 8 failures before the fix (3 prose
rejections, 5 whitespace/parenthesized-call bypasses). Green: all 113 tests in
that file, `test_node_bid.py`, `test_describe_branch_approval.py`, and
`test_universe_path_io_guard.py` passed on Windows and the Linux oracle
(Python 3.11.16, bwrap 0.12.0, uid 1001; no skips).

The subsequent runtime-boundary regression failed on both an isolated `open(`
string and comment before the sandbox change. It now uses the same AST helper
with its original, wider forbidden list. The affected heavy file is
`test_branch_runner.py`; `test_node_sandbox.py` exercises actual execution and
the Linux jail. Windows skips for those six jail cases are not Linux proof.
