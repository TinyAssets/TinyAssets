# shard-floor (2026-11-21)

`required-tests shard 1/6` failed on #5709's merge group (run 44881570063): "only 255 tests ran; the floor is 260 (`MIN_RAN_FLOORS["shard"]`)". No test was lost. Research first (Fact 14), read-only, from the Actions API and the two trees.

1. **Measured.** #5708's whole-surface merge group (44880330953, tree `88e74e1cd`) and #5709's (44881570063, tree `bbdd0941c`) ran the same suite: 3,940 and 3,941 cases passed, 25 skipped in each. Between the trees `tests/` gained one file, `tests/test_shape_prune_workflow.py`; the table (`.github/test-durations.json`) and `ROOT_TEST_FILES` did not change. `_packed(6)` on the two trees moves 102 of 291 files to another shard. Shard 1's user venue fell from 399 cases to 253 (its root venue is the volume proof's 2), shard 5's rose from 322 to 476, and the six shards ran 255 to 1,083 cases. The pack balances seconds, so a shard's case count is whatever its files' cases per second happen to be.
2. **The floor's two halves.** The per-shard floor (260) and the aggregate's union floor (`--min-ran 2200`) both count cases. Six per-shard floors sum to 1,560, under the union's 2,200, so the per-shard one adds nothing on volume; what it adds is "no one shard went nearly vacuous", in a unit the packing does not hold steady. `shard-2-time.md` (4) found the same disagreement from the other side.
3. **What the vacuous cases tripped, on the whole surface.**
   - *A shard's collection collapses to nothing:* pytest exits 5, which `evaluate` refuses. *To a few files* (a shard-specific ignore, a conftest that drops files, two shards packing differently so a file belongs to neither): only the per-shard floor, and only under 260.
   - *Mass skip:* the union floor and the skip budget (`MAX_REQUIRED_SKIPPED`, 134).
   - *Mass deselect or deletion:* the union floor, from a 44% loss.
   - *A reshuffle with no lost test:* passes the union; can fail a shard, as on #5709.
4. **Which files must report.** Of the 290 tracked `tests/**/test_*.py` outside the heavy list, 56 report no case on a green whole-surface run: they hold no test function (helpers kept by name after the shape prune). The other 234 are exactly the table's 234 keys, because the refresher writes a file only when a whole-surface junit reported it. On 44880330953's union every one of the 234 reported.
5. **Weighed.**
   - *Each shard against what its own selection collected (ran >= collected - skips).* Collection is what collapses: a dropped or deselected file lowers collected and ran together. It catches only mass skip, which the budget already does.
   - *A per-shard floor from its planned files' measured cases.* Adds a case count per file to the table and the refresher: new machinery for a suite the cutover deletes.
   - *Collect the surface in the aggregate to learn which files hold tests.* Exact, but installs the old stack and collects 290 files on the gate's critical path.
   - *Only the union floor.* Loses the one-shard collapse: a shard of 1,083 cases gone leaves 2,858, over 2,200.
   - *Built: the aggregate's existing coverage check, on the whole surface too.* `aggregate(must_cover=...)` already requires every selected file to report a case on a selective run. On the whole surface it now gets every tracked file the table measured (`whole_surface_cover`), whichever shard packed it, and the shard profile's floor is 0. One file plus its test, under the coordinator's scope cap for the old suite.
6. **The vacuous cases now.**
   - *Collapse (one shard drops most of its files):* the absent files are named; the union alone would pass it.
   - *Mass skip:* union floor and skip budget, unchanged.
   - *Mass deselect:* a whole file deselected is named; a deselect inside files is the union floor's, unchanged.
   - *Reshuffle, no test lost:* passes, and no shard floors its own count.
7. **Held.** `tests/test_tests_workflow.py`: `test_a_whole_surface_run_passes_however_its_files_pack` (the table's files packed as the shards pack them, and again after one added file reshuffles them; a shard holding a root proof's 2 cases passes its own verdict) and `test_a_vacuous_whole_surface_run_fails_and_names_why` (collapse, mass skip, mass deselect, each through the whole-surface aggregate's CLI, each asserting its reason).
8. **Found and left** (the cutover deletes all of it; no new work for the old suite).
   - *A file emptied of tests on purpose, or all marked slow,* fails the whole-surface gate by name until it leaves the table in the same change: the same "say it in the same PR" the floor asks of a shrinking suite.
   - *A file the table does not know yet* is not required; its cases count toward the union floor until the next refresh.
   - *The floor's copies:* `MIN_RAN_FLOOR` (2,200), the workflow's `--min-ran 2200`, and the local oracle's `--min-ran 10700` (`linux_oracle._run_ci`), which a local whole-surface aggregate now fails on (about 3,940 run). Deleting `--min-ran` would touch the workflow, the oracle and three workflow tests: past the cap.
   - *Stale comments in `tests.yml`* ("same 1000-test floor", "the 6,200 floor") name numbers no code holds.
   - *A heavy file in the table* would be required by the whole-surface aggregate, which passes no `--exclude-from`; none is today (heavy files never run in the required shards, so the refresher never measures them).
