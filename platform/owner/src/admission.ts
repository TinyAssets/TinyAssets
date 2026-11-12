/**
 * The owner's admission (tasks 2, "Owner admission and erasure"; tasks 5, F1): one row in
 * the owner's own object, which is strongly consistent and on every path, so no door reads
 * it from anywhere else and the alarm reads it locally. The Python's `owner_admission`
 * record (`tinyassets/broker/admission.py`), ported:
 *
 * - `pending`: the owner's object was made (migration 10 writes the row with the schema)
 *   and the platform has not admitted it: every door and lane refuses (`OWNER_UNADMITTED`).
 *   Any `getByName` makes an object, so being made never admits it: only the platform's
 *   explicit admit at sign-up does (`admissionSet` with `{admit: true}`, which the doors'
 *   sign-in callback sends when the owner directory holds no record of the owner, slice
 *   L). A stale `getByName` of a wiped owner whose tombstone a crash lost therefore makes
 *   an object that refuses everything, and the directory's erased record keeps the
 *   platform from ever admitting that name again: `erase.ts`'s window, closed;
 * - `admitted`: the platform admitted the owner, or every suspension was lifted;
 * - `suspended`, for `billing`, `abuse` or both: the platform's word, and no work runs.
 *   Each door declares its class (`DoorClass`) in its binding check (`OwnerDO`'s `#bind`):
 *   a billing suspension refuses `work` (spend and growth) and leaves the owner its reads,
 *   sign-in and what only lowers risk or storage; an abuse suspension refuses every door.
 *   Work is refused where it is enforced, not only where it starts: every authority check
 *   (`Authority`), every sandbox lease and egress call (`Launcher.openLease`,
 *   `egressOpen`), and every lane with the Python's `require_work` semantics in its own
 *   unit: the `wakes` lane's claim (`ledger.ts`), whose lane then has no job for its open
 *   rows. A suspension also ends the sandbox's in-flight egress streams, the prompt stop;
 * - `erased`: the tombstone the owner's erasure leaves as its last step, written after
 *   the wipe in the same synchronous turn (`erase.ts`, `WriteGate.wipe`): exactly this one
 *   row in an otherwise empty database. A stale `getByName` then finds it before anything
 *   else runs: the object migrates nothing, starts no Lifecycle, and every door refuses
 *   `OWNER_ERASED`, so nothing ever makes owner state there again.
 *
 * Only the platform sets admission (`admissionSet`), as the reserved caller
 * `PLATFORM_CALLER` (`contract/src/binding.ts`), which no owner may be named and which
 * reaches no other door; a guard keeps the name out of every source but that constant
 * (`admission-caller.test.ts`). A suspension adds a reason, a resumption takes one away,
 * and an erased owner is never set again.
 */
import type { DoorClass as TableClass } from "../../contract/src/doors";
import { wiped } from "./erasure";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { ADMISSION_TABLE } from "./schema";
import type { TierName } from "../../contract/src/control";
import type { AdmissionRequest } from "../../contract/src/owner";
import { tierName } from "./tier";
import type {
  Door, OwnerState,
} from "./owner-do";
import { clock } from "./clock";

/** Why the platform suspends an owner. */
export const SUSPENSIONS: readonly string[] = Object.freeze(["abuse", "billing"]);

/** The refusals, each fixed: what a door or a `require_work` lane answers. */
export const OWNER_SUSPENDED = "owner_suspended: the owner's account is suspended";
export const OWNER_ERASED = "owner_erased: the owner's account was erased";
export const OWNER_UNADMITTED = "owner_unadmitted: the platform has not admitted this owner";
const NEVER_ADMITTED = "an owner the platform never admitted is not suspended";
export const PLATFORM_ONLY = "only the platform sets an owner's admission";
export const PLATFORM_RESERVED = "the platform caller reaches no owner door but admission";
/** The import doors' refusals (`import.ts`): their caller, their binding, the owner's state. */
export const IMPORT_PLATFORM_ONLY =
  "only the platform imports, with an import binding";
export const IMPORT_DOORS_ONLY = "an import binding reaches the import doors alone";
export const IMPORT_PENDING_ONLY = "only a pending owner is imported into";
/** What a discarded instance answers every door: its database is gone (`importDiscard`). */
export const DISCARDED =
  "this object's owner was discarded for the import; it starts afresh once the runtime " +
  "starts it again";

/** The owner's admission as its row holds it. */
export interface Admission {
  readonly state: "pending" | "admitted" | "suspended" | "erased";
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
export function workRefusal(state: Admission): Refusal | null {
  return state.state === "admitted" ? null
    : state.state === "erased" ? new Refusal("owner_erased", OWNER_ERASED)
      : state.state === "pending" ? new Refusal("owner_unadmitted", OWNER_UNADMITTED)
        : new Refusal("owner_suspended", `${OWNER_SUSPENDED} (${state.reasons.join(", ")})`);
}

/** Whether the owner is admitted now: `require_work` refuses it nothing. A read. */
export function isAdmitted(storage: OwnerStorage): boolean {
  return workRefusal(admission(storage)) === null;
}

/**
 * What a door does, which decides what a suspension leaves it: `work` spends or grows
 * (a message, a launch, a write that adds), `read` only reads (and signs in), `reduce` only
 * lowers risk or storage (a cancel, a revoke, a delete or purge). Every door's row names its
 * class (`contract/doors.json`); the platform's own doors' rows name `admission`, read by none,
 * and the cutover's import doors `import`, taken only while the owner is `pending` (`#bind`).
 */
export type DoorClass = Exclude<TableClass, "admission" | "import">;

/**
 * Why a door of class `door` is refused under `state`, or null. A `billing` suspension
 * refuses work and spend only, so the owner can still sign in, read and export, and lower
 * its risk and storage; an `abuse` suspension refuses every door (export and erasure go
 * through the platform alone); an erased owner refuses everything.
 */
export function doorRefusal(state: Admission, door: DoorClass): Refusal | null {
  if (state.state === "suspended" && door !== "work" && !state.reasons.includes("abuse")) {
    return null;
  }
  return workRefusal(state);
}

/**
 * In a unit: the platform admits a pending owner (`{admit: true}`, at sign-up; an owner
 * admitted or suspended already is left as it stands, so a repeated sign-up admits nothing
 * twice and lifts no suspension), or suspends (`suspended` true) or resumes it for
 * `reason`; the admission as it then stands. The owner is suspended while any reason
 * holds; a pending owner is never suspended (there is nothing yet to suspend). An erased
 * owner never reaches here: its object holds only the tombstone, which the binding check
 * refuses before any door acts.
 */
function setAdmission(storage: OwnerStorage, request: unknown): Admission {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Refusal("invalid", CHANGE);
  }
  const keys = Object.keys(request).sort().join();
  const now = admission(storage);
  if (keys === "admit") {
    if ((request as Record<string, unknown>)["admit"] !== true) {
      throw new Refusal("invalid", "an admit is {admit: true}");
    }
    if (now.state !== "pending") {
      return now;
    }
    storage.sql.exec("UPDATE owner_admission SET state = 'admitted'");
    return { state: "admitted", reasons: [] };
  }
  if (keys !== "reason,suspended") {
    throw new Refusal("invalid", CHANGE);
  }
  const { reason, suspended } = request as Record<string, unknown>;
  if (typeof reason !== "string" || !SUSPENSIONS.includes(reason)
      || typeof suspended !== "boolean") {
    throw new Refusal("invalid",
                      "an admission change is a reason (abuse or billing) and whether it " +
                      "suspends");
  }
  if (now.state === "pending") {
    throw new Refusal("refused", NEVER_ADMITTED);
  }
  const reasons = new Set(now.reasons);
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

const CHANGE = "an admission change is an admit, or a reason and whether it suspends";

/** The owner's tier (`tier.ts`), from its own admission row, which only the platform sets. */
export function ownerTier(storage: OwnerStorage): TierName {
  const row = storage.sql.exec<{ tier: TierName }>("SELECT tier FROM owner_admission")
    .toArray()[0];
  if (row === undefined) {
    throw new Error("the owner's admission row is missing");
  }
  return row.tier;
}

/** In a unit: the platform sets the owner's tier (`tierSet`). */
export function setTier(storage: OwnerStorage, request: unknown): { tier: TierName } {
  const tier = tierName(request);
  storage.sql.exec("UPDATE owner_admission SET tier = ?", tier);
  return { tier };
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

// -- THE DOORS: the `admission` rows' handlers (`contract/doors.json`) ----------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `admission` doors' handlers, by door. */
export const admissionDoors = Object.freeze({
  async admissionSet(o: OwnerState, door: Door<AdmissionRequest>): Promise<Admission> {
    const set = await o.synced(() => {
      door.check();
      return setAdmission(o.storage, door.request);
    });
    if (set.state === "suspended") {
      // The prompt stop: its streams, and its runs' CLIs (each settles from its runner).
      await o.door(() => {
        o.egress.end("admission");
        o.launcher.stop(o.runs.endAll(clock()));
      });
    }
    if (doorRefusal(set, "read") !== null) {
      o.relay.closeAll("suspended");  // reads refused: an abuse suspension
    }
    return set;
  },
});
