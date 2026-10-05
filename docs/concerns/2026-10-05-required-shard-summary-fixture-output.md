---
severity: P3
title: Required shard summaries include test fixture output
filed: '2026-10-05'
summary: Gate-runner unit tests inherit the real shard summary destination and append synthetic failure messages to a successful hosted job summary.
---

# Required shard summaries include test fixture output

Found during independent CI venue acceptance review on 2026-10-05.

In selective merge-group run [37246536654](https://github.com/TinyAssets/TinyAssets/actions/runs/37246536654),
`junit-required-shard-1/summary-shard-1.md` contains fixture messages including
`SHARD SET INCOMPLETE` even though the actual required gate passed. The shard
owns `tests/test_ci_required_tests.py`; these tests inherit
`GITHUB_STEP_SUMMARY=/out/summary-shard-N.md` and write their synthetic results
into the same file the workflow appends to the job summary.

The JUnit and shard manifests remain the authoritative gate inputs. Independent
re-parsing confirmed all 172 selected files exactly once, six successful shard
exits and the matching selection digest. This finding affects the readability
of the hosted summary, not that verdict.

Resolve by isolating the summary destination used by gate-runner unit tests,
while retaining real shard and aggregate summaries. Verify the fixture cases
still exercise summary generation and no synthetic failure reaches the hosted
job summary; then delete this concern.
