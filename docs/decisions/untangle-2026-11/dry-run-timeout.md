# dry-run-timeout as built (2026-11-23)

`process/dry-run-timeout`: every job the merge queue's required checks wait on is killed by its own watchdog inside the queue's window, so a hung leg fails as itself, named by `required-tests`, instead of as a queue timeout. Found in #5793's review (`dry-run-queue.md`): `cutover-dry-run` had 45 minutes, past the queue's 35, and so did `platform-recorders`. Research came first (Fact 14). Everything was read from the REST API, read-only (actions runs and jobs, timings only), on 2026-11-23, at `d315aaa61`.

## Research

### The queue's window

- `docs/ops/merge-queue-ruleset.json`: `check_response_timeout_minutes` 35. The queue fails a group whose required checks have not reported within it. `scripts/ruleset_drift.py` holds the live ruleset to the file, and `dry-run-queue.md` read them equal on 2026-11-22.
- A job's check reports when the job ends. A job starts once every job it `needs` has ended. So `required-tests` reports, at the latest, by its own watchdog added to the longest chain of watchdogs it waits on, counted from each job's start.
- GitHub's docs host was not reachable from this session, so the definition above is the ruleset's field and the queue's behaviour as `dry-run-queue.md` measured it, not a quoted page.

### What main had

The chains ending in `required-tests` (its own watchdog was 15):

| Chain | Minutes |
|---|---|
| `cutover-dry-run-legs` 10 + `cutover-dry-run` 45 | 55 + 15 = 70 |
| `platform-recorders` 45 | 45 + 15 = 60 |
| `select` 10 + `required-tests-shard` 30 | 40 + 15 = 55 |
| `affected-tests` 30 (pull requests only) | 30 + 15 = 45 |
| `platform-tests` 29 | 29 + 15 = 44 |
| `platform-sizes` 28 | 28 + 15 = 43 |
| `platform-checks` 26 | 26 + 15 = 41 |

Every one is past 35, and the old stack's `select` to shard chain is, too, without the gate's own. The other required checks are one job each: `Diff scope declared` 5 (`pr-scope-guard.yml`) and `invariants` 10 (`invariants.yml`).

### Measured

300 Tests runs, 100 of each event: pull requests 44984142362 to 45117873134, merge groups 44890178699 to 45118762010, pushes 44872446981 to 45110101165 (2026-11-21 05:46 to 2026-11-23 05:35). Each run's latest attempt, every job not skipped; seconds of job, start to end.

| Job | n | p50 | p95 | max (run) |
|---|---|---|---|---|
| `cutover-dry-run`, every leg | 2,483 | 40 | 292 | 436 (PR 45092062586, owner 2/2) |
| `cutover-dry-run` in the queue | 234 | 42 | 287 | 373 |
| `cutover-dry-run-legs` | 191 | 17 | 24 | 29 (push 44926491163) |
| `cutover-dry-run-checks` | 190 | 97 | 105 | 109 (PR 45068029618) |
| `platform-recorders`, pull requests | 396 | 361 | 433 | 494 |
| `platform-recorders`, the queue | 392 | 336 | 422 | 513 (44924361778, 3/4) |
| `platform-recorders`, pushes (no change to compare) | 344 | 10 | 18 | 88 |
| `required-tests` | 300 | 50 | 67 | 92 (push 45000387004) |
| `select` | 200 | 49 | 77 | 125 (queue 44989843138) |
| `required-tests` shards, slowest shard | 200 | 635 | 853 | 917 (push 45071657634, 2/6) |
| `platform-tests`, every leg | 4,186 | 35 | 297 | 423 |
| `platform-sizes`, every leg | 2,691 | 54 | 305 | 652 |
| `platform-checks`, every leg | 897 | 42 | 245 | 363 |

- In the queue, `required-tests` reported 500 s into its run at the median, 972 s at p95 and 1,364 s at most (44921871950).
- A job's wait for a runner, in the queue: up to 525 s (`platform-tests`), 388 s (`platform-recorders`). The wait counts toward the queue's window and toward no watchdog.
- 76 of the 808 recorder shards on pull requests and in the queue took over 7 minutes.

## Decision

1. **The rule: each required check's job reports, by its chain of watchdogs, within the queue's check timeout.**
   - It is a recorded technical ceiling (Fact 15). A watchdog past it can never fire as itself in the queue: the queue fails the group first, unnamed.
   - The other legs' rule stands under it: about 6 x the slowest measured time. Where 6 x would be past the ceiling, the job is at the ceiling.
2. **`required-tests` 15 → 5:** 3 x its slowest, 92 s. That leaves 30 to the jobs it waits on.
3. **The dry run.** `cutover-dry-run-legs` 10 → 3, 6 x its slowest, 29 s. `cutover-dry-run` 45 → 27, the ceiling less the legs job's: 3.7 x the slowest leg's 436 s, where 6 x would be 44. `cutover-dry-run-checks` 20 → 11, 6 x its slowest, 109 s.
4. **`platform-recorders` 45 → 30,** the ceiling: 3.5 x the slowest shard's 513 s, where 6 x would be 51. The 45 was item 4's estimate times six, left in `ci-recorders.md` item 9 until CI measured the shards; this is that measurement.
5. **The old suite's chain, fixed (the old stack takes fixes):** `select` 10 → 4, about 2 x its slowest, 125 s. `required-tests-shard` 30 → 26, 1.7 x the slowest shard's 917 s. The shards' plan stays within half the watchdog (`test_every_shard_runs_its_user_venue_and_plans_within_half_its_watchdog`: 578 s of 780).
6. **Unchanged, within the ceiling:** `platform-tests` 29, `platform-sizes` 28, `platform-checks` 26, `platform-app` 15, `process` 8, `structural-guards` 5, `openspec` 5, and `affected-tests` 30 (pull requests only; 30 + 5 is the window exactly).
7. **The guard:** `platform/ci/guards/required-checks.test.ts`, which already reads the ruleset's required checks.
   - For every check GitHub Actions posts, it adds each chain of watchdogs through the reporting job's `needs` and refuses one past `check_response_timeout_minutes`. Each refusal names the chain.
   - A job with no `timeout-minutes` counts as GitHub's 360. An expression bounds nothing.
   - It reads no job names, so the dry run's and the old suite's jobs leave at the cutover without an edit to it.
   - Mutations: past the timeout, unset, an expression, the gate's own watchdog the whole window, and a needed job's needed job. On main's `tests.yml` it fails with the seven chains in the table above.

## Weighed and refused

- **Each job under the window on its own** (the literal "a leg's timeout can't exceed the queue's"). `cutover-dry-run` at 34 would pass that and still report at 34 + 3 + 5. The gate reports only after its whole chain.
- **A margin for runner waits in the rule.** The queue's measured worst wait is 525 s on one job. Taking it out of the ceiling would put `platform-tests`, `platform-sizes` and `platform-checks` under their 6 x figures, which cover a slow runner. That is the commoner failure. The wait is the vendor's, and the gate's own 5 minutes, against its 92 s, is the slack.
- **Raising `check_response_timeout_minutes`.** That is a ruleset change, which the founder applies. It would also let a hung group hold the queue longer.

## Left

- The `review` check, posted by the review App, is outside the rule: its chain runs across workflows by `workflow_run` (`review-queue.yml` 2, then `review.yml` `resolve` 5 and `decide` 5). It sums to 12 minutes, well inside 35, but the guard does not hold it, and `workflow_run`'s start delay is GitHub's.
- A runner's pickup wait (above) can still take a group past the window when a job hangs at its full watchdog late in the run.
- [ ] `platform-recorders`' shard count. `ci-recorders.md` item 9's rule is that past 7 minutes a shard, the count rises before the watchdog moves. 76 of 808 shards went past it, and the recorders are now the longest pull-request leg (p50 361 s against the owner 2/2 leg's 298, on pull requests). This change moves the watchdog down, to the ceiling, not up. The count's rise is its own change to `ci/record.ts` and the legs guard (`process/recorder-shards`).
