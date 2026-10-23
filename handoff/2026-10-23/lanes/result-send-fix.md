Pushed to `conv/c11-durable-send`.

Head: `08917b8c0bbdfdbdf43105088a6eee3b3b867677`

Addressed findings 1–10: box startup recovery, per-thread nonblocking dispatch, retired ingress cleanup, retry handling, truthful history/status, stronger writer guards, and recovery across all agents.

Validation:
- Failing-first regressions reproduced all ten findings.
- Targeted Linux oracle: **60 passed, zero skipped**.
- Ruff, JavaScript syntax checks, diff checks, and commit hooks passed.

Working tree clean. Nothing left undone; no production access or full-suite run.