# R7-sweep as built (2026-11-15)

Lane R's `sweep` PR carries two items owed to it: the generations R2 keeps after no row names them (`r5w.md` item 8, `r5r.md` item 14), and F4, a rotation during a restore's hold (`r5w.md` item 14).

1. **The research: where unnamed generations came from.**
   - A pass puts a generation's objects and records them after. A crash or a lost answer between the two leaves an object no row counts: a part past the recorded count (a burst cut again after a crash may cut fewer), or a segment never acknowledged.
   - The prune (`#prune`) and the give-up (`#abandon`) deleted only the objects the row counted (`parts`, `segment_list`, the root's segments). So the objects above were left for good once the row went.
   - The prune deleted the root first and then read the segments from the root. A crash between the root's delete and the segments' left the next try with no root, so it deleted the parts and no segment.
   - The prune's R2 calls ran outside any unit, across awaits, after it chose what to delete. A pin in between could pin a generation the prune then deleted.
   - A restore leaves its namespace's other generations unnamed once done: the pin's fresh one, any fallback, everything a lost or wiped object had.
   - Owner erasure deletes the whole prefix (`deleteNamespace`), so erasure is safe; this is about live owners. An unnamed generation holds rows the owner may have deleted since, so left alone it breaks the 30-day promise as well as costing storage.
2. **The design: one list, one deleter.**
   - The list is `#retained` (#5611), unchanged. The prune is now one quiet unit: it deletes the row of every generation `#retained` leaves out, and returns the generations it keeps. No R2 call, so no window for a pin, and no root read under a key a rotation may have retired.
   - R2 keeps a generation's objects only while the list names it. Every unit that deletes a row (the prune, a give-up) or adopts a generation (a restore's done) owes a sweep, `owner_backup_state.sweep`, in the same unit. "Owed" is read from the record, never from time.
   - The sweep is a step of the manifest pass: at its start, and right after a finish's prune. It asks the files Worker for a page of the namespace's generations (`sweepManifests`, `DELETE_BATCH` keys) with the list read afresh, after a prune, for each page. The Worker deletes every object of a generation the list leaves out, the page's roots before its objects. The bucket lists keys in order and a root's key begins its objects', so no root is ever left naming an object gone.
   - It finds what a crash left as well as what a row named, so the give-up and the prune stop deleting objects themselves. `deleteManifest` is gone; the finish's tail past E, inside a kept generation, uses `deleteSegments`.
   - Generation objects are only ever put by a pass, one pass at a time (`run`), so the sweep never meets a put in flight.
3. **Crash and failure.**
   - `sweep` counts the sweeps owed. A sweep settles only the count it read when it began (`WHERE sweep = ?`), so one owed meanwhile (a prune between pages) makes the next pass sweep from the start.
   - A files call that fails backs the sweep off in its row (`sweep_retry`, `MANIFEST_RETRY_S`). `next` wakes the lane then, so a failing R2 never loops the alarm. The pass's own catch no longer stamps a retry on the oldest finished row: `next` never read it there, and the prune it was for makes no R2 call now.
   - A sweep cut short by the pass's time, or by an erasure's halt, stays owed.
4. **What is never swept.** Every generation on the list: the one being written (its landed but unrecorded put too), the newest, each one an open export pins, the fallback, and the one a restore's pin holds. While a restore holds the owner, only its pin's own pass runs, before the wipe, so no sweep meets a wiped object, whose namespace only the record keeps. The sweep therefore needs no second list of a restore's generations.
5. **Migration 13** adds `owner_backup_state.sweep` and `sweep_retry`. An object that ever began or held a generation owes one sweep, for what a prune or give-up before this left. It is unpinned here; its pin PR follows the merge (`untangle/r7-sweep-pin13`).
6. **The cost.** One R2 list (Class A) a page of the owner's manifest keys per sweep. A changing owner prunes about once a night, so that is about one list a night for a typical owner. Owners at the ceiling, with up to `MANIFEST_PARTS` objects per kept generation, need about 17 pages per generation. Deletes are free. The prune's root read (Class B) is gone.
7. **F4, the research.**
   - `FILES_KEYRING` is the files Worker's own ring, separate from `OPERATOR_KEYRING`, which signs the operator channel's messages.
   - `dataKeyRewrap` wraps the object's data key again under the active kid, then each finished generation's root (`rewrapManifestRoot`, in place, refused `root_changed` when a pass wrote the root meanwhile). A pass's root is always written under the active kid (`putManifestRoot`).
   - A wiped object holds no namespace and no row, and a failed restore's object holds the adopted namespace and no row. So the rewrap skips both roots a restore can still load: the pinned generation and the pin's fresh one. Once a rotation removed the old kid, the wiped owner would have no root that opens.
   - The rotation's driver, an operator op that asks `dataKeyRewrap` of every owner and then removes the old kid, is not built yet.
8. **F4, the shape: the rewrap is refused while a restore holds the owner** (`refuseRestoring`, transient `restoring`). Making the rewrap cover the record's generations is not smaller: a wiped object would have to take the record's namespace in place of its own, which only the record keeps, and that would be a second path to a namespace. The refusal is one line in the door's unit, and it holds whatever the driver does, since the driver must have every owner's answer before the kid goes. The restore reads its roots under the old kid, still in the ring, and once done the rewrap covers the adopted generation as any. Cost: a rotation waits for every hold to end. A failed restore is ended by its own words (wiped again and undone), so the wait is the operator's to end, and the refusal names it.
9. **The proof.** On the fake clock and records only:
   - `export.test.ts`, "the sweep of generations no row names":
     - a part that landed with its answer lost, its generation then given up, is gone after the next pass;
     - with a sweep owed while a pass is under way, the generation being written (its landed, unrecorded part), an open export's, the fallback and the newest all keep every object, and an unnamed one goes;
     - a sweep whose answer is lost after its deletes landed stays owed and backs off to its retry, and one owed while it runs is not settled by it.
   - `restore.test.ts`:
     - the pin's fresh generation stays in R2 while held, and is swept once the restore is done, the restored one kept;
     - the stale newest the lane deletes at `RESTORABLE_LIFE_S` is gone from R2 too;
     - `dataKeyRewrap` is refused while pinned, wiped and restoring, and answers once done.
   - `chunks.test.ts`: the files Worker's sweep pages through, keeps what it is told, and never leaves a root naming an object gone.
   - **Mutations**, each failing its test:
     - a give-up owing no sweep;
     - a prune owing none;
     - the list without the generation being written;
     - no back-off;
     - a settle that ignores the count;
     - an adoption owing none;
     - the rewrap's refusal removed;
     - objects deleted before roots.
10. **Found, and owed.**
    - The chunks a restore leaves unnamed: those the owner put after E have no row in the restored object, so nothing collects them, and their files are gone from the owner's view. Erasure's prefix delete takes them. For a live owner, a chunk sweep has to map chunk names, which are HMACs under the data key, back to the object's hashes, so it is the files plane's design with its own research (`untangle/r-restore-chunks`). It is rare: it follows only an operator's restore.
    - The rotation's driver: the operator op and kit mode that ask `dataKeyRewrap` (or `gatewayReseal`) of every owner until each answers, then remove the kid (`tasks.md`, lane R's line; `untangle/r7-rotation`).
    - The driver's own research must cover the off-account copy. Its versions keep a root under the old kid for up to 22 days (`r4.md` item 4), so removing a kid the moment every owner answers would leave a restore from the copy's older versions unreadable.
