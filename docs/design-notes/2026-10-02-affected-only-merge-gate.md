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

**Attribution, completed 2026-10-03** (`python scripts/miss_attr.py --limit 40`;
the script is now committed — the first version of this note cited one that was
never in the repo, so this table could not be reproduced). Re-measured on the
40 most recent failed merge-group runs at that date, which is a different window
from the 40 above:

| | |
|---|---|
| failed runs | 40 (**20** selected ALL) |
| new failures | 62 |
| inside the selection | 38 |
| outside the selection | 24 |

**Corrected 2026-10-03 after review: this table does NOT establish "no escape".**
`classify` originally treated several distinct entries failing one test as proof
that *every* occurrence was inherited. That is circular: if entry A introduces
an unselected failure and B..N queue behind it, every group fails it, and the
originating entry gets laundered into the evidence. Clustering only supports a
claim about the entries BEHIND the first one. `miss_attr.py` now always reports
the earliest entry per test as needing base/head evidence, and says so in its
output; the two rows below that rest on something other than clustering are
marked.

Two of the three rows survive the correction, on evidence that is not
clustering. The first does not, on its own.

| outside the selection | occurrences | verdict |
|---|---|---|
| `test_storage_registry_complete.py::test_every_on_disk_name_is_classified` | 12 | 11 inherited by the 5 entries behind the first; the EARLIEST entry still needs base/head evidence. It is an exact-count repo ratchet, which is the shape that fails from main's state rather than from a diff, but that is an argument, not the evidence. **Open.** |
| `test_branch_mutation_authority.py` (5 cases, pr-4297 only) | 10 | **Settled, not by clustering**: pr-4297's own Linux-oracle set-compare, posted on the PR 2026-10-02, reports **0 new failures** on its head vs the same base (base 218 failed, head 216) |
| `test_wiki_file_bug.py::test_collision_retries_and_advances_id` (pr-4265 only) | 2 | consistent with main's flake floor below, and not in `known-failing-tests.txt`. **Open** — a flake floor makes a lone failure weak evidence either way |

Two method notes, because both nearly produced a wrong answer:

* **A single entry is not evidence of causation.** Measured the same day, three
  consecutive main-push `required-tests` runs each failed exactly ONE test and
  all three were DIFFERENT (`test_dev_hygiene`, `test_open_receivers`,
  `test_mcp_probe`), on unchanged code. Main carries a roughly one-flake-per-run
  floor on the required surface, so a lone failure under a lone entry is more
  likely a flake than a regression — and `main-red`'s attempt-1 re-run is
  load-bearing rather than a nicety.
* **Replaying an old diff against today's tree misclassifies deletions.** The
  selector returns ALL when a changed non-test `.py` no longer exists, so a
  deletion PR measured against a later tree looks selective. pr-4297 is exactly
  that case (a deletion PR, later closed, whose files still exist on main) and
  it produced five phantom escapes on the first pass. `miss_attr.py --entry-tree`
  checks out each entry and is the authoritative mode; the default fast mode
  says so when anything is left unattributed.

## Design

### Amended 2026-10-03 after cross-family review: the gate is SHAPE-restricted

Round-2 review of the implementation found the selection unsound for a gate, by
reproducing two real omissions:

- changing `tinyassets/run_file_erasure.py` does not select
  `tests/test_account_deletion.py` — the import is inside a function, and the
  graph counts only import-time statements outside `tests/`;
- changing `tinyassets/providers/daily_quota_shapes.json` selects none of the
  six quota tests — a runtime data file is not a graph edge. A mutation to its
  daily-match regex makes an unselected test fail.

**Digests and coverage cannot see an OMISSION.** They prove the selection was
honestly executed, never that it was complete. And soundness cannot be bought
cheaply: `_imports` already records that following lazy imports made each test
reach ~470 of the 525 modules in `tinyassets/`, so a sound import graph
degenerates to ALL for essentially any code change.

So the gate no longer trusts selection for code. `affected_tests.provable_shape`
is a **whitelist of diff shapes that carry a completeness argument**, and
everything else is ALL:

- **`prose`** — every path is under `docs/`, `openspec/`, `ideas/`, or is a
  top-level `.md`. Nothing imports or executes it, so it can only affect a test
  that READS it, by name or by walking its directory.

That is the only entry. A **`tests`-only** shape (every path a
`tests/test_*.py`) shipped in round 2 and was **removed in round 3**: its
argument was that such a change can only affect the tests importing it, which
the graph captures in full for test files — but review then showed that
*deleting* a shared test module omits all four modules that import it, which is
the same class of hole that disqualified code changes. The argument leaned on
the import graph being complete, so it went.

The selection is additionally unioned with **every tree-walking test file**,
not just the walkers naming the changed root, which is what shrinks the
residual: a test that reaches a prose file through a path it builds without
naming the file or its directory. That residual is stated rather than hidden,
and it is why the list has one entry.

**Measured payoff** (120 squash merges on main, 2026-10-03): prose-only is
**26%** of merges, running ~120 of 1113 test files instead of the whole
surface. The removed test-only shape was a further 8%, and mixed prose+test 1%.
The general affected-only gate would have covered the other 66% too but could
not be trusted to.

### Round 3 (2026-10-03): absence of node ids proves nothing

One real escape, and it was in the pruner that round 2 added. Changing only
`tests/test_custom_ui_forms_browser.py`, with its Playwright `importorskip`
moved to module scope and a broken expectation introduced:

1. `select` installs only `.[dev]`, so collection skips the whole module and it
   yields no node ids;
2. the pruner read "no node ids" as "all slow" and dropped it from the
   selection — without proving anything about its markers;
3. the shards DO install Playwright, but the file had already left their
   selection;
4. the browser no-skip assertion filters against that pruned selection and
   accepts an empty intersection;
5. `slow-tests` installs only `.[dev]` too, so it skips the module as well.

A broken non-slow browser test lands. The other selected walkers keep the
selection non-empty, so the ALL fallback does not save it.

**Fix: keep is the default, and a file leaves the selection only on POSITIVE
proof.** It must appear with no marker filter AND be absent under
`-m "not slow"`. Collecting with the shards' full extras would also close this
specific case, but it is the weaker fix — it moves the line rather than
removing it, since any other module-level skip reopens the same hole. Keeping a
file is free and self-correcting: the shards have the extras, so they collect
and run it, and if nothing reports it the coverage check fails and names it.
Pinned by `test_a_module_scope_skip_never_removes_a_selected_file` and
`test_a_file_that_cannot_be_imported_is_kept`.

Round 3's other two probes were `DISAGREE_EVIDENCE`: deleting a shared test
module defeats selection but `slow-tests` collects the whole suite and catches
the broken imports, and cumulative mixed paths cannot evade the whitelist
because every reported path is checked. Noted caveat: `git diff --name-only`
reports only a rename's destination.

### The gate (`tests.yml`)

- A new `select` job runs on every non-PR event. **It installs dependencies
  first**: selection imports every conftest, `tests/conftest.py` needs pytest
  and langgraph, and on a bare runner that raises and falls back to ALL. The
  fallback is correct and is the only safe direction, but it made the first
  implementation of this gate completely inert while still paying for the job.
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
    quietly run a different or smaller selection. A manifest with no `selection`
    key reads as `<none>` and fails, so a shard predating the contract cannot
    satisfy it by omission.
  - `ALL` takes the command it always took — `--profile shard` on the shards,
    `--min-ran 10700` on the aggregate, no `--affected` anywhere. The
    whole-surface path is unchanged, which is why it carries no new risk.
    Handing the aggregate an `ALL` selection file is REFUSED outright, because
    `--affected` implies `--profile affected` whose floor is 0 and that would
    silently judge the full suite with the vacuity check switched off.
  - **A selective group is gated by COVERAGE, not by a floor** (amended
    2026-10-03, replacing "floor 0 plus a ≥5-files-ran-zero-tests collapse
    check"). Every selected file must appear in the union with at least one
    case — any outcome, skip included, so a platform-guarded file is not a
    failure — and a file that reported nothing at all is named. The floor could
    not be the check here: a selection can honestly be a single test, so no
    count is meaningful, and floor 0 on the GATING path contradicts
    `MIN_RAN_FLOORS['affected']`'s own comment that it is zero *because* that
    run is advisory. Coverage is exact instead of a magic number, independent of
    anything a caller picks, strictly stronger than the ≥5 rule (it catches the
    1-to-4-file collapse that rule passed), and it catches a per-file collection
    error that previously only reduced `ran`.
  - A failed `select` skips the shards, and the aggregate fails closed on the
    non-success shard result and a `missing` digest. The aggregate reads the
    selection ARTIFACT rather than trusting the job output, and refuses if the
    digest it was told does not match the file it can read — otherwise "every
    shard reported the expected digest" would be a statement about a file
    nobody in that job verified.

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
