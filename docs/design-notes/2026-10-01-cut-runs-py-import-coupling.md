# Cut runs.py's import-time coupling (design, 2026-10-01)

**Status:** SHELVED 2026-10-01 (lead decision). R4 is rejected; R1 needs a real refactor. See "Why R1 is shelved" at the end.
**Signal.** #4201's selector found that `tinyassets/runs.py` is reached at
import time by **436 test files** (160 import it directly). Any change to it
selects about 40% of the suite, so the founder's "enforcing bad architecture"
shows up here as a number.

## What the coupling is (measured 2026-10-01 on main, static import-time graph)

- **runs.py is two modules in one file** (7,960 lines, 168 top-level
  definitions):
  - a run **store**: record shape, persistence CRUD, judgments and lineage,
    cooperative cancel and the workspace outbox, roughly lines 1-3650;
  - an **executor**: the synchronous runner, the async pool and sub-branch
    invocation.
  - The executor needs `graph_compiler`, and through it `langgraph.graph`,
    `langchain_core` and `langsmith`. Importing runs therefore costs about
    0.35 s of langgraph, even for a caller that only wants `get_run`.
- **About half the direct importers want only the store.** Of the 163 direct
  importers whose names can be classified, **77 use only store names**. The
  most imported are `initialize_runs_db` (21 test files), `create_run`,
  `update_run_status` and `get_run`. Fifteen of those importers are production
  modules: all of `run_file_*`, `storage/deliveries`, `storage/run_file_lock`,
  `api/run_activity`, and others.
- **The biggest hub is a central dispatch table, not runs.py.** The chain is
  `universe_server -> api/extensions -> api/runs -> runs -> graph_compiler`.
  `api/extensions` imports every action family's table plus its dispatcher
  eagerly, just to build one name-to-handler map. **212 test files import
  `universe_server`**, and each one inherits the whole engine. This is the same
  shape as `storage_accounting` `ROOT_ENTRIES` and the route lists: a central
  registry that every slice must edit, which every reader imports whole. That
  is why approved PRs conflict within minutes.
- **Import cost is not the win here.** The suite runs in one process per
  shard, so it pays an import once (about 1 s). The cost that bites is
  per-process: server start, engine children, and the 60 test files that spawn
  `sys.executable`. The largest single cost on that path was not runs.py but
  spaCy (2.8 s, about 200 MB), fixed separately in #4214.

## Design

1. **Split the store out.** Move the store section into `tinyassets/run_store.py`.
   It imports `branches` and `principals` and nothing from `graph_compiler`.
   - runs.py imports from run_store, and keeps re-exports **for one release
     only**: re-exports keep the coupling for anyone who still imports runs.
   - Then repoint the 77 store-only importers and delete the re-exports.
2. **Make the dispatch table lazy.**
   - `api/extensions` keeps only **action names**: small frozensets in a light
     `api/action_names.py`.
   - It imports each family's dispatcher inside the dispatch branch on first
     use, as `tinyassets/__init__.py`'s `__getattr__` already does.
   - Importing the server then builds the name map without importing the
     engine.
3. **Per-module registries, not central ones.** Each action family (and each
   `ROOT_ENTRIES`-style storage root) declares its own entries in its own
   module. The aggregator discovers them by a registered module list, or by
   entry points, so adding a family edits only that family's file. The same
   rule applies to app.html modules (see the app.html design).

**Rejected: lazy imports alone.** Moving `import runs` inside functions, with no
split, only hides the dependency from the static graph. The test still
exercises runs at runtime, and the selector would then under-select. The split
and the name-only table remove the real dependency; laziness is used only
where the dependency really is conditional (dispatch by action name).

**R4's honest limit.**
- A test that calls a *run* action through the server still runs `api/runs`,
  but after R4 the static selector no longer sees that dependency. Those tests
  move from "selected by import" to "caught by the queue".
- Only tests calling *other* actions are truly decoupled.
- Mitigation: keep the run action names in `api/action_names.py` as the one
  definition, which `api/runs` asserts its table against. A selector rule
  then maps that file to `api/runs`, so a test importing the names is selected
  for a runs change.
- Measured alone, R4 takes runs.py fan-in from 436 to 235 and graph_compiler
  from 460 to 266.

## Expected effect (simulated against the real graph, 2026-10-01)

| | runs.py fan-in | graph_compiler fan-in |
|---|---|---|
| today | 436 test files | 460 |
| after step 1 (store split) | 386 | 413 |
| after steps 1+2 (plus lazy dispatch) | **164** | **200** |

- A change to runs.py or the compiler selects about 60% fewer test files.
- The 60-commit median selection does not move (125): most of those commits
  never touched this subtree, and their selections come from other hubs and
  ALL fallbacks.
- Every `universe_server` importer (server start, engine children, the 212
  test files) stops loading langgraph at import.

## Slices (each behaviour-identical, one PR each)

- **R1.** `run_store.py` gets the store section moved verbatim. runs.py
  re-exports it, and tests are unchanged (proof that nothing moved
  semantically).
- **R2.** Repoint the 15 production store-only importers.
- **R3.** Repoint the store-only test importers, in a mechanical sed-style PR.
- **R4.** Add `api/action_names.py` and make the dispatch in `api/extensions`
  lazy. Guard test: importing `tinyassets.universe_server` loads neither
  `tinyassets.runs` nor `langgraph`, in the same shape as #4214's spaCy guard.
- **R5.** Delete the runs.py re-exports, and re-measure fan-in with
  `scripts/affected_tests.py`.
- **R6** (separate design if wanted). Per-module registry for storage
  `ROOT_ENTRIES` and the route lists.

## Why R1 is shelved (measured 2026-10-01)

**R4 is rejected.** A lazy dispatch hides a test's real runtime dependence on
`api/runs` from the selector, so failures move to the queue. That is the
problem this lane exists to fix, and a faster selector that misses tests is a
regression.

**R1 is not a move.** The store section of `runs.py` reaches the executor
through exactly one call: `nominate_workspace_waiter -> _dispatch_waiting_run`.
The paths are:

- `update_run_status`, `terminalize_unstarted_run` and
  `attach_existing_child_run` go through
  `_finish_terminal_workspace_release -> _kick_workspace_sweep ->
  _workspace_sweep_once -> nominate_workspace_waiter -> _dispatch_waiting_run`;
- `list_runs` goes through `_reconcile_workspace_on_read ->
  _start_workspace_sweeper`;
- `request_cancel` goes through `_settle_cancelled_waiter`.

**The store's transitive closure is 158 top-level definitions, about 4,300
lines,** and it includes `_invoke_graph` and so `graph_compiler`.

**Splitting it means inverting that edge,** which is a design call:

- explicit dispatcher registration, failing loudly when none is registered;
- or a lazy import, which hides the dependency and is rejected for the same
  reason as R4.

It also means repointing 15 production modules and about 60 tests, all in
concurrency-critical workspace waiting.

**The payoff is unproven for selection:** the 60-commit median selection does
not move (125).

**Revisit** when the app.html split or a `runs.py` feature change makes the
inversion cheap. Every test that drives waiter dispatch already imports
`tinyassets.runs`, so explicit registration (option A) would not cost selection
accuracy.

**Found on the way:** `_TERMINAL_STATUSES` is defined twice in `runs.py`
(around lines 3135 and 7074), with identical values today. Fold it into one
definition with the next `runs.py` change.
