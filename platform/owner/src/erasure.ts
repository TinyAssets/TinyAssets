/**
 * Erasing an owner (design-decision §1.4 item 6; tasks 5, S4-S5; ADR-034): the internal
 * path the erasure door (slice 10) will call. The order is what makes it complete:
 *
 * 1. **Mark the owner erasing** (`owner_erasure`), in one unit. From then on every door
 *    refuses (`OwnerDO`'s binding check) and no lane runs (the object's alarm resumes the
 *    erasure instead of driving Lifecycle).
 * 2. **End every run**: the sandbox's in-flight egress streams end (`Egress.drop`), and the
 *    step is recorded done (`step` 2). The runs and sandbox slices (R, S) add the sandbox's
 *    kill and the engines' session files' purge to this step (ADR-034).
 * 3. **`deleteAll()` the owner**, Lifecycle's alarm disabled first, so nothing is written
 *    after it but the tombstone: the erased admission row (`admission.ts`), written into the
 *    empty database in the same synchronous turn (`WriteGate.wipe`); a killed turn can
 *    lose it (the window `erase.ts` names, not measured in production; slice 10's to
 *    close).
 *
 * Each step resumes after a crash, from the step the mark records: the erasure's job
 * resumes it on the object's alarm, every start of the object resumes it (so a crash after
 * step 3 disabled the alarm, or a wipe that failed, is finished at the next start), and so
 * does every door the mark refuses; every step is idempotent.
 *
 * After step 3 the instance answers nothing (its in-memory mark), and the database holds
 * the tombstone alone: an object a later `getByName` starts finds it before anything else,
 * migrates nothing, starts no Lifecycle and refuses every door (`OWNER_ERASED`), so
 * nothing ever makes owner state there again. The erasure door is slice 10's.
 */
import { Refused } from "./errors";
import type { OwnerStorage } from "./gate";

/** What every door and lane answers while the owner is erased. */
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

/** The step the owner's erasure recorded: 0 none, 1 marked, 2 every run ended. */
export function erasureStep(storage: OwnerStorage): 0 | 1 | 2 {
  return (storage.sql.exec<{ step: 1 | 2 }>("SELECT step FROM owner_erasure").toArray()[0]
    ?.step ?? 0);
}

/** In a unit: the erasure's step 2 done: every run ended. */
export function runsEnded(storage: OwnerStorage): void {
  storage.sql.exec("UPDATE owner_erasure SET step = 2");
}

/** In a unit: mark the owner erasing, once (a resumed erasure keeps its start). */
export function markErasing(storage: OwnerStorage, now: number): void {
  storage.sql.exec("INSERT INTO owner_erasure (only, started_at) VALUES (1, ?) " +
                     "ON CONFLICT (only) DO NOTHING", now);
}
