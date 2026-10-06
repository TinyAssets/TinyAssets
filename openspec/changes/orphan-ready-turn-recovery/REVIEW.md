Checking the lock primitive — whether a same-process liveness probe can misread a live runner as dead.

Two items so far: exception masking in the coordinator's finally, and failure-publication robustness. Checking the dispatcher tick and activity record shape.

Main finding confirmed: the new `finally` lost the old exception guard around `close_quiescent`. Last step is skimming the new tests to see what they actually prove.

I reviewed PR #4524 at `bd8ef42e36` against `origin/main`: the canonical `tinyassets/` files, the new tests, and the specs. I ran no tests, as agreed. One change needs fixing: the coordinator's exit path can now hide the real error. The rest of the repair holds up.

**Finding: an error can now replace the real failure (`tinyassets/agent_turn_coordinator.py:478-487`)**

The old code wrapped `close_quiescent()` in `try/except Exception` and logged a failure. The new `finally` block calls it with no guard:

```python
finally:
    try:
        if self.turn is not None and self.turn.state == "ready":
            self.close_quiescent()
    finally:
        self._release_turn()
        ...
```

`close_quiescent()` goes through `_accept(journal.abandon(...))`. That raises `JournalUnavailable` on a conflict, and the journal can also raise `LeaseLost`, `RestoreInProgress` or a busy-database error. Any of these escapes the `finally` and replaces the exception the turn was actually failing with.

- **Scenario:** the turn fails before round one with `AllProvidersExhaustedError` (every provider skipped), and the abandon write then hits a lease or database error.
- **What breaks:** `_call_writer` (`universe_intelligence.py:1306`) no longer sees `AllProvidersExhaustedError`, so the one safe retry is skipped.
- **What the user sees:** a generic journal or platform error instead of the real provider-capacity class. The `turn_effects` / `turn_stage` evidence attached to the original exception is lost too.
- **Same risk for Stop:** `TurnInterrupted` and the activity refusal can be masked the same way.
- **Why it's this diff:** the file's own rule, "bookkeeping never replaces the outcome" (`_release_turn`, `:384`), already applies right next to it, so this reverses an invariant the old code protected.

**Fix:** put the guard back inside the new structure. The row is still recoverable afterwards, because the claim is released, the token reads as dead, and Stop or boot recovery settles it.

```python
try:
    if self.turn is not None and self.turn.state == "ready":
        try:
            self.close_quiescent()
        except Exception:  # noqa: BLE001 - bookkeeping never replaces the outcome
            _LOG.exception("could not close settled agent turn progress")
finally:
    self._release_turn()
    ...
```

Optionally add a test that patches `journal.abandon` to raise and checks that the original exception class comes through.

**What I checked and found sound**
- **Liveness probe:** the lock is `flock` (it locks the open file, not the process). So a probe from the same process sees a live runner, not a dead one, and closing the probe's descriptor doesn't drop the runner's lock.
- **Claim lifecycle:** a fresh token per turn, never reused. The claim is released if the insert fails and handed to the coordinator once the row commits. Release happens after `close_quiescent`, so a runner always reads alive while it can still write. Both journal callers go through the coordinator's `try/finally`. Forked children close inherited descriptors (`register_at_fork`).
- **Recovery rules:** rows from an earlier owner generation are still always settled. Current-generation rows are settled only when their token is empty or proven dead; an unknown token is preserved. Every change still sits behind the owner lease and current-home checks.
- **Cross-user guard:** Stop filters by the caller's own owner key, the universe and the addressed agent, and those filters run before the lease is touched. The test at `tests/test_orphan_ready_turn.py:45` covers another owner, another agent and a live runner.
- **Threading:** `LiveTurn.request()` is thread-safe, so running `request_interrupt` in a worker thread is fine.
- **Page:** `flushAfterTurn()` resets `interruptRequested` / `flushAfterInterrupt` through `takeInterruptFlush()`. `interruptPending` is already null at that point, so the queue sends.
- **Writer retry:** the abandoned zero-round root plus `http_turn.turn = None` matches the new rule that a returned task owns no ready row.

**Minor, not blocking**
- `_publish_failures` scans every failed activity on every tick. On first deploy it will post chat notices for failures from before this change. If one record makes `record_turn` raise, notices for that universe stop.
- `.agent-turn-runners/` gains one lock file per turn and nothing cleans it up.
- During a restore, `request_interrupt` can raise `RestoreInProgress` after the live stop has already been requested. The route then answers 500 and the page says "Could not stop."

The tests cover the incident paths: idle status read from another reader, boot recovery of legacy rows, the scoped-Stop guards, the cross-process claim, failure delivery, the native-only refusal, the fresh root on retry, and the browser queue drain. None covers the error-masking path above.

VERDICT: ADAPT


## Codex disposition

AGREE ? the cleanup error could mask the original provider refusal or owner Stop.
Restored the guarded abandonment inside the unconditional runner-release finally.
The new regression forces journal abandonment to fail, verifies the exact original
exception survives, verifies another reader reports idle, then recovers the orphan.

Independent final inspection also reproduced a closed request budget on the
all-skipped writer retry. Replacing the whole coordinator (sharing caller-owned
budgets through config) lets the retry dispatch and complete; the journal proves
an abandoned zero-round root followed by a completed two-round root.

Claude's raw verdict is ADAPT, not APPROVE. Its single requested correctness
correction is implemented and tested; Codex accepts the repaired diff. The three
minor observations remain recorded above; no native tool fence or production
deployment is claimed. Native execution follow-up remains in the existing
2026-10-03 concern. One cross-family review round, as required.

Verification before review: 414 Linux tests passed with zero skips (including
Chromium), ruff, plugin mirror import probe, and hygiene 0 removed / 0 tampering.
Retry follow-up: 74 Linux tests passed with zero skips. Final cleanup regression
and affected coordinator/interrupt suites: 36 passed with zero skips. Ruff and
plugin mirror import probe passed again after both corrections.
