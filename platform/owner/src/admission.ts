/**
 * The owner's admission (tasks 2, "Owner admission and erasure"; tasks 5, F1): one row in
 * the owner's own object, which is strongly consistent and on every path, so no door reads
 * it from anywhere else and the alarm reads it locally. The Python's `owner_admission`
 * record (`tinyassets/broker/admission.py`), ported:
 *
 * - `admitted`: the owner's object was made (migration 12 writes the row with the
 *   schema), or every suspension was lifted;
 * - `suspended`, for `billing`, `abuse` or both: the platform's word, and nothing works:
 *   every door refuses in the binding check (`OwnerDO`'s `#bind`), and so does every lane
 *   with the Python's `require_work` semantics, in its own unit: a scheduled launch's
 *   `deliver` (`scheduled.ts`, which ends the task terminal with the refusal) and the
 *   `wakes` lane's claim (`ledger.ts`), whose lane then has no job for its open rows;
 * - `erased`: the tombstone the owner's erasure leaves as its last step, written after
 *   the wipe in the same synchronous turn (`erase.ts`, `WriteGate.wipe`): exactly this one
 *   row in an otherwise empty database. A stale `getByName` then finds it before anything
 *   else runs: the object migrates nothing, starts no Lifecycle, and every door refuses
 *   `OWNER_ERASED`, so nothing ever makes owner state there again.
 *
 * Only the platform sets admission (`admissionSet`), as the reserved caller
 * `PLATFORM_CALLER`, which no owner may be named and which reaches no other door; a guard
 * keeps the name out of every source but this one's constant and `platform/admin`
 * (`admission-caller.test.ts`). A suspension adds a reason, a resumption takes one away,
 * and an erased owner is never set again.
 */
import { wiped } from "./erasure";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { ADMISSION_TABLE } from "./schema";

/** The reserved caller only the platform's admin path sends: `admissionSet`'s alone. */
export const PLATFORM_CALLER = "platform";
/** Why the platform suspends an owner. */
export const SUSPENSIONS: readonly string[] = Object.freeze(["abuse", "billing"]);

/** The refusals, each fixed: what a door or a `require_work` lane answers. */
export const OWNER_SUSPENDED = "owner_suspended: the owner's account is suspended";
export const OWNER_ERASED = "owner_erased: the owner's account was erased";
export const PLATFORM_ONLY = "only the platform sets an owner's admission";
export const PLATFORM_RESERVED = "the platform caller reaches no owner door but admission";

/** The owner's admission as its row holds it. */
export interface Admission {
  readonly state: "admitted" | "suspended" | "erased";
  /** Why it is suspended, sorted; empty unless it is. */
  readonly reasons: readonly string[];
}

/** The owner's admission row. A read; the row is the schema's, so its absence is a bug. */
export function admission(storage: OwnerStorage): Admission {
  const row = storage.sql.exec<{ state: Admission["state"]; reasons: string }>(
    "SELECT state, reasons FROM owner_admission").toArray()[0];
  if (row === undefined) {
    throw new Error("the owner's admission row is missing");
  }
  return { state: row.state, reasons: JSON.parse(row.reasons) as string[] };
}

/** The refusal `require_work` answers for `admission`, or null when the owner is admitted. */
export function workRefusal(state: Admission): string | null {
  return state.state === "admitted" ? null
    : state.state === "erased" ? OWNER_ERASED
      : `${OWNER_SUSPENDED} (${state.reasons.join(", ")})`;
}

/** The Python's `require_work`: refused loudly unless the owner is admitted. A read. */
export function requireWork(storage: OwnerStorage): void {
  const refusal = workRefusal(admission(storage));
  if (refusal !== null) {
    throw new Refused(refusal);
  }
}

/**
 * In a unit: the platform suspends (`suspended` true) or resumes the owner for `reason`;
 * the admission as it then stands. The owner is suspended while any reason holds. An erased
 * owner never reaches here: its object holds only the tombstone, which the binding check
 * refuses before any door acts.
 */
export function setAdmission(storage: OwnerStorage, request: unknown): Admission {
  if (typeof request !== "object" || request === null || Array.isArray(request)
      || Object.keys(request).sort().join() !== "reason,suspended") {
    throw new Invalid("an admission change is a reason and whether it suspends");
  }
  const { reason, suspended } = request as Record<string, unknown>;
  if (typeof reason !== "string" || !SUSPENSIONS.includes(reason)
      || typeof suspended !== "boolean") {
    throw new Invalid("an admission change is a reason (abuse or billing) and whether it " +
                      "suspends");
  }
  const reasons = new Set(admission(storage).reasons);
  if (suspended) {
    reasons.add(reason);
  } else {
    reasons.delete(reason);
  }
  const sorted = [...reasons].sort();
  const state = sorted.length === 0 ? "admitted" : "suspended";
  storage.sql.exec("UPDATE owner_admission SET state = ?, reasons = ?", state,
                   JSON.stringify(sorted));
  return { state, reasons: sorted };
}

/**
 * Whether this object's database is an erased owner's tombstone: its admission row says
 * `erased`. A read, safe on a database never migrated (its table is looked for first).
 */
export function tombstoned(sql: Pick<SqlStorage, "exec">): boolean {
  if (sql.exec("SELECT 1 FROM sqlite_master WHERE type = 'table' AND " +
                 "name = 'owner_admission'").toArray().length === 0) {
    return false;
  }
  return sql.exec<{ state: string }>("SELECT state FROM owner_admission").toArray()[0]
    ?.state === "erased";
}

/**
 * The erasure's tombstone (`WriteGate.wipe`): in a wiped database, the admission table made
 * again, holding one `erased` row, and nothing else. A database not wiped (its erasure mark
 * still there) is left alone, and one tombstoned already is not written again.
 */
export function writeTombstone(sql: Pick<SqlStorage, "exec">): void {
  if (!wiped(sql) || tombstoned(sql)) {
    return;
  }
  sql.exec(ADMISSION_TABLE);
  sql.exec("INSERT INTO owner_admission (only, state, reasons) VALUES (1, 'erased', '[]')");
}
