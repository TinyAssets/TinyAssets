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
- [x] Verified the same fault on main and the seven-branch combination in the public owner checks below. Earlier combined tree `9d4b4628` passed seven selected cases. Its original combined peak/fault case timed out at 86 seconds. The first standalone and combined reruns of that old case exited 137 before a verdict.
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
- [x] The original multi-segment capture/peak case passed on e9453549 in the ordinary Node 24 Workerd suite. It checks the restored rows and the staged-byte peak against the generation. An earlier ordinary run exited 137.
- [ ] Establish the historical recovery route before enabling mixed-version restores. An active log record answers a same-generation begin in place. A release after wipe refuses. A repeated wipe on an active step returns its status. Existing confirmed wipe/restart is available from a failed restore and retains the namespace, original generation, undo generation and hold time. Verify recovery for both pinned and lost owners. Completed historical adoptions need a separate inventory because their staging rows have been cleared.

- [x] Pinned undo evidence. The earlier ordinary run on `30545b0a` reached the second
  confirmed wipe after a real digest failure and admission refusal. The Worker
  then exited before the final digest assertion returned. A separate attempt
  with a smaller owner exited 137. Preserve the existing full undo scenario and
  check the real failure through the public restart of the retained generation
  independently. An unfinished run supplies no passing test verdict.
- [x] Scoped Node 22 TypeScript passed on `592ee821`. An independent native
  Workerd test used its Wrangler-built owner, a real Files Worker and local R2.
  Its local owner subclass exposed two fixed fixture operations to seed a notice
  and damage the loaded notice. Public owner export created the original
  generation. Restore detected the row digest failure, refused admission and
  retained the undo generation through the second confirmed wipe. Its public
  begin answered index and done=false. The source status named the actual undo
  generation. Undo then finished and, after admission, the owner read the file
  written after the original generation. The in-tree test now checks the
  generation on the status door that actually supplies it. Earlier attempts to
  run the selected Vitest case lost the Worker or exited 137. This Workerd proof
  applies to a checked failure with a pin. Active legacy staging and already
  completed historical restores still need a recovery decision.
- On b692a671, Node 22 TypeScript reported zero diagnostics for the restore
  test and owner restore module with the corrected status assertion. The
  ordinary Node 24.22.3 Vitest run on e9453549 passed the corrected pinned undo
  and multi-segment capture/peak cases together (2 passed, 63 outside the filter).
  After the digest failure the undo test observed admission refusal, the retained
  pin through a second confirmed wipe, and the retained generation at restart.

- [x] Checked the lost-owner counterpart to the pinned recovery above on the
  public owner and Files Workers from b692a671. A local fixture exported a
  file, added a later file, finished another generation, then cleared the
  synthetic object's storage and evicted it. The fresh object had no restore
  record. Restoring the newer generation encountered a deliberately changed
  notice during verification and refused admission. With no surviving pin,
  the failure returned a null undo generation. Its confirmation allowed the
  second wipe. The older authenticated generation then reached done. After
  admission, the earlier file read back correctly and the later file was
  absent. The existing lost-owner tests in owner/test/restore.test.ts cover
  restart from an earlier generation and from another held namespace. This
  independent local run used the same namespace. Recovery from an active
  historical partial segment and from already completed historical adoption
  still needs a decision.

- [x] On e9453549 the full ordinary owner restore test file passed on Node
  22.23.5 using the pinned Workerd runner. All 64 ordinary cases passed,
  including both undo cases. The size-tagged case was outside this selection.
  The historical partial and completed restore decisions above remain open.

- [x] The separate size-tagged case also passed on e9453549 with Node 22.23.5.
  On the seven staged sources, the complete ordinary file then passed 67 cases
  and the separate size run passed its one case, including restore when the
  generation's total segments exceed the log budget. Node 24.22.3 passed the
  full platform TypeScript check on the same combined sources.
- [x] The seven inputs were apply e9453549, opener 47044108, rollback c407e4b7,
  pass seconds 538cd9a9, begin e0aaf71a, pin 780d5c1c and finish 2bda742b,
  all independently based on f934871ad. Restore source combined cleanly.
  Retain both neighboring test declarations at the apply/begin and undo/pass
  overlaps. A TypeScript syntax-tree comparison found all 61 distinct test
  declarations from main and these branches in the combined file, each once.
- [ ] Mixed-version activation still depends on historical recovery for both
  an active partial record and a previously completed adoption. Recheck the
  coordinated result on main before cutover.
