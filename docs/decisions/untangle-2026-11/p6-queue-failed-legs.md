# P6 queue handoff on incomplete jobs

The merge queue gives a removed head two counted attempts. An exemption requires a
complete explanation in tests outside the entry's selection. An empty jobs response
on f934871 could exempt a removal. One unreadable log could also erase an identified
sibling's test from the handoff.

Read the paginated first attempt and compare the distinct job IDs with GitHub's total.
A required job that skipped, timed out, was cancelled, or failed outside its test step
supplies its own job link. An unavailable log preserves the other jobs' evidence.
The merge group's expected skips are the PR-only affected matrix and the scheduled
heavy suite. A rerun with shared artifacts or a selection modified after removal
cannot grant an exemption. Job and artifact reads use the queue workflow's existing
permissions.

A failed aggregate can report its own error. Its shell checks propagate the named
upstream results. Its Python decision also checks every shard's manifest, coverage,
and selection. Only the complete runner notice with the shard failure as its sole
reason establishes that this decision repeated the upstream failure. Shards can
publish a bounded display of new failures or a second quarantine failure. Both
conditions keep a removal counted while retaining any named tests. A platform test
leg must also finish its Vitest report. Its failed file and case totals must account
for the named tests, with no independent unhandled or source error.

## Retro-check

- PR #5678, run 44079286405. The first 30 of 40 jobs were green. Shard 5 failed
  during image setup. The aggregate reported the missing manifest and nine of
  38 selected files without cases. The handoff retains both jobs with zero named
  tests. [Shard](https://github.com/TinyAssets/TinyAssets/actions/runs/44079286405/job/134025918203)
  and [aggregate](https://github.com/TinyAssets/TinyAssets/actions/runs/44079286405/job/134028557126).
- PR #5606, run 43654574508. Shard 2 named a single owner-router test. The
  aggregate reported only the upstream shard result. The real ALL selection counts
  the failure. An empty selection supplied to the same replay exempts it.
  [Shard](https://github.com/TinyAssets/TinyAssets/actions/runs/43654574508/job/132630379611)
  and [aggregate](https://github.com/TinyAssets/TinyAssets/actions/runs/43654574508/job/132635913588).
- Runs 43943207669 and 43942110052 each had 39 jobs. Both failed during typecheck.
  Each now identifies the exact typecheck job without reading a test log.
  [First](https://github.com/TinyAssets/TinyAssets/actions/runs/43943207669/job/133565879859)
  and [second](https://github.com/TinyAssets/TinyAssets/actions/runs/43942110052/job/133562061670).
- Main alarm #5424 also supplies a real Vitest report with one failed file in 71
  and one failed case in 1,468. Cutting the log after its first failure preserves
  the case name and prevents an exemption. [Owner job](https://github.com/TinyAssets/TinyAssets/actions/runs/41948241365/job/126995263405).
- The cancelled run 37268149014 supplies incomplete job records for a control.
  It is excluded by the existing failed-run filter. On main run 41948241365,
  attempts 1 and 2 returned different owner job IDs. A run ID alone cannot
  identify the jobs that a removal observed.

This changes the queue handoff reader. The retry cap, required checks, enrollment
and coordinator's merge decision keep their existing owners.
