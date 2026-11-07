/**
 * The bundle builder (`import_research.md` §3, slice 2): the backup copy read into one
 * bundle an owner, in the format the import doors and the doors' consumer take (slices 3
 * and 4), written to a local folder. Uploading it to R2 staging is slice 4's.
 *
 * A bundle, at `<out>/<key>/` (`key` = `sha256(principal)[:16]`):
 * - `owner.json`: the owner's name (the old principal), uid, admission, tier and notice;
 * - `manifest/<n>.json`: every folder and file of the owner's imported tree, parents
 *   first, with each file's size, executable bit, mtime and expected content id, computed
 *   by the contract's own `inlineId`/`chunkedId` over the backup's bytes; a page holds at
 *   most `PAGE_ROWS` entries and `PAGE_BYTES` encoded (the export's page);
 * - `inline/<content id>` (content of `INLINE_BYTES` or less) and `chunks/<sha256>` (each
 *   `CHUNK_BYTES` chunk of larger content): the bytes, each named by what checks it;
 * - `rows/<n>.json`: the rows that carry live, the policy then the collections, in pages
 *   of the same bounds;
 * - `bundle.json`, written last: each part's SHA-256 and the counts. Its own SHA-256 is the
 *   bundle's digest, which the consumer's message is signed over.
 *
 * Beside the carried files, the tree holds `/workspace/imported/README.md` (what is
 * where), `links.txt` (each link and its target: never followed, never made) and
 * `refused.tar` (the names the files plane cannot store, kept verbatim).
 *
 * The owners on the exclusion list (the founder's and the canary's: they start fresh) get
 * the same bundle in the private folder instead, never in `out`. The list is input; each
 * name on it must be an owner of the backup, so a typo never lets an excluded owner be
 * imported. A dry run reads and hashes everything it would carry and writes nothing.
 */
import { closeSync, lstatSync, mkdirSync, openSync, readSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { chunkedId, inlineId } from "../contract/src/content";
import { sha256Hex } from "../contract/src/digest";
import { CHUNK_BYTES, FILE_DEFAULTS, INLINE_BYTES } from "../contract/src/limits";
import { ancestors, WORKSPACE } from "../contract/src/paths";
import { utf8Text } from "../contract/src/text";

import { layout, openFile, openStore, quoted } from "./backup";
import {
  erased, LINKS, ownerProblems, README, REFUSED, starterPaths, type Tiers, verifyCopy,
} from "./census";
import { type Archived, type Copy, IMPORTED, type Owner, type Problem, survey } from "./survey";
import { TarWriter } from "./tar";

/** The bundle format this builder writes. */
export const FORMAT = 1;
/** A page's most entries or rows, and its most bytes encoded: the export's page. */
export const PAGE_ROWS = 256;
export const PAGE_BYTES = 1024 * 1024;
/** The notice the owner is given once imported (research §3, order step 4). */
export const NOTICE = "Your files from the old TinyAssets are in /workspace/imported";

export interface Entry {
  readonly path: string;
  readonly kind: "dir" | "file";
  readonly size?: number;
  readonly mode?: 0 | 1;
  readonly mtime_ms?: number;
  readonly content_id?: string;
  /** A chunked file's chunks, in order (`chunks/<sha256>`). */
  readonly chunks?: readonly string[];
}

export type Row =
  | { readonly kind: "policy"; readonly document: unknown }
  | { readonly kind: "collection"; readonly collection: string; readonly row_id: string;
      readonly private: 0 | 1; readonly body: string };

export interface Options {
  /** Where the bundles go; null for a dry run, which writes nothing. */
  readonly out: string | null;
  /** The private folder: the excluded owners' bundles and the private cutover record. */
  readonly private: string | null;
  /** The principals whose owners start fresh: backed up, never imported. */
  readonly exclude: ReadonlySet<string>;
  readonly tiers: Tiers;
}

export interface Summary {
  readonly dry_run: boolean;
  readonly owners: number;
  readonly bundled: number;
  readonly excluded: number;
  readonly erased: number;
  readonly refused: Readonly<Record<string, number>>;
  readonly entries: number;
  readonly files: number;
  readonly inline: number;
  readonly chunks: number;
  readonly bytes: number;
  readonly rows: number;
  /** Each written bundle's digest, by owner key. */
  readonly digests: Readonly<Record<string, string>>;
}

/** An exclusion list: one principal a line; blank lines and `#` comments are skipped. */
export function exclusionList(text: string): Set<string> {
  return new Set(text.split("\n").map((line) => line.trim())
    .filter((line) => line !== "" && !line.startsWith("#")));
}

/** Path order, parents first: by each component's UTF-8 bytes. */
function byPath(a: string, b: string): number {
  const x = a.split("/");
  const y = b.split("/");
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    const order = Buffer.compare(Buffer.from(x[i]!), Buffer.from(y[i]!));
    if (order !== 0) {
      return order;
    }
  }
  return x.length - y.length;
}

/** Items in pages of at most `PAGE_ROWS` items and `PAGE_BYTES` encoded. */
export function pages<T>(items: readonly T[]): T[][] {
  const found: T[][] = [];
  let page: T[] = [];
  let bytes = 0;
  for (const item of items) {
    const size = Buffer.byteLength(JSON.stringify(item)) + 1;
    if (page.length > 0 && (page.length === PAGE_ROWS || bytes + size > PAGE_BYTES)) {
      found.push(page);
      page = [];
      bytes = 0;
    }
    if (size > PAGE_BYTES) {
      throw new Error("one entry or row is larger than a page");
    }
    page.push(item);
    bytes += size;
  }
  return page.length > 0 ? [...found, page] : found;
}

/** The owner's live rows: its policy document, then its collections' rows by collection and seq. */
function rowsOf(owner: Owner, copy: Copy): Row[] {
  const account = join(layout(copy.work).shared, ".broker", "accounts", owner.digest);
  const read = (store: string, sql: string): Record<string, unknown>[] => {
    const at = join(account, store);
    try {
      lstatSync(at);
    } catch {
      return [];
    }
    const db = openStore(at);
    try {
      const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?")
        .get(sql.match(/FROM (\w+)/)![1]!);
      return has === undefined ? [] : db.prepare(sql).all() as Record<string, unknown>[];
    } finally {
      db.close();
    }
  };
  const policy = read("policy.db", "SELECT document FROM policy")
    .map((row): Row => ({ kind: "policy", document: JSON.parse(String(row["document"])) }));
  const collections = read("conversations.db", "SELECT collection, row_id, private, body " +
                           "FROM collection_rows ORDER BY collection, seq")
    .map((row): Row => ({ kind: "collection", collection: String(row["collection"]),
                          row_id: String(row["row_id"]), private: Number(row["private"]) ? 1 : 0,
                          body: String(row["body"]) }));
  return [...policy, ...collections];
}

/** Content written into a bundle, each piece once. */
class Store {
  readonly #dir: string | null;
  readonly #written = new Set<string>();
  inline = 0;
  chunks = 0;
  bytes = 0;
  inlineBytes = 0;

  constructor(dir: string | null) {
    this.#dir = dir;
  }

  #put(kind: "inline" | "chunks", name: string, bytes: Uint8Array): boolean {
    const key = `${kind}/${name}`;
    if (this.#written.has(key)) {
      return false;
    }
    this.#written.add(key);
    if (this.#dir !== null) {
      writeFileSync(join(this.#dir, kind, name), bytes, { flag: "wx" });
    }
    return true;
  }

  /**
   * The file at `source` stored, its content id and chunks computed as the files plane does.
   * Its size is held to what the survey read, so a file that changed under it fails loudly.
   */
  file(source: Buffer | string, size: number): { content_id: string; chunks?: string[] } {
    const fd = openFile(source);
    try {
      if (size <= INLINE_BYTES) {
        const bytes = Buffer.alloc(size);
        if (readSync(fd, bytes, 0, size, 0) !== size || readSync(fd, Buffer.alloc(1), 0, 1, size)) {
          throw new Error("a file changed size while the bundle read it");
        }
        const id = inlineId(bytes);
        if (this.#put("inline", id, bytes)) {
          this.inline += 1;
          this.inlineBytes += size;
        }
        return { content_id: id };
      }
      const shas: string[] = [];
      const buffer = Buffer.allocUnsafe(CHUNK_BYTES);
      for (let offset = 0; offset < size; offset += CHUNK_BYTES) {
        const want = Math.min(CHUNK_BYTES, size - offset);
        if (readSync(fd, buffer, 0, want, offset) !== want) {
          throw new Error("a file changed size while the bundle read it");
        }
        const piece = buffer.subarray(0, want);
        const sha = sha256Hex(piece);
        shas.push(sha);
        if (this.#put("chunks", sha, piece)) {
          this.chunks += 1;
          this.bytes += want;
        }
      }
      if (readSync(fd, Buffer.alloc(1), 0, 1, size) !== 0) {
        throw new Error("a file changed size while the bundle read it");
      }
      return { content_id: chunkedId(size, shas), chunks: shas };
    } finally {
      closeSync(fd);
    }
  }
}

/** A store's kept tables copied into a new SQLite file at `to`: rows verbatim, no index. */
function tablesCopy(archived: Archived, to: string): void {
  const db = new DatabaseSync(to);
  try {
    const source = utf8Text(archived.source, "keep");
    db.prepare("ATTACH DATABASE ? AS src").run(
      `file:${encodeURI(source).replace(/[?#]/g, encodeURIComponent)}?mode=ro&immutable=1`);
    for (const table of archived.tables) {
      const made = db.prepare("SELECT sql FROM src.sqlite_master WHERE type = 'table' AND name = ?")
        .get(table.table);
      db.exec(String(made!["sql"]));
      db.exec(`INSERT INTO main.${quoted(table.table)} SELECT * FROM src.${quoted(table.table)}`);
    }
    db.exec("DETACH DATABASE src");
  } finally {
    db.close();
  }
}

/** The tree's README: what is where, and what each archived store holds. */
function readme(owner: Owner): string {
  const tree = owner.tree;
  const lines = [
    "# Your files from the old TinyAssets",
    "",
    "Everything here was copied unchanged from your old TinyAssets when it moved to the new " +
    "platform. Nothing here is used by your agents unless you or they choose to.",
    "",
  ];
  if (owner.centers.length > 0) {
    lines.push("Each command center you had is a folder here:", "",
               ...owner.centers.map((center) => `- \`${center.name}/\`` +
                                               (center.retired ? " (retired)" : "")), "");
  }
  if (tree.archived.length > 0) {
    lines.push("Your old records are SQLite files in the `.records` folders (copies, read-only):",
               "", "| File | Table | Rows |", "|---|---|---|");
    for (const archived of tree.archived) {
      const file = archived.path.slice(IMPORTED.length + 1);
      lines.push(...(archived.whole ? [`| \`${file}\` | (all, the file as it was) | |`]
        : archived.tables.map((table) => `| \`${file}\` | ${table.table} | ${table.rows} |`)));
    }
    lines.push("");
  }
  if (tree.links.length > 0) {
    lines.push(`\`links.txt\` lists the ${tree.links.length} links your files held and where ` +
               "each pointed. They were not followed and are not made here.", "");
  }
  if (tree.refused.length > 0) {
    lines.push(`\`refused.tar\` keeps ${tree.refused.length} names that cannot be stored as ` +
               "files here (not UTF-8, or longer than 255 bytes a name or 4,096 a path), " +
               "with everything in them, unchanged.", "");
  }
  return `${lines.join("\n")}`;
}

/** `links.txt`: each link's path and its target, as JSON strings, one a line. */
function linksText(owner: Owner): string {
  return ["# Each link your old files held: its path -> where it pointed. Not followed.",
          ...owner.tree.links.map((link) =>
            `${JSON.stringify(link.path)} -> ${JSON.stringify(link.target)}`), ""].join("\n");
}

/** The backup's own time (its archive's stamp): the mtime of the files the import makes. */
function backupTime(copy: Copy): number {
  const stamp = /(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z/.exec(copy.manifest.archive)!;
  return Date.parse(`${stamp[1]}T${stamp[2]}:${stamp[3]}:${stamp[4]}Z`);
}

interface Built {
  readonly entries: number;
  readonly files: number;
  readonly rows: number;
  readonly digest: string | null;
  readonly store: Store;
  readonly problems: Problem[];
}

/**
 * One owner's bundle at `dir` (or, with `dir` null, everything it would hold computed and
 * nothing written). The fit is checked again on the exact counts.
 */
export function buildOwner(owner: Owner, copy: Copy, dir: string | null, tiers: Tiers,
                           fit: boolean): Built {
  const store = new Store(dir);
  const work = dir === null ? null : join(dir, ".work");
  if (dir !== null) {
    mkdirSync(dir, { recursive: false, mode: 0o700 });
    for (const part of ["inline", "chunks", "manifest", "rows", ".work"]) {
      mkdirSync(join(dir, part), { mode: 0o700 });
    }
  }
  const entries = new Map<string, Entry>();
  const made = backupTime(copy);
  const file = (path: string, source: Buffer | string, size: number, mode: 0 | 1,
                mtime: number) => {
    entries.set(path, { path, kind: "file", size, mode, mtime_ms: mtime,
                        ...store.file(source, size) });
  };
  for (const item of owner.tree.carried) {
    if (item.kind === "dir") {
      entries.set(item.path, { path: item.path, kind: "dir" });
    } else {
      file(item.path, item.source!, item.size, item.mode, item.mtime_ms);
    }
  }
  let generated = 0;
  const make = (path: string, write: (at: string) => void, mtime = made) => {
    generated += 1;
    if (work === null) {
      return;
    }
    const at = join(work, String(generated));
    write(at);
    file(path, at, lstatSync(at).size, 0, mtime);
  };
  for (const archived of owner.tree.archived) {
    if (archived.whole) {
      file(archived.path, archived.source, archived.size, 0,
           Math.floor(lstatSync(archived.source).mtimeMs));
    } else {
      make(archived.path, (at) => tablesCopy(archived, at),
           Math.floor(lstatSync(archived.source).mtimeMs));
    }
  }
  if (owner.tree.links.length > 0) {
    make(LINKS, (at) => writeFileSync(at, linksText(owner), { flag: "wx" }));
  }
  if (owner.tree.refused.length > 0) {
    make(REFUSED, (at) => {
      const tar = new TarWriter(openSync(at, "wx", 0o600));
      for (const refusal of owner.tree.refused) {
        tar.add(refusal.member, refusal.source);
      }
      tar.end();
    });
  }
  if (entries.size > 0 || generated > 0) {
    make(README, (at) => writeFileSync(at, readme(owner), { flag: "wx" }));
  }
  for (const path of [...entries.keys()]) {
    for (const parent of ancestors(path)) {
      if (!entries.has(parent)) {
        entries.set(parent, { path: parent, kind: "dir" });
      }
    }
  }
  const ordered = [...entries.values()].sort((a, b) => byPath(a.path, b.path));
  const rows = rowsOf(owner, copy);
  const problems: Problem[] = [];
  if (fit && dir !== null) {
    const all = new Set([...entries.keys(), ...starterPaths()]);
    for (const path of starterPaths()) {
      ancestors(path).forEach((parent) => all.add(parent));
    }
    all.delete(WORKSPACE);
    const largest = ordered.reduce((a, entry) => Math.max(a, entry.size ?? 0), 0);
    const stored = store.bytes + store.inlineBytes + owner.tree.rows.body_bytes;
    const tier = tiers[owner.plan?.tier ?? "free"];
    const at = `owner/${owner.key}`;
    if (all.size > FILE_DEFAULTS.max_entries) {
      problems.push({ code: "over_entries", at });
    }
    if (store.inlineBytes > FILE_DEFAULTS.max_inline_bytes) {
      problems.push({ code: "over_inline_bytes", at });
    }
    if (largest > FILE_DEFAULTS.max_file_bytes) {
      problems.push({ code: "over_file_bytes", at });
    }
    if (tier === undefined || stored > tier.storage_bytes) {
      problems.push({ code: tier === undefined ? "no_tier" : "over_storage", at });
    }
  }
  let digest: string | null = null;
  if (dir !== null) {
    rmSync(join(dir, ".work"), { recursive: true });
    if (problems.length > 0) {
      rmSync(dir, { recursive: true });
    } else {
      const parts: Record<string, string> = {};
      const part = (name: string, value: unknown) => {
        const text = `${JSON.stringify(value)}\n`;
        writeFileSync(join(dir, name), text, { flag: "wx", mode: 0o600 });
        parts[name] = sha256Hex(text);
      };
      part("owner.json", {
        format: FORMAT, owner: owner.principal, key: owner.key, uid: owner.uid,
        admission: owner.admission, tier: owner.plan?.tier ?? "free", notice: NOTICE,
        centers: owner.centers,
      });
      pages(ordered).forEach((page, n) => part(`manifest/${n}.json`, { entries: page }));
      pages(rows).forEach((page, n) => part(`rows/${n}.json`, { rows: page }));
      const index = `${JSON.stringify({
        format: FORMAT, key: owner.key, owner: owner.principal, parts,
        counts: { entries: ordered.length, inline: store.inline, chunks: store.chunks,
                  chunk_bytes: store.bytes, inline_bytes: store.inlineBytes, rows: rows.length },
      })}\n`;
      writeFileSync(join(dir, "bundle.json"), index, { flag: "wx", mode: 0o600 });
      digest = sha256Hex(index);
    }
  }
  return { entries: ordered.length, files: ordered.filter((e) => e.kind === "file").length,
           rows: rows.length, digest, store, problems };
}

/**
 * Every owner's bundle. The backup copy is verified first; a fleet-wide problem (the
 * archive, a pin, a shared store or entry no rule names) stops the whole run, and an
 * owner's own problem stops that owner alone, counted, while the others go on
 * (`restore_credentials.py`'s rule).
 */
export function buildBundles(copy: Copy, options: Options): Summary {
  const dry = options.out === null;
  if (!dry && (options.private === null || options.private === options.out)) {
    throw new Error("the bundles need a private folder of their own for the excluded owners");
  }
  const fleetProblems = [...verifyCopy(copy)];
  const fleet = survey(copy);
  fleetProblems.push(...fleet.problems);
  if (fleetProblems.length > 0) {
    throw new Error(`the backup copy is refused: ${[...new Set(fleetProblems.map((p) => p.code))]
      .sort().join(", ")}`);
  }
  const principals = new Set(fleet.owners.map((owner) => owner.principal));
  const missing = [...options.exclude].filter((principal) => !principals.has(principal));
  if (missing.length > 0) {
    throw new Error(`${missing.length} name(s) on the exclusion list are no owner of this ` +
                    "backup: fix the list before anything is written");
  }
  const refused: Record<string, number> = {};
  const digests: Record<string, string> = {};
  const totals = { bundled: 0, excluded: 0, entries: 0, files: 0, inline: 0, chunks: 0,
                   bytes: 0, rows: 0 };
  const record: Record<string, unknown>[] = [];
  const live = fleet.owners.filter((owner) => !erased(owner));
  for (const owner of live) {
    const excluded = options.exclude.has(owner.principal);
    const problems = excluded ? owner.tree.problems : ownerProblems(owner, options.tiers);
    if (problems.length > 0) {
      for (const code of new Set(problems.map((problem) => problem.code))) {
        refused[code] = (refused[code] ?? 0) + 1;
      }
      continue;
    }
    const base = excluded ? options.private : options.out;
    const built = buildOwner(owner, copy, base === null ? null : join(base, owner.key),
                             options.tiers, !excluded);
    if (built.problems.length > 0) {
      for (const code of new Set(built.problems.map((problem) => problem.code))) {
        refused[code] = (refused[code] ?? 0) + 1;
      }
      continue;
    }
    totals[excluded ? "excluded" : "bundled"] += 1;
    totals.entries += built.entries;
    totals.files += built.files;
    totals.inline += built.store.inline;
    totals.chunks += built.store.chunks;
    totals.bytes += built.store.bytes + built.store.inlineBytes;
    totals.rows += built.rows;
    if (built.digest !== null) {
      digests[owner.key] = built.digest;
    }
    record.push({ key: owner.key, owner: owner.principal, uid: owner.uid, excluded,
                  tier: owner.plan?.tier ?? "free", ends_at: owner.plan?.ends_at ?? null,
                  customer_id: owner.plan?.customer_id ?? null,
                  subscription_id: owner.plan?.subscription_id ?? null });
  }
  if (!dry) {
    writeFileSync(join(options.private!, "cutover.private.json"),
                  `${JSON.stringify({ format: FORMAT, owners: record }, null, 2)}\n`,
                  { flag: "wx", mode: 0o600 });
  }
  return { dry_run: dry, owners: fleet.owners.length, erased: fleet.owners.length - live.length,
           refused, digests, ...totals };
}
