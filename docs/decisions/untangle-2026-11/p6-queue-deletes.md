# P6c as built (2026-11-21)

`untangle/p6-queue-deletes` is the third of P6's four PRs. Its design is the P6 research's §4
P6c (2026-11-21, read on `f36f7dbd5`): deletion only, decided on the 30 days of runs that
[`p6-process-code.md`](p6-process-code.md) item 8 records (2026-10-22 to 2026-11-21,
read-only REST reads of Actions runs, jobs, job logs and PR timelines).

| Workflow | Runs | Acted | What it did |
|---|---:|---:|---|
| `rerun-cancelled-checks.yml` (cron `*/15`) | 130 | 1 | GitHub ran the schedule about 4 times a day, not 96. One rerun: #5442's `Diff scope declared`, run 42134000114, 2026-11-06. |
| `queue-freshness.yml` | 402 | 26 | 188 `act` jobs: 26 textual `conflict` removals (labelled `stale-vs-main`, auto-merge disarmed), 338 `unchecked`, 1 `fresh`, 0 `semantic-conflict`. |

1. **Queue-freshness is deleted**: `.github/workflows/queue-freshness.yml` and
   `scripts/queue_freshness.py`. Its semantic half, the old Python suite's selection and
   quarantine run on the merged tree, never reached a verdict. Its textual half repeats
   GitHub's own conflict state: the queue refuses a conflicting group, and auto-enroll arms the
   PR again on its next `synchronize`, which undoes the disarm. Nothing in the repo or on the
   host reads `stale-vs-main`; the label goes on a host-actions row (no live act here). Not
   kept as an 80-line textual half with no probe job: that would still be a copy of GitHub's
   state.
2. **Rerun-cancelled-checks is deleted**: `.github/workflows/rerun-cancelled-checks.yml` and
   `scripts/rerun_cancelled_required.py`. One action in 30 days, and GitHub's throttling of the
   schedule means it cannot do what its header promised. The state it caught is loud (a
   missing required check on an armed PR) and recoverable (a rerun, or a push). The 2026-09-24
   concern line asking for an automatic rerun goes with it, as decided.
3. **Neither had a test file**, so no test is removed. `scripts/tests/test_workflow_scripts.py`
   (P6a) holds that no workflow names a deleted script.
4. **The cutover manifest loses `scripts/queue_freshness.py`'s edit entry**: the file no longer
   exists to edit at the cutover. `ci/guards/cutover.test.ts` fails on an entry naming a missing
   file.
5. **`hotfix.py`'s review read stays.** `review_passed_at` and its helpers had two readers;
   `require_review` in `hotfix.py` is still one, so nothing there is dead. The cutover deletes
   them with `hotfix.py`.
6. **History keeps the names**: ADR-038, `required-checks.md`, `b24-manifest.md`,
   `p2-*.md` and `p6-process-code.md` describe what was true then (ADR-005).
