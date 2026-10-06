# Current coordinator verification

September18,2026 UTC. Current main base64e29743; isolated branch
codex/current-executor-liveness. Candidate is not deployed.

Focused command:
`python -m pytest -q tests/test_current_executor_liveness.py tests/test_loop_telemetry.py tests/test_assigned_queue_consumer.py tests/test_api_status.py tests/test_no_anonymous_principal.py tests/test_last_activity_canary.py`

Windows/Python3.14:126 passed, no skips,7.07s. Native Ubuntu WSL Docker oracle
using Python3.11.16/git2.47.3/bubblewrap0.12.0 and the same six-file cohort:
126 passed, no skips,7.93s. Linux invocation uses
`python3 scripts/linux_oracle.py -- -q` with those test paths, process-local
GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE mapped into the Windows linked worktree.
No copy warning. Only existing dependency deprecation warnings.

Ruff passes for the two runtime modules, canary script and two test files.
`python packaging/claude-plugin/build_plugin.py` stages445 files and import-probe
passes. `git diff --check` and
`openspec validate observe-current-executor-liveness --strict` pass.

The new regressions exercise disabled, enabled-but-missing, current beside old
files, stalled beside fresh peers, graceful retirement/replacement, stop timeout,
wrong data root, successful versus failed polls, unexpected thread exit,
private-field allowlisting and explicit engine-child observation boundaries.
The canary regression rejects unknown liveness despite recent activity.

## Independent review boundary

Lead relayed the terminal Fable architecture ADAPT on September18 and explicitly
cleared this lifecycle slice while preserving real per-universe heartbeat writes
and the host watchdog. Exact-head implementation review remains pending and
the PR must remain draft. No source-family self-review is an approval.

## Live acceptance still required

Lead owns normal merge/deploy, authenticated deployed-sha containment/public
canary and rendered ordinary app proof. Then run/read the scheduled activity
probe to show it observes the current executor. The distinct private revert
evidence mismatch remains open; this patch must not claim the entire scheduled
bundle is green. No retired fleet restart, private activity disclosure or owner
workflow edit is included. Organic clean use is not yet observed.

Rollback: ordinary revert/deploy, no schema or data migration.
