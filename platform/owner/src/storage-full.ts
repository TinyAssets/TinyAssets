/**
 * The one wall (founder, 2026-11-03): the tier's storage is the only limit a user meets, and
 * every refusal at it says so in the same words, with one fixed code, `storage_full`. It
 * names what is stored (never the refused write: that is what it needs more), the whole, what
 * is free, how to free space, and the upgrade path: the next tier's storage, or that the
 * owner's own lower limit is what binds (the owner raises it). With no larger tier, or no
 * tier known, it says nothing about plans. Reads and deletes are never refused at it: a
 * change that leaves the storage no higher than it found it passes (`Files.#bounds`).
 *
 * Bytes held by uploads still open (their chunks counted from the upload's begin, so a
 * finished upload never finds the room gone) are reported apart from what is stored. When
 * they alone are what leaves no room, the refusal is `uploads_open`, never `storage_full`:
 * nothing stored needs freeing and no upgrade would help, since they are released when their
 * uploads finish or are cancelled, each within a day (`INTENT_TTL_S`).
 *
 * The refusal carries its facts (`StorageFull`) as its own `facts`. Its message, the plain
 * sentence, ends with the same facts as JSON, as clients have always been shown it; no code
 * reads them back from there. The doors answer `storage_full` 507 and `uploads_open` 409.
 */
import { type Facts, Refusal } from "../../contract/src/refusal";
import type { TierName, Tiers } from "../../contract/src/control";

export const STORAGE_FULL = "storage_full" as const;
export const UPLOADS_OPEN = "uploads_open" as const;

/** What a `storage_full` or `uploads_open` refusal carries, after its sentence. */
export interface StorageFull {
  /** Bytes stored: what the owner keeps, before the refused change, open uploads apart. */
  readonly used_bytes: number;
  /** What the refused change would add. */
  readonly needed_bytes: number;
  /** Bytes held by uploads still open, released when they finish or are cancelled. */
  readonly held_bytes: number;
  readonly limit_bytes: number;
  /** What is left: the figure less what is stored and held, never below zero. */
  readonly free_bytes: number;
  /** The owner's tier, and its storage, when the tier numbers are known. */
  readonly tier: TierName | null;
  readonly tier_bytes: number | null;
  /** True when the owner's own lower storage setting is what binds, not the tier's. */
  readonly own_limit: boolean;
  /** The next tier and its storage, or null when none is larger or no tier is known. */
  readonly upgrade: { readonly tier: TierName; readonly limit_bytes: number } | null;
  /** What frees space, in the order the app offers it. */
  readonly free: readonly string[];
}

const FREE = Object.freeze(["delete files you no longer need",
                            "delete old versions of files", "delete old mail"]);

/** Bytes as a person reads them: decimal units, as storage is sold. */
export function shownBytes(bytes: number): string {
  const units = ["bytes", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000;
    unit++;
  }
  return unit === 0 ? `${bytes} bytes` : `${value.toFixed(value >= 100 ? 0 : 1)} ${units[unit]}`;
}

/** The smallest tier above `tier` that gives more storage, or null. */
function next(tier: TierName, tiers: Tiers): { tier: TierName; limit_bytes: number } | null {
  const larger = (Object.entries(tiers) as [TierName, Tiers[TierName]][])
    .filter(([name, numbers]) => name !== tier && numbers.storage_bytes > tiers[tier].storage_bytes)
    .sort(([, a], [, b]) => a.storage_bytes - b.storage_bytes)[0];
  return larger === undefined ? null : { tier: larger[0], limit_bytes: larger[1].storage_bytes };
}

/**
 * The facts of a refusal at the storage figure: `stored` what the owner keeps, `needed` what
 * the change adds, `held` what open uploads hold, `limit` the figure.
 */
export function storageFull(stored: number, needed: number, held: number, limit: number,
                            tier: TierName | null, tiers: Tiers | null): StorageFull {
  const tierBytes = tier === null || tiers === null ? null : tiers[tier].storage_bytes;
  const own = tierBytes !== null && limit < tierBytes;
  return { used_bytes: stored, needed_bytes: needed, held_bytes: held, limit_bytes: limit,
           free_bytes: Math.max(0, limit - stored - held), tier, tier_bytes: tierBytes,
           own_limit: own,
           upgrade: own || tier === null || tiers === null ? null : next(tier, tiers),
           free: FREE };
}

/** Whether open uploads alone leave no room: what is stored and the change would fit. */
export function heldOnly(full: StorageFull): boolean {
  return full.held_bytes > 0 && full.used_bytes + full.needed_bytes <= full.limit_bytes;
}

/** The refusal at the storage figure: `uploads_open` when open uploads alone leave no room. */
export function storageFullRefusal(full: StorageFull): Refusal {
  return new Refusal(heldOnly(full) ? UPLOADS_OPEN : STORAGE_FULL, storageFullMessage(full),
                     { facts: full as unknown as Facts });
}

/** The refusal's message: its code, the plain sentence, then the facts as JSON. */
function storageFullMessage(full: StorageFull): string {
  if (heldOnly(full)) {
    return `${UPLOADS_OPEN}: ${shownBytes(full.held_bytes)} of your storage is held by uploads ` +
      "still open; it is released when they finish or are cancelled (each within a day). " +
      `Your storage itself has room: ${shownBytes(full.used_bytes)} of ${
        shownBytes(full.limit_bytes)} used, and this needs ${shownBytes(full.needed_bytes)}. ` +
      JSON.stringify(full);
  }
  const held = full.held_bytes === 0 ? ""
    : ` ${shownBytes(full.held_bytes)} more is held by uploads still open, released when they ` +
      "finish or are cancelled.";
  const way = full.own_limit
    ? `, or raise your own storage limit (set at ${shownBytes(full.limit_bytes)}; your plan ` +
      `holds ${shownBytes(full.tier_bytes!)})`
    : full.upgrade !== null
      ? `, or upgrade to ${full.upgrade.tier} for ${shownBytes(full.upgrade.limit_bytes)}`
      : "";
  return `${STORAGE_FULL}: your storage is full: ${shownBytes(full.used_bytes)} of ${
    shownBytes(full.limit_bytes)} used, and this needs ${shownBytes(full.needed_bytes)} more (${
    shownBytes(full.free_bytes)} free).${held} Free some space (${full.free.join(", ")})${
    way}. ${JSON.stringify(full)}`;
}
