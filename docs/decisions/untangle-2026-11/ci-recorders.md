# ci-recorders as built (2026-11-21)

`process/ci-recorders`: the recorders become CI's, a leg of `tests.yml`, and builders and
reviewers stop running whole workspace suites and the recorders before a PR opens.

1. **Research: where a PR's time went, and how a recorder change is declared today.**
   - Measured on 2026-11-21: a PR spends 22 to 56 minutes from open to merge, and 20 to 120 in
     its build before it opens. The owner pass spent about 40 of its 62 minutes on the
     recorders. CI ran none: nothing caught a builder who skipped them.
   - One tree's recorders on the build host that morning (`npm run record`, under the run
     lock): wire 46 s, tools 10 s, rpc 229 s, rv 776 s (279 tests in one file), about
     18 minutes; base and head, about 35.
   - CI's own critical path then (runs 44880330953 and 44880553664): the old stack's Python
     shards, 12 to 14 minutes; the slowest platform leg, the owner's 2/2, 5 to 6 minutes.
   - The declaration was prose in the body, in one of two forms: "wire, tools, rpc and rv
     are the same", or "rpc and rv differ only as declared: the three new doors' lines ...".
     The reviewer either ran the recorders again or read the builder's word.
2. **The declaration is one body line, `Recorders-Differ: rpc, rv -- why`, naming each
   recorder meant to differ.** CI compares base and head and fails a recorder that differs
   unless the line names it. The line is the one source of which recorders change; the
   prose says how, and each shard's log shows the lines that differ, for the reviewer.
   - **Rejected: the recordings committed in the repo**, with CI comparing head against them.
     They are 9.5 MB (rv 8 MB; tools 7 lines of about 22 KB each), and every PR that adds a
     door rewrites them. rv's `PROTO` and `TABLE` lines list every door on one line, so any
     two such PRs conflict. A builder would have to make the new recordings, by running them
     locally, which is the cost this removes, or by a second CI round. A recording made on one
     machine and checked on another adds machine variance that base and head on one runner
     cancel.
   - The leg reads the body by the API when it runs, not from the event: a re-run of a job
     replays its event's payload (`executable-gates.md`, the scope guard). So a builder who
     adds the line re-runs the failed shards. A merge group's PR is the one its ref names
     (`review_checks.py` `queued_pr`, as `review.yml` reads it); the read is `--gh-read`, which
     asks again after a 5xx.
   - The grammar: at most one line, at a line's start (a quoted line does not count), naming
     only the table's recorders; anything else fails loudly. A named recorder that comes out
     the same passes: a shard sees only its part, and a declared change no recorder sees is
     legitimate (o1's body names one).
3. **A leg of `tests.yml`, required through `required-tests`' needs, not a workflow of its
   own.** A check nobody is required to pass can be merged past, which is the gap. A required
   workflow of its own would need a ruleset change and the founder's `PUT` (a live wait), and
   `required-tests` already holds every platform leg. The risk, a flaky recorder blocking
   merges, is `recorders.md` item 4's: three runs of main gave identical lines, and base and
   head on one runner cancel what the runner adds.
   - It runs on every event, with no job `if` (a skipped required leg passes,
     `legs.test.ts`). The script decides: a run whose working tree's diff from its base
     touches no `platform/*/src` records nothing and passes, the skill's rule since #5644; a
     push, schedule or dispatch has no change to compare.
4. **Sharded by test, base and head at once.**
   - One leg a tree would take about 18 minutes, three times the slowest platform leg: the
     build host's single thread is about a usual runner's (`budgets-variance.md`), and a
     recorder is one thread.
   - Vitest 5.0.5 shards by file only, and rv is one file. So `record.ts` lists each
     workspace's recorder tests (`vitest list --staticParse=false`: the static parse names a
     loop's tests by its template) and runs its shard's by `-t`, their exact names. A test's
     shard is its name's SHA-256 modulo the count, so base and head put a test in the same
     shard; an index would move every test after an added door. rv splits 72, 61, 76 and 70
     over four.
   - Each workspace's recorders run in one process, two startups a tree, not four. The two
     trees record at once on the runner's four CPUs.
   - The runner is the head's for both trees, so a change to how lines are kept applies to
     both sides; each tree's recorders and code are its own. A recorder file the base lacks
     records no line there (`--passWithNoTests`), so its lines differ, as they should.
   - Rejected: rv split into files by door ranges (an index in a file's name), a shard tag in
     each recorder (every recorder carrying CI's split), and main's recordings cached by tree
     (a merge group's cache is its ref's alone, and main moves every few minutes).
   - Measured on the build host under the run lock, its load about 11 on four cores: shard
     3/4 of base and head at once took 12 min 29 s of wall and 10 min of CPU, `npm ci` of the
     base 12 s of it. It kept 153 wire lines, 3,696 rpc and 18,901 rv of each tree (about a
     quarter of 804, 13,720 and 73,945), and all four recorders were the same on both sides.
     On an idle four-CPU runner the shard is about its CPU over the two trees, some 5 to 6
     minutes, the owner's 2/2 leg's time.
5. **`record` and `record:diff` go; `npm run record:check -- <base> <k>/<n> <body>` is the one
   entry**, CI's and anyone's. It installs a tree with none (`npm ci`), so a host's linked
   dependencies are left alone.
6. **The done-check and the skill's runs bullet** say what builders and reviewers run:
   typecheck, the touched tests, the guards the change can break and their mutations. Whole
   suites and the recorders are CI's, and a builder pushes a draft early. The done-check's
   "after a red CI job, rerun only its failing tests" went for the byte cap; the done-check
   now runs only touched tests anyway.
7. **Guarded** by `legs.test.ts` `platform-recorders`: the job's shape (every shard of one
   count, its check whole and last, read-only permissions, nothing conditional), the
   aggregate's gate step reading its result, and the script's verdict and shard split.
8. **Found:** at main `88e74e1cd`, the wire recorder's `wire: inbound hooks` test gets 404
   `not_found` for every event, `inbound ok` included (its URL is the door's own,
   `https://tinyassets.io/hooks/inbound/<token>`). So a change to `inbound.ts`'s capability
   refusals shows in no recorder; this PR's first mutation, there, came out the same.
   - [ ] The wire recorder sees the capability door's answers (`untangle/recorder-inbound`).
9. **Left:** the watchdog, 45 minutes, is about six times the estimate in item 4 until CI
   measures the shards.
   - [ ] The shards' time read from this PR's own run (`live:` its `platform-recorders` legs);
     past 7 minutes a shard, the count rises before the watchdog moves.
