# python-slow-runner as built (2026-11-18)

Keep the six Python shards, their 30-minute job limit and the queue's 35-minute check limit
through the cutover. The longest Python shard after the packing change took 15m06s.
The longest time from creation of a merge-group Tests run to its aggregate completing was
16m24s. Neither reached the project's 80% threshold for investigating a measured limit.

## Measurements

I read the Actions job lists for 51 completed Tests runs, beginning with #5656's merge group
on November 16 at 21:35 UTC and ending with the November 18 04:25 UTC scheduled run.
There were 25 merge groups, 21 pushes to main and five scheduled runs. Each had one
attempt and six shard jobs. Job duration is completion minus start. The aggregate column
measures from workflow creation, so it includes waiting and the aggregate's other dependencies.
It measures the Tests check, not the full time a PR spends in the merge queue.

| Event and scope | Runs | Longest shard | Latest aggregate |
| --- | ---: | ---: | ---: |
| Merge group, whole suite | 5 | 14m29s | 16m24s |
| Merge group, selected tests | 20 | 6m53s | 9m14s |
| Main push, whole suite | 21 | 15m06s | 16m56s |
| Scheduled, whole suite | 5 | 14m29s | 16m29s |

To reproduce, list `tests.yml` runs for the three events in that UTC interval, then read
`actions/runs/{id}/jobs?per_page=100` for each. Use the executed step to distinguish “Run this shard of the required test surface” from
“Run this shard of the selection”. Across
31 whole-suite runs, the median slowest shard was 14m07s. Of all 306 shards, 305 passed;
the one failure ended after 47s on a selected run. No shard timed out.
[First group](https://github.com/TinyAssets/TinyAssets/actions/runs/43961661660) ·
[Slowest shard](https://github.com/TinyAssets/TinyAssets/actions/runs/44023407874/job/133838230764) ·
[Longest group](https://github.com/TinyAssets/TinyAssets/actions/runs/44018231396) ·
[Failed shard](https://github.com/TinyAssets/TinyAssets/actions/runs/44079286405/job/134025918203) ·
[Last scheduled run](https://github.com/TinyAssets/TinyAssets/actions/runs/44237193457)

## Why the Vitest calibration stays with Vitest

In #5610, the contract job that recorded the fivefold Vitest slowdown ran on a different
host from every Python shard. Its six Python shards finished in 4m14s to 5m22s. The
earlier Python pressure was real: shard 2 took 28m22s on November 11, before #5656
separated the root- and user-venue packing and refreshed the table. That change removed
the observed bottleneck. Across four whole-suite runs of the very same main commit
`f934871ad`, the highest ratio between longest and shortest time for the same shard
was 1.31. These are job times including setup, not CPU benchmarks.
[Vitest incident](https://github.com/TinyAssets/TinyAssets/actions/runs/43668466020) ·
[Earlier Python shard](https://github.com/TinyAssets/TinyAssets/actions/runs/43046611022/job/130612533814) ·
[Repacking decision](shard-2-time.md)

A genuinely fivefold slowdown could still exhaust a Python job. More shards cannot divide
the single 477-second root test, and extending only the job limit cannot extend the queue
limit. The existing packing check catches a refreshed table whose planned tests exceed half
the watchdog. If an actual shard reaches 24 minutes or a merge group's Tests check reaches
28 minutes, compare the image build, the individual test times and the time waiting for
other jobs before changing a limit. A timeout or job failure remains visible to the aggregate.
