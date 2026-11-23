/**
 * The owner's admission: one row in the owner's own object, which is strongly consistent and
 * on every path, so no door reads it from anywhere else and the alarm reads it locally:
 *
 * - `pending`: the owner's object was made (migration 10 writes the row with the schema)
 *   and the platform has not admitted it: every door and lane refuses (`OWNER_UNADMITTED`).
 *   Any `getByName` makes an object, so being made never admits it: only the platform's
 *   explicit admit at sign-up does (`admissionSet` with `{admit: true}`, which the doors'
 *   sign-in callback sends when the owner directory holds no record of the owner). A
 *   stale `getByName` of a wiped owner whose tombstone a crash lost therefore makes
 *   an object that refuses everything, and the directory's erased record keeps the
 *   platform from ever admitting that name again: `erase.ts`'s window, closed;
 * - `admitted`: the platform admitted the owner, or every suspension was lifted;
 * - `suspended`, for `billing`, `abuse` or both: the platform's word, and no work runs.
 *   Each door declares its class (`DoorClass`) in its binding check (`OwnerDO`'s `#bind`):
 *   a billing suspension refuses `work` (spend and growth) and leaves the owner its reads,
 *   sign-in and what only lowers risk or storage; an abuse suspension refuses every door.
 *   Work is refused where it is enforced, not only where it starts: every authority check
 *   (`Authority`), every sandbox lease and egress call (`Launcher.openLease`,
 *   `egressOpen`), and every lane that requires work, in its own
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
 *
 * **The tier** (ADR-034 decision 8): every tier gets the same product and the same security,
 * and tiers differ only by simultaneous runs and storage.
 *
 * - **Where the numbers live.** In the platform's control record (`contract/src/control.ts`,
 *   its `tiers`), which the founder edits with no deploy: `{ free: { runs, storage_bytes },
 *   paid: { runs, storage_bytes } }` (founder, 2026-11-02: free 5 runs and 25 GB, paid 10 and
 *   250 GB). No number is in code: a record without them refuses every run (`TIER_UNREAD`),
 *   never a default. The binder, which reads the record at every outside and owner call to
 *   gate it, hands the record read fresh to the doors in the binding (`Binding.control`),
 *   never a request field, so a door never waits on a read of its own. The object keeps one
 *   copy of the record (`control_kept`, `contract/src/control.ts` `SqlKeeper`), written at
 *   each bind that hands one and at each of its own reads (`control-gate.ts`: a code-mode
 *   script's tool calls, the `runs` lane's pass), and every run and file byte reads the numbers
 *   from it, the lane's runs too (a schedule's, a mail's). An edit reaches an owner's
 *   door-started runs at once, its lane's at the lane's next pass.
 * - **Where an owner's tier lives.** In the owner's own admission row (its `tier` column,
 *   `free` until set), which only the platform sets (`tierSet`, as the reserved
 *   `PLATFORM_CALLER`, the one caller `admissionSet` takes too). No client, request or
 *   binding names it: the object reads its own row.
 * - **What is held.** The tier's simultaneous runs, at admission, against the object's own
 *   live runs (`runs.ts`), and its storage, as the files byte quota's `max_bytes`
 *   (`policy.ts`'s `fileLimits`), which counts the owner's file chunks and the bodies placed
 *   in R2 (ADR-035), so the storage figure is the kept record's for every change.
 *
 * The contract's `RUNS_CEILING` is a technical ceiling, not a tier: the most live runs the
 * owner's database is costed for (the owner-bound guard counts each live run's rows at it),
 * so a record past it is malformed.
 */
import { keyed } from "../../contract/src/checks";
import { taken } from "../../contract/src/handed";
import type { DoorClass as TableClass } from "../../contract/src/doors";
import { wiped } from "./erasure";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import { holdingStep, refuseRestoring } from "./restore";
import { ADMISSION_TABLE } from "./schema";
import {
  controlOf, SqlKeeper, TIER_NAMES, type TierName, type Tiers,
} from "../../contract/src/control";
import {
  type AdmissionRequest, type OwnerStateAnswer, SUSPENSIONS, type TierRequest,
} from "../../contract/src/owner";
import {
  type Call, type Door, type DoorUnits, type ObjectReads, type PlatformLine, proven,
} from "./door";
import type { Egress } from "./egress";
import { type Files, TIER_UNREAD } from "./files";
import type { Launcher } from "./launch";
import type { Relay } from "./relay";
import type { Runs } from "./runs";
import { clock } from "./clock";

/** The refusals, each fixed: what a door or a `require_work` lane answers. */
export const OWNER_SUSPENDED = "owner_suspended: the owner's account is suspended";
export const OWNER_ERASED = "owner_erased: the owner's account was erased";
export const OWNER_UNADMITTED = "owner_unadmitted: the platform has not admitted this owner";
const NEVER_ADMITTED = "an owner the platform never admitted is not suspended";
export const PLATFORM_ONLY = "only the platform calls an owner's reserved doors";
export const PLATFORM_RESERVED = "the platform caller reaches no owner door but the reserved ones";
export const PLATFORM_TIER_ONLY = "only the platform sets an owner's tier";
/** The import doors' refusals (`import.ts`): their caller, their binding, the owner's state. */
export const IMPORT_PLATFORM_ONLY =
  "only the platform imports, with an import binding";
export const IMPORT_DOORS_ONLY = "an import binding reaches the import doors alone";
export const IMPORT_PENDING_ONLY = "only a pending owner is imported into";
/** What a discarded instance answers every door: its database is gone (`importDiscard`). */
export const DISCARDED =
  "this account was reset for its import and has not started again yet";

/** The owner's admission as its row holds it. */
export interface Admission {
  readonly state: "pending" | "admitted" | "suspended" | "erased";
  /** Why it is suspended, sorted; empty unless it is. */
  readonly reasons: readonly string[];
}

/** The refusal `require_work` answers for `admission`, or null when the owner is admitted. */
function workRefusal(state: Admission): Refusal | null {
  return state.state === "admitted" ? null
    : state.state === "erased" ? new Refusal("owner_erased", OWNER_ERASED)
      : state.state === "pending" ? new Refusal("owner_unadmitted", OWNER_UNADMITTED)
        : new Refusal("owner_suspended", `${OWNER_SUSPENDED} (${state.reasons.join(", ")})`);
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

const CHANGE = "an admission change is an admit, or a reason and whether it suspends";

/** A tier change's tier, or `invalid`. */
function tierName(request: unknown): TierName {
  if (!keyed(request, ...taken("tier_change"))
      || !(TIER_NAMES as readonly unknown[]).includes(request["tier"])) {
    throw new Refusal("invalid", "a tier change names free or paid");
  }
  return (request as { tier: TierName }).tier;
}

/** The owner's admission and tier, in its own row, and the kept record's tier numbers. */
export class OwnerAdmission {
  readonly #storage: OwnerStorage;

  constructor(storage: OwnerStorage) {
    this.#storage = storage;
  }

  /** The owner's admission row. A read; the row is the schema's, so its absence is a bug. */
  row(): Admission {
    const row = this.#storage.sql.exec<{ state: Admission["state"]; reasons: string }>(
      "SELECT state, reasons FROM owner_admission").toArray()[0];
    if (row === undefined) {
      throw new Error("the owner's admission row is missing");
    }
    return { state: row.state, reasons: JSON.parse(row.reasons) as string[] };
  }

  /** The owner's `require_work` refusal, or null while it is admitted. A read. */
  unadmitted(): Refusal | null {
    return workRefusal(this.row());
  }

  /** Whether the owner is admitted now: `require_work` refuses it nothing. A read. */
  admitted(): boolean {
    return this.unadmitted() === null;
  }

  /** The owner's tier, from its own admission row, which only the platform sets. A read. */
  tier(): TierName {
    const row = this.#storage.sql.exec<{ tier: TierName }>("SELECT tier FROM owner_admission")
      .toArray()[0];
    if (row === undefined) {
      throw new Error("the owner's admission row is missing");
    }
    return row.tier;
  }

  /**
   * The tier numbers of the control record the object keeps (`control_kept`), or null when it
   * keeps none or the record holds none. A read.
   */
  tiers(): Tiers | null {
    const kept = new SqlKeeper(this.#storage.sql).kept();
    return kept === null ? null : controlOf(kept.text)?.tiers ?? null;
  }

  /** The kept tier numbers, refused when there are none: no run is admitted without them. */
  numbers(): Tiers {
    const tiers = this.tiers();
    if (tiers === null) {
      throw new Refusal("tier_unread", TIER_UNREAD);
    }
    return tiers;
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
  set(platform: PlatformLine, request: unknown): Admission {
    proven(platform, "reserved");
    const now = this.row();
    const sql = this.#storage.sql;
    if (keyed(request, ...taken("admission_admit"))) {
      if (request["admit"] !== true) {
        throw new Refusal("invalid", "an admit is {admit: true}");
      }
      if (now.state !== "pending") {
        return now;
      }
      sql.exec("UPDATE owner_admission SET state = 'admitted'");
      return { state: "admitted", reasons: [] };
    }
    if (!keyed(request, ...taken("admission_change"))) {
      throw new Refusal("invalid", CHANGE);
    }
    const { reason, suspended } = request;
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
    sql.exec("UPDATE owner_admission SET state = ?, reasons = ?", state, JSON.stringify(sorted));
    return { state, reasons: sorted };
  }

  /** In a unit: the platform sets the owner's tier (`tierSet`). */
  setTier(platform: PlatformLine, request: unknown): { tier: TierName } {
    proven(platform, "reserved");
    const tier = tierName(request);
    this.#storage.sql.exec("UPDATE owner_admission SET tier = ?", tier);
    return { tier };
  }
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

// -- THE DOORS: the `admission` rows' handlers (`owner/doors.json`) -------------------------------

/**
 * What the `admission` doors reach: the admission, what a suspension ends (the sandbox's
 * egress, the runs, the app sockets), and what the owner's state counts.
 */
export interface AdmissionDoorNeeds extends Pick<DoorUnits, "door" | "synced">,
                                            Pick<ObjectReads, "storage"> {
  readonly admission: OwnerAdmission;
  readonly egress: Pick<Egress, "end">;
  readonly launcher: Pick<Launcher, "endAll">;
  readonly runs: Pick<Runs, "live">;
  readonly relay: Pick<Relay, "closeAll">;
  readonly files: Pick<Files, "storedNow">;
}

export const admissionDoors = Object.freeze({
  /**
   * The platform suspends the owner for a reason (`billing`, `abuse`), or lifts it
   * (`admission.ts`): taken only from the reserved `PLATFORM_CALLER`, with no outside client
   * and no session, whatever the owner's admission is now; an erased owner is never set
   * again. A suspension then ends the sandbox's in-flight egress streams: the prompt stop,
   * since every lease, egress call and wake claim is refused while it lasts. Lifting one
   * gives the `wakes` lane its job back.
   */
  async admissionSet(o: AdmissionDoorNeeds, door: Door<AdmissionRequest, "reserved">):
      Promise<Admission> {
    const set = await o.synced(() => {
      const { platform } = door.check();
      refuseRestoring(o.storage);
      return o.admission.set(platform, door.request);
    });
    if (set.state === "suspended") {
      // The prompt stop: its streams, and its runs' CLIs (each settles from its runner).
      await o.door(() => {
        o.egress.end("admission");
        o.launcher.endAll(clock());
      });
    }
    if (doorRefusal(set, "read") !== null) {
      o.relay.closeAll("suspended");  // reads refused: an abuse suspension
    }
    return set;
  },
  /**
   * The owner's state, for the platform's operator (`owner.state`): its admission, tier and
   * restore hold, and two counts. A read in one unit, so it changes nothing; an owner being
   * erased, or erased, is refused it by the binding check, as at every door.
   */
  async ownerState(o: AdmissionDoorNeeds, door: Door<undefined>): Promise<OwnerStateAnswer> {
    return o.door(() => {
      door.check();
      const { state, reasons } = o.admission.row();
      return { state, reasons, tier: o.admission.tier(), restore: holdingStep(o.storage),
               runs: o.runs.live(), stored_bytes: o.files.storedNow().stored };
    });
  },
  /**
   * The platform sets the owner's tier (`free`, `paid`): taken only from the reserved
   * `PLATFORM_CALLER`, as `admissionSet` is. The numbers are the control record's, never
   * this door's.
   */
  tierSet(o: AdmissionDoorNeeds, { platform, request }: Call<TierRequest, "reserved">):
      { tier: TierName } {
    return o.admission.setTier(platform, request);
  },
  /**
   * The owner's own tier, for its app (`read`): its admission row's, which only the platform
   * sets. The numbers are the control record's, which the doors read and show beside it.
   */
  tierRead(o: AdmissionDoorNeeds, _call: Call<undefined>): { tier: TierName } {
    return { tier: o.admission.tier() };
  },
});
