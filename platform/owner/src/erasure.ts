/**
 * Erasing an owner (design-decision §1.4 item 6; tasks 5, S4-S5; ADR-034), begun at
 * `ownerErase`. The order is what makes it complete:
 *
 * 1. **Mark the owner erasing** (`owner_erasure`), in one unit. From then on every door
 *    refuses (`OwnerDO`'s binding check) and no lane runs (the object's alarm resumes the
 *    erasure instead of driving Lifecycle).
 * 2. **The outside record** (slice L): the owner Worker's erasure queue tells the doors,
 *    which write the directory's erased record of the owner, delete its keys' and inbound
 *    routes' entries, revoke its every OAuth grant and delete its identity at the issuer,
 *    then confirm (`erasureOutsideDone`). Sent at the mark and again at each attempt until
 *    confirmed; the wipe waits for the confirmation, since the doors read the names from
 *    the object (`erasureOutside`). No outside step depends on the request that began the
 *    erasure: a half-done erasure always finishes.
 * 3. **End every run**: every sandbox of the owner killed, paused ones included, with the
 *    engines' sign-ins and session files inside them, and the sandbox Worker's own state of
 *    the owner deleted (`SandboxService.erase`, slice S); the sandbox's in-flight egress
 *    streams end (`Egress.drop`); and the step is recorded done (`step` 2).
 * 4. **Delete every R2 copy**: every object under the owner's namespace prefix, its files'
 *    and its placed bodies' (ADR-035) chunks and its generations (the nightly manifests and
 *    exports, slice P), once `PUT_GRACE_MS` (a day) has passed since the mark, its job waking
 *    it then, and every put this instance began has landed (no put begins under the mark, and
 *    the manifest lane is halted); repeatable, and repeated after the wipe. Each generation's
 *    root carries the owner's wrapped data key, so once they and the object's own copy are
 *    gone nothing holds it: a chunk the bucket still keeps is unreadable (crypto-erasure,
 *    `files/src/datakey.ts`).
 * 5. **`deleteAll()` the owner**, Lifecycle's alarm disabled first, so nothing is written
 *    after it but the tombstone: the erased admission row (`admission.ts`), written into the
 *    empty database in the same synchronous turn (`WriteGate.wipe`). A killed turn can lose
 *    the tombstone; the object a later `getByName` then makes is `pending` and refuses
 *    everything, and the directory's erased record keeps the platform from ever admitting
 *    the name again (`admission.ts`).
 *
 * Each step resumes after a crash, from the step the mark records: the erasure's job
 * resumes it on the object's alarm, every start of the object resumes it (so a crash after
 * step 3 disabled the alarm, or a wipe that failed, is finished at the next start), and so
 * does every door the mark refuses; every step is idempotent.
 *
 * After step 3 the instance answers nothing (its in-memory mark), and the database holds
 * the tombstone alone: an object a later `getByName` starts finds it before anything else,
 * migrates nothing, starts no Lifecycle and refuses every door (`OWNER_ERASED`), so
 * nothing ever makes owner state there again. The erasure door is the owner's
 * `ownerErase` (slice L), which marks the owner and leaves the rest to the job.
 */
import { ERASING, Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import type {
  Call, Door, OwnerState,
} from "./owner-do";

/**
 * How long after the wipe an erasure is complete: a SQLite Durable Object can be restored
 * "to any point in the past 30 days" (Cloudflare's SQLite storage API, point-in-time
 * recovery), and that copy holds the owner's wrapped data key. So the prefix deletes are the
 * erasure, and crypto-erasure (`files/src/datakey.ts`) its backstop, complete only once the
 * wipe is older than this window. The account-erase answer says so (`erasureAnswer`).
 */
export const RECOVERY_WINDOW_S = 30 * 86_400;

/**
 * What the account-erase door answers (slice L's door, which marks first and answers at
 * the mark): `erased` once the wipe ran (`wipedAt`, s), and, either way, `complete_at`: the
 * wipe's earliest time, the mark plus `PUT_GRACE_MS` (or the wipe itself, should a retry
 * have run it later), plus the platform's own point-in-time recovery. The contract slice L's
 * door answers with (tasks.md).
 */
export function erasureAnswer(markedAt: number, wipedAt: number | null):
    { erased: boolean; complete_at: number; note: string } {
  const wipe = Math.max(markedAt + PUT_GRACE_MS / 1000, wipedAt ?? 0);
  return { erased: wipedAt !== null, complete_at: wipe + RECOVERY_WINDOW_S,
           note: "Every door is closed and the owner's data is being deleted. A platform " +
             "recovery copy of the owner's database exists for 30 days after the deletion; " +
             "past complete_at, nothing of it remains." };
}

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
    throw new Refusal("erasing", ERASING);
  }
}

/** The step the owner's erasure recorded: 0 none, 1 marked, 2 every run ended. */
export function erasureStep(storage: OwnerStorage): 0 | 1 | 2 {
  return (storage.sql.exec<{ step: 1 | 2 }>("SELECT step FROM owner_erasure").toArray()[0]
    ?.step ?? 0);
}

/** When the owner was marked erasing, in seconds; a read, the mark standing. */
export function erasureStartedAt(storage: OwnerStorage): number {
  return storage.sql.exec<{ started_at: number }>("SELECT started_at FROM owner_erasure")
    .one().started_at;
}

/**
 * How long the erasure holds its mark before its R2 step and its wipe, so a put an earlier
 * instance of the object began before the mark has landed by then: a day, since no request
 * into the object outlives the longest one a door holds open, an egress stream's day
 * (`EGRESS_DEADLINE_S`), whatever R2's put latency (not documented; to measure live). Under
 * the mark every door refuses and no put begins; the erasure's own Lifecycle job wakes it
 * when the day is up (review rvlim N5), so nothing rests on an in-memory wait.
 */
export const PUT_GRACE_MS = 24 * 60 * 60_000;

/** Whether the doors confirmed the erasure's outside record (step 2). A read. */
export function outsideDone(storage: OwnerStorage): boolean {
  return storage.sql.exec<{ outside: number }>("SELECT outside FROM owner_erasure")
    .toArray()[0]?.outside === 1;
}

/** In a unit: the doors' confirmation of the outside record. */
function markOutside(storage: OwnerStorage): void {
  storage.sql.exec("UPDATE owner_erasure SET outside = 1");
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

/** An erasure door's refusal when no erasure is under way. */
export const NO_ERASURE = "no erasure of this owner is under way";

/** What the owner types to erase its account. */
export const ERASE_CONFIRM = "erase my account";

function erasureConfirmed(r: Record<string, unknown>): void {
  if (r["confirm"] !== ERASE_CONFIRM) {
    throw new Refusal("invalid", `an erasure is confirmed by ${JSON.stringify(ERASE_CONFIRM)}`);
  }
}

export interface EraseRequest {
  readonly confirm: string;
}

// -- THE DOORS: the `erasure` rows' handlers (`contract/doors.json`) ------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `erasure` doors' handlers, by door. */
export const erasureDoors = Object.freeze({
  async ownerErase(o: OwnerState, door: Door<EraseRequest>): Promise<{ erasing: true }> {
    const confirmed = await o.door(() => door.within(({ r }) => {
      erasureConfirmed(r);
      return true;
    }));
    if (!confirmed) {
      throw new Error("an erasure's widening returned without confirming it");
    }
    await o.erasure.begin();
    return { erasing: true as const };
  },
  erasureOutside(o: OwnerState, { erased }: Call<undefined>):
      { keys: string[]; routes: string[] } | null {
    return erased ? null : { keys: o.keys.ids(), routes: o.inbound.routes() };
  },
  erasureOutsideDone(o: OwnerState, { erased }: Call<undefined>): { confirmed: true } {
    if (erased) {
      return { confirmed: true } as const;
    }
    markOutside(o.storage);
    o.erasure.resume();
    return { confirmed: true };
  },
});
