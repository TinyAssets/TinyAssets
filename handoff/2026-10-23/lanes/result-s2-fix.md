Pushed `7b87f1d847eaceec869c952603bfd2ace55599a1` to the same branch and updated PR #5243.

- Used the authorized fallback: S2 drifted, original probe/ceiling 5 restored; S3 tracks duplicate executor.
- Strengthened bypass checks and clarified findings 4–5.
- Failing-first: 6 failed, 25 passed. After fixes: 31 passed via Linux oracle.
- Ruff, shape checker, lane check, and commit hooks passed.

Executor merge remains deferred because admission, yield journaling, and error handling require a broader change. The PR explicitly records that unfinished work.