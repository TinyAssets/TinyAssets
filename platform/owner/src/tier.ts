/**
 * The tier (ADR-034 decision 8): every tier gets the same product and the same security,
 * and tiers differ only by simultaneous runs and storage.
 *
 * **Where the numbers live.** In the platform's control record (`doors/src/directory.ts`,
 * `c:control`'s `tiers`), which the founder edits with no deploy: `{ free: { runs,
 * storage_bytes }, paid: { runs, storage_bytes } }` (founder, 2026-11-02: free 5 runs and
 * 25 GB, paid 10 and 250 GB). No number is in code: a record without them refuses every
 * run (`TIER_UNREAD`), never a default. The owner's object binds no store the record is in
 * (its Worker binds only `FILES`, `GATEWAY` and `HOOKS`, the config guard), so the binder,
 * which reads the record at every outside and owner call, hands its `tiers` to the run
 * doors in the binding (`Binding.tiers`), never a request field; the object keeps the last
 * numbers it was handed, with when (`platform_tiers`), for the runs its own `runs` lane
 * starts (a schedule's, the owner's mail's). An edit reaches an owner's door-started runs
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
 * `RUNS_CEILING` is a technical ceiling, not a tier: the most live runs the owner's
 * database is costed for (the owner-bound guard counts each live run's rows at it), so a
 * record past it is malformed.
 */
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";

export const TIER_NAMES = Object.freeze(["free", "paid"] as const);
export type TierName = (typeof TIER_NAMES)[number];

/** The most live runs any tier record may give: what the owner-bound guard costs. */
export const RUNS_CEILING = 1000;

export interface TierNumbers {
  /** Simultaneous runs. */
  readonly runs: number;
  /** Storage, in bytes. */
  readonly storage_bytes: number;
}

export type Tiers = Readonly<Record<TierName, TierNumbers>>;

/** No tier numbers were handed to this owner's object: no run is admitted without them. */
export const TIER_UNREAD = "tier_unread: the platform's tier numbers are not known here";
/** Over the tier's simultaneous runs. */
export const TIER_RUNS = "tier_runs: the owner's tier runs this many runs at once";
export const PLATFORM_TIER_ONLY = "only the platform sets an owner's tier";

function plain(value: unknown, keys: string): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).sort().join() === keys;
}

/** The tier numbers exactly, or `Invalid`. */
export function parseTiers(value: unknown): Tiers {
  if (!plain(value, "free,paid")) {
    throw new Invalid("the tier record names free and paid");
  }
  const numbers = (tier: unknown): TierNumbers => {
    if (!plain(tier, "runs,storage_bytes")) {
      throw new Invalid("a tier is its runs and its storage_bytes");
    }
    const { runs, storage_bytes: bytes } = tier;
    if (typeof runs !== "number" || !Number.isSafeInteger(runs) || runs < 1
        || runs > RUNS_CEILING || typeof bytes !== "number" || !Number.isSafeInteger(bytes)
        || bytes < 1) {
      throw new Invalid(`a tier's runs are 1 to ${RUNS_CEILING}, its storage whole bytes`);
    }
    return Object.freeze({ runs, storage_bytes: bytes });
  };
  return Object.freeze({ free: numbers(value["free"]), paid: numbers(value["paid"]) });
}

/** A tier change's tier, or `Invalid`. */
export function tierName(request: unknown): TierName {
  if (!plain(request, "tier") || !(TIER_NAMES as readonly unknown[]).includes(request["tier"])) {
    throw new Invalid("a tier change names free or paid");
  }
  return request["tier"] as TierName;
}

/**
 * In a unit: the numbers the binder handed (kept, with when, for the lane's runs), else the
 * last kept; refused when there are none. The binder hands `null` when the control record
 * holds none: the kept numbers go too, so a record that drops its tiers refuses every run
 * from then on (the lane's as well), never one admitted on numbers it no longer holds.
 */
export function tierNumbers(storage: OwnerStorage, handed: unknown, now: number): Tiers {
  const sql = storage.sql;
  if (handed === null) {
    sql.exec("DELETE FROM platform_tiers");
    throw new Refused(TIER_UNREAD);
  }
  if (handed !== undefined) {
    const tiers = parseTiers(handed);
    const text = JSON.stringify(tiers);
    sql.exec("INSERT INTO platform_tiers (only, tiers, read_at) VALUES (1, ?, ?) " +
               "ON CONFLICT (only) DO UPDATE SET tiers = excluded.tiers, " +
               "read_at = excluded.read_at", text, now);
    return tiers;
  }
  const kept = sql.exec<{ tiers: string }>("SELECT tiers FROM platform_tiers").toArray()[0];
  if (kept === undefined) {
    throw new Refused(TIER_UNREAD);
  }
  return parseTiers(JSON.parse(kept.tiers));
}

/**
 * In a unit: the numbers the binder handed kept, written only when they differ from the
 * kept ones (most calls hand the same); `null`, a record with none, drops the kept ones, so
 * no run is admitted and no file byte added on numbers the record no longer holds. A door
 * that handed none (`undefined`) keeps what is kept.
 */
export function keepTiers(storage: OwnerStorage, handed: unknown, now: number): void {
  if (handed === undefined) {
    return;
  }
  const sql = storage.sql;
  if (handed === null) {
    if (keptTiers(storage) !== null) {
      sql.exec("DELETE FROM platform_tiers");
    }
    return;
  }
  const text = JSON.stringify(parseTiers(handed));
  const kept = sql.exec<{ tiers: string }>("SELECT tiers FROM platform_tiers").toArray()[0];
  if (kept?.tiers !== text) {
    sql.exec("INSERT INTO platform_tiers (only, tiers, read_at) VALUES (1, ?, ?) " +
               "ON CONFLICT (only) DO UPDATE SET tiers = excluded.tiers, " +
               "read_at = excluded.read_at", text, now);
  }
}

/** The kept tier numbers, or null when none are kept. A read. */
export function keptTiers(storage: OwnerStorage): Tiers | null {
  const kept = storage.sql.exec<{ tiers: string }>("SELECT tiers FROM platform_tiers")
    .toArray()[0];
  return kept === undefined ? null : parseTiers(JSON.parse(kept.tiers));
}
