# r-log-staged-bytes as built (2026-11-15)

`r-fallback-chunks.md` item 5 owed this: a generation was judged restorable by `log_bytes`, its segments' sum, though a restore stages only the last line per key. An owner rewriting a few hot rows (`model_spend`, `runs`, `collection_clock`, a connection) across a long base pass was refused a generation that would fit.

1. **The research** (Fact 14, before the build).
   - The premise holds, slightly sharpened: a segment already keeps the last line per key of its own span (`ChangeLog.segment`), so `log_bytes` counted a key once per segment it appears in, not once per image. A restore stages across all segments (`restore.ts` `stageLog`), so a key in N segments was counted N times.
   - No table the capture writes keeps a key once its segment is acknowledged (`acknowledge` deletes the lines), so the count needs one.
   - `restoreBegin` summed the root's segments again: a second copy of the rule, from the root. Kept, it would refuse after the wipe a generation the pin had accepted. On a lost object, whose only record is the root, it would refuse a newest the live object had judged restorable, with no fallback kept. The root carries no staged size, and adding one is the files Worker's contract (another lane).
2. **What a restore stages is its peak, not its final sum.** A restore stages segment by segment, so what it holds at once is the largest sum at any segment's end. An owner who writes many rows and then deletes them leaves a small final sum, but the restore held all of them before the gone lines came. At each segment's end the capture's sum and the restore's are equal: both are the last line per key over the segments so far. `staged` is that peak, in bytes as the segments write the lines, each with its newline.
3. **The capture counts it** (`changelog.ts`), in the unit that acknowledges each segment.
   - It records each kept line's table, key and bytes as written in `log_staged`, sized one row at a time as `#writtenBytes` sizes them. It keeps the running sum and its peak in `log_capture`.
   - A crash anywhere repeats a segment whole and counts it once, at its acknowledgement, and loses nothing.
   - A segment past E (the tail) is not counted, since the root never names it.
   - Once the peak passes `LOG_BYTES` the generation can never be restored, so counting stops and the keys go.
   - **Bound:** a key's record is no wider than its line. So `log_staged` holds at most `LOG_BYTES` of lines and the one segment that passed it: a new owner-bound term (`LOG_BYTES + CHUNK_BYTES`), about 72 MB of the 505 MB room under half. It is emptied at every begin and stop.
   - The finish records the peak as the row's `staged`. `restorableAt` reads it, so the fallback, `restore.source`, the pin and the `lost` telling do too. `#finish` reads `restorableAt` rather than its own copy of `<= LOG_BYTES`.
4. **The restore counts what it stages** (`restore.ts`) and enforces it.
   - Each staged line keeps its bytes as its segment holds them (`restore_rows.n`), and the record keeps the sum and its peak (`owner_restore.staged`, `.peak`). The `log` step fails the restore, named, once the peak passes `LOG_BYTES`, so `restore_rows` holds at most that and one segment (its term is now `2 * (LOG_BYTES + CHUNK_BYTES)`).
   - `restoreBegin`'s sum of the root goes. A lost object and a wiped one are judged by the same rule.
   - The capture's figure predicts the restore's, and `restore.test.ts` holds the two equal.
   - `adopt` takes the restore's peak, not the root's sum, so an adopted generation is judged as it was.
   - Pin and begin reset both counts.
   - **A lost object's restore that fails here is wiped for any generation of its namespace** (review F1; a decided change to `r5w.md`'s rule, the coordinator, 2026-11-15).
     - The gap: a lost object names no generation (`restore.source` is empty), so its newest is begun, and one past `LOG_BYTES` fails only at this step. On main, begin refused it by the root's sum and the one kept before it was begun next. Here every route to that one was refused (begin, `restore.pin`, `restore.release`, and the wipe's `wipedFor`), so the owner was stuck with the restore that fails.
     - **`r5w.md`'s rule changes:** "a wiped object takes only the generation it was pinned for, or the one its pin wrote" now binds only where a pin held the owner (`fresh`). The reason: a pinned live owner keeps its undo, the fresh generation of its present state; a lost object has no live data to protect.
     - So `wipedFor` admits any generation of the namespace for a wiped object no pin held, and `restorePin` answers a failed restore no pin held as it stands for any generation: the kit pins, wipes and begins the kept one (`restore.test.ts`, "a lost object's restore that fails at its log step › is wiped and begun again from the generation kept before it, as the kit asks it"). The failure's words no longer point at `restore.source`, which a lost object cannot answer.
   - **Corrects `r5r.md` item 2's** "a generation whose whole log through E passed `LOG_BYTES` is refused": one whose staged peak passes it fails at its `log` step, and the object judges it so beforehand.
5. **Migration 15** renames `owner_backups.log_bytes` to `staged`. It adds `log_staged`, `log_capture.staged` and `.peak`, `restore_rows.n`, and `owner_restore.staged` and `.peak`.
   - **A generation from before** keeps its segments' sum as `staged`. That sum is never less than its peak, so it is judged as on main, conservatively. A restore of it measures its own staging exactly.
   - A generation under way at the migration is given up by the schema check (review L5-2), so none crosses it.
   - A restore under way past `index` is failed, named: what it staged was never measured. At `index` nothing is staged yet.
   - It is unpinned here. The pin is `untangle/r-log-staged-bytes-pin15`, after the merge.
6. **The proof** (`restore.test.ts`):
   - "what a restore stages › takes a generation whose segments sum past LOG_BYTES while its hot rows' last lines fit, and restores it" (`large`). Three connections at a row's widest are rewritten in turn during the base pass: the segments sum to past `LOG_BYTES` while the peak is about 4 MB. The generation is restorable, a lost object restores it, the rows match those at E, and the adopted generation is still restorable. 22 s here under load, so about 60 s at CI's scale, against the large class's 246 s.
   - "a restore › reads a generation the owner wrote through, … staging at most what the capture counted": the capture's `staged` equals the peak read from the segments (`stagedOf`), which lies above the final sum and below the segments' sum. The restore's own `staged` and `peak` equal the final sum and the peak, and so does the adopted row.
   - "… is counted once, its last line per key, though a segment's put is answered and lost and the lane starts again": a crash mid-capture.
   - "… fails a restore at its log step once what it stages passes LOG_BYTES, named, never admitting".
   - "maps migration 14's generations as staging their whole log, and fails a restore under way past index …". "maps a record of migration 13 unchanged through migration 14" now migrates to 14, its subject, not to the latest.
   - **Mutations,** each failing its test:
     - counting every segment's lines, the old count: the crash test;
     - the finish recording the final sum, not the peak: the written-through test;
     - begin refusing by the root's sum again: the large test, refused;
     - no failure past `LOG_BYTES` at the log step: the failure test, in 1 s. It holds the first segment's read, then refuses the next base part's, so a restore whose log step went on stops at `apply`, never admitted, and the test fails on the record. Before, it ran on to `done`, and the admitted object's manifest lane (due the next night) kept the failed run from ending (review: past 600 s);
     - `adopt` taking the root's sum: the large test (the owner is told none can be restored);
     - the migration leaving a restore under way: the migration test.
