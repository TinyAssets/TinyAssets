# Affected-only merge gate, full suite after merge (lean-CI lever L1, 2026-10-02)

Founder mandate (2026-10-02): the auto-merge queue should always be fast, with
a target of a stamp-to-merge median under ~15 minutes at ~76 merges/day and no
runner backlog. L1 is the biggest lever. The merge-group gate runs only what
the entry's diff can affect, the whole required suite moves to the run on main
after every merge, and a red main is acted on automatically.

Two PRs, landing in this order:

1. **`main-red`**: `scripts/main_red.py` plus `.github/workflows/main-red.yml`. It is
   the safety net, and it is useful on its own today.
2. **The gate change**: `select` job, affected shards, and selection-aware aggregate
   in `tests.yml` / `scripts/ci_required_tests.py`. It ships only once main-red
   is live and the required post-merge run is trustworthy (lead, 2026-10-02).

## Measured (2026-10-02, GitHub API; scripts quoted in the PRs)

- **Stamp to merge** (from the last APPROVE receipt comment to the merge):
  - 24 h to 03:50Z: median **50 min**, p90 115, n=76.
  - The 3 days before: median 17 min.
- **Runner load:** the newest 1,000 runs give an average demand of ~21.5
  concurrent jobs against the free plan's ~20. Concurrency was at or above 20
  for ~75% of the time, with a peak of 37.
  - Job-minutes by source: merge_group Tests 39%, PR Tests 30%, push Tests 8%,
    PR Docker smoke 5%, scope guard 4.5%.
- **Queue wait for a runner:**

  | event | median | p90 |
  |---|---|---|
  | merge_group | 9.7 min | 23.7 min |
  | pull_request | 5.1 min | — |
  | push | 3.8 min | 30 min |

- **A merge-group shard:** ~35 s of setup (checkout 3 s, setup-python 4 s,
  install 27 s) and a median of **268 s** of tests. The tests are the cost, not
  the setup, so caching (L2) can win at most ~12% per shard.
- **Main's required surface is trustworthy post-merge:**
  - Push runs since 09-30: 51 green, 5 red, 4 unfinished.
  - The hourly runs are red only on `heavy-tests`, a known baseline that
    test-hygiene is triaging. Their required shards pass.

### Miss rate: would an affected-only gate have let a real failure through?

`miss_rate.py` takes the 40 most recent FAILED merge-group runs (2026-10-01/02).
For each one:
- the run's new failures (its junit, minus the quarantine ledger);
- the selection `scripts/affected_tests.py` computes for that entry's own diff,
  from the base sha in the queue branch name to the group head.

| | |
|---|---|
| failed runs | 40 (14 selected ALL) |
| new failures | 70 |
| inside the selection | 37 |
| outside the selection | 33 |

The 33 outside the selection are NOT 33 escapes. They cluster on a few tests
that fail in group after group, whatever the entry changed:

| test | failing groups |
|---|---|
| `test_storage_registry_complete.py::test_every_on_disk_name_is_classified` | 10 |
| `test_command_center_copy.py::test_the_app_script_copy_says_command_center` | 5, including docs-only entries |
| `test_agent_workspace.py::test_every_explicit_provider_view_masks_the_workspace` | 5 |
| `test_onboarding_openai_device.py::test_no_named_universe_still_uses_the_callers_own_home` | 3 |

A one-file `docs/concerns/` entry cannot break a copy test. These failures came
in with the entry's BASE (main, or an entry ahead of it), and today they eject
every unrelated entry behind them. That is the measured "approved PRs go stale
against main and the queue rebuilds" cost. `miss_attr.py` checks each one by
running the missed test on the entry's base and on its head.

**Attribution (in progress at the time of writing; this table is updated before landing):**
so far, every missed failure checked also fails on the entry's base, so it was carried in rather than caused by the entry.

## Design

### The gate (`tests.yml`)

- A new `select` job runs on every non-PR event.
  - **On `merge_group`:** `git diff --name-only $merge_group.base_sha HEAD` is
    exactly this entry's diff. `affected_tests.py --changed-from` computes the
    selection: the static import graph, path mentions and tree walkers, or ALL
    whenever it is unsure (shared infrastructure, conftest closure, unreachable
    or deleted `.py`).
  - **On every other event** (push to main, schedule, dispatch): `ALL`,
    unconditionally.
  - It publishes `affected.txt` as an artifact and its digest as a job output.
- `required-tests-shard` (6, unchanged) `needs: select`:
  - Each shard downloads the selection.
  - A shard owning no selected file writes an empty junit plus a manifest
    (`--slice-count`) and skips install and pytest. It frees its runner in
    seconds.
  - Otherwise it runs its slice (`--affected --profile affected`), or the whole
    shard when the selection is `ALL` (`--profile shard`, floor 1000).
  - Every manifest records `selection` (digest) and `selected` (file count).
- `required-tests` (the protection context, `always()`) passes
  `--selection <digest>`:
  - Every shard must report that digest, or the gate fails. A shard cannot
    quietly run a different or smaller selection.
  - `ALL` keeps the whole-surface floor (`--min-ran 10700`).
  - An affected group has floor 0. A selection of ≥5 files that ran zero tests
    fails as a collapse.
  - A failed `select` skips the shards, and the aggregate fails closed on the
    non-success shard result and a `missing` digest.

### After merge (`main-red.yml`)

- **Trigger:** `workflow_run` of Tests on main, for push and schedule runs.
  Merge-group runs are excluded by the branch filter.
- **What counts:** only the `required-tests` aggregate job.
  - `heavy-tests` (red baseline) never counts.
  - Quarantined and `flaky` ledger lines never count; the aggregate already
    subtracts them.
- **Decision:**
  - Red on attempt 1: re-run the failed jobs. A flake costs one re-run.
  - Red on attempt 2 of a push run whose parent commit was green on the same
    check: open a revert PR (`auto-revert/<sha12>`) with a user token so CI
    runs on it, and raise the alarm.
  - Red otherwise (parent unknown or red, a scheduled run, a revert commit):
    raise the alarm only. Reverting without a known culprit reverts the wrong
    change.
  - Green on main's tip: close the alarm.
- **The alarm:** one open issue labelled `main-red`, with plain comments. No
  @-mention, because every PR here is the founder's account.
- **The revert PR is not armed.** It needs a Drain-Review receipt like every PR
  (#4255). Making a mechanically-exact revert receipt-exempt would be an
  authority change and is not proposed here.
- **Dry run** (2026-10-02, the last 12 push runs on main): 11 green read as
  `none`, and 1 red read as `rerun` (its successor was green).

## Expected effect

- The merge-group Tests job-minutes, 39% of all runner time, shrink to the
  selected slices.
  - Docs, CI and test-only entries select tens of files, not ~1,060.
  - Shards that own nothing finish in seconds.
  - The 14 of 40 sampled failing groups that selected ALL behave as today.
- An entry is no longer ejected for a failure its base carried. Main-red
  handles that once, post-merge, instead of every entry behind it paying.
- **Gate time:** select ~1.5 min, then a shard slice. Single-digit minutes for
  most entries, against ~20 min including the runner wait today.
- **Measured after landing:** stamp-to-merge median/p90, merge-group runs per
  merge, merge_group runner wait (`stamp_to_merge.py`, `mg_wait.py`,
  `runner_load.py`).

## Risks and what bounds them

- **A selection miss lands a break on main.** Bound: main-red re-runs, then
  reverts the culprit, or alarms. The window is one post-merge run (~10-20 min).
  Main is red in that window for later entries only inside their own selection.
- **The selector is heuristic.** It returns ALL whenever unsure, and the push
  and hourly runs still run everything.
- **`base_sha` is the entry's parent** (main, or the entry ahead), so each
  entry gates its own diff. Entries ahead are gated by their own runs, and a
  failed entry's dependants are rebuilt by the queue as today.
- **The protection context is unchanged** (`required-tests`, `always()`), so no
  ruleset change is needed.
