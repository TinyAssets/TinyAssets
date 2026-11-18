# p6-calibrated-measurements as built (2026-11-18)

The CI budget writer takes the slowest passed test in each project after scaling
it to the reference runner. On main at `f934871a`, a passing test with its
duration missing silently disappeared. A report covering every project and the
large class, with two positive times in the same project (9 and 9,001 ms),
wrote 9 ms when the longer test's duration was removed. A duration of `1e999`
in the JSON wrote a `null` budget.

The reader now requires a finite, nonnegative duration and a finite, positive
runner calibration on every passed assertion. It identifies the file and test
when it refuses a report, before the CLI writes the budget file. Vitest can
legitimately measure zero elapsed time. Failed and skipped tests still supply
no measurement.

The same complete fixture wrote 9,001 ms with Node 22.23.5 and 24.22.3. Each
CLI left the existing budget file byte-identical when the longest test lost
its duration, had negative or overflowing duration, or had zero or overflowing
calibration. A local Vitest JSON report supplied duration and calibration on
all six passing budget tests. The slow-runner regression from #5610 still
selects the usual runner's 4,643 ms. Project-wide decisions on controls for
other measurements remain in P6, d3 §B8.
