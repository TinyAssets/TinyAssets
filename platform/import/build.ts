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
 * files plane cannot store, and links whose target is not UTF-8) and `ignored.tar` (what the sync's defaults leave out of a
 * live tree, kept verbatim all the same, Fact 9), the archived stores' table copies, and
 * `policy.refused.json`: an old policy document the platform's validator refuses
 * (`contract/src/policy-document.ts`), kept verbatim while the owner keeps the default policy,
 * so a document the new rules refuse never stops an owner's import.
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
import {
  DEFAULT_TIER, TIER_NAMES, type TierName, type TierNumbers, type Tiers,
} from "../contract/src/control";
import { sha256Hex } from "../contract/src/digest";
import { CHUNK_BYTES, INLINE_BYTES } from "../contract/src/limits";
import { FILE_DEFAULTS } from "../contract/src/file-defaults";
import { chunkName, type Entry, inlineName, type Row } from "../contract/src/import-message";
import { ancestors, IMPORTED, PROFILE, WORKSPACE } from "../contract/src/paths";
import { validate } from "../contract/src/policy-document";
import { carried } from "../owner/src/starter-package";

import { layout, openFile, openStore, quoted, storeUri } from "./backup";
import { type Archived, type Copy, type Owner, type Problem, type Refusal } from "./survey";
import { TarWriter } from "./tar";

/** The files the import itself writes into an owner's tree beside what it carries. */
export const README = `${IMPORTED}/README.md`;
export const LINKS = `${IMPORTED}/links.txt`;
export const REFUSED = `${IMPORTED}/refused.tar`;
export const IGNORED = `${IMPORTED}/ignored.tar`;
export const POLICY_REFUSED = `${IMPORTED}/policy.refused.json`;

/** The tier the owner's plan names, by name, or `DEFAULT_TIER`. */
export const tierName = (owner: Owner): string => owner.plan?.tier ?? DEFAULT_TIER;

/** The numbers of the owner's tier in `tiers`, or none when its plan names no tier there. */
export function tierOf(owner: Owner, tiers: Tiers): TierNumbers | undefined {
  const name = tierName(owner);
  return (TIER_NAMES as readonly string[]).includes(name) ? tiers[name as TierName] : undefined;
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

/**
 * The owner's live rows: its policy document, then its collections' rows by collection and seq;
 * and the old policy as it was stored when the platform's validator refuses it (`validate`),
 * which is then no row: the owner keeps the default, and its document is archived verbatim.
 */
function rowsOf(owner: Owner, copy: Copy): { rows: Row[]; refusedPolicy: string | null } {
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
  const policy: Row[] = [];
  let refusedPolicy: string | null = null;
  for (const row of read("policy.db", "policy", "SELECT document FROM policy")) {
    const text = String(row["document"]);
    try {
      const document: unknown = JSON.parse(text);
      validate(document);
      policy.push({ kind: "policy", document });
    } catch {
      refusedPolicy = text;
    }
  }
  const collections = read("conversations.db", "collection_rows",
                           "SELECT collection, row_id, private, body FROM collection_rows " +
                           "ORDER BY collection, seq")
    .map((row): Row => ({ kind: "collection", collection: String(row["collection"]),
                          row_id: String(row["row_id"]), private: Number(row["private"]) ? 1 : 0,
                          body: String(row["body"]) }));
  return { rows: [...policy, ...collections], refusedPolicy };
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

  #put(part: string, bytes: Uint8Array): boolean {
    if (this.#seen.has(part)) {
      return false;
    }
    this.#seen.add(part);
    if (this.#dir !== null) {
      writeFileSync(join(this.#dir, part), bytes, { flag: "wx" });
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
        if (this.#put(inlineName(id), bytes)) {
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
        if (this.#put(chunkName(sha), piece)) {
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

/**
 * The tree's README: first what an agent tells its owner about the move, then what is where
 * and what each archived store holds. Beside the owner's one notice, it is the only place the
 * move is explained: written once per imported owner, and deleted by the owner with the folder,
 * so nothing about the move ships in the starter, the plugins or the MCP instructions.
 */
function readme(owner: Owner, policyArchived: boolean): string {
  const tree = owner.tree;
  const lines = [
    "# Your files from the old TinyAssets",
    "",
    "## For your agent",
    "",
    "If you are an agent reading this for its owner, tell them this in plain words before " +
    "your questions:",
    "",
    "- Their old TinyAssets data came across: their files are in this folder, copied " +
    "unchanged, and the rest of this file lists what is here. You may read it for context; " +
    "nothing in it is merged or used until they choose.",
    "- Their old schedules, reminders and automations came across as records only: none of " +
    "them runs now. Set one up again only if they ask.",
    "- None of their connections came across. In the TinyAssets app they can connect their " +
    "model sources again (the AI accounts their agents run on), connect the AI assistants " +
    "they use, and make new API keys. Each is their own act, in the app: never ask them to " +
    "paste a key, token or password into a conversation.",
    "- An offer to merge these files into their workspace, with them choosing what to keep: " +
    "say what is here, folder by folder, and on their word copy only what they pick, to where " +
    "they say, then check it. Never merge on your own, never overwrite one of their files " +
    "without asking, and never delete or move anything in this folder until they say so; " +
    "then delete only what they named.",
    `- An offer to write \`- Old TinyAssets files are in ${IMPORTED}/: merge only what they ` +
    "pick, delete nothing there until they say` as a directive on their profile " +
    `(\`${PROFILE}\`), with when it was observed, so every agent of theirs keeps to it; ` +
    "rewrite it once they decide.",
    "",
    "## What is here",
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
    lines.push("Your old records are SQLite files in the `.records` folders (copies, kept as " +
               "they came: don't write to them):",
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
    lines.push(`\`refused.tar\` keeps ${tree.refused.length} entries that cannot be stored ` +
               "here, unchanged: names that are not UTF-8, or longer than 255 bytes a name or " +
               "4,096 a path, with everything in them; and links whose target is not UTF-8, " +
               "which `links.txt` does not list.", "");
  }
  if (tree.ignoredTrees.length > 0) {
    lines.push(`\`ignored.tar\` keeps ${tree.ignored} files your agents can rebuild ` +
               "(dependencies, build outputs, caches, by the sync's default list), unchanged, " +
               "so nothing of yours is left behind.", "");
  }
  if (policyArchived) {
    lines.push("`policy.refused.json` is your old policy, unchanged: the new platform's rules " +
               "refuse it, so you start with the default policy. Set yours again in the app.",
               "");
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
  /** Whether the old policy was refused by the validator, and archived (`POLICY_REFUSED`). */
  readonly policyArchived: boolean;
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
    const { rows, refusedPolicy } = rowsOf(owner, copy);
    if (refusedPolicy !== null) {
      make(POLICY_REFUSED, (at) => writeFileSync(at, refusedPolicy, { flag: "wx" }));
    }
    if (entries.size > 0) {
      make(README, (at) => writeFileSync(at, readme(owner, refusedPolicy !== null),
                                         { flag: "wx" }));
    }
    for (const path of [...entries.keys()]) {
      for (const parent of ancestors(path)) {
        if (!entries.has(parent)) {
          entries.set(parent, { path: parent, kind: "dir" });
        }
      }
    }
    return { entries: [...entries.values()].sort((a, b) => byPath(a.path, b.path)),
             rows, store, policyArchived: refusedPolicy !== null };
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
 * hold it: its entries (folders count, and the starter's seed with them) and its small files'
 * unique bytes inline, counted for the report; its largest file (`max_file_bytes`), and
 * every byte stored against the tier's storage. An owner past one is refused before the
 * window (research §3: the import never raises a setting; the fix is the owner's, told).
 * The file index's ceiling (`owner/src/files.ts` `INDEX_BYTES`) is the object's, which
 * refuses past it loudly as the import writes (`file_index_full`).
 */
export function fitOf(owner: Owner, built: Built, tiers: Tiers): Fit {
  const seeded = carried().files.map((file) => file.path);
  const all = new Set<string>([...built.entries.map((entry) => entry.path), ...seeded]);
  for (const path of seeded) {
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
  over("over_file_bytes", fit.largest, FILE_DEFAULTS.max_file_bytes);
  const tier = tierOf(owner, tiers);
  if (tier === undefined) {
    fit.problems.push({ code: "no_tier", at });
  } else {
    over("over_storage", fit.stored, tier.storage_bytes);
  }
  return fit;
}
