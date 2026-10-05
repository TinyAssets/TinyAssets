---
severity: P2
title: The required suite's summed test time rose 40-60% on the bare runner between 10-01 and 10-04
filed: '2026-10-04'
summary: bare-runner merge-group summed test seconds went from 1431-1509s (10-01) to 2128-2413s (10-04); the 2400s cap began failing unrelated PRs and was provisionally raised to 3000s in #4316; the drift is measured, its cause is not
---

# Required suite time drift

**Found:** 2026-10-04, Claude lead while landing #4316 and #4457.

**Measured** (summed per-test seconds across the six required shards: 10-04 rows
from each run's "summed test seconds" line, 10-01 rows from the runs' uploaded
JUnit artifacts, whose logs predate that line):

| Date | Run | Venue | Skips | Seconds |
|---|---|---|---|---|
| 10-01 | 36926964890 / 36924723282 / 36920148863 / 36912072109 | bare runner | 126 / 126 / 127 / 126 | 1,492 / 1,431 / 1,509 / 1,856 (outlier) |
| 10-04 | 37241639736 (main) | bare runner | 134 | 2,128 |
| 10-04 | 37243264963 (#4457: 41+/5- across four files; failed the cap) | bare runner | 134 | 2,413 |
| 10-04 | 37243158547 (includes #4316) | oracle container | 95 | 2,397 |
| 10-04 | 37243991116 (#4316 whole surface; every test passed) | oracle container | 95 | 2,596 |

The bare-runner rise (about 40-60% in three days) came before the container move.
For #4457, the same case IDs took 2,128s on the passing base, which points to
broad timing differences across unchanged modules. That is a hypothesis, not a
proven cause. The oracle runs execute 26,296 cases against 26,207 on the bare runner (50
more collected, 39 fewer skipped), so they are not directly comparable.

The cap was provisionally raised from 2,400s to 3,000s in #4316. That accepts the
drift; it does not explain it.

**To do:** compare junit `time` per test between a 10-01 and a 10-04 bare-runner
run on main. Check for added fixed costs (fixture setup or teardown waits, server
start/stop, browser launches) and for runner image or type changes. #4450 already
removed one fixed HTTP-fixture shutdown cost. Delete this file once the cause is
identified and addressed. Lowering the cap alone does not resolve it.
