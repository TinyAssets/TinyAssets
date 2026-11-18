# ci-measured-reference as built (2026-11-18)

## Measurement

#5646 left a 34 ms estimate until three merge groups supplied calibrated Vitest
reports. The first three after it merged were 43938938427, 43939779247 and
43940676068 on November 16. Each had all 14 medium and nine large reports,
4,685 passed assertions per run. The reports' declared pass counts matched
the rows read. Running `node ci/budgets.mjs --measured ...` over those 69
reports gave a median calibration of 20.265482 ms, all 20 medium project
figures and 80,968 ms for the largest test. Each published figure identifies
its test and the reports used to compute it.

## Factor

The original survey in `budgets-variance.md` measured usual job variation up
to 2.24 times and occasional runners about five times slower. Scaling for the
runner handles the latter. A factor of three gives the former about 25%
headroom at the upper observation; it also tightens timeouts that the
5-second floor does not already determine.

For a separate check, take the 30 later merge-group Tests runs through
44080531921. Among the 27 successes in chronological order sample six evenly
spaced, including the first and last. Also read all three unsuccessful runs.
All nine uploaded the full set of 23 reports. Together they recorded 42,645
passing assertions and no failed Vitest assertions; the three failures were
elsewhere in those workflows. Replay each test's own duration, size class and
runner calibration through `speedOf` and `limit`. The longest relative to its
proposed budget was the doors binder guard in 43943207669, 2,466 ms of 6,000
(41.1%). None reached the repo's 80% headroom check. Hook durations are not
in the Vitest reports. Their existing floor and runner scaling still apply.

Held-out successful runs: 43945279766, 43972250912, 43989792160,
44022296733, 44062465248, 44080531921. The three unsuccessful runs:
43942110052, 43943207669, 44079286405. These artifacts and the measurement
runs above suffice to reproduce the comparison without a workflow dispatch.
