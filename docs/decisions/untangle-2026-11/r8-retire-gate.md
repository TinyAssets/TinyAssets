# r8-retire-gate as built (2026-11-15)

Lane R's owed item from the rotation driver (`r7-rotation-driver.md` item 11): a restore from the off-account copy between a `FILES_KEYRING` rotation's pass 1 and `retire`'s gate puts roots under the old kid back in R2; the next night uploads them again with a fresh `KEEP_DAYS`, and a later pass rewraps them in R2 only, so `retire` could pass while the copy still holds them. Researched first, as its own step (Fact 14), on `origin/main` at `e51470584`.

1. **What main holds.**
   - **The driver (#5629).** `rotate` (`deploy/operator.ts`) keeps `deploy:rotation:<keyring>:<active kid>` in the control namespace, its passes `{began, through, owing, swept}`, each time an operator answer's `done`. `retirement` (`deploy/deploy.ts`) held a copied ring to a second pass begun `KEEP_DAYS` + 1 days after T, `offsite.ts` `firstNightAfter` of pass 1's `swept`. Only `FILES_KEYRING` is copied (`platform.json` `keyrings.rotated`).
   - **The copy (`deploy/offsite.ts`, R4).** A nightly sync of the files bucket into B2, each version locked `KEEP_DAYS` (22) from its upload and deleted by the lifecycle 22 days after it is hidden or replaced. With `--use-server-modtime`, an object written again in R2 is uploaded again as a new current version, under a new 22-day lock.
   - **R8, the restore from the copy: not on main.** Nothing writes R2 from the copy. `--b2-version-at` appears only in comments (`offsite.ts`, `checklist.md`), and `checklist.md` says "A restore from the copy is a later step of its own". Its plan (`operator_research.md` §4.6 and §6 row 8): a byte copy of `o/<ns>/` back to the same keys with the B2 read key and an R2 write key minted for a day, the copy back in the operator workflow (now `platform-deploy.yml`), and a monthly drill.
2. **What the gate must read once R8 exists: one time, the latest restore from the copy.** After a pass swept, nothing else puts a root under a retiring kid back in R2:
   - every root a pass or a rewrap writes is under the active kid (`r7-sweep.md` item 7);
   - a restore's hold counts in `left`, so no pass sweeps past it (`r7-rotation.md` item 3);
   - a restore inside the account loads a generation the pass already rewrapped;
   - the import writes through the object's write path and is refused during the window.
   - A generation holds no gateway blob (`owner/src/restore.ts`: connections' secrets are lost and connected again), so `GATEWAY_KEYRING` has no such path.

   So the fix is a rule on a record alone: the gate reads when the latest write from the copy into R2 began.
3. **The shapes weighed.**
   - **Refuse to restore during a rotation: rejected.** A `FILES_KEYRING` rotation lasts more than 23 days (a pass, the next night, then 23 days), and the copy keeps a hidden version 22 days. A mistaken deletion found early in a rotation could not be restored until its versions were gone, so its data would be lost. After an account loss the control namespace is gone too, so the refusal would have nothing to read.
   - **Mark the rotation's record: rejected.** R8 would have to find every rotation record (one per keyring and active kid), parse and rewrite another module's record, and race a rotation run writing the same key.
   - **Read R8's workflow runs from GitHub, as the nights are read: rejected.** It names a workflow and job nothing defines yet.
   - **List the copy's root versions and read each one's kid: rejected.** The deploy job would need the B2 keys and a read of every root version of every owner. The time gate is exact while each night holds the bucket's lock and lifecycle (`held`).
   - **Chosen: the copy's own restore record.** `deploy:offsite:restore`, `{kind, began}` in seconds, in the control namespace (`offsite.ts` `RESTORE_KEY`, read by `restoreBegan`). R8 writes it before its first byte into R2, knowing nothing of rotations, and `retire` reads it.
     - R8 runs in the kit's concurrency group (`platform-deploy`), so no rotation run interleaves with it. One write before the first byte then covers the whole restore: a pass begun after it asks every owner after the last byte landed, and a crash after some bytes still leaves the record.
     - The drill counts if it writes R2 from the copy. Every such write counts.
     - After an account loss, the record and every rotation record go with the old account's KV. R8 writes the record into the new control namespace before its first byte, and any rotation there begins after it.
4. **The predicate, `retirement`.** The latest pass swept. For a copied ring:
   - the copy is not overdue;
   - the settled pass is the first that swept and began at or after the restore record's `began` (with none recorded, the first that swept);
   - T is `firstNightAfter` the settled pass's `swept`;
   - now is at least T + `KEEP_DAYS` + 1 days, or every copy job was skipped and therefore
     never began. A failed copy may have uploaded objects before it stopped. Retire shares the
     copy job's concurrency group, so no copy can still be uploading while it reads this.

   Why that is enough:
   - The settled pass left no root in R2 under a retiring kid.
   - T, listing R2 after it, replaced each root the pass rewrapped and hid each one removed since, a restored root a later prune took included. So every version under a retiring kid is gone by T + 23 days.
   - Item 2 says nothing else brings one back, and a later restore moves the settled pass.
   - With no settled pass, `retire` refuses, naming the restore and asking for a pass begun after it.
5. **The second pass goes.** It had one reason, an R8 restore landing between pass 1 and the gate (`r7-rotation.md` item 3). Item 11 showed it does not cover that, since the copy uploads the restored roots again. The record now does. Without a restore, a pass after the gate finds nothing: R2 has held no root under a retiring kid since the settled pass, and after the gate the copy holds none either.
   - So `FILES_KEYRING` retires 23 days after the night that follows one pass, like `GATEWAY_KEYRING` after one pass, plus the copy's wait. `checklist.md` step 3 says so.
   - The cost: a restore from the copy restarts the wait even if it brought nothing back under the old kid. The record says when, not what. Knowing what would mean reading the kid of every root restored, and restores are rare.
6. **Found and fixed in `retirement`.**
   - The settled pass was found twice: `retireAsk` found pass 1 to read its night, and `retirement` found it again to judge the gate. Nothing tied the two finds together. Now `retirement` finds it once and asks `copy.nightAfter` for its night.
   - `copied` and `copy` carried one fact twice, with a throw for when they disagreed ("its copy's nights were not read"). Now `copy` is null exactly when the copy never holds the ring.
   - `copy.night` was null before any pass swept. That case is gone too.
   - Three tests each faked GitHub's record of the copy's nights: `canary.test.ts`, `rotation.test.ts` and the new `retire` test. They now share one fake, `deploy/guards/copy-nights.ts`.
7. **Owed: R8, `untangle/r8-copy-restore` (`tasks.md`).** Every write from the copy into R2 puts the record before its first byte and runs in the kit's concurrency group, and every erasure the copy can't show is applied first (`r4.md` item 13). A copy back by hand records nothing, so R8's steps never tell the founder to copy back by hand, as research §4.6 step 1 did, and `checklist.md` forbids it meanwhile. R8 adds the record's writer beside `restoreBegan`.
8. **The proof.** Fakes and fixed times only; nothing live.
   - `rotation.test.ts`, `retirement`:
     - one pass and 23 days after its next night hold;
     - a restore before that pass began changes nothing;
     - a later pass with no restore moves no gate;
     - a restore after the pass began refuses, naming it;
     - pass 2 begun after the restore is held to its own night's gate, and holds from it;
     - every other refusal as before.
   - `deploy.test.ts`: mode `retire` of `FILES_KEYRING` names its words, then refuses once the control namespace holds a restore record after the pass began, then stops loudly on a record out of shape, with no secret set.
   - **Mutations**, each failing its test:
     - the settled pass not held to the restore (`rotation.test.ts`, the restore case; `deploy.test.ts`);
     - the second pass required again (`rotation.test.ts`, "holds once a pass swept");
     - `retireAsk` not reading the record (`deploy.test.ts`).
