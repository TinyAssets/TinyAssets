/**
 * The owner's file settings as a new owner starts with them: what each setting is
 * until the owner's policy document sets it (`owner/src/policy.ts` says what each means and
 * its range). Each is off by default (Fact 15): no file size cap but the most one upload
 * holds, no retention, so every version is kept until the owner deletes it or sets one, and
 * no split of the owner's files with outside clients, so a client may hold all of them until
 * the owner sets a share or a reserve (founder, 2026-11-21, call 11). A share is off at the
 * end of its range, where it binds nothing the owner's whole does not, as `max_file_bytes`
 * is. The owner's object reads them, and the cutover's importer holds each imported owner to
 * them, never raising one. An object, so not among `limits.ts`'s numbers (its frozen bounds'
 * guard reads every export there as one). The files plane's room in the object is no
 * setting: it is the index's ceiling (`owner/src/manifest.ts` `INDEX_BYTES`).
 */
import { CHUNK_BYTES, MAX_INTENT_CHUNKS } from "./limits.ts";

/** The most versions policy can keep of a path (`FILE_SETTINGS.keep_versions`). */
export const KEEP_CEILING = 1000;

/** The file settings in force: a number the owner set, or its off value (null: no bound). */
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
  client_share: 100,
  owner_reserve: 0,
});
