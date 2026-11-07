/**
 * The survey: one read of a backup copy, by rule 0 (`rules.ts`), that both the census and
 * the bundle builder stand on. It reads the shared layer's fleet stores (who the owners
 * are, their admission, tier, volumes and tombstones) and, for each owner, its broker
 * stores and its mounted image: every entry and table held to its rule, every store
 * checked by SQLite's `integrity_check`, the files to carry and the stores to archive
 * listed, never their contents.
 *
 * A problem is a reason code and where it is: a rule's path and a table's name (schema
 * names), or a name's digest (`#` and the first 12 hex of its SHA-256), never a name the
 * owner chose, so the census's output holds no user content.
 */
import { lstatSync, readdirSync, readlinkSync, type Stats } from "node:fs";
import { join } from "node:path";

import { sha256Hex } from "../contract/src/digest";
import { storedPath, WORKSPACE } from "../contract/src/paths";
import { DEFAULT_IGNORES, ignoredBy, type Matcher } from "../contract/src/sync-ignore";

import {
  intact, isStore, layout, type Manifest, nameText, openStore, quoted, rowsIn, tablesOf,
} from "./backup";
import {
  type ErasureFacts, erasureFacts, imageTombstones, names, withImageFacts,
} from "./erasure";
import {
  ACCOUNT, ACCOUNT_NAME, CENTER, CENTER_NAME, type EntryRule, type Level, ROOT, ruleOf,
  SESSIONS, SHARED, SIDECAR, type StoreRule, tableUse, type Use,
} from "./rules";

/** Where the owner's imported tree goes. */
export const IMPORTED = `${WORKSPACE}/imported`;
/** Where the archived stores go, in that tree and in each center's. */
export const RECORDS = ".records";

export type Code =
  // the backup copy itself
  | "manifest_archive" | "image_mismatch" | "image_missing" | "unpinned" | "pending_work"
  | "pin_mismatch" | "not_mounted"
  // rule 0
  | "unknown_entry" | "unknown_table" | "unknown_center" | "bad_store" | "unclean_store"
  | "integrity" | "path_collision" | "unreadable" | "orphan_row" | "never_admitted"
  | "unfinished_erasure" | "erasure_unread"
  // the new platform's limits
  | "over_entries" | "over_inline_bytes" | "over_file_bytes" | "over_storage" | "no_tier"
  | "over_tar_member";

export interface Problem {
  readonly code: Code;
  readonly at: string;
}

/** A folder or file the bundle writes at `path`, read from `source` (a folder may have none). */
export interface Carried {
  readonly path: string;
  readonly kind: "file" | "dir";
  readonly source: Buffer | null;
  readonly size: number;
  readonly mode: 0 | 1;
  readonly mtime_ms: number;
}

/** A link: listed in `links.txt`, never followed nor imported. */
export interface Link {
  readonly path: string;
  readonly target: string;
}

/** A name that cannot be stored: kept, with all below it, as a member of `refused.tar`. */
export interface Refusal {
  readonly member: Buffer;
  readonly source: Buffer;
}

export interface TableCount {
  readonly table: string;
  readonly use: Use;
  readonly rows: number;
}

/** A store kept under `/workspace/imported`: copied whole, or its archived tables. */
export interface Archived {
  readonly path: string;
  readonly source: Buffer;
  readonly whole: boolean;
  readonly size: number;
  readonly tables: readonly TableCount[];
}

/** A store's tables, as the census counts them: where its rule is, and each table. */
export interface StoreCount {
  readonly at: string;
  readonly tables: readonly TableCount[];
}

export interface Tree {
  readonly carried: Carried[];
  readonly links: Link[];
  readonly refused: Refusal[];
  readonly archived: Archived[];
  readonly stores: StoreCount[];
  readonly problems: Problem[];
  /** What the sync's defaults leave out of the live tree: kept verbatim in `ignored.tar`. */
  readonly ignoredTrees: Refusal[];
  /** Counts of what is walked and not carried live: the ignored files, and the rest. */
  ignored: number;
  specials: number;
  hardlinks: number;
  sqliteInUser: number;
  /** The rows that carry live: the policy document, and the collections' rows and bodies. */
  rows: { policy: number; collection: number; body_bytes: number };
}

export interface Admission {
  readonly state: "admitted" | "suspended" | "erased";
  readonly reasons: readonly string[];
}

export interface Plan {
  readonly tier: string;
  readonly ends_at: number | null;
  readonly customer_id: string | null;
  readonly subscription_id: string | null;
}

export interface Volume {
  readonly uid: number;
  readonly snapshot_id: string | null;
  readonly snapshot_sha256: string | null;
  readonly work: string | null;
}

export interface Owner {
  readonly principal: string;
  /** The SHA-256 of the principal, as the old stack keyed it (admission, broker accounts). */
  readonly digest: string;
  /** The census's key: the digest's first 16 hex (`volume_lifecycle.report_fault`'s). */
  readonly key: string;
  readonly uid: number | null;
  readonly admission: Admission | null;
  readonly plan: Plan | null;
  readonly volume: Volume | null;
  /**
   * What the old stack's erasure facts say of the owner (`erasure.ts`, their one reader):
   * `erased` when its admission reads erased, `pending` when any other source names it
   * (an erasure asked for and not yet through), null when none does. Either way the owner
   * is in no bundle.
   */
  readonly erasure: "erased" | "pending" | null;
  readonly centers: readonly { readonly name: string; readonly retired: boolean }[];
  readonly tree: Tree;
}

export interface Fleet {
  readonly problems: Problem[];
  readonly stores: StoreCount[];
  readonly owners: Owner[];
  /** Every erasure fact the shared layer and the mounted images hold (`erasure.ts`). */
  readonly erasure: ErasureFacts;
  /** Erasure tombstones no identity holds: by principal, and by digest alone. */
  readonly tombstones: { readonly principals: readonly string[]; readonly digests: readonly string[] };
  readonly orphanAccounts: number;
  /** Fleet rows whose principal or digest is no owner's: each a fleet problem too. */
  readonly orphans: { readonly admission: number; readonly plans: number; readonly volumes: number };
  /** Identities with no volume row and no pin: owners who never had a box. */
  readonly noVolume: number;
  /** Owners withheld for a deletion job not finished. */
  readonly erasurePending: number;
}

/** The digest of a name the owner may have chosen: how a problem names it. */
export function hidden(name: Buffer | string): string {
  return `#${sha256Hex(name).slice(0, 12)}`;
}

export function newTree(): Tree {
  return { carried: [], links: [], refused: [], archived: [], stores: [], problems: [],
           ignoredTrees: [], ignored: 0, specials: 0, hardlinks: 0, sqliteInUser: 0,
           rows: { policy: 0, collection: 0, body_bytes: 0 } };
}

function entriesOf(dir: Buffer): Buffer[] {
  return readdirSync(dir, { encoding: "buffer" }).sort(Buffer.compare);
}

const at = (dir: Buffer, name: Buffer): Buffer => Buffer.concat([dir, Buffer.from("/"), name]);

/**
 * A store held to its rule: a regular SQLite file that passes `integrity_check`, with no
 * hot journal or unmerged log beside it (a box stopped cleanly leaves neither), and every
 * table named by the rule.
 */
function checkStore(path: Buffer, rule: StoreRule, where: string, sidecars: Map<string, Stats>,
                    problems: Problem[]): TableCount[] | null {
  if (rule.tables === "drop") {
    // Kept only in the final backup, so never read: no table of it is the import's.
    return [];
  }
  const stat = lstatSync(path);
  if (!stat.isFile() || !isStore(path)) {
    problems.push({ code: "bad_store", at: where });
    return null;
  }
  for (const kind of ["wal", "journal"]) {
    if ((sidecars.get(kind)?.size ?? 0) > 0) {
      problems.push({ code: "unclean_store", at: where });
      return null;
    }
  }
  let db: ReturnType<typeof openStore>;
  try {
    db = openStore(path);
  } catch {
    problems.push({ code: "integrity", at: where });
    return null;
  }
  try {
    let whole: boolean;
    try {
      whole = intact(db);
    } catch {
      whole = false;
    }
    if (!whole) {
      problems.push({ code: "integrity", at: where });
      return null;
    }
    if (rule.tables === "archive") {
      // Kept whole: one total, so no name a table was given (an agent's own) is reported.
      return [{ table: "*", use: "archive",
                rows: tablesOf(db).reduce((sum, table) => sum + rowsIn(db, table), 0) }];
    }
    const counts: TableCount[] = [];
    for (const table of tablesOf(db)) {
      const use = tableUse(rule, table);
      if (use === null) {
        problems.push({ code: "unknown_table", at: `${where}/${table}` });
      } else {
        counts.push({ table, use, rows: rowsIn(db, table) });
      }
    }
    return counts;
  } finally {
    db.close();
  }
}

export interface LevelVisit {
  /** A store the level's rule names, checked, with its tables counted. */
  store(name: string, path: Buffer, rule: StoreRule, tables: readonly TableCount[]): void;
  /** An entry carried as the owner's files. */
  user?(name: string, path: Buffer): void;
  /** An entry no rule of the level names; true when the caller claims it (a center). */
  other?(name: string, path: Buffer): boolean;
}

/**
 * Every entry of the folder `dir`, held to `rules` (`where` names the level in problems):
 * stores checked, their sidecars claimed, folders with rules of their own walked, user
 * entries handed on, dropped entries left unread.
 */
export function visitLevel(dir: Buffer, rules: Level, where: string, visit: LevelVisit,
                           problems: Problem[], stores: StoreCount[]): void {
  const names = entriesOf(dir).map((name) => ({ name, value: nameText(name) }));
  const present = new Set(names.map((entry) => entry.value));
  const sidecars = new Map<string, Map<string, Stats>>();
  const claimed = new Set<string>();
  for (const { name, value } of names) {
    const sidecar = SIDECAR.exec(value ?? "");
    const base = sidecar?.[1];
    if (base !== undefined && present.has(base) && ruleOf(rules, base)?.kind === "store") {
      const of = sidecars.get(base) ?? new Map<string, Stats>();
      of.set(sidecar![2]!, lstatSync(at(dir, name)));
      sidecars.set(base, of);
      claimed.add(value!);
    }
  }
  for (const { name, value } of names) {
    if (value !== null && claimed.has(value)) {
      continue;
    }
    if (value === null) {
      problems.push({ code: "unknown_entry", at: `${where}/${hidden(name)}` });
      continue;
    }
    const path = at(dir, name);
    const rule: EntryRule | null = ruleOf(rules, value);
    if (rule === null) {
      if (visit.other?.(value, path) !== true) {
        problems.push({ code: "unknown_entry", at: `${where}/${hidden(name)}` });
      }
    } else if (rule.kind === "store") {
      const tables = checkStore(path, rule, `${where}/${value}`, sidecars.get(value) ?? new Map(),
                                problems);
      if (tables !== null) {
        stores.push({ at: `${where}/${value}`, tables });
        visit.store(value, path, rule, tables);
      }
    } else if (rule.kind === "dir") {
      if (!lstatSync(path).isDirectory()) {
        problems.push({ code: "unknown_entry", at: `${where}/${value}` });
      } else {
        visitLevel(path, rule.entries, `${where}/${value}`, nested(visit, value), problems, stores);
      }
    } else if (rule.kind === "user") {
      if (visit.user === undefined) {
        problems.push({ code: "unknown_entry", at: `${where}/${value}` });
      } else {
        visit.user(value, path);
      }
    } else if (rule.kind === "empty") {
      // Absent or empty is fine; a link, a file, or a folder holding anything is not.
      const stat = lstatSync(path);
      if (!stat.isDirectory() || readdirSync(path).length > 0) {
        problems.push({ code: "unfinished_erasure", at: `${where}/${value}` });
      }
    }
  }
}

/** The visit of a folder inside a level: its stores named by their path from the level. */
function nested(visit: LevelVisit, folder: string): LevelVisit {
  return {
    store: (name, path, rule, tables) => visit.store(`${folder}/${name}`, path, rule, tables),
    ...(visit.user === undefined ? {}
      : { user: (name: string, path: Buffer) => visit.user!(`${folder}/${name}`, path) }),
  };
}

/** What the walk of the owner's files keeps of a node's `lstat`. */
function carriedOf(path: string, source: Buffer, stat: Stats): Carried {
  return { path, kind: stat.isDirectory() ? "dir" : "file", source,
           size: stat.isDirectory() ? 0 : stat.size, mode: (stat.mode & 0o100) !== 0 ? 1 : 0,
           mtime_ms: Math.floor(stat.mtimeMs) };
}

/** Whether `path` (a stored path under the workspace) is one the sync's defaults ignore. */
export function ignores(matcher: Matcher, path: string, dir: boolean): boolean {
  return matcher(path.slice(WORKSPACE.length + 1), dir) !== null;
}

/**
 * The owner's files below `source`, carried at `path`: every node read by `lstat` and never
 * followed. A link is listed; a socket, FIFO or device is skipped and counted; a name that
 * is not UTF-8, or whose stored path the files plane would refuse, is kept (with everything
 * below it) for `refused.tar` as `member`; a path the sync's defaults ignore is counted and
 * left in the final backup.
 */
export function walkUser(source: Buffer, path: string, member: Buffer, tree: Tree,
                         matcher: Matcher): void {
  try {
    walkNode(source, path, member, tree, matcher);
  } catch (error) {
    // A node the file system will not hand over (a path past the host's own `PATH_MAX`
    // from where the image is mounted, or an unreadable folder) stops the owner, loudly.
    if (typeof (error as { code?: unknown }).code !== "string") {
      throw error;
    }
    tree.problems.push({ code: "unreadable", at: `imported/${hidden(member)}` });
  }
}

function walkNode(source: Buffer, path: string, member: Buffer, tree: Tree,
                  matcher: Matcher): void {
  const stat = lstatSync(source);
  if (stat.isSymbolicLink()) {
    const target = nameText(readlinkSync(source, { encoding: "buffer" }));
    if (target === null) {
      tree.refused.push({ member, source });
    } else {
      tree.links.push({ path, target });
    }
    return;
  }
  if (!stat.isDirectory() && !stat.isFile()) {
    tree.specials += 1;
    return;
  }
  if (ignores(matcher, path, stat.isDirectory())) {
    tree.ignoredTrees.push({ member, source });
    tree.ignored += stat.isDirectory() ? countFiles(source) : 1;
    return;
  }
  tree.carried.push(carriedOf(path, source, stat));
  if (stat.isFile()) {
    tree.hardlinks += stat.nlink > 1 ? 1 : 0;
    tree.sqliteInUser += stat.size > 0 && isStore(source) ? 1 : 0;
    return;
  }
  for (const name of entriesOf(source)) {
    const text = nameText(name);
    const child = at(source, name);
    const childMember = Buffer.concat([member, Buffer.from("/"), name]);
    let stored: string | null = null;
    if (text !== null) {
      try {
        stored = storedPath(`${path}/${text}`);
      } catch {
        stored = null;
      }
    }
    if (stored === null) {
      tree.refused.push({ member: childMember, source: child });
    } else {
      walkUser(child, stored, childMember, tree, matcher);
    }
  }
}

/** The regular files below `dir`, by the folder's own entry types (no `lstat` each). */
function countFiles(dir: Buffer): number {
  let files = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true, encoding: "buffer" })) {
    const name = Buffer.from(entry.name);
    files += entry.isFile() ? 1 : entry.isDirectory() ? countFiles(at(dir, name)) : 0;
  }
  return files;
}

/** The owner's store `name` archived at `path`: copied whole, or its kept tables. */
function archive(tree: Tree, path: string, source: Buffer, rule: StoreRule,
                 tables: readonly TableCount[]): void {
  const kept = tables.filter((table) => table.use !== "drop");
  if (rule.tables === "drop" || (rule.tables !== "archive" && kept.length === 0)) {
    return;
  }
  tree.archived.push({ path, source, whole: rule.tables === "archive",
                       size: lstatSync(source).size, tables: kept });
}

/**
 * Rows of one table of a store, read whole (the fleet stores are small, research §1); none
 * when the store has no such table.
 */
function rows(path: string, table: string): Record<string, unknown>[] {
  const db = openStore(path);
  try {
    if (!tablesOf(db).includes(table)) {
      return [];
    }
    return db.prepare(`SELECT * FROM ${quoted(table)}`).all() as Record<string, unknown>[];
  } finally {
    db.close();
  }
}

/** The shared layer's fleet stores, read once. */
function fleetStores(shared: string, problems: Problem[], stores: StoreCount[]) {
  const seen = new Map<string, readonly TableCount[]>();
  visitLevel(Buffer.from(shared), SHARED, "shared", {
    store: (name, _path, _rule, tables) => seen.set(name, tables),
  }, problems, stores);
  const read = (store: string, table: string): Record<string, unknown>[] =>
    seen.get(store)?.some((t) => t.table === table) === true
      ? rows(join(shared, store), table) : [];
  return { read };
}

/** The old broker's deletion steps, in order (`broker/deletion_jobs.py` `STEPS`). */
const DELETION_STEPS = Object.freeze(["withdrawal", "volume", "snapshots", "custody", "billing",
                                      "credentials", "identity", "receipt"]);

/**
 * The broker's deletion jobs (`deletion_jobs`: `id`, the owner's digest, and `record`, the
 * job as `new_job` makes it), by owner: finished once every step is done, in order
 * (`load_unfinished_jobs`'s own test). A row the old code could not have written stops
 * loudly.
 */
function deletionJobs(rows: Record<string, unknown>[]):
    Map<string, { readonly id: string; readonly finished: boolean }> {
  const found = new Map<string, { id: string; finished: boolean }>();
  for (const row of rows) {
    const record = JSON.parse(String(row["record"])) as Record<string, unknown>;
    const owner = record["owner"];
    const steps = record["completed_steps"];
    if (typeof owner !== "string" || !Array.isArray(steps) || row["id"] !== sha256Hex(owner)
        || record["id"] !== row["id"]) {
      throw new Error("a deletion_jobs row is no job the old broker wrote");
    }
    found.set(owner, { id: String(row["id"]),
                       finished: JSON.stringify(steps) === JSON.stringify(DELETION_STEPS) });
  }
  return found;
}

/** Each owner's admission, by digest: the old stack's states and reasons, checked. */
function admissions(read: (store: string, table: string) => Record<string, unknown>[]) {
  const found = new Map<string, Admission>();
  for (const row of read(".broker/state/owner-identities.db", "owner_admission")) {
    const reasons = JSON.parse(String(row["reasons"])) as unknown;
    const state = row["state"];
    if ((state !== "admitted" && state !== "suspended" && state !== "erased")
        || !Array.isArray(reasons) || reasons.some((r) => r !== "billing" && r !== "abuse")
        || (state === "suspended") !== (reasons.length > 0)) {
      throw new Error("an owner_admission row is no state and reasons the old stack wrote");
    }
    found.set(String(row["digest"]), { state, reasons: reasons as string[] });
  }
  return found;
}

/** Where the backup copy is, and what is mounted read-only where. */
export interface Copy {
  readonly work: string;
  readonly manifest: Manifest;
  /** The root an owner's image is mounted at, or why it is not (`mountProblem`). */
  readonly volume: (uid: number) => { readonly root: string } | { readonly problem: string };
}

/** The survey of the whole backup copy: the fleet, then each owner. */
export function survey(copy: Copy): Fleet {
  const where = layout(copy.work);
  const problems: Problem[] = [];
  const stores: StoreCount[] = [];
  const { read } = fleetStores(where.shared, problems, stores);
  const admitted = admissions(read);
  const volumes = new Map(read(".broker/state/owner-identities.db", "owner_volumes")
    .map((row) => [String(row["principal"]), {
      uid: Number(row["uid"]), snapshot_id: row["snapshot_id"] as string | null,
      snapshot_sha256: row["snapshot_sha256"] as string | null, work: row["work"] as string | null,
    }]));
  const plans = new Map(read(".broker/plans.db", "plans").map((row) => [String(row["owner"]), {
    tier: String(row["tier"]), ends_at: row["ends_at"] as number | null,
    customer_id: row["customer_id"] as string | null,
    subscription_id: row["subscription_id"] as string | null,
  }]));
  const jobs = deletionJobs(read(".broker/state/owner-identities.db", "deletion_jobs"));
  const identities = read(".broker/state/owner-identities.db", "owner_identities")
    .map((row) => ({ principal: String(row["principal"]), uid: Number(row["machine_id"]) }));
  const digests = new Set(identities.map((identity) => sha256Hex(identity.principal)));
  // Erasure is read once, here, from every source the old stack consults.
  const erasure = erasureFacts(read, where.shared);
  if (erasure.unfinished > 0) {
    problems.push({ code: "unfinished_erasure", at: "shared/.disaster-restore.json" });
  }

  // Every fleet row is some identity's, or a counted problem: never a row nothing reads.
  const principals = new Set(identities.map((identity) => identity.principal));
  const orphans = { admission: 0, plans: 0, volumes: 0 };
  for (const [digest, admission] of admitted) {
    if (!digests.has(digest) && admission.state !== "erased") {
      orphans.admission += 1;
      problems.push({ code: "orphan_row", at: `owner_admission/${digest.slice(0, 16)}` });
    }
  }
  for (const [principal] of plans) {
    if (!principals.has(principal)) {
      orphans.plans += 1;
      problems.push({ code: "orphan_row", at: `plans/${sha256Hex(principal).slice(0, 16)}` });
    }
  }
  for (const [principal, volume] of volumes) {
    if (!principals.has(principal)) {
      orphans.volumes += 1;
      problems.push({ code: "orphan_row", at: `owner_volumes/${volume.uid}` });
    }
  }

  // The accounts' folder and each account in it are folders, never followed through a link.
  const accounts = join(where.shared, ".broker", "accounts");
  let orphanAccounts = 0;
  let accountNames: Buffer[] = [];
  if (lstatExists(accounts)) {
    if (lstatSync(accounts).isDirectory()) {
      accountNames = entriesOf(Buffer.from(accounts));
    } else {
      problems.push({ code: "unknown_entry", at: "shared/.broker/accounts" });
    }
  }
  const accountDirs = new Set<string>();
  for (const name of accountNames) {
    const text = nameText(name);
    if (text === null || !ACCOUNT_NAME.test(text)
        || !lstatSync(at(Buffer.from(accounts), name)).isDirectory()) {
      problems.push({ code: "unknown_entry", at: `shared/.broker/accounts/${hidden(name)}` });
    } else if (!digests.has(text)) {
      orphanAccounts += 1;
    } else {
      accountDirs.add(text);
    }
  }
  const pins = new Map(copy.manifest.snapshots.map((pin) => [pin.uid, pin]));
  const matcher = ignoredBy(DEFAULT_IGNORES);
  // Every image's own tombstones, read before any owner is decided: every image the manifest
  // pins, an erased owner's too. One that cannot be read whole stops the run, since whom
  // its tombstones name cannot be told.
  const inImages: string[] = [];
  const unread = (uid: number, why: string) =>
    problems.push({ code: "erasure_unread", at: `erasure/${uid}/${why}` });
  const bound = new Set([...volumes.values()].map((volume) => volume.uid));
  for (const pin of copy.manifest.snapshots) {
    if (!bound.has(pin.uid)) {
      unread(pin.uid, "no_volume");
      continue;
    }
    const mounted = copy.volume(pin.uid);
    if ("problem" in mounted) {
      unread(pin.uid, "not_mounted");
      continue;
    }
    const found = imageTombstones(mounted.root);
    if ("problem" in found) {
      unread(pin.uid, found.problem);
    } else {
      inImages.push(...found.digests);
    }
  }
  for (const uid of bound) {
    if (!pins.has(uid)) {
      unread(uid, "not_pinned");
    }
  }
  const facts = withImageFacts(erasure, inImages);
  const tombstones = [...facts.facts].filter(([digest]) => !digests.has(digest));
  const owners = identities.map((identity) => {
    const digest = sha256Hex(identity.principal);
    const volume = volumes.get(identity.principal) ?? null;
    const owner: Owner = {
      principal: identity.principal, digest, key: digest.slice(0, 16),
      uid: identity.uid, admission: admitted.get(digest) ?? null,
      plan: plans.get(identity.principal) ?? null, volume,
      erasure: admitted.get(digest)?.state === "erased" ? "erased"
        : names(facts, identity.principal) ? "pending" : null,
      centers: [], tree: newTree(),
    };
    if (owner.erasure !== null) {
      return owner;
    }
    if (accountDirs.has(digest)) {
      surveyAccount(owner, join(accounts, digest));
    }
    if (volume === null && pins.has(identity.uid)) {
      // A pinned image no volume row binds: never left unread.
      owner.tree.problems.push({ code: "pin_mismatch", at: `volume/${identity.uid}` });
      return owner;
    }
    return surveyVolume(owner, copy, pins.get(volume?.uid ?? -1) ?? null, matcher);
  });
  for (const pin of copy.manifest.snapshots) {
    if (!owners.some((owner) => owner.uid === pin.uid)) {
      problems.push({ code: "pin_mismatch", at: `pin/${pin.uid}` });
    }
  }
  const noVolume = owners.filter((owner) => owner.erasure === null && owner.volume === null
                                 && !pins.has(owner.uid!))
    .length;
  // A job whose owner is no identity: whose data it would withhold cannot be told.
  for (const [principal, job] of jobs) {
    if (!principals.has(principal)) {
      problems.push({ code: "orphan_row", at: `deletion_jobs/${job.id.slice(0, 16)}` });
    }
  }
  const erasurePending = owners.filter((owner) => owner.erasure === "pending").length;
  return { problems, stores, owners, orphanAccounts, orphans, noVolume, erasurePending,
           erasure: facts,
           tombstones: {
             principals: tombstones.filter(([, fact]) => fact.principal !== null)
               .map(([, fact]) => fact.principal!),
             digests: tombstones.filter(([, fact]) => fact.principal === null)
               .map(([digest]) => digest),
           } };
}

function lstatExists(path: string): boolean {
  try {
    lstatSync(path);
    return true;
  } catch {
    return false;
  }
}

/** The owner's own broker stores: the carried and archived ones under `.records/broker/`. */
function surveyAccount(owner: Owner, dir: string): void {
  if (!lstatExists(dir)) {
    return;
  }
  const tree = owner.tree;
  visitLevel(Buffer.from(dir), ACCOUNT, "account", {
    store: (name, path, rule, tables) => {
      archive(tree, `${IMPORTED}/${RECORDS}/broker/${name}`, path, rule, tables);
      const has = (table: string) => tables.some((t) => t.table === table);
      if (name === "policy.db" && has("policy")) {
        tree.rows.policy = tables.find((t) => t.table === "policy")!.rows;
      }
      if (name === "conversations.db" && has("collection_rows")) {
        const db = openStore(path);
        try {
          const row = db.prepare("SELECT COUNT(*) AS n, " +
                                 "COALESCE(SUM(length(CAST(body AS BLOB))), 0) AS bytes " +
                                 "FROM collection_rows").get()!;
          tree.rows.collection = Number(row["n"]);
          tree.rows.body_bytes = Number(row["bytes"]);
        } finally {
          db.close();
        }
      }
    },
  }, tree.problems, tree.stores);
}

/** The owner's image: its root, each center and each center's sessions. */
function surveyVolume(owner: Owner, copy: Copy, pin: { snapshot_id: string; sha256: string } | null,
                      matcher: Matcher): Owner {
  const tree = owner.tree;
  const volume = owner.volume;
  if (volume === null) {
    return owner;
  }
  if (volume.uid !== owner.uid) {
    tree.problems.push({ code: "pin_mismatch", at: `volume/${volume.uid}` });
    return owner;
  }
  if (volume.work !== null) {
    tree.problems.push({ code: "pending_work", at: `volume/${volume.uid}` });
  }
  if (volume.snapshot_id === null || pin === null) {
    tree.problems.push({ code: "unpinned", at: `volume/${volume.uid}` });
    return owner;
  }
  if (pin.snapshot_id !== volume.snapshot_id || pin.sha256 !== volume.snapshot_sha256) {
    tree.problems.push({ code: "pin_mismatch", at: `volume/${volume.uid}` });
    return owner;
  }
  const mounted = copy.volume(volume.uid);
  if ("problem" in mounted) {
    tree.problems.push({ code: "not_mounted", at: `volume/${volume.uid}` });
    return owner;
  }
  const root = Buffer.from(mounted.root);
  const centersDb = join(mounted.root, ".centers.db");
  const centers = lstatExists(centersDb) && lstatSync(centersDb).isFile() && isStore(centersDb)
    ? rows(centersDb, "centers").map((row) => ({ name: String(row["center"]),
                                                  retired: Number(row["retired"]) !== 0 }))
    : [];
  for (const center of centers) {
    if (!CENTER_NAME.test(center.name)) {
      throw new Error("a centers row is no center name the old stack mints");
    }
  }
  const names = new Set(centers.map((center) => center.name));
  for (const center of centers) {
    if (ruleOf(ROOT, center.name) !== null) {
      // A center named as a root entry would be read by that entry's rule, not as a center.
      tree.problems.push({ code: "path_collision", at: `center/${hidden(center.name)}` });
    }
  }
  const centerDirs: string[] = [];
  visitLevel(root, ROOT, "root", {
    store: (name, path, rule, tables) =>
      archive(tree, `${IMPORTED}/${RECORDS}/${name}`, path, rule, tables),
    user: (name, path) => walkUser(path, `${IMPORTED}/${name}`, Buffer.from(name), tree, matcher),
    other: (name) => {
      if (names.has(name) && lstatSync(join(mounted.root, name)).isDirectory()) {
        centerDirs.push(name);
        return true;
      }
      return false;
    },
  }, tree.problems, tree.stores);
  for (const center of centerDirs) {
    const base = `${IMPORTED}/${center}`;
    visitLevel(Buffer.from(join(mounted.root, center)), CENTER, `center`, {
      store: (name, path, rule, tables) =>
        archive(tree, `${base}/${RECORDS}/${name}`, path, rule, tables),
      user: (name, path) =>
        walkUser(path, `${base}/${name}`, Buffer.from(`${center}/${name}`), tree, matcher),
    }, tree.problems, tree.stores);
  }
  const sessions = join(mounted.root, ".agent-sessions");
  if (lstatExists(sessions) && lstatSync(sessions).isDirectory()) {
    for (const name of entriesOf(Buffer.from(sessions))) {
      const center = nameText(name);
      if (center === null || !names.has(center)
          || !lstatSync(at(Buffer.from(sessions), name)).isDirectory()) {
        tree.problems.push({ code: "unknown_center", at: `sessions/${hidden(name)}` });
        continue;
      }
      visitLevel(at(Buffer.from(sessions), name), SESSIONS, "sessions", {
        store: (store, path, rule, tables) =>
          archive(tree, `${IMPORTED}/${center}/${RECORDS}/sessions/${store}`, path, rule, tables),
      }, tree.problems, tree.stores);
    }
  }
  const paths = new Set<string>();
  for (const item of [...tree.carried, ...tree.archived]) {
    if (paths.has(item.path)) {
      tree.problems.push({ code: "path_collision", at: `imported/${hidden(item.path)}` });
    }
    paths.add(item.path);
  }
  return { ...owner, centers };
}
