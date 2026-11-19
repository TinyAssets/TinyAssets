# R8 copy back after a manifest is pruned

R7 gives the owner one list of retained generations and Files one operation that removes their objects. R8 also has a writer outside the owner's manifest pass. The deployment kit can restore a missing root from the off-account copy after the owner has finished collecting that generation. The command's final check can then report the stale write, but the owner has already settled its sweep. This leaves the copy back's claim that an unreferenced object will be collected dependent on a later recovery action.

## Where the two operations meet

1. In `deploy/offsite.ts`, `copyBack` inventories missing objects and requests the owner's current `restore.names`. In `owner/src/restore.ts` this door returns the finished generations from `backups.manifests()`. A finished generation can still have a row because an export reader retains it.
2. The kit records the off-account restore before it copies anything. This record gives key retirement the start time of a copy back. It neither holds a generation for the copy nor schedules work in the owner's manifest lane.
3. The reader can expire while the kit awaits the record. The owner's next `Backups.run` prunes the old generation, sweeps its remaining objects and settles `owner_backup_state.sweep`. The kit's earlier inventory still selects the missing root. The byte check verifies the root selected for copying. Parts present in the original inventory are outside that check.
4. Once the kit asks for current names a second time, it refuses the stale write and directs the operator to restore the owner. A failed copy response or byte check can end the command before that second request. An ordinary retry recomputes what is missing. It need not see any work even if a root now names a missing part.

## Offline comparison

The same six cases ran independently on main `f934871ad`, the local combination `eff491215` and the isolated Files cross-page correction `180e98f58`, on Node 22.23.5 and 24.22.3. Each case started with a new SQLite database using all 18 owner migrations, two finished generations and a live export on the older one. Real Files methods minted the synthetic key, sealed both roots and parts, and subsequently opened the selected root and read its part. The copy initially contained every object. Removing just the older root from the primary store made that root the only missing object.

The comparisons invoked the original `copyBack`, `restoreDoors.restoreNames`, `Backups.run` and Files methods. Synthetic ordered object stores supplied the bucket API. The command effects applied the kit's exact file selection and byte comparison to those stores. The clock, key-record write and selected errors were local controls. No provider or live operator call was made.

| Circumstance | Observed result |
| --- | --- |
| Export retained throughout | The copy restored one root. Its part opened and the command succeeded. |
| Export already expired and owner sweep finished before inventory | The kit selected no object and wrote no restore record. The older root and part stayed absent. |
| Restore-record write refused while the export remained live | The command stopped before copying. The older root stayed absent and the part remained. |
| Export expired while the kit awaited the record | The owner removed the part and settled its sweep. The kit copied the root, verified those bytes and refused at its second owner read. Files opened the root and reported the missing part. |
| Copy landed and its response failed after the same interleaving | The first attempt ended before the second owner read. The root was present with its part missing. |
| Readback failed after the same interleaving | The first attempt also ended before the second owner read. The root was present with its part missing. |

In all three interrupted or stale-write cases, another normal owner backup pass left the old root in place with zero sweeps owed. The kit's retry reported that nothing needed writing. The test observed this interval and those retries only. It does not establish indefinite retention, prevalence on a live account or a completed restore from this generation.

## Open dependency

Before the staged recovery drill relies on this path, establish who durably owns completion of a copy back while the owner can retire the selected generation. The owner must retain its single source of eligibility. A stale or interrupted external write needs a defined way to reach a reconciled state after eviction or host loss, through either a proved hold over the right interval or durable work that survives the writer. Confirm the behavior for a damaged source that needs a missing root, normal export expiry, owner erasure and the operator's prescribed restore. The existing global restore record is used by the offsite key-retirement gate and lacks the per-owner lifecycle semantics needed for that conclusion. No protocol or change to operator admission is selected here.
