# Opening an owner backup

Every new generation opens through the same owner backup method. An export can request one, the nightly lane can start one, a schema transition can restart one and a restore pin can request its undo. Opening timestamps the attempt, resets the change-log capture and inserts the generation row. The quiet WriteGate retains writes preceding an exception. These three local effects need one synchronous transaction so a failed opening cannot leave the log capturing for a generation the owner never recorded.

The regression interrupts the generation-row insert after the change log starts capturing through the ordinary owner export, checks the timestamp and capture alongside the generation table, then retries the export. Already-open generation reuse and the unchanged-owner export fast path retain their existing behavior. The restore's fresh pointer belongs to the enclosing restore transition and has its own regression. Publishing parts to Files and handling previously interrupted records remain separate.

## Combined owner verification

Rehearsed the opener `eb845985` with restore begin `e0aaf71a`, apply `10b99ee4`, finish `2bda742b`, pin capture `780d5c1c`, pass seconds `538cd9a9`, and gate rollback `c407e4b7`. The opener protects the new generation and capture. Its caller's transaction also protects the pointer that identifies that generation. Keep both boundaries. The seven source changes combine automatically. Retaining both neighboring restore tests and both independent task entries yields local tree `9d4b46280699776c17ce802247ed46fcf71ac430`.

- [x] Compare the seven changes against their callers and earlier subset evidence. A pass started by a pin uses its own corrected budget. Joining an older scheduled pass and polling a recorded retry remain separate concerns.
- [x] Five restore regressions and one export regression passed through the ordinary Workerd entrypoint on the seven-branch tree above (61 and 28 cases outside the respective selections).
- [x] The nested write-gate rollback case also passed (five outside the selection). Seven selected Workerd tests passed across the three entries.
- [x] The first combined peak/fault test timed out at 86 seconds. The isolated interruption regression on staged apply commit `278199e4` failed on main with one partial row and passed on the seven combined branches (tree `1cbfcc25`). It verifies cursor, staged rows, bytes and peak immediately after the failure, then verifies the restored digest and counts on retry. The original broader peak fixture has its own status in the [apply branch evidence](https://github.com/TinyAssets/TinyAssets/blob/fc5247236fdcf2c85e8057a3da95e90ef0cace01/docs/decisions/untangle-2026-11/restore-page-commit.md).
- [ ] Historical partial writes, joining an older 45-second pass, direct polling during a recorded five-minute retry, and main and pre-cutover verification remain open.
