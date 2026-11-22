# matcher-ceiling as built (2026-11-22)

Dark deploy run 45057290524 (main `99d42771a`) stopped at its pair job: the template's standing check `matcher-probe` measured the sync-ignore automaton's step at 8.206 ns against `STEP_NS` 6 (`contract/src/sync-ignore.ts`). The matcher's code had not changed, and the rerun (45057867370) measured 4.963 and passed.

1. **The research** (read-only: the repo, the jobs API and the `"check":"matcher"` lines of the job logs; no dispatch, no E2B call).
   - **What `STEP_NS` was meant to protect.** `AUTOMATON_STATES` (8,192) is a technical ceiling on the matcher's time. `limits-audit.md` derived it by holding the worst sync-out's matching to the sandbox's shortest session, E2B Hobby's 3,600 s (`sandbox_runtime_ceiling_s`). The bound was 170.9 G automaton word steps at a step of `STEP_NS`, plus 310 s of index work. The standing check measured a sandbox's step and held it to `STEP_NS`.
   - **Where the 6 came from.** Slice STORE set `STEP_NS` three times (#5404's rounds).
     - 2.5, with 1.80 measured.
     - 3.5, after a build measured 2.50.
     - 6, after the slowest branch measured 4.40.

     Each value was about a third over the last measurement, not derived.
   - **The spread.** There are 15 distinct standing checks on the slowest branch.
     - `template/passed.json` holds 7 of them.
     - The job logs of the 32 `platform-deploy.yml` and `sandbox-template.yml` runs still listed hold 8 more.
     - 14 of the 15 read 3.674 to 5.120 ns, and one read 8.206.
     - `stepNanos` takes the median of five 600 ms runs, so the 8.2 was a slow host for seconds on end, not a moment.
     - The template asks for 2 vCPU (`image.ts`), and the matcher runs on one thread.
   - **E2B's documentation on CPU.** E2B's compute page could not be read from this session, because the host is outside its network. A search found only that the default is 2 vCPU, that the size is set per template, and that each sandbox is a Firecracker microVM. No source found documents dedicated cores or host classes.
   - **Neither input of the derivation holds today** (the review of #5796).
     - **The walk has no entry ceiling.** The 500,000 entries were the tiers' `max_entries`, which #5548 removed.
       - `syncOut` matches every entry `walkFiles` reaches under `/workspace`, files the index refuses (`file_index_full`) included. Only the removal pass is bounded, by the 512 MiB index.
       - So only the disk's 3,788,096 inodes bound the walk, and hard links not even those. At the inodes, the same arithmetic gives a step of 6.04 ns at most.
       - So no value of `STEP_NS` keeps the session promise for a runaway tree.
     - **A session does not end a sync-out** (checked in `runner.ts`).
       - `syncAgent` spawns the agent with no deadline (`Number.POSITIVE_INFINITY`); it stops only when the run's heartbeat lapses.
       - The run's heartbeats keep the lease meanwhile.
       - `quiet` counts a run whose CLI is not alive, so a syncing run, as quiet.
       - A renewal pauses the sandbox with its memory and resumes it, which carries the sync-out across the session's end. The sync client retries network errors.
       - So a sync-out past 3,600 s is a renewal during the sync, not a lost sync. The session is no deadline on matching.
   - **What else the gate could catch.**
     - The matcher's code speed is held on every PR by `contract/guards/sync-ignore.test.ts`'s cost bounds, in each host's own units (CPU time, a complexity claim). That suite runs on Node 22 in CI, and the template pins Node v22.22.0.
     - A deploy-time sample of one throwaway sandbox measures one E2B host. Stopping the deploy changes no production host: production sandboxes land on whatever hosts E2B gives them.
   - **What an owner can feel** is how long a sync-out takes. The run's report already says when matching took long, past `SLOW_MATCH_MS` (5 s, `ta.sync_ignore_slow`), and refuses nothing.
2. **The choice: no gate.** The standing check held a sampled host to a speed that protects nothing that binds. No deadline exists for it to keep, and even a binding one would not be kept by sampling one host at deploy. So `matcher-probe`, `stepNanos` and `STEP_NS` go.
   - **Weighed against a ceiling on the walk** (entries or name bytes, the rest unsynced and said, so that `STEP_NS` can be derived from it with #5796's first formula). It would refuse files to keep a promise the platform does not make, because the session does not end a sync-out. Fact 15 admits a technical ceiling only with the platform fact that forces it, and there is none here, so it would be an invented limit.
   - **Weighed against a different promise** (a sync-out's latency an owner can feel). That promise is the owner's own list against the owner's own tree. It is already said past `SLOW_MATCH_MS`, and no deploy-time sample of one host can keep it.
   - **Weighed against a ceiling from the spread, the least of the runs, CPU time, or a retry on a miss.** Each one still holds a host to a speed that guards nothing. A retry is also a shim that hides a slow host.
3. **As built.**
   - `contract/src/sync-ignore.ts`:
     - `STEP_NS` and `stepNanos` are deleted.
     - `AUTOMATON_STATES`' comment says what the ceiling does (a byte costs at most ⌈S/32⌉ word steps), and that its value's derivation is owed.
   - `sandbox/runner/probes.ts`:
     - The plan has two sandboxes, the sign-ins and the routes.
     - `matcher-probe`, its command and its check kind are gone.
     - The bundle no longer carries a constant to judge by.
   - `sandbox/template/checks.ts`:
     - A pair's record has no `matcher` entry.
     - The runner's install is measured in the first sandbox. Every sandbox installs the runner before anything else runs in it, where before the measurement came from the matcher's sandbox.
   - `sandbox/guards/template.test.ts`: `passing()` reads the sign-ins' and routes' verdicts. Records already in `passed.json` keep their `matcher` field as history.
   - `template-workflow.test.ts` (two sandboxes, no matcher failure case) and `runner.test.ts` (the matcher probe's test; the runner's refusal of probe commands) follow.
   - `limits-audit.md`'s `AUTOMATON_STATES` row says why its old derivation no longer holds, and that its value is owed.
   - **The bundles.** The runner bundle is byte for byte the same (`fdd886565dc407f5`), so no template rebuild. The probes change (`f83396a521dc9216` to `311cea63a2dea235`), so the pair tag changes. The next deploy's pair job runs the standing checks once on the pinned image, as for any probes change. `protocol.json`'s protocol 1 still lists `matcher-probe`, because it is a frozen record. Protocol 5, the live one, has none.
4. **Proof.**
   - The pin's guard holds a record with no matcher entry as passing.
   - Mutation: `passing()` requiring `matcher.ok` again fails "is pinned only once it passed its standing checks" (`template.test.ts`).
   - `template-workflow.test.ts` holds the plan to two sandboxes.
5. **Left.**
   - **`AUTOMATON_STATES`' value.** With no deadline, what derives 8,192 is open.
     - Candidates: the matcher's memory (about 36 bytes a state, beside `LIST_BYTES`' heap per list byte), or a sync-out's matching on a stated tree against `SLOW_MATCH_MS`.
     - Until then it stays 8,192: lines past it are said, never dropped in silence.
     - Owed as `untangle/automaton-states`, below.
   - **`sizing.ts`'s note on the agent's measured peak at 500,000 entries.** It belongs to `LIST_BYTES`' heap derivation, not to this lane. It goes to `untangle/automaton-states` with the other 500,000.

- [ ] untangle/automaton-states: research and derive `AUTOMATON_STATES`' value now that no session deadline or entry ceiling bounds a sync-out (memory, or the matching's latency against `SLOW_MATCH_MS`), and restate `sizing.ts`'s 500,000-entry peak with it.
- [ ] live: the next deploy's pair job passes with the new probes (pair tag `…-rfdd886565dc407f5-p311cea63a2dea235`): its log shows the sign-in and route checks and no `"check":"matcher"` line (the kit runs `checks.ts pair` without `--record`, so nothing lands in `passed.json`).
