# dry-run-queue as built (2026-11-22)

`process/dry-run-queue`: the cutover's dry run now runs in the merge queue too, so a PR whose dry run passed before `main` moved is checked against the move before it merges. This was the residual risk named in `dry-run-required.md` item 2 (from the review of #5731). Research came first (Fact 14). Everything was read from the REST API, read-only, on 2026-11-22, at `0c4d44c38`.

## Research

### How the merge group is wired today

- **The live rules agree with `docs/ops/merge-queue-ruleset.json`.** Strict status checks are off. The queue uses `ALLGREEN`, builds 6 entries and times out after 35 minutes. A queued PR's only run on the tree it lands on is its merge group run.
- **`tests.yml` starts on `merge_group` `checks_requested`.** Every platform leg runs there: `platform-tests`, `platform-checks`, `platform-sizes`, `platform-app`, the recorders and the process leg. So do the old suite's shards.
- **Only the dry run was skipped there.** Its two jobs had `if: github.event_name != 'merge_group'`, and so did its gate step in `required-tests`. `ci/guards/cutover.test.ts` pinned that condition (`NOT_QUEUED`).

### How often `main` moves under a queued PR

- I checked the last 30 queue entries (#5763 to #5792). In 5 of them, `main` took a commit after the PR's Tests run started and before its group was built. In those 5 the group ran a tree the PR's dry run never saw.
- Under load the share is larger: 9 groups were built in one half hour on 2026-11-21, 11:53 to 12:23.

### What the dry run costs

- **Per run.** Twelve green PR runs (45016279248 to 45046429980) and six push runs (45018634495 to 45044049239), all inside Tests:
  - 15 jobs, 1,295 to 1,558 job-seconds;
  - it finished 282 to 434 s into the run.
- **The queue today.** The last 100 merge group runs (44871815090 to 45049067166, 36 hours) had:
  - 42 jobs each, median 5,848 job-seconds;
  - wall time median 504 s, p90 963 s, max 1,364 s (44921871950). The timeout is 2,100 s.
  - `required-tests` started 349 to 1,309 s in, median 451 s;
  - a runner wait over 60 s in 37 runs and over 300 s in 11 runs, max 525 s.
  - 98 passed. The 2 failures were test failures, not timeouts.
- **The added load.**
  - About +24% of the queue's runner time: roughly 1,400 job-seconds on 5,848.
  - About +5% of Tests' runner time overall. Over the same 36 hours Tests ran about 199 PR runs (about 6,850 job-seconds each), 83 push runs (about 7,700 each), 100 merge group runs and 5 scheduled runs. All but the merge group runs already include the dry run.
  - Runners are free on this public repo (`tests.yml`'s `schedule` comment), so the cost is runner waits, not money.
- **The critical path.**
  - With no contention, the dry run ends before the queue's `required-tests` starts in most runs. The median start is 451 s against the dry run's 282 to 434 s.
  - On the fastest queue runs (349 s) it can add about 85 s.
  - Under contention it adds its share of the waits. The worst run measured, plus a quarter more waiting, is still about 25 minutes against the 35-minute timeout.
- **Busiest window.** In the 2026-11-21 12:00 burst, 93 jobs ran at once at the peak. The queue's own runs were 56,565 of the window's roughly 97,700 job-seconds, and the PR runs' "Cutover dry run" workflow was 1,646.

## Decision

1. **The dry run runs on every event, the merge group included.**
   - `tests.yml`'s `cutover-dry-run-legs` and `cutover-dry-run-checks` lose their `if`. `required-tests`' "Require the cutover dry run" step loses its `if`.
   - That removes three event special cases. The dry run now follows the same rule as the platform legs it reruns.
   - It is affordable. The queue's runs grow by about a quarter in runner time and about 5% of Tests' runner time. The critical path is mostly unchanged, and the queue's slowest measured run keeps about 10 minutes of headroom under its timeout.
2. **The guard holds it on every event.**
   - `ci/guards/cutover.test.ts` `dryRunGateProblems` now refuses any `if` on the three jobs and on the gate step. `NOT_QUEUED` is gone.
   - New mutations: the checks job skipping the queue, and the gate step skipping the queue. The existing "legs on pull requests only" mutation still fails.
   - `main`'s `tests.yml` fails the new guard, with 2 problems.
3. **Prose corrected in place.**
   - `dry-run-required.md` item 2 and `b24-dry-run.md` item 2.
   - `tasks.md`'s "manifest's dry run green" line.
   - `tests.yml`'s two dry-run comments.
   - The manifest's `tests.yml` edit names the job names and the step name, which are unchanged, so `platform/cutover.json` needs no change.

## Weighed and refused

- **Strict status checks.** These would rerun every PR's whole Tests run (about 6,850 job-seconds) each time `main` moves. The queue's group run already is the run on the landing tree.
- **Run it in the queue only when `main` moved since the PR's run.**
  - This would save the 25 of 30 entries where `main` did not move. But the gate would have to read the PR run's base from the API.
  - A wrong condition there skips the dry run, and a skipped gate fails open. `tests.yml` already refuses that trade for `required-tests`.
  - With `ALLGREEN` groups, an entry's base also holds the entries ahead of it, so "did not move" would have to account for those too. That is a special case.
- **Scope the queue's dry run to the group's diff.** What breaks the tree is the combination of the PR's diff and `main`'s move, not the diff alone (`dry-run-required.md` item 3).
- **Fold each dry-run leg into its `platform-tests` leg** (run the leg, apply the manifest, run it again). There would be no new runner pickups, but every `platform-tests` leg would take about twice as long. That puts about 350 s on the critical path of every PR and queue run.

## Found in review, fixed

- **`tests.yml`'s trigger comment was stale.** It said `push` fires only on the rare human merge, that every merge was the default token's, and that `required-tests` gates only through `pull_request`.
  - Why `push` fires: since the queue (2026-09-26, #4061) GitHub merges as `github-merge-queue[bot]`, whose push starts runs. All 96 push runs on 2026-11-21 and 2026-11-22 are that actor's.
  - Why fewer runs than merges: entries the queue merges together move `main` once. 96 runs for 110 commits; four groups finished at 04:23-04:26 on 2026-11-21 and one push run started, for the last.
  - The comment now says the push run is the post-merge check, the `schedule` the hourly one and `heavy-tests`' only automatic run, and the gate is `pull_request` and `merge_group`. `tests/test_tests_workflow.py`'s copy of the old claim (its bug 2 and one message) is corrected with it.
- **The guard missed a step-level skip.** A step of a dry-run job with an `if` (a leg's command on `!= 'merge_group'`) let the job succeed without running. `dryRunGateProblems` now refuses an `if` on any step of the three jobs, with the mutation "a leg's command skips the queue".
