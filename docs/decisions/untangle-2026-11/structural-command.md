# structural-command as built (2026-11-18)

The builder page from #5644 told builders to pass `--head HEAD` to the old stack's structural guards. #5661 carried that line forward. At `f934871ad`, the program accepts a selection file, and otherwise runs all 41 targets against the checkout. It reports unknown arguments on stderr and ignores them. Giving it a revision never selected tests for that revision.

The local instruction now runs the module directly, as the Linux oracle does. Tests already passes `--selection` with the output of `affected_tests.py --gate`, using the PR merge parent or the merge group's base. The test-hygiene command separately takes a base and a head. No caller needs a new option. The page fits its existing byte budget.

For the retro-check, all 99 live branch tips and all 13 open PR heads were read on November 18. Fourteen older branch tips still carried the original working module command. The other 85, including main and the staged receipt retirement, carried the unsupported argument. Across the tracked callers and their changed versions, the builder page was the only place that supplied it. The founder’s pending receipt change and the ruleset are independent of this correction.
