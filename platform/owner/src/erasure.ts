/**
 * Erasing an owner (design-decision §1.4 item 6; tasks 5, S4-S5): the internal path the
 * erasure door (slice 10) will call. The order is what makes it complete:
 *
 * 1. **Mark the owner erasing** (`owner_erasure`), in one unit that also marks every thread
 *    `dropping`. From then on every door refuses (`OwnerDO`'s binding check), no lane or
 *    wake job runs (the object's alarm resumes the erasure instead of driving Lifecycle),
 *    no thread is recorded (`Threads.record` reads the mark in its own unit) and no Session
 *    opens (the owner never opens a dropping thread).
 * 2. **Destroy every store a row names**, each through the one drop path
 *    (`OwnerAgent.drop`): its Session ended and closed, its store wiped, then its row
 *    forgotten, so a crash leaves only rows whose stores are still to wipe. Each drop is
 *    bounded, and the erasure goes on to the next thread past one that fails; the step is
 *    recorded done (`step` 2) only once no row is left.
 * 3. **`deleteAll()` the owner**, Lifecycle's alarm disabled first, so nothing is written
 *    after it but the tombstone: the erased admission row (`admission.ts`), written into the
 *    empty database in the same synchronous turn (`WriteGate.wipe`); a killed turn can
 *    lose it (the window `erase.ts` names, slice 10's to close).
 *
 * A namespace's objects cannot be listed, so a store whose name no row holds could never be
 * found again. Every store is named in its row before it is made, in the same unit that
 * reads this mark, so after the mark no store is made that no row names, and every store a
 * thread still being made opens is named by a row step 2 reads (its open, if in flight, is
 * awaited before its store is destroyed). Each step resumes after a crash, from the step
 * the mark records: the erasure's job resumes it on the object's alarm, every start of the
 * object resumes it (so a crash after step 3 disabled the alarm, or a wipe that failed,
 * is finished at the next start), and so does every door the mark refuses; every step is
 * idempotent.
 *
 * After step 3 the instance answers nothing (its in-memory mark), and the database holds
 * the tombstone alone: an object a later `getByName` starts finds it before anything else,
 * migrates nothing, starts no Lifecycle and refuses every door (`OWNER_ERASED`), so
 * nothing ever makes owner state there again. The erasure door is slice 10's.
 */
import { Refused } from "./errors";
import type { OwnerStorage } from "./gate";

/** What every door, lane and new thread answers while the owner is erased. */
export const ERASING = "this owner is being erased";
/** How long an erasure waits before it is tried again, should an attempt fail. */
export const ERASE_RETRY_MS = 60_000;

/**
 * Whether the owner's database was wiped: its erasure's mark, which only the wipe takes
 * away, is gone. A read, safe on a wiped database (the mark's table is looked for).
 */
export function wiped(sql: Pick<SqlStorage, "exec">): boolean {
  return sql.exec("SELECT 1 FROM sqlite_master WHERE type = 'table' AND " +
                    "name = 'owner_erasure'").toArray().length === 0;
}

/** Whether the owner is marked erasing. A read. */
export function erasing(storage: OwnerStorage): boolean {
  return storage.sql.exec("SELECT 1 FROM owner_erasure").toArray().length > 0;
}

/** Refuse, loudly, while the owner is marked erasing. A read. */
export function refuseErasing(storage: OwnerStorage): void {
  if (erasing(storage)) {
    throw new Refused(ERASING);
  }
}

/** The step the owner's erasure recorded: 0 none, 1 marked, 2 every store destroyed. */
export function erasureStep(storage: OwnerStorage): 0 | 1 | 2 {
  return (storage.sql.exec<{ step: 1 | 2 }>("SELECT step FROM owner_erasure").toArray()[0]
    ?.step ?? 0);
}

/** In a unit: the erasure's step 2 done: no row names a store any more. */
export function storesDestroyed(storage: OwnerStorage): void {
  storage.sql.exec("UPDATE owner_erasure SET step = 2");
}

/**
 * In a unit: mark the owner erasing, once (a resumed erasure keeps its start). The unit
 * marks every thread `dropping` too (`Threads.dropAll`), so nothing opens or resolves one
 * again.
 */
export function markErasing(storage: OwnerStorage, now: number): void {
  storage.sql.exec("INSERT INTO owner_erasure (only, started_at) VALUES (1, ?) " +
                     "ON CONFLICT (only) DO NOTHING", now);
}
