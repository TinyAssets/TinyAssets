/**
 * The read-only pre-admission comparison. window.sh reads the old broker's current
 * off-host deletion records with the backup store's read key. This offline job
 * compares them with the original owners of the bundles this run actually made,
 * including when rehearsal bundles carry substitute names. The same ordinal as
 * stage and verify is all that leaves the host. This observation cannot authorize
 * admission: the old broker may accept another request after its records were read.
 */
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { object } from "../contract/src/checks";
import { sha256Hex } from "../contract/src/digest";
import { HEX64 } from "../contract/src/ids";
import { BUNDLE_FORMAT, rehearsalName } from "../contract/src/import-message";

import { layout, openStore, tablesOf } from "./backup";
import { DIGESTS, PRIVATE_RECORD } from "./bundle";
import { bundlesIn } from "./stage";

/** An error while parsing a broker record must never print a principal or a status token. */
function document(path: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    throw new Error("the deletion comparison could not read a complete JSON record");
  }
  if (!object(value)) {
    throw new Error("the deletion comparison expected a JSON object");
  }
  return value;
}

/** Return the staged bundle ordinals that have a deletion record, finished or unfinished. */
export function compareDeletions(work: string, jobs: string, rehearsal: string | null):
    { bundles: number; withhold: number[] } {
  const held = new Set<string>();
  for (const file of readdirSync(jobs, { withFileTypes: true })) {
    const id = file.name.slice(0, -5);
    if (!file.isFile() || !file.name.endsWith(".json") || !HEX64.test(id)) {
      throw new Error("the deletion authority named an invalid job file");
    }
    const job = document(join(jobs, file.name));
    if (job.id !== id || !Array.isArray(job.completed_steps)
        || !(typeof job.owner === "string" && job.owner.length > 0 && sha256Hex(job.owner) === id
             || job.owner === null && job.origin === "cutover")) {
      throw new Error("an off-host deletion record does not match its identity");
    }
    held.add(id);
  }

  // JobRows.save commits each off-host record before its SQLite row. Its authority
  // never removes a record. Missing even one known id makes this listing incomplete.
  const db = openStore(layout(work).identities);
  try {
    if (tablesOf(db).includes("deletion_jobs")) {
      for (const row of db.prepare("SELECT id FROM deletion_jobs").iterate()) {
        if (typeof row.id !== "string" || !held.has(row.id)) {
          throw new Error("the current deletion authority is missing a job present in the backup");
        }
      }
    }
  } finally {
    db.close();
  }

  const privateDir = join(work, "bundle", "private");
  const mapping = document(join(privateDir, PRIVATE_RECORD));
  const saved = document(join(privateDir, DIGESTS));
  if (mapping.format !== BUNDLE_FORMAT || !Array.isArray(mapping.owners)
      || saved.format !== BUNDLE_FORMAT || !object(saved.digests)) {
    throw new Error("the bundle builder's private records do not describe this run");
  }
  const originals = new Set<string>();
  const candidates = new Map<string, { original: string; name: string; bundle: string }>();
  for (const row of mapping.owners) {
    if (!object(row) || typeof row.owner !== "string" || typeof row.excluded !== "boolean") {
      throw new Error("the private cutover record contains an invalid owner");
    }
    const original = sha256Hex(row.owner);
    const originalKey = original.slice(0, 16);
    const name = rehearsal === null || row.excluded ? row.owner : rehearsalName(rehearsal, row.owner);
    const key = sha256Hex(name).slice(0, 16);
    const bundle = saved.digests[originalKey];
    if (row.key !== key || originals.has(originalKey) || candidates.has(key)
        || typeof bundle !== "string" || !HEX64.test(bundle)) {
      throw new Error("the private cutover record does not identify a unique bundle");
    }
    originals.add(originalKey);
    if (!row.excluded) {
      candidates.set(key, { original, name, bundle });
    }
  }
  if (Object.keys(saved.digests).length !== originals.size) {
    throw new Error("the private cutover record does not cover the saved bundle digests");
  }

  let staged;
  try {
    staged = bundlesIn(join(work, "bundle", "out"));
  } catch {
    throw new Error("the deletion comparison could not verify the staged bundles");
  }
  if (staged.length !== candidates.size) {
    throw new Error("the bundles and the private cutover record cover different owners");
  }
  const withhold: number[] = [];
  for (const [index, bundle] of staged.entries()) {
    const owner = candidates.get(bundle.key);
    if (owner?.name !== bundle.owner || owner?.bundle !== bundle.bundle) {
      throw new Error("a bundle differs from the private cutover record");
    }
    if (held.has(owner.original)) {
      withhold.push(index + 1);
    }
  }
  return { bundles: staged.length, withhold };
}
