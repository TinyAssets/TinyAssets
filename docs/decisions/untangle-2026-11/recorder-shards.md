# recorder-shards as built (2026-11-23)

`process/recorder-shards`: the recorders' shards stop being the slowest leg of a pull request's run. Owed by `dry-run-timeout.md`: 76 of 808 shards had run past the 7 minutes at which `ci-recorders.md` item 9 raises the count. Research came first (Fact 14). Timings were read from the REST API, read-only, at `a06fc61db` (#5811): actions runs and jobs, and from 80 recorder jobs' logs only the timestamps of the check's own phase lines (`npm ci`'s summary, `<side> <workspace>: passed`).

## Research

### Per shard, and per run

240 Tests runs, each run's latest attempt: 120 pull requests and 120 merge groups, 44870941452 to 45128321821 (2026-11-21 05:27 to 2026-11-23 07:03). Seconds of job, start to end.

| | Pull requests | Queue |
|---|---|---|
| Runs that compared a change | 119 | 95 |
| Shards: p50, p95, max | 358, 433, 494 | 342, 424, 513 |
| Shards past 7 minutes | 54 of 476 | 23 of 380 |
| The run's slowest shard: p50, p95 | 408, 476 | 400, 463 |
| The run's slowest other leg: p50, p95 | 364, 436 | 355, 672 |
| Runs whose slowest leg is a recorders shard | 88 of 119 | 78 of 95 |
| A shard's wait for a runner: p50, p95, max | 3, 89, 329 | 3, 182, 388 |

The next slowest legs by p50, on pull requests: `affected-tests 3/4` 321, owner 2/2 303, `affected-tests 4/4` 279. In the queue the old suite's shards' p95 (up to 838) is the critical path until the cutover deletes them.

### Inside a shard

The 20 latest runs, 10 of each event, 74 shards that recorded. A shard sets up (checkout and Node, about 6 s), installs both trees at once (`npm ci`, 13 s), then records each workspace on both sides at once: the doors (42 s, p50), then the owner.

| Shard | Owner phase, p50 (max) | Owner tests in it today | Doors tests |
|---|---|---|---|
| 1/4 | 246 (332) | 125 | 11 |
| 2/4 | 251 (331) | 135 | 14 |
| 3/4 | 339 (389) | 156 | 8 |
| 4/4 | 290 (346) | 145 | 14 |

- The slowest shard is the one with the most tests, and the order of the four times is the order of their counts. A test's shard was its name's SHA-256 modulo the count, so the counts are as uneven as a random draw: 125 to 156 of 561.
- The rv and rpc lines a shard keeps do not follow its time (1/4 keeps the most rv lines and is the fastest), so lines are no proxy for weight.
- `vitest list` of one workspace's recorders takes about 12 s on the build host, and a run's startup about the same: about 24 s a workspace a shard, fixed.
- So a shard is about F = 70 s fixed (setup, install, two workspaces' listing and startup) plus its part of W = about 1,150 s of recording per side, the two sides at once.

### Runner cost

The repository is public: standard runners bill no minutes, so the cost is runner time against the account's concurrent jobs. A pull request's run is 54 jobs and 6,850 runner-seconds (p50); the recorders are 1,403 of them. Each shard added costs one more F, about 70 s. A shard waits 3 s for a runner at the median, so the concurrency is not saturated.

### The count, by the model

Mean shard = F + W/n. Slowest shard, evened by count, about 1.03 x the mean (the residual is weight within a block, and a block's tests are dealt evenly). The max, from the measured shards' max/p50 of 1.38 to 1.50, about 1.43 x the mean.

| n | Mean | Slowest of a run | Max | Runner-s a run |
|---|---|---|---|---|
| 4, by hash (today) | 358 | 408 (measured) | 513 (measured) | 1,403 (measured) |
| 4, dealt | 358 | about 370 | about 510 | 1,430 |
| 5, dealt | 300 | about 310 | about 430 | 1,500 |
| 6, dealt | 262 | about 270 | about 375 | 1,570 |
| 8, dealt | 214 | about 220 | about 305 | 1,710 |

## Decision

1. **The split: dealt by name, not by hash.** `record.ts` `dealt`: the names both sides list, sorted, every count-th from the index-th. Each shard holds as many tests as another, give or take one, and as many of each block (`rpc: every door`, `rv rpc: ... admission state` and the rest), since a block's names sort together. Both sides list first, and one list deals both, so a test is in the same shard of base and head by construction, which the hash gave by each side computing the same.
   - `ci-recorders.md` item 4 rejected an index because an added door would move every test after it between base and head. A deal over the union of both sides' names has no such move: base and head share one list.
   - The `1/1` path that skipped the listing goes: every count lists, so one path runs. It costs `1/1` (run by no CI leg) 12 s a workspace.
   - So does the skip of a shard dealt no test (review): vitest runs a `-t` that matches nothing as every test skipped, exit 0, so that shard runs the same path.
2. **The count: 6.** The rule (`ci-recorders.md` item 9): past 7 minutes a shard, the count rises before the watchdog moves. Six is the smallest count whose estimated max, about 375 s, is under 7 minutes; five's, about 430, is not. Its slowest shard, about 270 s, is under the run's slowest other leg (364 at the median, on pull requests), so the recorders leave a pull request's critical path. It costs two shards' setup, about 150 runner-seconds a run, 2 % of a pull request's runner time; eight would buy 50 s more of a leg that is no longer the slowest for twice that.
3. **The watchdog stays at 30, the ceiling.** 6 x the estimated max is 37 minutes, still past `dry-run-timeout.md`'s ceiling. #5811's chain guard (`required-checks.test.ts`) is unchanged and passes.
4. **The guard** (`legs.test.ts` `platform-recorders`) holds every shard of one count once, whatever the count; its mutations that named `1/4` now take the matrix's own first shard, so a count change needs no edit there. The split's test deals two sides' lists, one with tests the other lacks, and holds: every name in one shard, the same shards whatever order the lists come in, and each shard within one test of another, overall and in each block. Mutations: the hash split back (one shard 59 of 189 against 40) and the deal in list order, unsorted; each fails it.

## Weighed and refused

- **Balancing by measured weight.** The kit has no per-test weight for the recorders (`budgets.json` holds each project's slowest test, not a test's time). A committed weight per test name would be a copy of a measurement that every added door makes stale. The counts explain the bulk of the spread; dealing removes it.
- **The doors and the owner at once on a side** (four processes, plus their workerd, on the runner's four CPUs). It would save at most the doors' 42 s, less what the contention costs, which is unmeasured. The count is the measured lever.
- **Raising the count alone.** At six by hash, today's names give one shard 110 owner tests against a mean of 93.5; the uneven draw stays, and grows relative to a shard as the count rises.

## Left

- `ran()` removes `CLOUDSDK_AUTH_ACCESS_TOKEN` from each child's environment: a special case for one variable, outside this change's scope.
- This PR's own run records at six (its diff touches `platform/ci`): its shards are the first measurement of the new count.
