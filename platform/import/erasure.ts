/**
 * Every erasure fact the old stack keeps, in one place: the union of every source its own
 * code consults when it decides a principal is erased or being erased. The census and the
 * bundle builder read erasure through this alone, so an owner any source names is in no
 * bundle (`import12_report.md` lists each reader of the old code these come from).
 *
 * The sources, each keyed by the owner's digest (`sha256(principal)`, `admission.digest`
 * and `account_deletion.principal_digest` alike):
 * - `owner_admission` with state `erased` (`broker/admission.py`);
 * - `deletion_jobs`, every job (`broker/deletion_jobs.py`): a job exists only once its
 *   owner asked to be erased, finished or not;
 * - the router's `deleted_owners` (`.router-deleted-owners.sqlite3`, by principal);
 * - custody's `deleted_principals` (`.broker/credential-custody.db`, by digest);
 * - the main store's own `deleted_principals` (`.tinyassets.db` at the shared root, by
 *   `founder_sub`, a digest: `cutover_deletions._legacy_main_digests`), and the same table
 *   in each mounted image's root and center main stores (`digest`, `deletion_schema.py`;
 *   `account_deletion._tombstone_clause` reads it): `imageTombstones`, read from every
 *   image the manifest pins before any owner is decided, an erased owner's included;
 * - `owner_box_revocations` where `deleted = 1` (`owner-identities.db`);
 * - `.boxhost/boxhost.db` `owner_destroys` (by `owner_id`);
 * - every `.erasure-ledger.sqlite3` `events` entry (its digest, and the principal its
 *   payload names), the router's erasure work log;
 * - the router's `.workos-revocations.sqlite3` `revocations` where `deleted = 1`
 *   (`router_account_deletion.revoke_local_tokens`: "deletion can never be undone");
 * - each `.broker/accounts/<digest>/.deleted` marker.
 *
 * And one that names no principal: `.disaster-restore.json`'s `pending_erasures` and
 * `erasure_failures` (`cutover_deletions.census_count`), paths an erasure has not finished.
 * Any is `unfinished_erasure`, stopping the run, since no owner can be told from a path.
 * So is any image whose tombstones cannot be read whole (`erasure_unread`): pinned with no
 * volume row, not mounted as itself, a store not intact or unreadable, a log not merged;
 * and a volume row whose image the manifest does not pin, since that image is not in the
 * copy at all.
 */
import { lstatSync, readdirSync } from "node:fs";
import { join } from "node:path";

import { sha256Hex } from "../contract/src/digest";

import { intact, isStore, nameText, openStore, readSmall, tablesOf } from "./backup";
import { CENTER_NAME, ROOT, ruleOf } from "./rules";

/** One digest's facts: which sources name it, and its principal where any does. */
export interface Fact {
  readonly sources: Set<string>;
  principal: string | null;
}

export interface ErasureFacts {
  /** Every digest any source names as erased or being erased. */
  readonly facts: ReadonlyMap<string, Fact>;
  /** What stops the run: a disaster report's unfinished erasures. */
  readonly unfinished: number;
}

/** A fleet store's rows (`survey.ts`'s `read`: none when the store or table is absent). */
export type Rows = (store: string, table: string) => Record<string, unknown>[];

/** The main store's names (`storage/__init__.py`'s legacy chain). */
export const MAIN_STORES: readonly string[] = Object.freeze([".tinyassets.db", ".workflow.db",
                                                             ".author_server.db"]);

/** A main store's tombstones, by digest, in either column the old code wrote. */
export function mainTombstones(rows: readonly Record<string, unknown>[]): string[] {
  return rows.map((row) => String(row["founder_sub"] ?? row["digest"]));
}

function exists(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch {
    return false;
  }
}

/** The union of every erasure fact the shared layer holds. */
export function erasureFacts(read: Rows, shared: string): ErasureFacts {
  const facts = new Map<string, Fact>();
  const add = (source: string, digest: string, principal: string | null = null) => {
    const fact = facts.get(digest) ?? { sources: new Set<string>(), principal: null };
    fact.sources.add(source);
    fact.principal ??= principal;
    facts.set(digest, fact);
  };
  const byPrincipal = (source: string, principal: unknown) => {
    if (typeof principal === "string" && principal !== "") {
      add(source, sha256Hex(principal.trim()), principal);
    }
  };
  const identities = ".broker/state/owner-identities.db";
  for (const row of read(identities, "owner_admission")) {
    if (row["state"] === "erased") {
      add("owner_admission", String(row["digest"]));
    }
  }
  for (const row of read(identities, "deletion_jobs")) {
    const record = JSON.parse(String(row["record"])) as { owner?: unknown };
    byPrincipal("deletion_jobs", record.owner);
  }
  for (const row of read(".router-deleted-owners.sqlite3", "deleted_owners")) {
    byPrincipal("deleted_owners", row["owner"]);
  }
  for (const row of read(".broker/credential-custody.db", "deleted_principals")) {
    add("deleted_principals", String(row["digest"]));
  }
  for (const store of MAIN_STORES) {
    for (const digest of mainTombstones(read(store, "deleted_principals"))) {
      add("main_deleted_principals", digest);
    }
  }
  for (const row of read(identities, "owner_box_revocations")) {
    if (Number(row["deleted"]) !== 0) {
      byPrincipal("owner_box_revocations", row["principal"]);
    }
  }
  for (const row of read(".boxhost/boxhost.db", "owner_destroys")) {
    byPrincipal("owner_destroys", row["owner_id"]);
  }
  for (const row of read(".erasure-ledger.sqlite3", "events")) {
    const payload = JSON.parse(String(row["payload"] ?? "{}")) as { principal?: unknown };
    add("erasure_ledger", String(row["digest"]),
        typeof payload.principal === "string" ? payload.principal : null);
  }
  for (const row of read(".workos-revocations.sqlite3", "revocations")) {
    if (Number(row["deleted"]) !== 0) {
      byPrincipal("workos_revocations", row["owner"]);
    }
  }
  const accounts = join(shared, ".broker", "accounts");
  if (exists(accounts) && lstatSync(accounts).isDirectory()) {
    for (const name of readdirSync(accounts)) {
      if (/^[0-9a-f]{64}$/.test(name) && exists(join(accounts, name, ".deleted"))) {
        add("deleted_marker", name);
      }
    }
  }
  let unfinished = 0;
  const report = join(shared, ".disaster-restore.json");
  if (exists(report)) {
    const doc = JSON.parse(readSmall(report).toString("utf8")) as Record<string, unknown>;
    for (const key of ["pending_erasures", "erasure_failures"]) {
      unfinished += Array.isArray(doc[key]) ? (doc[key] as unknown[]).length : 0;
    }
  }
  return { facts, unfinished };
}

/** One image's tombstones, or why they cannot be read whole (`erasure_unread`). */
export type ImageRead = { readonly digests: readonly string[] } | { readonly problem: string };

/**
 * The tombstones one mounted image's main stores hold: at its root, in each center its
 * `.centers.db` lists, and in every other folder at the root that is not dotted, whatever
 * rule names it. The old daemon knows a universe by its ownership rows
 * (`daemon_server.owned_universe_ids`: `universe_acl` and `founder_home`), never a dotted
 * name; reading every such folder is the safe superset of those, so no folder a row could
 * name, or a rule claims (`scratch`), holds a tombstone unread. An entry no rule names that
 * is not a file or a folder, or a folder whose name no center has, is a problem. Each store
 * is read whole or not at all: a link in a store's place,
 * a log beside it not merged (`-wal` or `-journal`, whose rows an immutable read never
 * sees), a store that fails `integrity_check` or cannot be opened is a problem, never a
 * store with no tombstones.
 */
export function imageTombstones(root: string): ImageRead {
  type Read = { rows: Record<string, unknown>[] } | { problem: string };
  const tombstonesIn = (path: string, table: string): Read => {
    if (!exists(path)) {
      return { rows: [] };
    }
    for (const log of [`${path}-wal`, `${path}-journal`]) {
      if (exists(log) && (!lstatSync(log).isFile() || lstatSync(log).size > 0)) {
        return { problem: "unclean" };
      }
    }
    try {
      if (!isStore(path)) {
        return { problem: "unreadable" };
      }
      const db = openStore(path);
      try {
        if (!intact(db)) {
          return { problem: "not_intact" };
        }
        return { rows: tablesOf(db).includes(table)
          ? db.prepare(`SELECT * FROM "${table}"`).all() as Record<string, unknown>[] : [] };
      } finally {
        db.close();
      }
    } catch {
      return { problem: "unreadable" };
    }
  };
  const centers = tombstonesIn(join(root, ".centers.db"), "centers");
  if ("problem" in centers) {
    return centers;
  }
  const folders = [root];
  const listed = new Set<string>();
  for (const row of centers.rows) {
    const name = String(row["center"]);
    const folder = join(root, name);
    if (!CENTER_NAME.test(name) || (exists(folder) && !lstatSync(folder).isDirectory())) {
      return { problem: "unreadable" };
    }
    listed.add(name);
    if (exists(folder)) {
      folders.push(folder);
    }
  }
  // Every other folder at the root that is not dotted. One a root rule claims (`scratch`,
  // `wiki`) is read too, its own rule aside; of the rest, a file is no universe and anything
  // but a folder is not told.
  for (const entry of readdirSync(root, { encoding: "buffer" })) {
    const name = nameText(entry);
    if (entry[0] === 0x2e || (name !== null && listed.has(name))) {
      continue;
    }
    if (name === null) {
      return { problem: "unreadable" };
    }
    const stray = join(root, name);
    const stat = lstatSync(stray);
    if (ruleOf(ROOT, name) !== null) {
      if (stat.isDirectory()) {
        folders.push(join(root, name));
      }
      continue;
    }
    if (stat.isFile()) {
      continue;
    }
    if (!stat.isDirectory() || !CENTER_NAME.test(name)) {
      return { problem: "unreadable" };
    }
    folders.push(stray);
  }
  const found: string[] = [];
  for (const folder of folders) {
    for (const store of MAIN_STORES) {
      const read = tombstonesIn(join(folder, store), "deleted_principals");
      if ("problem" in read) {
        return read;
      }
      found.push(...mainTombstones(read.rows));
    }
  }
  return { digests: found };
}

/** The facts with the tombstones the images' main stores hold. */
export function withImageFacts(erasure: ErasureFacts, digests: Iterable<string>): ErasureFacts {
  const facts = new Map(erasure.facts);
  for (const digest of digests) {
    const fact = facts.get(digest) ?? { sources: new Set<string>(), principal: null };
    facts.set(digest, { sources: new Set([...fact.sources, "image_deleted_principals"]),
                        principal: fact.principal });
  }
  return { facts, unfinished: erasure.unfinished };
}

/** Whether any erasure fact names the principal (by its digest, trimmed or not). */
export function names(erasure: ErasureFacts, principal: string): boolean {
  return erasure.facts.has(sha256Hex(principal)) || erasure.facts.has(sha256Hex(principal.trim()));
}

/** Every source this module reads: what each mutation of the guards removes in turn. */
export const ERASURE_SOURCES = Object.freeze([
  "owner_admission", "deletion_jobs", "deleted_owners", "deleted_principals",
  "main_deleted_principals", "owner_box_revocations", "owner_destroys", "erasure_ledger",
  "workos_revocations", "deleted_marker", "image_deleted_principals",
]);
