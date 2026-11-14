/**
 * The owner's file settings as a new owner starts with them (plan §6): what each setting is
 * until the owner's policy document sets it (`owner/src/policy.ts` says what each means and
 * its range). Each is off by default (Fact 15): no file size cap but the most one upload
 * holds, and no retention, so every version is kept until the owner deletes it or sets one.
 * The two shares are the owner's own split of its files with outside clients. The owner's
 * object reads them, and the cutover's importer holds each imported owner to them, never
 * raising one. An object, so not among `limits.ts`'s numbers (its frozen bounds' guard reads
 * every export there as one). The files plane's room in the object is no setting: it is the
 * index's ceiling (`owner/src/files.ts` `INDEX_BYTES`).
 */
import { CHUNK_BYTES, MAX_INTENT_CHUNKS } from "./limits";

/** The most versions policy can keep of a path (`FILE_SETTINGS.keep_versions`). */
export const KEEP_CEILING = 1000;

/** The file settings in force: a number the owner set, or null for off (no bound). */
export interface FileDefaults {
  readonly max_file_bytes: number;
  readonly retention_days: number | null;
  readonly keep_versions: number | null;
  readonly keep_recent: number | null;
  readonly client_share: number;
  readonly owner_reserve: number;
}

export const FILE_DEFAULTS: FileDefaults = Object.freeze({
  // The most one upload holds (40 GiB); the owner may set it lower.
  max_file_bytes: MAX_INTENT_CHUNKS * CHUNK_BYTES,
  retention_days: null,
  keep_versions: null,
  keep_recent: null,
  client_share: 25,
  owner_reserve: 25,
});
