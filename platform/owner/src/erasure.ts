/**
 * Erasing an owner (design-decision §1.4 item 6; tasks 5, S4-S5): the internal path the
 * erasure door (tasks 7) will call. The order is what makes it complete:
 *
 * 1. **Mark the owner erasing** (`owner_erasure`), in one unit that also marks every thread
 *    `dropping`. From then on every door refuses (`OwnerDO`'s binding check), no lane or
 *    wake job runs (the object's alarm resumes the erasure instead of driving Lifecycle),
 *    no thread is recorded (`Threads.record` reads the mark in its own unit) and no Session
 *    opens (the owner never opens a dropping thread).
 * 2. **Destroy every store a row names**, each through the one drop path
 *    (`OwnerAgent.drop`): its Session ended and closed, its store wiped, then its row
 *    forgotten, so a crash leaves only rows whose stores are still to wipe.
 * 3. **`deleteAll()` the owner**, its alarm with it.
 *
 * A namespace's objects cannot be listed, so a store whose name no row holds could never be
 * found again. Every store is named in its row before it is made, in the same unit that
 * reads this mark, so after the mark no store is made that no row names, and every store a
 * thread still being made opens is named by a row step 2 reads (its open, if in flight, is
 * awaited before its store is destroyed). Each step resumes after a crash: the mark is kept,
 * the alarm that resumes it is set again at each attempt and at each start, and steps 2
 * and 3 are idempotent.
 *
 * After step 3 the instance answers nothing (its in-memory mark): an object made again by
 * a later call is a new, empty owner. Keeping a stale binder from making it (a tombstone)
 * is the admission row's, with the erasure door (tasks 7).
 */
import { Refused } from "./errors";
import type { OwnerStorage } from "./gate";

/** What every door, lane and new thread answers while the owner is erased. */
export const ERASING = "this owner is being erased";
/** How long an erasure waits before it is tried again, should an attempt fail. */
export const ERASE_RETRY_MS = 60_000;

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

/**
 * In a unit: mark the owner erasing, once (a resumed erasure keeps its start). The unit
 * marks every thread `dropping` too (`Threads.dropAll`), so nothing opens or resolves one
 * again.
 */
export function markErasing(storage: OwnerStorage, now: number): void {
  storage.sql.exec("INSERT INTO owner_erasure (only, started_at) VALUES (1, ?) " +
                     "ON CONFLICT (only) DO NOTHING", now);
}
