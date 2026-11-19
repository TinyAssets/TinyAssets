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

### Switching writers at the interrupted cursor

Scoped Node 22 TypeScript passed for the restore source, test and their dependencies on `278199e4`. A separate SQLite replay used the actual restore piece and write gate from main `f934871ad`, the staged apply branch and the seven-branch runtime described in the backup opener decision. The test supplied a synthetic two-row manifest segment to the Files reader. The two encoded lines were 311 bytes each. An ordinary SQLite trigger stopped the second insert. The retry constructed a new gate on the same database after dropping the trigger. All nine ordered writer/reader pairs gave these results on both Node 22.23.5 and Node 24.22.3.

| Writer at interruption | Rows and bytes left at cursor 0 | Next reader | Bytes recorded at cursor 1 | Stored row bytes |
| --- | --- | --- | ---: | ---: |
| Main | 1 row, 311 bytes | Main, staged apply, or combined | 311 | 622 |
| Staged apply or combined | 0 rows, 0 bytes | Main, staged apply, or combined | 622 | 622 |

This extends the historical hold to a cursor that has **never advanced**. Retrying on corrected code can acknowledge an old partial segment with the wrong count. Recovering only the final row sum also leaves a previous peak unverified. Keep activation for an existing in-flight restore open until replay from its immutable generation or an authorized restart proves the historical counts. The SQLite replay covers the single restore piece. Whole-Worker attempts that exited 137 supply no verdict.

### Public owner and runtime-switch checks

- [x] The new case fails on main source `f934871ad`: log cursor zero, zero recorded bytes and peak, yet one staged row survives. It passes on seven-branch tree `1cbfcc25`, which applies only the revised tests to `9d4b4628` (one passed, 66 outside the selection). Together with the prior seven selected cases this checks both the nested gate and an interrupted segment. These two ordinary Workerd entrypoint runs used identical, locally prebuilt Wrangler dry-run bundles for Files, Gateway and Codemode. Their source, config, contract, CI and lockfile matched. The owner Worker ran each tree's source.
- [x] A second local Workerd fixture switched the actual restore and gate modules over the same Durable Object database using Miniflare configuration reloads. All nine writer/reader combinations matched the SQLite table above, including the stored peak.
- [x] Independently checked the public owner restore on the staged runtime with a 97-byte legacy prefix. In the focused test, immediately after dropping the trigger, take the first line of the first segment from the real Files manifest. Insert it into `restore_rows(tbl,k,row,n)` using the exported table key, JSON row and original newline-inclusive byte count. Keep the cursor and counters at zero. The normal retry answered done and the complete restored-row digest matched, then the final count assertion failed: staged and peak both read 98 against the immutable generation's 195. This diagnostic seeds the older writer's prefix. The separate runtime-switch fixture produces it by interrupting the actual main writer.
- [ ] The original multi-segment capture/peak case is still unverified on this head. Its ordinary rerun exited 137. The smaller segment case has its own completed verdict.
- [ ] Establish the historical recovery route before enabling mixed-version restores. An active log record answers a same-generation begin in place. A release after wipe refuses. A repeated wipe on an active step returns its status. Existing confirmed wipe/restart is available from a failed restore and retains the namespace, original generation, undo generation and hold time. Verify recovery for both pinned and lost owners. Completed historical adoptions need a separate inventory because their staging rows have been cleared.

- [ ] Pinned undo evidence. The ordinary run on `30545b0a` reached the second
  confirmed wipe after a real digest failure and admission refusal. The Worker
  then exited before the final digest assertion returned. A separate attempt
  with a smaller owner exited 137. Preserve the existing full undo scenario and
  check the real failure through the public restart of the retained generation
  independently. An unfinished run supplies no passing test verdict.
