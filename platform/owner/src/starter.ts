/**
 * The starter package in the owner's object (starter_research.md §3.3–3.4; the founder's
 * answers of 2026-11-03): the files every owner's default agent, `main`, starts from, written
 * into the owner's files by the object itself, as the owner, and kept current by one rule.
 *
 * The package is data (`platform/starter/`, carried here as `starter.json`): a version and
 * its files, each `managed` (the platform's to update) or `personal` (`SOUL.md`, `USER.md`:
 * seeded once, never touched again). Each path the object ever wrote holds a **receipt**:
 * the version it last reconciled, the content id it installed, the content id it last
 * offered beside it, and its state.
 *
 * **Seeding.** An admitted owner with no reconciled version is seeded by the `starter` lane
 * (`ownerLanes`), at once: so every owner, however it first arrives (the app, or Claude or
 * ChatGPT over MCP), has `main`. Each file is written through the files plane as the
 * owner's own write (`writer` `owner`, history op `starter`, so `file_history` shows it), a
 * receipt for each, `agent.json` last: until it lands there is no agent, and a seed that
 * fails part way leaves none. A file already at a path (the owner's own) is never
 * overwritten.
 *
 * **Updates**, when the carried version is newer than the reconciled one (once a deploy, at
 * the owner's next wake), the dpkg conffile rule:
 * - a managed file whose content is still what was installed is replaced (a compare-and-swap
 *   on its etag, so an edit made meanwhile is never lost);
 * - an edited one is never overwritten: the new version is written beside it as
 *   `<file>.ta-new`, a name no engine loads, and the owner gets one notice for the pass;
 * - a deleted one is never restored (its receipt's `deleted`);
 * - a change that widens what the agent may do (more `agent.json` scopes, a new or changed
 *   script, or what the package marks `widens` for that version) arrives as a proposal even
 *   when unedited: written beside it as `.ta-new`, with a notice, applied only by the owner
 *   moving it over the file;
 * - the owner adopting an offered `.ta-new` (the file's content now the offered one) is
 *   taken as installed.
 * Undo is the files plane's own history: every write here is a version, and `file_restore`
 * brings the last one back (the file is then the owner's edit, never replaced again).
 *
 * A pass that fails (a refused or failed write) stops where it is, keeps what it did, tells
 * the owner once for that version, and is tried again `STARTER_RETRY_S` later, as its row
 * says: never by inventing a file. Every write is a unit of the object's gate; a pass is
 * single-flight on its lane.
 */
import starterJson from "../../starter/starter.json" with { type: "json" };

import { agentFile } from "./agent-file";
import { Invalid } from "./errors";
import { type Content, cut, chunkedId, FileConflict, type Files, INLINE_BYTES, inlineId }
  from "./files";
import type { OwnerStorage, Write } from "./gate";
import { ownersOnly, storedPath } from "./paths";

/** The most files a package may hold: the receipts' bound (one row a path; the owner-bound guard). */
export const STARTER_FILES_MAX = 64;
/** How long a pass that failed waits before it is tried again. */
export const STARTER_RETRY_S = 300;
/** The name beside a file that holds its newer version, which no engine loads. */
export const NEWER = ".ta-new";
/** The history op of the starter's own writes. */
export const STARTER_OP = "starter";

export type StarterClass = "managed" | "personal";
export type ReceiptState = "installed" | "edited" | "deleted" | "proposed";

export interface StarterFile {
  readonly path: string;
  readonly class: StarterClass;
  /** The version whose change to this file widens what the agent may do, and why. */
  readonly widens?: { readonly version: number; readonly why: string };
  readonly text: string;
}

export interface StarterPackage {
  readonly version: number;
  readonly files: readonly StarterFile[];
}

export interface Receipt {
  readonly path: string;
  readonly version: number;
  readonly class: StarterClass;
  readonly state: ReceiptState;
  /** The content the starter last installed there, or null when it never installed any. */
  readonly installed: string | null;
  /** The content it last wrote beside it as `.ta-new`, or null. */
  readonly offered: string | null;
}

/** A package checked whole: a positive version, at most `STARTER_FILES_MAX` files, `agent.json` last. */
export function starterPackage(value: unknown): StarterPackage {
  const pkg = value as { version?: unknown; files?: unknown };
  if (typeof pkg !== "object" || pkg === null || !Number.isSafeInteger(pkg.version)
      || (pkg.version as number) < 1 || !Array.isArray(pkg.files) || pkg.files.length < 1
      || pkg.files.length > STARTER_FILES_MAX) {
    throw new Invalid(`a starter package is a version and 1 to ${STARTER_FILES_MAX} files`);
  }
  const paths = new Set<string>();
  const files = pkg.files.map((file: Partial<StarterFile>): StarterFile => {
    const path = storedPath(file.path);
    if (!ownersOnly(path) || path.endsWith(NEWER) || paths.has(path)
        || (file.class !== "managed" && file.class !== "personal")
        || typeof file.text !== "string") {
      throw new Invalid(`starter file ${String(file.path)} is a distinct path in the owner's ` +
                        "trees, managed or personal, with its text");
    }
    const widens = file.widens;
    if (widens !== undefined && (!Number.isSafeInteger(widens.version) || widens.version < 2
                                 || widens.version > (pkg.version as number)
                                 || typeof widens.why !== "string" || widens.why === "")) {
      throw new Invalid(`starter file ${path}'s widens names a later version and why`);
    }
    paths.add(path);
    return Object.freeze({ path, class: file.class, text: file.text,
                           ...(widens === undefined ? {} : { widens }) });
  });
  const agents = files.filter((file) => file.path.endsWith("/agent.json"));
  if (agents.length > 0 && files.at(-1) !== agents.at(-1)) {
    throw new Invalid("a starter package writes agent.json last");
  }
  for (const agent of agents) {
    agentFile(agent.text);
  }
  return Object.freeze({ version: pkg.version as number, files: Object.freeze(files) });
}

/** The package this code carries, checked. */
export function carried(): StarterPackage {
  return starterPackage(starterJson);
}

/** The content id the files plane gives these bytes. */
export function contentIdOf(bytes: Uint8Array): string {
  return bytes.byteLength <= INLINE_BYTES ? inlineId(bytes)
    : chunkedId(bytes.byteLength, cut(bytes).map((chunk) => chunk.sha256));
}

/** Why replacing `before` with `after` at `file`'s path widens what the agent may do, or null. */
export function widening(file: StarterFile, from: number | null, before: Uint8Array | null,
                         after: Uint8Array): string | null {
  if (file.widens !== undefined && from !== null && file.widens.version > from) {
    return file.widens.why;
  }
  if (file.path.endsWith("/agent.json")) {
    const decode = (bytes: Uint8Array) => new TextDecoder().decode(bytes);
    let had: readonly string[] = [];
    try {
      had = before === null ? [] : agentFile(decode(before)).scopes;
    } catch {
      had = [];  // an unreadable file held no scope
    }
    const added = agentFile(decode(after)).scopes.filter((scope) => !had.includes(scope));
    return added.length === 0 ? null : `agent.json adds the scopes ${added.join(", ")}`;
  }
  if (!file.path.endsWith(".md")) {
    return before === null ? `${file.path} is a new script` : `${file.path} changes a script`;
  }
  return null;
}

type ReceiptRow = {
  path: string; version: number; class: StarterClass; state: ReceiptState; installed: string | null;
  offered: string | null;
};
type StateRow = { version: number; retry_at: number | null; noticed: number | null };

export interface StarterDeps {
  readonly storage: OwnerStorage;
  readonly files: Files;
  readonly write: Write;
  /** In a unit: one owner notice. */
  readonly notice: (type: string, detail: string) => void;
  /** Whether the owner is admitted for work: a read. */
  readonly admitted: () => boolean;
  readonly clock: () => number;
}

export class Starter {
  readonly #deps: StarterDeps;
  /** The package it reconciles; null for none (a test's object of other things). */
  readonly #pkg: StarterPackage | null;

  constructor(deps: StarterDeps, pkg: StarterPackage | null) {
    this.#deps = deps;
    this.#pkg = pkg;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#deps.storage.sql;
  }

  #state(): StateRow | undefined {
    return this.#sql.exec<StateRow>("SELECT version, retry_at, noticed FROM starter_state")
      .toArray()[0];
  }

  /** The receipts, by path. A read. */
  receipts(): Receipt[] {
    return this.#sql.exec<ReceiptRow>(
      "SELECT path, version, class, state, installed, offered FROM starter_receipts " +
        "ORDER BY path").toArray();
  }

  /** The version this object carries, 0 for none. */
  carried(): number {
    return this.#pkg?.version ?? 0;
  }

  /** The version last reconciled whole, 0 for none. A read. */
  version(): number {
    return this.#state()?.version ?? 0;
  }

  /** When the lane next has work: now while the carried version is not reconciled, past a retry. */
  next(now: number): number | null {
    if (this.#pkg === null || !this.#deps.admitted()) {
      return null;
    }
    const state = this.#state();
    if (state !== undefined && state.version >= this.#pkg.version) {
      return null;
    }
    return Math.max(now, state?.retry_at ?? now);
  }

  /** One pass: the package reconciled, or the failure kept in the row and told once. */
  async run(now: number): Promise<void> {
    if (this.next(now) === null || this.next(now)! > now) {
      return;
    }
    const pkg = this.#pkg!;
    try {
      await this.#reconcile(pkg);
    } catch (error) {
      const why = error instanceof Error ? error.message : String(error);
      await this.#deps.write(() => {
        const state = this.#state();
        const version = pkg.version;
        this.#sql.exec(
          "INSERT INTO starter_state (only, version, retry_at, noticed) VALUES (1, 0, ?, ?) " +
            "ON CONFLICT (only) DO UPDATE SET retry_at = excluded.retry_at, " +
            "noticed = excluded.noticed", this.#deps.clock() + STARTER_RETRY_S, version);
        if (state?.noticed !== version) {
          this.#deps.notice("starter_failed", `your agent's starter files (version ${version}) ` +
                            `could not all be written, and are tried again: ${why}`);
        }
      });
      if (!(error instanceof Error) || !["Refused", "Invalid", "FileFault", "FileConflict"]
            .includes(error.name)) {
        console.error("the starter pass failed; it runs again later");  // no paths or ids
      }
    }
  }

  async #reconcile(pkg: StarterPackage): Promise<void> {
    const seeded = this.version() > 0;
    const edited: string[] = [];
    const proposed: string[] = [];
    for (const file of pkg.files) {
      const bytes = new TextEncoder().encode(file.text);
      const id = contentIdOf(bytes);
      const seen = await this.#deps.write(() => {
        const receipt = this.#sql.exec<ReceiptRow>(
          "SELECT path, version, class, state, installed, offered FROM starter_receipts " +
            "WHERE path = ?", file.path).toArray()[0];
        const entry = this.#deps.files.stat(file.path);
        const content = entry?.kind === "file" ? this.#deps.files.open(file.path) : null;
        return { receipt, entry, content };
      });
      const { receipt, entry, content } = seen;
      if (receipt?.state === "deleted") {
        continue;  // never restored
      }
      if (receipt !== undefined && entry === null) {
        await this.#record(file, receipt.version, "deleted", receipt.installed, receipt.offered);
        continue;
      }
      if (content?.content_id === id) {
        await this.#record(file, pkg.version, "installed", id, null);
        continue;
      }
      if (receipt === undefined) {
        if (entry !== null) {
          // The owner's own file there before the starter's: never overwritten.
          await this.#offer(file, bytes, null, "edited", null, id);
          edited.push(file.path);
        } else if (seeded && widening(file, this.version(), null, bytes) !== null) {
          await this.#offer(file, bytes, null, "proposed", null, id);
          proposed.push(`${file.path}: ${widening(file, this.version(), null, bytes)}`);
        } else if (!await this.#install(file, bytes, "")) {
          await this.#offer(file, bytes, null, "edited", null, id);
          edited.push(file.path);
        }
        continue;
      }
      if (file.class === "personal") {
        continue;  // seeded once, never updated
      }
      const base = receipt.offered !== null && content?.content_id === receipt.offered
        ? receipt.offered : receipt.installed;
      if (content !== null && content.content_id === base) {
        const before = await this.#bytes(content);
        const why = widening(file, receipt.version, before, bytes);
        if (why !== null) {
          if (receipt.offered !== id) {
            await this.#offer(file, bytes, receipt, "proposed", base, id);
            proposed.push(`${file.path}: ${why}`);
          }
        } else if (!await this.#install(file, bytes, content.entry.etag)) {
          await this.#offer(file, bytes, receipt, "edited", base, id);
          edited.push(file.path);
        }
        continue;
      }
      if (receipt.offered !== id) {
        await this.#offer(file, bytes, receipt, "edited", receipt.installed, id);
        edited.push(file.path);
      }
    }
    await this.#deps.write(() => {
      const kept = pkg.files.map((file) => file.path);
      this.#sql.exec(`DELETE FROM starter_receipts WHERE path NOT IN (${
        kept.map(() => "?").join(", ")})`, ...kept);
      this.#sql.exec(
        "INSERT INTO starter_state (only, version, retry_at, noticed) VALUES (1, ?, NULL, NULL) " +
          "ON CONFLICT (only) DO UPDATE SET version = excluded.version, retry_at = NULL, " +
          "noticed = NULL", pkg.version);
      if (edited.length > 0) {
        this.#deps.notice("starter_update", `TinyAssets updated your agent's starter files ` +
          `(version ${pkg.version}). You edited ${edited.length === 1 ? "this one" : "these"}, ` +
          `so ${edited.length === 1 ? "it was" : "they were"} left as you made them, and the ` +
          `new version is beside ${edited.length === 1 ? "it" : "each"} as ${NEWER}: ${
            edited.join(", ")}. Ask your agent to merge, or delete the ${NEWER} file.`);
      }
      if (proposed.length > 0) {
        this.#deps.notice("starter_proposal", `A TinyAssets update (version ${pkg.version}) ` +
          `widens what your agent may do, so it waits for you beside the file as ${NEWER}; ` +
          `move it over the file to accept it: ${proposed.join("; ")}.`);
      }
    });
  }

  /** The content's bytes, inline or fetched. */
  async #bytes(content: Content): Promise<Uint8Array> {
    if (content.inline !== undefined) {
      return content.inline;
    }
    const parts = await this.#deps.files.fetch((content.chunks ?? []).map(({ sha256 }) =>
      ({ sha256 })));
    const out = new Uint8Array(parts.reduce((sum, part) => sum + part.byteLength, 0));
    let at = 0;
    for (const part of parts) {
      out.set(part, at);
      at += part.byteLength;
    }
    return out;
  }

  /**
   * The file written at its path, as the owner, against `expected` (`""` creates only, an
   * etag replaces only that version), and its receipt; false when someone else's write won.
   */
  async #install(file: StarterFile, bytes: Uint8Array, expected: string): Promise<boolean> {
    try {
      await this.#deps.files.write(file.path, bytes, { expected, writer: "owner",
                                                       op: STARTER_OP });
    } catch (error) {
      if (error instanceof FileConflict) {
        return false;
      }
      throw error;
    }
    await this.#record(file, this.#pkg!.version, "installed", contentIdOf(bytes), null);
    return true;
  }

  /** The version written beside the file as `.ta-new`, and the receipt that says so. */
  async #offer(file: StarterFile, bytes: Uint8Array, receipt: Receipt | null,
               state: "edited" | "proposed", installed: string | null, id: string):
      Promise<void> {
    await this.#deps.files.write(`${file.path}${NEWER}`, bytes, {
      expected: null, writer: "owner", op: STARTER_OP });
    await this.#record(file, receipt?.version ?? this.#pkg!.version, state, installed, id);
  }

  #record(file: StarterFile, version: number, state: ReceiptState, installed: string | null,
          offered: string | null): Promise<void> {
    return this.#deps.write(() => {
      this.#sql.exec(
        "INSERT INTO starter_receipts (path, version, class, state, installed, offered) " +
          "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (path) DO UPDATE SET version = excluded.version, " +
          "class = excluded.class, state = excluded.state, installed = excluded.installed, " +
          "offered = excluded.offered", file.path, version, file.class, state, installed, offered);
    });
  }
}
