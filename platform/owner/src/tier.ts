/**
 * The tier (ADR-034 decision 8): every tier gets the same product and the same security,
 * and tiers differ only by simultaneous runs and storage.
 *
 * **Where the numbers live.** In the platform's control record (`contract/src/control.ts`,
 * its `tiers`), which the founder edits with no deploy: `{ free: { runs,
 * storage_bytes }, paid: { runs, storage_bytes } }` (founder, 2026-11-02: free 5 runs and
 * 25 GB, paid 10 and 250 GB). No number is in code: a record without them refuses every
 * run (`TIER_UNREAD`), never a default. The binder, which reads the record at every outside
 * and owner call to gate it, hands its `tiers` to the doors in the binding (`Binding.tiers`),
 * never a request field, so a door never waits on a read of its own; the object keeps the last
 * numbers it was handed, with when (`platform_tiers`), for the runs its own `runs` lane
 * starts (a schedule's, a mail's). An edit reaches an owner's door-started runs
 * at once, its lane's at its next door call.
 *
 * **Where an owner's tier lives.** In the owner's own admission row (its `tier` column,
 * `free` until set), which only the platform sets (`tierSet`, as the reserved
 * `PLATFORM_CALLER`, the one caller `admissionSet` takes too). No client, request or
 * binding names it: the object reads its own row.
 *
 * **What is held.** The tier's simultaneous runs, at admission, against the object's own
 * live runs (`runs.ts`), and its storage, as the files byte quota's `max_bytes`
 * (`policy.ts`'s `fileLimits`), which counts the owner's file chunks and the bodies placed
 * in R2 (ADR-035). Every door that hands the numbers keeps them, written only when they
 * changed (`keepTiers`), so the storage figure is the record's for every change.
 *
 * The contract's `RUNS_CEILING` is a technical ceiling, not a tier: the most live runs the
 * owner's database is costed for (the owner-bound guard counts each live run's rows at it),
 * so a record past it is malformed.
 */
import { parseTiers, TIER_NAMES, type TierName, type Tiers } from "../../contract/src/control";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import type {
  Call, OwnerState,
} from "./owner-do";

/**
 * The most live runs any tier record may give (`contract/src/control.ts`'s `RUNS_CEILING`,
 * the one definition), as `sandbox/template/build.py` reads it: by its text at this path, for
 * the template's tag, which no code imports. `sandbox/guards/template.test.ts` holds this
 * text to the contract's. Fix A6 moves the build's read, and this line goes.
 */
export const RUNS_CEILING = 1000;

/** No tier numbers were handed to this owner's object: no run is admitted without them. */
export const TIER_UNREAD = "tier_unread: the platform's tier numbers are not known here";
/** Over the tier's simultaneous runs. */
export const TIER_RUNS = "tier_runs: the owner's tier runs this many runs at once";
export const PLATFORM_TIER_ONLY = "only the platform sets an owner's tier";

/** A tier change's tier, or `invalid`. */
export function tierName(request: unknown): TierName {
  if (typeof request !== "object" || request === null || Array.isArray(request)
      || Object.keys(request).join() !== "tier"
      || !(TIER_NAMES as readonly unknown[]).includes((request as { tier: unknown }).tier)) {
    throw new Refusal("invalid", "a tier change names free or paid");
  }
  return (request as { tier: TierName }).tier;
}

/**
 * In a unit: the numbers the binder handed, else the last kept; refused when there are none.
 * The binder hands `null` when the control record holds none: the kept numbers go too, so a
 * record that drops its tiers refuses every run from then on (the lane's as well), never one
 * admitted on numbers it no longer holds. Kept as `keepTiers` keeps them.
 */
export function tierNumbers(storage: OwnerStorage, handed: unknown, now: number): Tiers {
  const tiers = keepTiers(storage, handed, now);
  if (tiers === null) {
    throw new Refusal("tier_unread", TIER_UNREAD);
  }
  return tiers;
}

/**
 * In a unit: the numbers the binder handed kept, with when, for the lane's runs, and written
 * only when they differ from the kept ones (most calls hand the same): their one write. `null`,
 * a record with none, drops the kept ones, so no run is admitted and no file byte added on
 * numbers the record no longer holds. A door that handed none (`undefined`) keeps what is
 * kept. The numbers kept after it, or null.
 */
export function keepTiers(storage: OwnerStorage, handed: unknown, now: number): Tiers | null {
  if (handed === undefined) {
    return keptTiers(storage);
  }
  const sql = storage.sql;
  if (handed === null) {
    if (keptTiers(storage) !== null) {
      sql.exec("DELETE FROM platform_tiers");
    }
    return null;
  }
  const tiers = parseTiers(handed);
  const text = JSON.stringify(tiers);
  const kept = sql.exec<{ tiers: string }>("SELECT tiers FROM platform_tiers").toArray()[0];
  if (kept?.tiers !== text) {
    sql.exec("INSERT INTO platform_tiers (only, tiers, read_at) VALUES (1, ?, ?) " +
               "ON CONFLICT (only) DO UPDATE SET tiers = excluded.tiers, " +
               "read_at = excluded.read_at", text, now);
  }
  return tiers;
}

/** The kept tier numbers, or null when none are kept. A read. */
export function keptTiers(storage: OwnerStorage): Tiers | null {
  const kept = storage.sql.exec<{ tiers: string }>("SELECT tiers FROM platform_tiers")
    .toArray()[0];
  return kept === undefined ? null : parseTiers(JSON.parse(kept.tiers));
}

export interface TierRequest {
  readonly tier: TierName;
}

// -- THE DOORS: the `tier` rows' handlers (`contract/doors.json`) ---------------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/** The `tier` doors' handlers, by door. */
export const tierDoors = Object.freeze({
  tierSet(o: OwnerState, { request }: Call<TierRequest>): { tier: TierName } {
    return o.setTier(request);
  },
  tierRead(o: OwnerState, _call: Call<undefined>): { tier: TierName } {
    return { tier: o.tier() };
  },
});
