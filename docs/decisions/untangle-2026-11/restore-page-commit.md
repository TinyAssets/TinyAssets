# Restore pages and their cursors

On main `f934871ad`, a write unit keeps the SQL prefix it executed before it throws.
Ordinary refusals rely on that property. A restore page has a narrower promise. The
log rows, the staged byte count and the cursor describe one segment. Likewise every
row loaded from a part or a log page belongs to one apply cursor. A failed second SQL
statement used to leave the first committed with the previous cursor.

Two synthetic owner databases using the real restore step, WriteGate and all 18
migrations on Node 22 and 24 showed the same failures. Main kept 311 bytes of a
622-byte log segment after a second-row fault. On retry it recorded 311 for the full
622. A second model receipt interrupted during apply left one receipt. An unchanged
retry then failed its immutable-column update. An already present archive segment
also rejects its own range in a BEFORE INSERT trigger.

The index, log, apply and check page callbacks now own a synchronous savepoint within
the existing write unit. Their SQL effects commit with their cursor. A retry skips an
already identical historical row before attempting its insert. It compares every
loaded field except reference counts rebuilt by triggers. Held admission fields still
wait for the final check. A different seeded or trigger-created row still takes the
generation's values through the existing upsert. A conflicting immutable row still
refuses. The separate check continues to compare restored rows and table totals.

On the corrected code the two fault controls rolled back their prefix, the log recorded
622 bytes on retry, and an older already-committed receipt resumed without charging
it twice. An identical archived segment also resumed, a changed overlapping segment
refused, a rebuilt content reference count survived, and admission stayed pending.

A restore whose older code already advanced beyond an undercounted log segment can
have an incorrect historical peak. Its final row sum cannot establish every prior
segment's peak. Before accepting such an in-flight restore, establish an authorized
restart from its immutable generation or a verified replay of the staging history.
The current wipe door takes a pinned or failed restore. An active restore that stopped
on an exception alone has neither state. This slice supplies no new operator action.

The final fence, stale-generation identity across asynchronous reads, and the separate
preservation of expired spend counters have their own ordering and recovery questions.

## Interrupted log segment verification

- [x] On staged test commit `278199e4`, the focused ordinary Node 22 Workerd case passed (63 outside its selection). The fixture writes two notices during a real generation. After a second-row failure, the restore still points to log segment zero with no rows, staged bytes or peak. Retry finishes and the restored rows equal the generation digest. The final staged bytes and peak match an independent reading of the generation. This isolates the fault from the original multi-segment peak test, which remains in the suite.
- [ ] Verify the same fault on the current main source and the seven-branch combination. Earlier combined tree `9d4b4628` passed seven selected cases. Its original combined peak/fault case timed out at 86 seconds. The first standalone and combined reruns of that old case exited 137 before a verdict.
- [ ] Determine how to handle an in-flight segment whose first row was already committed by the old writer and whose cursor still points to that segment. The new savepoint prevents a fresh partial write. Existing rows reduce the byte delta computed during replay, so a preserved cursor alone cannot certify the byte count.
