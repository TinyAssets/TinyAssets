# budgets-variance as built (2026-11-15)

The runner-variance rule that #5640's review left (`budgets-ci-3.md` item 4): a CI runner four to five times slower than usual failed tests at their budgets, `search-cpu.test.ts`'s CPU bound, and a job at its watchdog, when nothing had regressed.

1. **The measurement** (read-only: the jobs API and job logs, no dispatch and no re-run).
   - **Runs read.** The 200 merge-group runs of `tests.yml` from 2026-11-06 00:04 to 2026-11-15 21:09, and the 100 pull-request runs from 2026-11-14 09:40 on, every attempt. For each platform leg whose test step usually takes 10 s or more, its test step's time was divided by the median of the same leg's 14 nearest runs in time, so a leg's growth across merges doesn't count as variance: 2,699 legs in the merge groups, 1,624 in the pull requests.
   - **Usual runners stay within about 2.2 times.** In the merge groups the ratio's median is 1.00, p95 1.28, p99 1.47, p99.5 1.60, and the highest but one is 2.13 (an import leg). In the pull requests: p99 1.44, the highest but one 2.24.
   - **Slow runners are about 5 times, with nothing in between.** There are two:
     - #5610's contract leg (merge group 43668466020) at 4.96 times its usual time;
     - #5635's owner leg (pull request 43733505410, attempt 1), cancelled at its watchdog at 3.24 times, part-way.
   - **How often.** That is 2 legs of 4,323, about 1 run in 150. #5640's builder estimated 2 in 100.
   - **The slowness is there from the job's first step.** Both slow legs' `npm ci` took 30 and 31 s, 4.3 and 4.4 times its usual 7 s, and their checkouts 13 s against 4 to 5. Across all legs, a leg's `npm ci` ratio and its test ratio correlate at 0.66 (merge groups) and 0.60 (pull requests), in logs. So a measurement taken before the tests sees a slow runner.
   - **The slowness is CPU.** `search-cpu.test.ts` measures its thread's own CPU, not the wall. Its distinct-words case read 5,354 ms on #5635's slow runner. On the usual runners of 12 recent merge groups it read 913 to 1,797 ms, in two clusters (about 950, and 1,500 to 1,800): ubuntu-latest runs on more than one CPU type. On the build host it reads 767 to 1,233 ms (median 940, ten runs). So a single-thread V8 workload scales with the runner, and the build host's single thread is about a usual runner's, though its whole test runs are about 2.7 times faster.
2. **How the limits were applied.**
   - **Budgets.** `ci/classes.ts` `derived()` gave each project's `testTimeout` and `hookTimeout` at config load: the factor of 4 times the slowest test measured (`budgets.json`). `class-check.ts` holds each test to it, and the large tag has its own. Nothing knew the runner.
   - **Host CPU and time bounds.** `search-cpu.test.ts` held two cases to a literal `3_000`, a copy of a tenth of `CPU_LIMIT_MS`. `move-cpu.test.ts` and `backup-cut.test.ts` hold steps to `STEP_CPU_MS`. The memory leg (`owner/memory/leg.mjs`) holds the worst edit's time to `CPU_LIMIT_MS` and two queues' turnover to `SLOT_WAIT_MS`. All are held on whatever runner the leg got.
   - **Measuring.** `budgets.mjs` took each project's slowest test across the reports read. A slow runner's report raised a figure for good: #5615 set `contract/guards` from the slow run's 16,442 ms (66 s) with a second call on that one report, where the usual runners give 4,643 ms (19 s).
   - **Watchdogs.** Each job's `timeout-minutes` was about 3 times its slowest leg.
3. **The choice: each leg measures its runner first and scales every bound by it.** One predicate: a bound measured on the budgets' runners, times this runner's speed, never under 1.
   - **Weighed against the same leg's other tests in the run.** A timeout must be set before its test runs, and a leg's first tests have no peers yet. A check after the fact would leave the run's own timeouts unbounded meanwhile, a second predicate. And a slowdown shared by the whole leg (a slower kit, say) would scale itself away and never be caught.
   - **Weighed against a higher factor.** To pass a 5-times runner with today's margin, every run's factor would have to be about 20. Every run would then catch a regression only past 20 times its slowest test, to cover 1 run in 150.
   - **Weighed against a retry on a miss.** It is a special case. A test's retry runs on the same slow runner, and it doubles a failing leg's time.
4. **As built.**
   - **The calibration** (`classes.ts` `calibrate`): a fixed V8 workload of the tests' kind (strings, a map, a sort, JSON), its least of five rounds after one warm-up. It takes about 19 ms on the build host. It runs once as `classes.ts` loads, in each Vitest process's config load, before any test, so under the same conditions everywhere. `RUNNER` is `{ calibration_ms, speed }`, and `speedOf` gives the speed as `max(1, calibration / reference)`. A faster host keeps the measured budgets, so a test that passes on a usual CI runner passes on any faster host.
   - **Budgets.** `limit(slowest_ms, factor, speed)` is the one formula: `factor` times the slowest times the speed, up to the second, at least 5 s. Each project's medium budget, the large tag's, and the hook floor (10 s times the speed) come from it. `limit`'s explicit factor and speed replace `derived()`'s `factor` parameter, which only the guard passed.
   - **The pool is given the runner.** `classTimeouts` carries `RUNNER`, and `class-check.ts` writes each test's `calibration_ms` into its meta, which Vitest's JSON report keeps.
   - **Measuring follows the same predicate.** `budgets.mjs` sets the budgets' runner (`budgets.json` `runner`) to the median calibration of the tests read. It counts each test as that runner would measure it: its time divided by its runner's speed. A slow runner's report no longer raises a figure. A report without a calibration fails loudly, and so does a set of reports with no passed test.
   - **`contract/guards`.** Its slow-runner figure, a special case, gives way to the usual runners' 4,643 ms (the three runs' `tests-report-8`, `node ci/budgets.mjs`), so 19 s, and 95 s on a 5-times runner.
   - **Host bounds.**
     - `owner/guards/cpu.ts` `onThisRunner(ms)` is `ms` times the provided runner's speed. It is called in the test pool only, since the memory leg imports the same module outside it.
     - `search-cpu.test.ts`'s bound is `onThisRunner(CPU_LIMIT_MS / 10)`, which also replaces the `3_000` copy. `move-cpu.test.ts` and `backup-cut.test.ts` use `onThisRunner(STEP_CPU_MS)`.
     - The memory leg is its own process, so it measures `RUNNER` itself. Its three time bounds are each times `RUNNER.speed`, and its row prints the speed.
   - **The reference** is 34 ms, estimated because no CI run carries the calibration yet. The calibration's 18.7 ms on the build host (the median of six loads) is scaled by search-cpu's CPU on CI over the host's: 1,713 ms (merge group 43749136142, the slowest of the three runs the budgets come from) over 940. So a usual runner of either CPU type reads a speed of about 1, and #5635's runner about 3.1. The estimate is safe both ways:
     - If it is low, usual runners scale their budgets up a little.
     - If it is high, a 5-times runner still gets about 12 times its slowest test, against 5 times taken.
5. **Watchdogs: re-derived to cover the slow runner, not scaled.** A job's `timeout-minutes` is fixed before its first step, so no step's measurement can reach it. A step's `timeout-minutes` can read a step output, but GitHub's expressions have no arithmetic. So a calibration step would have to be handed the job's figure again, a copy, and there would be a second measurement beside each Vitest process's own. A watchdog bounds a hang. The scaled budgets bound slowness. So the rule becomes about 5 times the slowest leg's measured time: on a slow runner the whole leg takes about 5 times as long, as the budgets now allow. The figures come from the four merge-group runs since #5640's shards (43759409585, 43759511841, 43761103673 and 43762132768), which also settles #5640's `live:` watchdog re-derivation:
   - `platform-tests`: the owner's shard 2/2 took 290 s, so 25 minutes (18 before).
   - `platform-sizes`: the owner's shard 3/3 took 280 s, so 24 minutes (15 before).
   - `platform-checks`: memory took 251 s, so 21 minutes (12 before).

   In the 300 runs read, no job was cancelled at its watchdog except #5635's slow runner. So the longer bound costs a hang's report minutes, not a false red.
6. **Proof.** Each test fails under its own mutation:
   - **A slow runner passes a test within its scaled budget.** `timeouts.test.ts`: #5635's 10,370 ms admission-caller guard passes at a 4-times speed, and would fail at 1. Mutation: `limit` drops the speed.
   - **A usual or faster runner keeps the measured budget, so a test twice past it fails.** `timeouts.test.ts`. Mutation: `speedOf` loses its floor of 1.
   - **A slow runner's report sets no figure higher.** `budgets.test.ts`: #5610's 16,442 ms on a runner five times slower counts as 3,288 ms, and the 4,643 ms stands. Mutation: `derive` counts each test's own time.
   - **Each test's report carries its runner's calibration.** `timeouts.test.ts`. Mutation: `class-check.ts` writes no meta.

   The host bounds multiply by the same speed. On a usual runner it is 1 by construction, so no test can tell `onThisRunner` from `ms` there without a test-only hook.
7. **Left.**
   - **The reference and the factor.** The reference is an estimate until reports carry the calibration. `node ci/budgets.mjs` over the next three merge-group runs' reports replaces it with the measured median (`live:` three merge-group runs after this merges). With the runner measured, the factor of 4 only has to cover a usual runner's own variance (to about 2.2 times measured), so it can fall and catch more. That too waits for the same calibrated reports.
   - **`owner/test/wakes.test.ts:602`.** Its `took < 1000` is a liveness bound around a 30 ms send timeout, 33 times over, not a budget, and stays.
