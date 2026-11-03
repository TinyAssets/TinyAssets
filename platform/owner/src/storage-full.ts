/**
 * The one wall (founder, 2026-11-03): the tier's storage is the only limit a user meets, and
 * every refusal at it says so in the same words, with one fixed code, `storage_full`. It
 * names what is used and the whole, how to free space, and the upgrade path: the next tier's
 * storage, or that the owner's own lower limit is what binds (the owner raises it), or that
 * no larger tier exists. Reads and deletes are never refused at it: a change that leaves the
 * storage no higher than it found it passes (`Files.#bounds`).
 *
 * A refusal crosses Workers RPC as its name and message only, so the facts ride in the
 * message: the plain sentence first, then one JSON object (`StorageFull`) the app parses
 * (`storageFullOf`). The doors answer it 507 Insufficient Storage.
 */
import type { TierName, Tiers } from "./tier";

export const STORAGE_FULL = "storage_full";

/** What a `storage_full` refusal carries, after its sentence. */
export interface StorageFull {
  readonly used_bytes: number;
  readonly limit_bytes: number;
  /** The owner's tier, and its storage, when the tier numbers are known. */
  readonly tier: TierName | null;
  readonly tier_bytes: number | null;
  /** True when the owner's own lower storage setting is what binds, not the tier's. */
  readonly own_limit: boolean;
  /** The next tier and its storage, or null when no larger tier exists. */
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

/** The larger tier above `tier`, if the record gives one more storage. */
function next(tier: TierName, tiers: Tiers): { tier: TierName; limit_bytes: number } | null {
  const larger = (Object.entries(tiers) as [TierName, Tiers[TierName]][])
    .filter(([name, numbers]) => name !== tier && numbers.storage_bytes > tiers[tier].storage_bytes)
    .sort(([, a], [, b]) => a.storage_bytes - b.storage_bytes)[0];
  return larger === undefined ? null : { tier: larger[0], limit_bytes: larger[1].storage_bytes };
}

/** The facts of a refusal at the storage figure. */
export function storageFull(used: number, limit: number, tier: TierName | null,
                            tiers: Tiers | null): StorageFull {
  const tierBytes = tier === null || tiers === null ? null : tiers[tier].storage_bytes;
  const own = tierBytes !== null && limit < tierBytes;
  return { used_bytes: used, limit_bytes: limit, tier, tier_bytes: tierBytes, own_limit: own,
           upgrade: own || tier === null || tiers === null ? null : next(tier, tiers),
           free: FREE };
}

/** The refusal's message: the code, the plain sentence, then the facts as JSON. */
export function storageFullMessage(full: StorageFull): string {
  const way = full.own_limit
    ? `or raise your own storage limit (set at ${shownBytes(full.limit_bytes)}; your plan ` +
      `holds ${shownBytes(full.tier_bytes!)})`
    : full.upgrade !== null
      ? `or upgrade to ${full.upgrade.tier} for ${shownBytes(full.upgrade.limit_bytes)}`
      : "(you are on the largest plan)";
  return `${STORAGE_FULL}: your storage is full: ${shownBytes(full.used_bytes)} of ` +
    `${shownBytes(full.limit_bytes)} used. Free some space (${full.free.join(", ")}) ${way}. ` +
    JSON.stringify(full);
}

/** The facts a `storage_full` message carries, or null for any other message. */
export function storageFullOf(message: string): StorageFull | null {
  if (!message.startsWith(`${STORAGE_FULL}:`)) {
    return null;
  }
  const at = message.indexOf("{");
  return at < 0 ? null : JSON.parse(message.slice(at)) as StorageFull;
}
