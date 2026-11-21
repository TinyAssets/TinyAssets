# P6b as built (2026-11-21)

`untangle/p6-tests-beside` is the second of P6's four PRs: the process tests beside their
scripts, held by the tamper rule. Its design is the P6 research's §4 P6b (2026-11-21). The
others are `untangle/p6-process-code` (merged, #5708), `untangle/p6-queue-deletes` and
`untangle/p6-queue-helpers`.

1. **The tamper rule holds every `tests/` folder, by pytest's own rule.** A Python test file is
   `test_*.py` or `*_test.py` under a `tests/` folder at any depth (`python_tests.py`
   `is_test_file`), and a Python file under one (a conftest, a helper) is read for its markers
   (`in_tests`). It read only the root's `tests/` before, so a case deleted from
   `scripts/tests/` passed with no `Test-Removal:` line. A bare `test_*.py` would take
   `scripts/test_hygiene_gate.py` and `scripts/test_shape.py` for tests; the folder keeps them
   out. The rule needs no edit at the cutover: `scripts/tests/` is the only `tests/` left.
2. **What a Python test file holds has one home, `scripts/python_tests.py`,** beside
   `platform_tests.py`: the test file rule, a test, a body's identity, a marker and an
   environment guard, moved whole from `test_inventory.py`. The hygiene gate and the weekly
   inventory both import it, so the report and the gate still agree, and the cutover deletes
   `test_inventory.py` whole. The inventory's report is byte-identical before and after
   (`--no-blame`, one hash seed).
3. **The hygiene gate reads its own blobs,** one `git cat-file --batch` a revision, not the
   shape resolver's batch. Every path it asks for is one the diff names at that revision, so
   a blob git cannot give raises (Fact 8), as `_show` does.
4. **The `verifies`-id check is `test_shape.py`'s own step** (`test_shape.py --base B --gate H`,
   `pr-scope-guard.yml` "Test shape"). It is the old suite's (it reads `tests/` and the OpenSpec
   ids), so the cutover deletes `test_shape.py`, its step and its tests whole, and the hygiene
   gate imports neither `test_shape` nor `test_inventory`. Its case in `tests/test_test_shape.py`
   calls the gate it now has, under a name without "hygiene".
5. **The process tests moved to `scripts/tests/` now, not at the cutover:**
   `test_review_check.py`, `test_bash_guard.py`, `test_queue_attempts.py`,
   `test_rulebook_ratchet.py` and `test_session_sync_gate.py`, with #5709's root lookup
   (the folder holding `AGENTS.md`) as it is. `test_skip_metadata.py` became
   `scripts/tests/test_test_hygiene_gate.py`, which also holds the new pins. The process leg
   runs `scripts/tests` alone (`legs.test.ts`), so nothing runs twice: it collected 233 on
   main (36 of them `tests/test_platform_setup.py`'s) and 202 here, every one of main's other
   197 among them, under its new path. `tests/test_platform_setup.py` stays in `tests/`, run
   by the old suite's shards: the manifest deletes it with `platform_setup.py`.
   `tests/test_test_shape.py` stays: it tests what the cutover deletes. On the dry run's tree
   (`ci/dry-run.ts apply`, no `tests/`), the leg passes 200, the two cases of the one test an
   edit names set aside.
   - The moved files lose their `pytest.mark.verifies` marks (and `test_review_writers.py`
     its one). Only `test_shape.py` reads them, over `tests/` alone, so outside it they claim
     a spec row nothing checks.
6. **The process leg's watchdog is 8 minutes,** about 6 x its slowest run, 1m14s in run
   44872746964 of 100 Tests runs from 2026-11-18 (the research's §1f). It was 5, a 4x margin,
   so a runner five times slower would have timed out the one Python leg that outlives the
   cutover.
7. **The manifest loses what the move made unnecessary:** the seven carry-out moves, the
   `tests.yml` edit's names of the paths that left, and the `test_inventory.py` and
   `test_shape.py` edits, which become deletes. `pr-scope-guard.yml`'s edit names its new
   step's script, `scripts/test_shape.py`, among its names and imports, so the dry run keeps
   the script the step runs. The hygiene gate's edit keeps its two names, the ledger import
   and `LEDGER`.
8. **`legs.test.ts` keeps an edit, for a reason the old one hid.** Its edit named the process
   targets this PR removed, so this branch first took the entry out, and the dry run's `ci`
   suite failed (run 44899198824): "a leg runs what no workspace's tests are: npm run test -w
   import". The guard reads `tests.yml`, whose edit takes out the import leg, and
   `platform/package.json`. The dry run writes no edit but does take the gone workspace out
   of `platform/package.json`, so on its tree the two disagree. The dry run sets aside a test
   an edit names, and the old entry had been doing that without saying so. The entry is back,
   naming what the cutover does take out of the file, its header's line on the old suite's
   jobs, and saying why the dry run sets it aside. Reproduced and checked with `ci/dry-run.ts
   apply` on a scratch checkout: before, 46 of `legs.test.ts`'s 77 failed; after, the `ci`
   suite passes (175 passed, 1 skipped).
