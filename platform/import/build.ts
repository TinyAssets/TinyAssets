/**
 * One owner's imported tree, made once for both the census and the bundle builder, so the
 * two count it by one rule: the files plane's own (`owner/src/files.ts` and the schema's
 * triggers). An entry is a folder or a file; a file's content is inline at `INLINE_BYTES` or
 * less, else `CHUNK_BYTES` chunks; the stored bytes the tier's storage is held against are
 * the unique chunks' bytes, the unique inline contents' bytes and the collection rows'
 * bodies (`stored = chunk_bytes + inline_bytes + body_bytes`), each content and chunk
 * charged once however many paths hold it.
 *
 * Beside what it carries, the tree holds what the import makes: `README.md`, `links.txt`
 * (each link and its target: never followed, never made), `refused.tar` (the names the
 * files plane cannot store) and `ignored.tar` (what the sync's defaults leave out of a
 * live tree, kept verbatim all the same, Fact 9), and the archived stores' table copies.
 * The census makes them in a temporary folder of its own and deletes them; the bundle
 * builder makes them in the bundle's.
 */
import {
  closeSync, lstatSync, mkdirSync, mkdtempSync, openSync, readSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { chunkedId, inlineId } from "../contract/src/content";
import { sha256Hex } from "../contract/src/digest";
import { CHUNK_BYTES, FILE_DEFAULTS, INLINE_BYTES } from "../contract/src/limits";
import { ancestors, WORKSPACE } from "../contract/src/paths";
import starterJson from "../starter/starter.json" with { type: "json" };

import { layout, openFile, openStore, quoted, storeUri } from "./backup";
import { type Archived, type Copy, IMPORTED, type Owner, type Problem, type Refusal } from "./survey";
import { TarWriter } from "./tar";

/** The files the import itself writes into an owner's tree beside what it carries. */
export const README = `${IMPORTED}/README.md`;
export const LINKS = `${IMPORTED}/links.txt`;
export const REFUSED = `${IMPORTED}/refused.tar`;
export const IGNORED = `${IMPORTED}/ignored.tar`;

/** The tier numbers, as the control record (`c:control`'s `tiers`) holds them. */
export type Tiers = Readonly<Record<string, { readonly storage_bytes: number }>>;

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

/** The starter's own paths, which it seeds around the imported tree (`starter.ts`). */
export function starterPaths(): string[] {
  return (starterJson as { files: { path: string }[] }).files.map((file) => file.path);
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

/** The owner's live rows: its policy document, then its collections' rows by collection and seq. */
function rowsOf(owner: Owner, copy: Copy): Row[] {
  const account = join(layout(copy.work).shared, ".broker", "accounts", owner.digest);
  const read = (store: string, table: string, sql: string): Record<string, unknown>[] => {
    const at = join(account, store);
    try {
      lstatSync(at);
    } catch {
      return [];
    }
    const db = openStore(at);
    try {
      const has = db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?")
        .get(table);
      return has === undefined ? [] : db.prepare(sql).all() as Record<string, unknown>[];
    } finally {
      db.close();
    }
  };
  const policy = read("policy.db", "policy", "SELECT document FROM policy")
    .map((row): Row => ({ kind: "policy", document: JSON.parse(String(row["document"])) }));
  const collections = read("conversations.db", "collection_rows",
                           "SELECT collection, row_id, private, body FROM collection_rows " +
                           "ORDER BY collection, seq")
    .map((row): Row => ({ kind: "collection", collection: String(row["collection"]),
                          row_id: String(row["row_id"]), private: Number(row["private"]) ? 1 : 0,
                          body: String(row["body"]) }));
  return [...policy, ...collections];
}

/** Content a tree holds, each piece once, as the files plane charges it; written to `dir`. */
export class Store {
  readonly #dir: string | null;
  readonly #seen = new Set<string>();
  inline = 0;
  chunks = 0;
  chunkBytes = 0;
  inlineBytes = 0;

  constructor(dir: string | null) {
    this.#dir = dir;
  }

  #put(kind: "inline" | "chunks", name: string, bytes: Uint8Array): boolean {
    const key = `${kind}/${name}`;
    if (this.#seen.has(key)) {
      return false;
    }
    this.#seen.add(key);
    if (this.#dir !== null) {
      writeFileSync(join(this.#dir, kind, name), bytes, { flag: "wx" });
    }
    return true;
  }

  /**
   * The file at `source`, its content id and chunks computed as the files plane does. Its
   * size is held to what the survey read, so a file that changed under it fails loudly.
   */
  file(source: Buffer | string, size: number): { content_id: string; chunks?: string[] } {
    const fd = openFile(source);
    try {
      if (size <= INLINE_BYTES) {
        const bytes = Buffer.alloc(size);
        if (readSync(fd, bytes, 0, size, 0) !== size || readSync(fd, Buffer.alloc(1), 0, 1, size)) {
          throw new Error("a file changed size while the import read it");
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
          throw new Error("a file changed size while the import read it");
        }
        const piece = buffer.subarray(0, want);
        const sha = sha256Hex(piece);
        shas.push(sha);
        if (this.#put("chunks", sha, piece)) {
          this.chunks += 1;
          this.chunkBytes += want;
        }
      }
      if (readSync(fd, Buffer.alloc(1), 0, 1, size) !== 0) {
        throw new Error("a file changed size while the import read it");
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
    db.prepare("ATTACH DATABASE ? AS src").run(storeUri(archived.source));
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
  if (tree.ignoredTrees.length > 0) {
    lines.push(`\`ignored.tar\` keeps ${tree.ignored} files your agents can rebuild ` +
               "(dependencies, build outputs, caches, by the sync's default list), unchanged, " +
               "so nothing of yours is left behind.", "");
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

function tarOf(at: string, members: readonly Refusal[]): void {
  const tar = new TarWriter(openSync(at, "wx", 0o600));
  for (const member of members) {
    tar.add(member.member, member.source);
  }
  tar.end();
}

export interface Built {
  /** Every entry, parents first. */
  readonly entries: readonly Entry[];
  readonly rows: readonly Row[];
  readonly store: Store;
}

/**
 * The owner's tree made: every entry with its content id, every content and chunk stored
 * in `dir`'s `inline/` and `chunks/` (or, with `dir` null, counted and not kept), and the
 * live rows. The files the import makes are made in `dir`'s `.work/` (or a temporary folder
 * of the job's own) and deleted after.
 */
export function buildTree(owner: Owner, copy: Copy, dir: string | null): Built {
  const store = new Store(dir);
  const work = dir === null ? mkdtempSync(join(tmpdir(), "ta-import-work-"))
    : join(dir, ".work");
  if (dir !== null) {
    for (const part of ["inline", "chunks", ".work"]) {
      mkdirSync(join(dir, part), { mode: 0o700 });
    }
  }
  try {
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
      const at = join(work, String(generated));
      write(at);
      file(path, at, lstatSync(at).size, 0, mtime);
    };
    for (const archived of owner.tree.archived) {
      const mtime = Math.floor(lstatSync(archived.source).mtimeMs);
      if (archived.whole) {
        file(archived.path, archived.source, archived.size, 0, mtime);
      } else {
        make(archived.path, (at) => tablesCopy(archived, at), mtime);
      }
    }
    if (owner.tree.links.length > 0) {
      make(LINKS, (at) => writeFileSync(at, linksText(owner), { flag: "wx" }));
    }
    if (owner.tree.refused.length > 0) {
      make(REFUSED, (at) => tarOf(at, owner.tree.refused));
    }
    if (owner.tree.ignoredTrees.length > 0) {
      make(IGNORED, (at) => tarOf(at, owner.tree.ignoredTrees));
    }
    if (entries.size > 0) {
      make(README, (at) => writeFileSync(at, readme(owner), { flag: "wx" }));
    }
    for (const path of [...entries.keys()]) {
      for (const parent of ancestors(path)) {
        if (!entries.has(parent)) {
          entries.set(parent, { path: parent, kind: "dir" });
        }
      }
    }
    return { entries: [...entries.values()].sort((a, b) => byPath(a.path, b.path)),
             rows: rowsOf(owner, copy), store };
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
}

export interface Fit {
  readonly entries: number;
  readonly inline_bytes: number;
  readonly largest: number;
  readonly stored: number;
  readonly problems: Problem[];
}

/**
 * The owner's fit against the platform's own limits, on the tree as the files plane would
 * hold it: its entries (folders count, and the starter's seed with them), its small files'
 * unique bytes inline (`max_inline_bytes`), its largest file (`max_file_bytes`), and every
 * byte stored against the tier's storage. An owner past one is refused before the window
 * (research §3: the import never raises a setting; the fix is the owner's, told).
 * `max_manifest_bytes` and `max_history` cannot bind first: 30,000 entries at one version
 * each take well under either.
 */
export function fitOf(owner: Owner, built: Built, tiers: Tiers): Fit {
  const all = new Set<string>([...built.entries.map((entry) => entry.path), ...starterPaths()]);
  for (const path of starterPaths()) {
    ancestors(path).forEach((parent) => all.add(parent));
  }
  all.delete(WORKSPACE);
  const fit = {
    entries: all.size,
    inline_bytes: built.store.inlineBytes,
    largest: built.entries.reduce((a, entry) => Math.max(a, entry.size ?? 0), 0),
    stored: built.store.chunkBytes + built.store.inlineBytes + owner.tree.rows.body_bytes,
    problems: [] as Problem[],
  };
  const at = `owner/${owner.key}`;
  const over = (code: Problem["code"], value: number, limit: number) => {
    if (value > limit) {
      fit.problems.push({ code, at });
    }
  };
  over("over_entries", fit.entries, FILE_DEFAULTS.max_entries);
  over("over_inline_bytes", fit.inline_bytes, FILE_DEFAULTS.max_inline_bytes);
  over("over_file_bytes", fit.largest, FILE_DEFAULTS.max_file_bytes);
  const tier = tiers[owner.plan?.tier ?? "free"];
  if (tier === undefined) {
    fit.problems.push({ code: "no_tier", at });
  } else {
    over("over_storage", fit.stored, tier.storage_bytes);
  }
  return fit;
}
