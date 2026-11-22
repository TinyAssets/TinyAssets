# matcher-ceiling as built (2026-11-22)

Dark deploy run 45057290524 (main `99d42771a`) stopped at its pair job: the template's standing check `matcher-probe` measured the sync-ignore automaton's step at 8.206 ns against `STEP_NS` 6 (`contract/src/sync-ignore.ts`). The matcher's code had not changed, and the rerun (45057867370) measured 4.963 and passed.

1. **The research** (read-only: the repo, the jobs API and the `"check":"matcher"` lines of the job logs; no dispatch, no E2B call).
   - **What `STEP_NS` protects.** `AUTOMATON_STATES` (8,192) is a technical ceiling on the matcher's time. `limits-audit.md` holds the arithmetic. At the files plane's ceilings a sync-out takes at most 170.9 G automaton word steps, plus 310 s of index work at its measured costs. That total is compared with the sandbox's shortest session, E2B Hobby's 3,600 s (`sandbox_runtime_ceiling_s`). The standing check's job is to show that a sandbox's step keeps that promise.
   - **Where the 6 came from.** Slice STORE set `STEP_NS` three times (#5404's rounds).
     - 2.5, with 1.80 measured.
     - 3.5, after a build measured 2.50.
     - 6, after the slowest branch measured 4.40.

     Each value was about a third over the last measurement. None was derived from the session. So 6 is a headroom with no platform fact behind it, which `limits-audit.md` calls an invented limit ("The ceiling is the half, and only the half": an underived headroom is an invented limit).
   - **The spread.** There are 15 distinct standing checks on the slowest branch.
     - `template/passed.json` holds 7 of them, from 2026-11-03 to 2026-11-21.
     - The job logs of the 32 `platform-deploy.yml` and `sandbox-template.yml` runs still listed hold 9 more (2026-11-21 to 2026-11-22), one of which repeats the last entry in `passed.json`.
     - 14 of the 15 read 3.674 to 5.120 ns: 3.674, 4.08, 4.383, 4.393, 4.40, 4.464, 4.497, 4.525, 4.539, 4.817, 4.860, 4.963, 5.103, 5.120.
     - One read 8.206.
     - The 3.5 ceiling's entry (2.414) is the earlier fastest branch and is not counted.
   - **The slow reading was the host, not a moment.** `stepNanos` takes the median of five 600 ms runs, so at least three of the five read about 8.2 ns. That is a sandbox about 1.65 times slower for seconds on end. The template asks for 2 vCPU (`image.ts`), and the matcher runs on one thread.
   - **E2B's documentation on CPU.** E2B's compute page (e2b.dev/docs/sandbox/compute) could not be read from this session, because the host is outside its network. A search found only that the default is 2 vCPU, that the size is set per template, and that each sandbox is a Firecracker microVM. No source found documents dedicated cores, burst behaviour or host classes. So no host speed can be taken as guaranteed. The 8.2 shows that E2B hosts this slow exist and that a sync-out can land on one.
2. **The choice: the ceiling is the session, and the check judges that.** `STEP_NS` is the slowest step at which `AUTOMATON_STATES`' worst sync-out still fits in the shortest session, with every term slowed alike.
   - **The arithmetic.** The index terms are counted at the step's ratio to the fastest step ever measured on that branch, 3.674. This is as if the indexes had been measured on the fastest host, the most any slower sandbox could stretch them. A sandbox whose step is s ns then takes 170.9 G × s + 310 s × s / 3.674 = s × 255.3 s, and the session holds that while s ≤ 14.1. So `STEP_NS` is 14.
     - At 14 ns the worst case is 3,574 s, 99.3% of the session.
     - At the measured 3.67 to 5.12 ns it is 938 to 1,307 s.
     - At the slow host's 8.21 ns it is 2,095 s, so the deploy stopped although the session held the worst case at 58%.
   - **Weighed against a ceiling from the spread** (say 10, about twice the usual). That is the same invented headroom with a larger number. The next slower host stops every deploy again, and nothing derives where to stop.
   - **Weighed against a measurement robust to a neighbour** (the least of the runs, or CPU time). A sync-out runs at the host's sustained wall-clock pace, a slow host's included. Measuring the best moment, or CPU time without steal, would pass a sandbox that cannot keep the promise. The median already ignores a moment; it should not ignore a host.
   - **Weighed against a retry on a miss.** It would hide a slow host behind a second throw of the dice. It is a special case and is not built.
   - **`AUTOMATON_STATES` stays 8,192.** It is what owners' lists already apply (every GitHub template whole on the defaults), and at 8,192 the derived step comfortably covers every host measured. The two change together. A larger state count lowers `STEP_NS` by the same arithmetic, which the constants' comments now say.
3. **As built.**
   - `contract/src/sync-ignore.ts`:
     - `STEP_NS` is 14.
     - Its comment says it is derived from `AUTOMATON_STATES` and the session, and that the check fails a sandbox too slow for that promise.
     - `AUTOMATON_STATES`' comment no longer claims "well inside" the session. It says the matching fits on any sandbox within `STEP_NS`.
     - `stepNanos`' comment says why the measurement is the median of wall-clock runs, not the least run or CPU time.
   - `limits-audit.md`'s `AUTOMATON_STATES` row carries the arithmetic above, with the measured spread and the failed run.
   - **The bundles.** Only the probes bundle changes, by one line (`var STEP_NS = 14`), measured by building both bundles before and after.
     - The runner bundle is byte for byte the same (`fdd886565dc407f5`), so no template rebuild.
     - The probes go from `f83396a521dc9216` to `39b9a7ff53a79ca7`. So the pair tag changes, and the next deploy's pair job runs the standing checks once on the pinned image, as it does for any probes change.
4. **Proof.** The change is a constant's value and its derivation. The predicate that holds a sandbox to it (`matcherProbe`'s `step <= STEP_NS`) is unchanged and tested (`runner.test.ts` "the matcher probe", `template-workflow.test.ts`'s failing probe). No test repeats the audit's arithmetic, because a copy of the audit's numbers in a test would be a second home for them. So no mutation is declared.
5. **Left.**
   - **The files plane's ceilings in the arithmetic** (500,000 entries, the 512 MiB index) are named in the same audit, row by row. If either changes, the arithmetic and `STEP_NS` are re-derived there. The audit is the one home of both, so this is a decided keep.
   - **The index costs (200 ns a byte, about 9 and 1 ns a step) were measured once, on a host #5404 does not name.** The derivation scales them from the fastest sandbox seen, which is the conservative reading.
   - `template-workflow.test.ts`'s fake probe answers `ceiling_ns: 6`. That is a fake E2B's own output, never read against `STEP_NS`, so it stays.
   - `runner.test.ts`'s matcher test checks that the probe's verdict and exit code agree with its ceiling. That is the probe's wiring, not a copy of the arithmetic, so it stays.

- [ ] live: the next deploy's pair job passes the standing checks with the new probes (pair tag `…-rfdd886565dc407f5-p39b9a7ff53a79ca7`) and records the matcher at `ceiling_ns` 14 in `template/passed.json`.
