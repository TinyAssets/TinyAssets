# R5w as built (2026-11-14)

R5w is lane R's `restore.wipe`, the step restore owes before anything live can be restored (`r5r.md` item 14; `operator_research.md` §3.2 and §3.3 step 2; d3 §G). Restore is R5r's (`r5r.md`), its migration 10 pinned by #5589. The channel is R2's, the modes R3's.

1. **The wipe empties the object by making its schema afresh, in one unit, not by `deleteAll`.** Every table the schema made is dropped, then every migration applied again from the first (`remigrate`, `contract/src/migrate.ts`), in the wipe's own unit. The table list is the export's two lists (`exported`, `NOT_EXPORTED`), which `export.test.ts` already holds equal to the schema's tables. The proof checks a wiped object's `sqlite_master` against a fresh object's.
   - Why not `deleteAll`: it takes Lifecycle's job table and the alarm with it. So the erasure and the discard disable Lifecycle's alarm for good first, and their instance never works again. A restore's object must work again. One unit is also atomic, so a wipe is never half done.
   - The alarm guard is untouched: there is no third `deleteAll`.
   - The runtime's own tables (Lifecycle's, `_cf_*`) stay, since no migration made them.
2. **The instance is reset after the wipe** (`OwnerState.reset`), once the wipe is durable (`WriteGate.durable`, the storage's `sync`). Modules keep some state in memory (for example `Connections`' cached usable list), so nothing the instance held may outlive the wipe. A restore then runs on a fresh instance, as it does into a lost object.
   - The wipe's call ends with the reset. The operator channel's redelivery answers it from the record the wipe left (proved in `doors/test/operator.test.ts`).
   - That a synced write outlives a reset in production is owed live: `facet-probe.md` Q8 never sampled it (`tasks.md`, live on the stage).
3. **Two ops, twice confirmed, through the operator channel alone.** `restore.pin {gen}` and then `restore.wipe {confirm}`; each is a `reserved` door, so only the platform's caller reaches it.
   - The words are `wipe-<owner>-<fresh>`. They name the owner a second time, after `--owner`, and the generation the pin wrote of the present state (research §3.3: "the kit requires `--confirm wipe-<owner>-<that gen>`").
   - That generation is known only once the pin has written it, so the confirmation is a second run, by an operator who has read the generation it names. The apply job's `production` approval is a gate of its own on top.
   - `restore.release` gives a pin up before the wipe. The owner is then as it was, except that its runs, which the hold ended, stay ended.
4. **An erased owner is refused three times over:** by the directory's erased record at the channel's gate; by the object's tombstone (`OWNER_ERASED`); and by its erasure mark (`ERASING`). Proved for the pin.
5. **The hold.** The pin's unit writes the restore's record at a new first step, `pinned`. So R5r's `restoring` covers the hold from the pin until done, or once a restore failed:
   - no lane runs (the schedule's halt);
   - every door but the restore's refuses, in one place in `#bind`;
   - the platform's admission refuses too, and so does the import.

   In the same unit, every run and stream is ended, as a suspension's prompt stop ends them. The sockets are then closed with the relay's `lost` kind, whose reconnect `restoring` refuses; a close kind of its own is a contract change for the apps, owed (`untangle/r-restore-close`). The hold is not a suspension: a suspension's reason would be written into the fresh generation, and the owner would be told "abuse" or "billing".
6. **The present state is written first (Fact 13).** After the hold, the pin begins a fresh generation (`Backups.begin`), whatever the owner's change mark says: it must begin after the hold. A generation already under way began before the hold, so it is carried to its end first, and then a new one is begun.
   - The pin drives the manifest lane's pass itself, at most `RESTORE_PASS_MS` a call, as `restoreStep` drives the load. While the record stands, no lane runs.
   - `Backups.run` now takes one pass at a time: a second pass asked meanwhile joins the first.
   - The kit asks the pin again until the generation is written.
7. **Pinning.** The record's generation is the pin (`pinned()`).
   - The manifest lane's prune skips it (`Backups`' `pinned` part, `#prunable`, beside the fallback).
   - The pin survives the wipe: the wipe writes the record again, at `wiped`, in the same unit that remakes the schema.
   - From the wipe until done, no prune can reach the generation: the object records no generation, and no lane runs.
   - The pin is released when the restore finishes, or when it is given up: at done, `Backups.adopt` makes the generation the object's newest, which the next one supersedes as any is; before the wipe, `restore.release` deletes the record.
   - Proved: the test wipes, then restores the pinned generation, equal at E in every table and counter. With the pin dropped from `#prunable` the test fails, because the fresh generation's finish prunes the pinned one.
8. **What happens to the owner's files and generations in R2: nothing.** The wipe makes no R2 call (the test holds every call it makes to a `get`). The record keeps the namespace.
   - A wiped object takes a restore of the generation it was pinned for, or of the one its pin wrote (the undo). It takes no other.
   - A failed restore is wiped again by the same words, then restored again or undone. Proved: a restore fails, the object is wiped again, and the undo restores the owner as it stood at the pin.
   - The generations and chunks a restore leaves unreferenced (the fresh one, any fallback, the chunks written after E) stay until the erasure's prefix delete or a sweep, as `r5r.md` item 14 says. The sweep is lane R's `sweep` PR (`untangle/r7-sweep`).
9. **Migration 11** makes `owner_restore` again with two first steps, `pinned` and `wiped`, and the pin's generation (`fresh`), keeping its row. Its pin PR is owed after merge (`untangle/r5w-pin11`).
10. **The founder's retro-check: had the wipe existed when restore was built, restore's design would differ, so restore is fixed here.**
    - A restore's object was always pending, so its doors refused only because the owner was pending, and the restoring check was written twice (`admission.ts`, the import's branch in `owner-do.ts`). The hold makes a live owner restoring. So `refuseRestoring` is now the one rule, before every door but the platform's own and in `admissionSet`.
    - `emptiness` takes a wiped record, and `restoreBegin` goes on from it.
    - `restoreStep` refuses a record that was only pinned or wiped.
    - `restore.source` answers the record's step, generation and pin.
    - The kit reads `restore.source` to decide whether a pin and a wipe come first.
11. **Found, and owed** (`tasks.md`, the line after this one's; `untangle/r-fallback-chunks`): the files plane collects early only garbage the newest finished generation does not name (`#earlyBefore`). So the fallback R5r keeps for a restore can lose a chunk to early collection. A restore of it would then retry its chunks step without end, since a chunk read that fails is thrown to be read again. The hold stops the files lane from the pin on, so a wipe adds no window of its own. The fix chooses how long a kept generation's chunks are protected, which is the files plane's design, so it is not made here.
12. **A fix the cross-PR re-check found (`list7_recheck.md` item 1).** A sign-in during a restore, or after a failed one, met the restore's plain `refused` (`RESTORING`). The sign-in's redirect (`doors/src/refused.ts` `asRedirect`) throws any refusal it does not know as a bug, so the owner's OAuth flow ended in an uncaught error.
    - The fix: the contract's code table gets `restoring`, 503 with a 60-second `Retry-After` and transient, named for its state as `erasing` is. `refuseRestoring` throws it, so the redirect answers `temporarily_unavailable`, as it does every transient code.
    - The wipe's window is the same window, so the hold refuses with the same code.
    - Proved in `doors/test/operator.test.ts`: a sign-in during a restore, and one after a failed restore, each get `temporarily_unavailable`. With the code made terminal, the test throws.
    - A consequence: an `owner.*` op during a hold is a refusal for the moment, so the channel's queue retries it and then dead-letters it. The kit then stops, naming the ask. The restore's own flow asks `owner.admit` only once done.
13. **Restore's chunk check reads `Files.check` (#5599), a known fix folded in at the coordinator's word.** The chunks step used to read each chunk back as a verified one-byte `fetch`, one call a chunk, a failure thrown. It now asks `Files.check` for the slice: `GET_READS` chunks a call, no byte returned, and each chunk answered `stored`, `missing` or `broken`.
   - The semantics fit restore's retry. A chunk not stored as its hash still throws, naming how many and which answers, so the record doesn't move and the next step reads the slice again. A refusal or a lost answer throws as before.
   - Proved: a chunk the generation names, deleted from the bucket, holds the restore at its chunks step, each step answering the throw, never done or admitted. With the throw removed, the test fails.
   - A chunk that is truly lost holds the restore there for good, as it did before. Item 11's fix (`untangle/r-fallback-chunks`) is what keeps a kept generation's chunks.
