/**
 * The owner's file settings as a new owner starts with them (plan §6): what each quota and
 * retention setting is until the owner's policy document sets it (`owner/src/policy.ts` says
 * what each means and its range). The owner's object reads them, and the cutover's importer
 * holds each imported owner to them, never raising one (Fact 15). An object, so not among
 * `limits.ts`'s numbers (its frozen bounds' guard reads every export there as one).
 */
import { CHUNK_BYTES, MAX_INTENT_CHUNKS } from "./limits";

export const FILE_DEFAULTS = Object.freeze({
  max_inline_bytes: 256 * 1024 ** 2,
  max_manifest_bytes: 512 * 1024 ** 2,
  max_entries: 30_000,
  // Off by default: the most one upload holds (40 GiB); the owner may set it lower.
  max_file_bytes: MAX_INTENT_CHUNKS * CHUNK_BYTES,
  max_history: 600_000,
  retention_days: 30,
  keep_versions: 10,
  keep_recent: 20,
  client_share: 25,
  owner_reserve: 25,
});
