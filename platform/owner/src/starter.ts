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
import { FileFault, Refusal } from "../../contract/src/refusal";
import { contentIdOf } from "../../contract/src/content";
import { type Content, FileConflict, type Files } from "./files";
import type { OwnerStorage, Write } from "./gate";
import { ownersOnly } from "./paths";
import { storedPath, within, WORKSPACE } from "../../contract/src/paths";

/**
 * The most paths a package may name, its files and the paths it once shipped and dropped
 * (`retired`) together: the receipts' bound (one row a path; the owner-bound guard).
 */
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
  /**
   * A personal file's earlier path, retired: an owner who holds a changed file there is
   * seeded here with a copy of it (the old file is left as it is) and told once.
   */
  readonly moved_from?: string;
  readonly text: string;
}

export interface StarterPackage {
  readonly version: number;
  readonly files: readonly StarterFile[];
  /**
   * Paths an earlier version shipped and this one does not: their receipts are kept, so a
   * file the owner deleted stays deleted if a later version ships its path again.
   */
  readonly retired: readonly string[];
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

/**
 * A package checked whole: a positive version, its files and retired paths at most
 * `STARTER_FILES_MAX` together and none twice, `agent.json` last.
 */
export function starterPackage(value: unknown): StarterPackage {
  const pkg = value as { version?: unknown; files?: unknown; retired?: unknown };
  const retired = pkg?.retired ?? [];
  if (typeof pkg !== "object" || pkg === null || !Number.isSafeInteger(pkg.version)
      || (pkg.version as number) < 1 || !Array.isArray(pkg.files) || pkg.files.length < 1
      || !Array.isArray(retired)
      || pkg.files.length + retired.length > STARTER_FILES_MAX) {
    throw new Refusal("invalid",
                      `a starter package is a version and 1 to ${STARTER_FILES_MAX} files ` +
                      "and retired paths");
  }
  const paths = new Set<string>();
  const files = pkg.files.map((file: Partial<StarterFile>): StarterFile => {
    const path = storedPath(file.path);
    // A managed file is instructions, in the owner's trees; a personal one (the profile, the
    // workspace's map) is data, anywhere in the workspace, the owner's to change. Who else
    // may write it is the agent trees' rule: the profile is in `OWNER_TREES`, so a grant the
    // owner narrowed never writes it, and the map is not.
    if (!within(path, WORKSPACE) || path === WORKSPACE || path.endsWith(NEWER)
        || paths.has(path) || (file.class !== "managed" && file.class !== "personal")
        || (file.class === "managed" && !ownersOnly(path)) || typeof file.text !== "string") {
      throw new Refusal("invalid",
                        `starter file ${String(file.path)} is a distinct path in the workspace ` +
                        "(a managed one in the owner's trees), managed or personal, with its " +
                        "text");
    }
    if (file.moved_from !== undefined && (file.class !== "personal"
        || !(Array.isArray(retired) && retired.includes(file.moved_from)))) {
      throw new Refusal("invalid",
                        `starter file ${path} moved from a retired path, and is personal`);
    }
    const widens = file.widens;
    if (widens !== undefined && (!Number.isSafeInteger(widens.version) || widens.version < 2
                                 || widens.version > (pkg.version as number)
                                 || typeof widens.why !== "string" || widens.why === "")) {
      throw new Refusal("invalid", `starter file ${path}'s widens names a later version and why`);
    }
    paths.add(path);
    return Object.freeze({ path, class: file.class, text: file.text,
                           ...(widens === undefined ? {} : { widens }),
                           ...(file.moved_from === undefined ? {}
                             : { moved_from: storedPath(file.moved_from) }) });
  });
  const agents = files.filter((file) => file.path.endsWith("/agent.json"));
  if (agents.length > 0 && files.at(-1) !== agents.at(-1)) {
    throw new Refusal("invalid", "a starter package writes agent.json last");
  }
  for (const agent of agents) {
    agentFile(agent.text);
  }
  const gone = retired.map((path: unknown) => {
    const at = storedPath(path);
    if (paths.has(at)) {
      throw new Refusal("invalid", `starter path ${at} is retired and shipped at once`);
    }
    paths.add(at);
    return at;
  });
  return Object.freeze({ version: pkg.version as number, files: Object.freeze(files),
                         retired: Object.freeze(gone) });
}

/** The package this code carries, checked. */
export function carried(): StarterPackage {
  return starterPackage(starterJson);
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
  /**
   * Whether the files can take a write: the tier's numbers, which bound the files' bytes,
   * are known (a door's binding hands them). A read; true when omitted.
   */
  readonly ready?: () => boolean;
  readonly clock: () => number;
}

export class Starter {
  readonly #deps: StarterDeps;
  /** The package it reconciles; null for none (a test's object of other things). */
  readonly #pkg: StarterPackage | null;
  /** The pass running now, which the lane and a door share: one at a time. */
  #running: Promise<void> | null = null;

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
    if (this.#pkg === null || !this.#deps.admitted() || this.#deps.ready?.() === false) {
      return null;  // nothing to do, or no write could land yet: no failure to tell of
    }
    const state = this.#state();
    if (state !== undefined && state.version >= this.#pkg.version) {
      return null;
    }
    return Math.max(now, state?.retry_at ?? now);
  }

  /**
   * The seed, awaited by a door that needs an agent (`converse`, `run_graph`): a pass now when
   * the owner has none reconciled, joined if one runs; nothing once seeded, or while a failed
   * pass waits its retry (its notice says why).
   */
  async seeded(now: number): Promise<void> {
    if (this.#running !== null) {
      await this.#running;
    } else if (this.version() === 0) {
      await this.run(now);
    }
  }

  /** One pass, single-flight: the lane's and a door's share it. */
  run(now: number): Promise<void> {
    if (this.#running === null) {
      this.#running = this.#pass(now).finally(() => {
        this.#running = null;
      });
    }
    return this.#running;
  }

  /** One pass: the package reconciled, or the failure kept in the row and told once. */
  async #pass(now: number): Promise<void> {
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
      if (!(error instanceof Refusal || error instanceof FileFault
            || error instanceof FileConflict)) {
        console.error("the starter pass failed; it runs again later");  // no paths or ids
      }
    }
  }

  async #reconcile(pkg: StarterPackage): Promise<void> {
    const seeded = this.version() > 0;
    const edited: string[] = [];
    const proposed: string[] = [];
    const withdrawn: string[] = [];
    const moved: string[] = [];
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
      if (receipt?.state === "proposed" && entry === null) {
        // A proposal of a new file not adopted yet: still a proposal, its offer kept current.
        if (receipt.offered !== id) {
          await this.#offer(file, bytes, receipt, "proposed", null, id);
          proposed.push(`${file.path}: ${widening(file, receipt.version, null, bytes)
            ?? "a newer version of a proposal you have not adopted"}`);
        }
        continue;
      }
      if (receipt !== undefined && entry === null) {
        await this.#record(file, receipt.version, "deleted", receipt.installed, receipt.offered);
        continue;
      }
      if (content?.content_id === id) {
        await this.#record(file, pkg.version, "installed", id, null);
        await this.#withdraw(file, receipt, id, withdrawn);
        continue;
      }
      if (receipt === undefined) {
        const from = seeded ? this.version() : null;
        // The owner's own file there before the starter's (or one written as the pass ran):
        // never overwritten, and a widening over it is still a proposal (review R2-M1).
        const owned = async (current: Content | null) => {
          const why = widening(file, from, current === null ? null : await this.#bytes(current),
                               bytes);
          await this.#offer(file, bytes, null, why === null ? "edited" : "proposed", null, id);
          if (why === null) {
            edited.push(file.path);
          } else {
            proposed.push(`${file.path}: ${why}`);
          }
        };
        if (entry !== null) {
          await owned(content);
        } else if (seeded && widening(file, this.version(), null, bytes) !== null) {
          await this.#offer(file, bytes, null, "proposed", null, id);
          proposed.push(`${file.path}: ${widening(file, this.version(), null, bytes)}`);
        } else if (!await this.#install(file, await this.#movedBytes(file, moved) ?? bytes, "")) {
          await owned(await this.#deps.write(() => {
            const now = this.#deps.files.stat(file.path);
            return now?.kind === "file" ? this.#deps.files.open(file.path) : null;
          }));
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
        } else {
          await this.#withdraw(file, receipt, id, withdrawn);
        }
        continue;
      }
      if (receipt.offered !== id) {
        // Edited: never overwritten. A widening is still a proposal, whatever the edit.
        const why = widening(file, receipt.version, content === null ? null
          : await this.#bytes(content), bytes);
        await this.#offer(file, bytes, receipt, why === null ? "edited" : "proposed",
                          receipt.installed, id);
        if (why === null) {
          edited.push(file.path);
        } else {
          proposed.push(`${file.path}: ${why}`);
        }
      }
    }
    // A retired path's proposal not adopted: the version that retires it withdraws it too
    // (review F3-retired-proposal), and its receipt keeps the path from coming back.
    for (const path of pkg.retired) {
      const receipt = await this.#deps.write(() => this.#sql.exec<ReceiptRow>(
        "SELECT path, version, class, state, installed, offered FROM starter_receipts " +
          "WHERE path = ?", path).toArray()[0]);
      if (receipt?.state !== "proposed") {
        continue;
      }
      const gone = { path, class: receipt.class, text: "" } satisfies StarterFile;
      const before = withdrawn.length;
      await this.#withdraw(gone, receipt, "", withdrawn);
      if (withdrawn.length > before || await this.#deps.write(
        () => this.#deps.files.stat(path) === null)) {
        await this.#record(gone, receipt.version, "deleted", receipt.installed, null);
      }
    }
    await this.#deps.write(() => {
      const kept = [...pkg.files.map((file) => file.path), ...pkg.retired];
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
      if (moved.length > 0) {
        // The managed files still holding text from before this version (the owner's edits,
        // and proposals not adopted): they may still name the old place (review R4-L2).
        const held = this.#sql.exec<{ path: string }>(
          "SELECT path FROM starter_receipts WHERE class = 'managed' AND state IN " +
            "('edited', 'proposed') ORDER BY path").toArray()
          .map((row) => row.path).filter((path) => this.#deps.files.stat(path) !== null);
        this.#deps.notice("starter_moved", `Your agent's files moved (version ${pkg.version}): ${
          moved.join("; ")}. The starter's files now read the new place, and the old file is ` +
          "left as it was. " + (held.length === 0
            ? "Delete it when you like, once nothing of your own still names it."
            : `${held.length === 1 ? "This file keeps" : "These files keep"} your own text ` +
              `until you adopt the ${NEWER} copy beside ${held.length === 1 ? "it" : "each"}, ` +
              `and may still read the old file, so keep it until then: ${held.join(", ")}.`));
      }
      if (withdrawn.length > 0) {
        this.#deps.notice("starter_withdrawn", `A TinyAssets update (version ${pkg.version}) ` +
          `takes back what it had proposed for ${withdrawn.join(", ")}: the ${NEWER} ` +
          `${withdrawn.length === 1 ? "copy is" : "copies are"} gone, and nothing you have ` +
          "was changed by it.");
      }
      if (proposed.length > 0) {
        this.#deps.notice("starter_proposal", `A TinyAssets update (version ${pkg.version}) ` +
          `widens what your agent may do, so nothing changes until you accept it. Each waits ` +
          `beside its file as ${NEWER}: ${proposed.join("; ")}. To accept one, ask your agent ` +
          `to adopt it, or replace the file with the ${NEWER} copy yourself; to refuse it, ` +
          `delete the ${NEWER} copy.`);
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

  /**
   * The bytes to seed a moved personal file with: the owner's file at its old path when it is
   * theirs (changed from what the starter installed there, or never the starter's), its move
   * told in `moved`; null to seed the package's own text.
   */
  async #movedBytes(file: StarterFile, moved: string[]): Promise<Uint8Array | null> {
    const from = file.moved_from;
    if (from === undefined) {
      return null;
    }
    const found = await this.#deps.write(() => {
      const entry = this.#deps.files.stat(from);
      const receipt = this.#sql.exec<{ installed: string | null }>(
        "SELECT installed FROM starter_receipts WHERE path = ?", from).toArray()[0];
      return { content: entry?.kind === "file" ? this.#deps.files.open(from) : null, receipt };
    });
    if (found.content === null || found.content.content_id === found.receipt?.installed) {
      return null;  // gone, or the starter's own text untouched: the new text is seeded
    }
    moved.push(`${from} is now ${file.path}, copied with what it held`);
    return this.#bytes(found.content);
  }

  /**
   * A proposal a later version withdrew (its file now installed as something else): its
   * `.ta-new` removed while it still holds the offered bytes, so nothing withdrawn can be
   * adopted (review R2-L1). One the owner changed is theirs, and stays.
   */
  async #withdraw(file: StarterFile, receipt: Receipt | undefined, installed: string,
                  withdrawn: string[]): Promise<void> {
    if (receipt?.state !== "proposed" || receipt.offered === null
        || receipt.offered === installed) {
      return;
    }
    const path = `${file.path}${NEWER}`;
    const copy = await this.#deps.write(() => {
      const entry = this.#deps.files.stat(path);
      return entry?.kind === "file" ? this.#deps.files.open(path) : null;
    });
    if (copy === null || copy.content_id !== receipt.offered) {
      return;
    }
    try {
      await this.#deps.write(() => this.#deps.files.remove(path, {
        expected: copy.entry.etag, recursive: false, writer: "owner" }));
    } catch (error) {
      if (error instanceof FileConflict) {
        return;  // changed as the pass ran: the owner's now
      }
      throw error;
    }
    withdrawn.push(file.path);
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
