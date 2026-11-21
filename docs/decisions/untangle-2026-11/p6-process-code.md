# P6a as built (2026-11-21)

`untangle/p6-process-code` is the first of P6's four PRs: process code in one place. Its design
is the P6 research's §4 P6a (2026-11-21, read on `f36f7dbd5`). The other three are
`untangle/p6-tests-beside`, `untangle/p6-queue-deletes` and `untangle/p6-queue-helpers`.

1. **The review module is `scripts/review_checks.py`, one file.** It holds the required `review`
   check (`--review-check`, `--signal-pr`, `--gh-read`) and the body's Untangle check
   (`--untangle-check`), which share one prose rule, `is_prose`. There is no drain and no
   receipt any more, so the old name described neither. It is not split three ways (review,
   untangle, prose): that would give the prose rule three homes and a sibling import, and the
   host's landing script copies this one file and runs it under `python3 -I`. Nothing shows
   the two checks changing for different reasons. Every reader moved: `review.yml`'s four
   calls, `pr-scope-guard.yml`'s step and its `SENSITIVE_RE`, `queue-freshness.yml`'s header,
   `queue_freshness.py`, `hotfix.py`, the tests and `tasks.md`. Decision logs, ADR-038 and the
   archived OpenSpec changes keep the old name: they are history (ADR-005).
2. **The hotfix holds its own read of the check.** `review_passed_at`, `review_run_passed`,
   `review_app_id`, `_gh_check_runs` and `_ACTIONS_APP_ID` move into `hotfix.py`, and their
   cases into `tests/test_hotfix.py`, so the cutover deletes them with the hotfix (the manifest
   already deletes `scripts/hotfix.py`). Until `untangle/p6-queue-deletes` deletes
   queue-freshness, `queue_freshness.py` imports `review_passed_at` from `hotfix`. The run's
   `external_id` stays in `review_checks.py`, where the writer makes it, and the reader imports
   it inside the function, since the host runs `hotfix.py` alone. The listing goes through the
   hotfix's own `run`, like its other `gh` reads, so a 5xx stops the hotfix (loud, rerun) and
   leaves queue-freshness's PR unchecked that round, its read-failure path already. The
   `app_id` and `env` parameters had no caller but the tests, and went.
3. **`test_review_check.py`'s oracle reads as GitHub does:** the App's newest `review` run on
   the commit, completed with success. It no longer calls the hotfix's reader.
4. **Every script a workflow names exists** (`scripts/tests/test_workflow_scripts.py`). Each
   workflow's and composite action's `run` steps are read as YAML, from their own working
   directory, and each `scripts/<x>.py` they name by path or module (`python -m scripts.x`,
   `from scripts.x import`, `from scripts import x`) must exist. A renamed script otherwise
   leaves a step naming a missing file, which only that step's own run shows. The nearest
   existing check, `tests/test_live_docs_reference_real_scripts.py`, does not hold this: it
   reads text, excuses a line near "retired" or "removed", guesses the working directory,
   misses module imports, and goes with `tests/` at the cutover. It is left as it is.
5. **`scripts/review_checks.py` is release-critical, held by a case** in
   `scripts/tests/test_scope_guard_policy.py`.
6. **A passed test with no duration stops `budgets.mjs`** (`platform/ci/budgets.mjs`
   `measured`). It was skipped, so a lost duration could lower its project's figure (Fact 8).
   A non-finite or non-positive calibration stops it too. The shape is codex's
   `p6-calibrated-measurements`.
7. **d3 §B8's three checks, decided** (`drift_code_audit.md` §6.2, §6.3, §6.5):
   - **§6.2, refuse a new Python guard of a `platform/` path: dropped.** At `f934871ad`, 14 of
     291 `tests/` modules named `platform/`, and some are rightly process tests: the queue
     classifier's inputs, the tamper check over platform tests, the setup reading
     `review-app.html`. After the cutover no `tests/` exists, and `untangle/p6-tests-beside`
     holds the process leg to `scripts/tests` alone. Building it would add about 40 lines to
     `ci_structural_guards.py`, which the cutover deletes.
   - **§6.3, no owed text in an accepted ADR: dropped.** 5 of 32 accepted ADRs say "owed" 7
     times, each one history or already in `tasks.md`. A word ban would edit history, which
     ADR-005 forbids, and `tasks.md` with the Untangle check already gives owed work one home.
   - **§6.5, a calibrated control for every measurement: met by construction.** No
     `measure()` helper. The Vitest budgets are calibrated per test (`meta.calibration_ms`,
     `ci/classes.ts`, #5646), and each watchdog is 6x a cited run. The Python durations table
     retires with the old suite. The one gap was item 6.
8. **d3 §B9's three workflows, measured** (30 days from 2026-10-22, read-only REST reads). The
   deletions and the classifier's fate are `untangle/p6-queue-deletes`' and
   `untangle/p6-queue-helpers`':

   | Workflow | Runs | Acted | What it did |
   |---|---:|---:|---|
   | `rerun-cancelled-checks.yml` (cron `*/15`) | 130 | 1 | GitHub ran it about 4 times a day, not 96. One rerun: #5442's `Diff scope declared`, run 42134000114. |
   | `queue-freshness.yml` | 402 | 26 | 26 textual conflicts removed and labelled `stale-vs-main`, which nothing reads; 338 `unchecked`, 1 `fresh`, 0 `semantic-conflict`. |
   | `queue-handoff.yml` | 490 | 2 handoffs | Every counted `failed_checks` removal since 2026-11-05 counted because the job's log read failed, not from its evidence. |

9. **The host's landing script needs no edit.** `land_pr2.sh` finds the file that defines
   `untangle_check_problems` on main, so it finds `scripts/review_checks.py`.
