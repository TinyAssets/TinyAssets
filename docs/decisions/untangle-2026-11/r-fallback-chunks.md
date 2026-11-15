# r-fallback-chunks as built (2026-11-15)

`r5w.md` item 11 found, and left to this PR, that early collection protects only what the newest finished generation names. The fallback R5r keeps for a restore (`r5r.md` item 2) could then lose a chunk, and since #5607 its restore fails at its chunks step (`r5w.md` item 13).

1. **The research.**
   - Early collection (`files.ts` `#earlyBefore`) takes garbage past the owner's figure only when it was let go before a time the object hands it. A generation names only chunks live when it began or since. So garbage let go after a kept generation began may be named by that generation.
   - The time was the newest finished generation's start, worked out in `owner-do.ts` from `backups.manifests().at(-1)` and `MANIFEST_PROTECT_S`. The rule for what is kept lived in `backup.ts` (`#prunable`, with the export pin checked again in `#prune`). A second copy, which generations name garbage, lived in `owner-do.ts` and knew only the newest.
   - Kept, but not counted by collection:
     - the fallback;
     - each generation an open export reads after a newer one finished (`owner_exports`, a week), whose export then meets `CHUNK_GONE` for a chunk early collection took;
     - the generation being written while none has finished: an owner's first, or the first after two nights without one. Once it finishes, it can name a chunk already taken.
   - The restore's pin (`restore.pin`) and its fresh generation are kept, but the hold halts every lane, the files lane with them (the schedule's halt, `owner-do.ts`). No collection runs while the pin stands, and the fresh generation is the newest once it finishes. Neither was losing chunks. Both are on the one list anyway, so their safety no longer depends on the halt.
2. **The fix: one list.** `Backups.#retained` is every generation kept at a time: the one being written, the newest finished, each an open export pins, the restore's and the fallback. It is the complement of `#superseded`, which is what the prune deletes. `Backups.protectsSince(now)` is the oldest retained generation's start, and the files plane reads it in place of the newest's. Early collection and the prune now read the same list, so any kind of generation that is kept is also protected.
3. **`MANIFEST_PROTECT_S` stays a measure of the lane's health,** now read from the lane's latest mark: protection holds while a generation finished or began within it. Past it no generation has finished or begun, so the owner's figure holds again, as the recorded ceiling says (`limits-audit.md`). It is not applied to each generation by its own age. The fallback is older than the newest by definition, so a bound per generation would let its chunks go after a night while the system still keeps it for a restore. The mark counts a generation's start as well as a finish. Otherwise a generation being written with none finished recently would be no one's to protect (item 1).
4. **The proof:** three cases in `storage.test.ts`: "never collects early a chunk only the fallback generation names", "...only an open export's generation names", and "...the generation being written names, none finished yet". In each, garbage past the figure that only that generation names is all kept. Once nothing keeps the generation (or it began after the garbage went), the prune deletes it and the oldest garbage goes early.
   - With `protectsSince` answering the newest finished one's start, as on main, all three fail.
   - With the mark read from the newest finished one alone, the third fails.
5. **The fallback's age (folded in at the coordinator's word, 2026-11-15).** Plain collection (`GC_GRACE_S`, 30 days) takes due garbage whatever names it, and the fallback stood with no bound. A fallback older than 30 days could then name a collected chunk.
   - **The research.**
     - Why a fallback exists: the newest's log through E passed `LOG_BYTES`, which a restore stages, because the owner wrote more than that during its base pass (#5587, R5r's review F2).
     - What makes a later generation restorable on its own: only a base pass during which the owner writes less. Nothing the lane does controls that, since the owner's writes always land. Writes during a pass mark the owner changed (`mark`), so the lane already begins a new generation the night after each one. A fallback reaches 29 days only if about 29 nightly generations in a row each had a log too big to restore.
     - What the lane could control (the review): the measure. `log_bytes` counts every image the log appended, but a restore stages only the last image per key (`restore.ts` `stageLog`), so an owner rewriting a few hot rows (`model_spend`, `runs`, `collection_clock`) during a long base pass is refused a generation that would fit. Measuring what a restore stages needs the capture to keep it, an owner migration: owed (`untangle/r-log-staged-bytes`).
     - So a forced generation near the age limit would be one more attempt of the same kind. It would cost a full base read (up to 22.2 GB, about 42 minutes) and could still fail. It costs more than it saves, so it is not built.
     - Holding garbage past 30 days is ruled out: it would break the 30-day promise for deleted data (the coordinator, from `r4.md` item 4). A generation that old also holds, in its parts, the rows of files deleted since it began.
   - **The design.** A generation is whole while nothing changed since it began, or while the owner's first change since is within `RESTORABLE_LIFE_S` (`GC_GRACE_S` less a day). It can be restored while it is whole and its log is within `LOG_BYTES`. That is one rule, `wholeAt` and `restorableAt` in `backup.ts`.
     - Every row and chunk it names that the owner deleted since went at that first change or later. So plain collection reaches no chunk before the first change is 30 days old, and no deleted row stays in it past that.
     - `restore.source` and `restore.pin` read the same rule (`Backups.restorable`), instead of each copying the `LOG_BYTES` check.
     - The fallback is the latest older generation that can be restored.
     - Any generation that is not whole, the newest included, is neither offered, pinned nor kept. An open export's is not kept either: the 30-day promise outranks the export's pin. The lane is woken when each generation stops being whole (`next`), and its prune deletes it.
     - The day of margin is the lane's nightly cadence, so even a missed wake prunes the generation in time. Once pinned, a generation needs no margin: the hold stops the files lane.
6. **The newest after a give-up (review F3, folded in at the coordinator's word).**
   - **The gap, on main:** a change after the newest began set the owner's one change mark, and the next generation's begin cleared it. If that generation was given up and the owner stayed idle, nothing marked the change again. The lane waited for a next change, the newest was offered as restorable after plain collection took what the change let go, and its parts kept the rows the owner deleted past the 30 days. It was silent until a restore of it failed at its chunks step.
   - **The research:**
     - The mark must belong to the generation, not the owner: "changed since this generation began" is true of the newest whatever happens to a later attempt. So migration 12 gives each generation its first change since it began (`owner_backups.changed_at`), set by `mark` on every generation that has none and cleared by nothing. The owner's one mark (`owner_backup_state.changed`) is dropped: every reader (the nightly due, the lane's begin, an export's reuse of the newest) now asks whether the newest has a change.
     - The export's reuse had the same bug: after a give-up it served a stale newest without the change.
     - The retry loop this PR's first attempt caused came from counting the next night from the newest's day. Once the newest is older than a day, that is now, so a generation given up at its ceiling would begin again at once.
     - Migration 12 also records when the last generation was begun (`began_at`, set at every begin). The next night is counted from the later of that day and the newest's, so a given-up generation is tried the next night, never at once: `export.test.ts` "past its ceiling … never retried in a loop" holds it.
     - The ceiling and line give-ups cannot be reached within the schema's bounds, so a nightly retry costs nothing in practice. Their notices now say "tried again the next night".
   - **The erasure side:** every retained generation, the newest included, is deleted once its first change is `RESTORABLE_LIFE_S` old (item 5).
     - Every try since has failed by then, and the owner is told none can be restored.
     - An owner with no generation is tried again each night. The tells come once a generation has finished (`lost` is null before that).
7. **The loud state, with its own mark (review F1).**
   - `manifest_unrestorable` gates on `owner_backup_state.lost` (migration 12), apart from `told`, the give-ups' mark. Before, it shared `told`, so a give-up after the fallback finished silenced it for good.
   - `told` is back to resetting at every finish, as on main.
   - `lost` is null until a generation first finishes. A finish within `LOG_BYTES` sets it to 0, and any other finish arms it at 0 if it was null.
   - The check runs at the end of each pass (a finish is always within one), after any give-up, so a give-up's own notice comes first.
   - The log says it too, and `restore.source` names every generation not restorable.
   - No per-owner operator alarm exists, since no part of the platform reads every owner (`r5w.md` item 14).
   - **Corrects `r5r.md` item 2's "a generation a restore can load always stands":** one stands while one meets the rule above, and the owner is told when none does.
8. **Migration 12** adds `owner_backups.changed_at`, `owner_backup_state.began_at` and `owner_backup_state.lost`, and drops `owner_backup_state.changed`.
   - It maps existing rows conservatively: every generation counts as changed from its start, except the newest of an owner with no change marked and none being written.
   - It is unpinned here. The pin follows the merge, as for migrations 10 and 11.
9. **The proof, on the fake clock:**
   - `restore.test.ts`, "lets the fallback go a day before plain collection could reach a chunk it names, and tells the owner none can be restored". A give-up's `told = 1` is set first (the review's reproduction). The lane's next pass finds no change, and its job is then due at the fallback's end. A minute before the end, the fallback is restorable. At the end it is not, and a pin of it is refused. The lane then prunes it, and the owner gets the notice.
   - `restore.test.ts`, "tries a given-up generation again the next night, and lets a stale newest go once the change it lacks is RESTORABLE_LIFE_S old":
     - three days after the newest, a file is deleted and the generation begun for it is given up;
     - the lane is due the next night, not at once;
     - once the change is `RESTORABLE_LIFE_S` old, the newest is not offered and a pin of it is refused;
     - the lane deletes it.
   - `export.test.ts`'s ceiling test: tried the next night, never at once.
   - **Mutations,** each failing its test:
     - without the age limit, the fallback stays restorable;
     - without the wake, the lane has no job;
     - without the telling, no notice is given;
     - with the notice sharing `told` again, no notice is given;
     - with a begin clearing the newest's mark, as main's did, the lane has no job after the give-up;
     - with the next night counted from the newest's day, the given-up generation is begun again at once;
     - without the life once changed, the stale newest is still offered.
