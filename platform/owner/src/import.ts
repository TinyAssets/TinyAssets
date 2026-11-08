/**
 * The cutover's import doors (`import_research.md` §3, slice 3): every existing owner's
 * data, carried from the old stack's backup into the owner's own object while it is still
 * `pending`, by the doors' import consumer (`doors/src/import.ts`) from the bundle the
 * importer's job built (`platform/import/bundle.ts`). Deleted at the cutover with the job,
 * the consumer and their bindings (tasks.md §9).
 *
 * The doors are the `import` class's (`contract/doors.json`): the object takes them only
 * from the platform's reserved caller with an import binding, and only while the owner is
 * `pending`; every other caller and state is refused `import_refused`, and the import
 * binding reaches no other door (`owner-do.ts`, `#bind`). They write only the imported tree
 * (`IMPORTED`), the owner's policy, its collections and one notice, each through the
 * object's own write path, so the schema's triggers, the charges, the counters, the quotas
 * (the tier's numbers as the binding carries them, and the owner's own settings, which the
 * import never raises: Fact 15) and a body's placement are the platform's own:
 *
 * - `importRows`: the policy (`Policy.imported`), then collection rows (`Collections.write`
 *   through the object's placing unit, a body past `PLACE_BYTES` placed in R2), at most
 *   `IMPORT_ROWS` rows and `IMPORT_BATCH_BYTES` encoded a call (the contract's bounds,
 *   `contract/src/import-message.ts`, which the consumer batches by);
 * - `importFiles`: folders and inline files (at most `INLINE_BYTES`), at most
 *   `IMPORT_FILES` and `IMPORT_BATCH_BYTES` of bytes a call, each held to its content id;
 * - `importFileBegin`, `importFileStored`, `importFileCommit`: a chunked file, by the upload
 *   door's own steps (`Files.begin`, its plan, the namespace and wrapped key, `stored`,
 *   `commit`): its bytes go from the staging bucket through the doors to `FILES`
 *   (`doors/src/file-bytes.ts`), never through this object; at most `IMPORT_INTENTS` uploads
 *   open at once, under `MAX_OPEN_INTENTS`;
 * - `importNotice`: one notice, that the old files are in `/workspace/imported`.
 *
 * The import keeps no state of its own (no table, no migration). Each step is idempotent
 * from the owner's state: a file already at its path with the same content id, size and
 * mode is skipped and any other there refused loudly (`import_conflict`); a row equal by key
 * and body is skipped, any other refused; the policy is set only while it is the default,
 * and skipped once it is the imported one; the notice is added once; an upload of the same
 * file still open is resumed, never begun again. So a delivery killed at any step converges
 * when it runs again. Everything it writes is the owner's own (`IMPORT_WRITER`).
 */
import { chunkedId, chunkPlan, cut, inlineId } from "../../contract/src/content";
import { HEX32, HEX64 } from "../../contract/src/ids";
import {
  IMPORT_BATCH_BYTES, IMPORT_FILES, IMPORT_INTENTS, IMPORT_ROWS,
} from "../../contract/src/import-message";
import { INLINE_BYTES, MAX_INTENT_CHUNKS, NOTICE_DETAIL } from "../../contract/src/limits";
import { IMPORTED, storedPath, within } from "../../contract/src/paths";
import { Refusal } from "../../contract/src/refusal";
import { utf8Bytes } from "../../contract/src/text";

import { clock } from "./clock";
import { checkBody, OWNER_FILER } from "./collections";
import type { Call, Door, OwnerState } from "./owner-do";

/** Whose the imported files and rows are: the owner's own. */
export const IMPORT_WRITER = OWNER_FILER;
/** The imported rows' history op, and the notice's type. */
export const IMPORT_OP = "import";
export const IMPORT_NOTICE = "imported";

const NOT_IMPORTED = "import_refused: the import writes the imported tree alone";
const TOO_MANY_UPLOADS = `import_refused: at most ${IMPORT_INTENTS} import uploads are open`;

/** A row the import carries (`import/build.ts`'s `Row`). */
export type ImportRow =
  | { readonly kind: "policy"; readonly document: unknown }
  | { readonly kind: "collection"; readonly collection: string; readonly row_id: string;
      readonly private: 0 | 1; readonly body: string };

/** A folder, or an inline file and its bytes. */
export type ImportEntry =
  | { readonly kind: "dir"; readonly path: string }
  | { readonly kind: "file"; readonly path: string; readonly size: number; readonly mode: 0 | 1;
      readonly content_id: string; readonly bytes: Uint8Array };

export interface ImportRowsRequest {
  readonly rows: readonly ImportRow[];
}

export interface ImportFilesRequest {
  readonly entries: readonly ImportEntry[];
}

/** A chunked file: its path, size, mode, content id and chunks, cut at 4 MiB. */
export interface ImportFileBeginRequest {
  readonly path: string;
  readonly size: number;
  readonly mode: 0 | 1;
  readonly content_id: string;
  readonly chunks: readonly string[];
}

export interface ImportFileStoredRequest {
  readonly intent_id: string;
  readonly chunks: readonly string[];
}

export interface ImportFileCommitRequest {
  readonly intent_id: string;
  readonly path: string;
  readonly mode: 0 | 1;
  readonly content_id: string;
}

export interface ImportNoticeRequest {
  readonly detail: string;
}

/** What a step did: how many it wrote, and how many were there already. */
export interface Imported {
  readonly written: number;
  readonly skipped: number;
}

/** A chunked file's upload, or `skipped` when the file is at its path already. */
export type ImportUpload =
  | { readonly skipped: true }
  | { readonly skipped: false; readonly intent_id: string; readonly namespace: string;
      readonly key: string; readonly chunks: { sha256: string; size: number }[];
      readonly pending: string[] };

function invalid(what: string): Refusal {
  return new Refusal("invalid", `invalid import request: ${what}`);
}

function conflict(what: string): Refusal {
  return new Refusal("import_conflict", `import_conflict: ${what}`);
}

const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const keys = (value: Record<string, unknown>) => Object.keys(value).sort().join();

/** A path the import may write: stored, and in the imported tree. */
function importPath(value: unknown): string {
  const path = storedPath(value);
  if (!within(path, IMPORTED)) {
    throw new Refusal("import_refused", NOT_IMPORTED);
  }
  return path;
}

function modeOf(value: unknown): 0 | 1 {
  if (value !== 0 && value !== 1) {
    throw invalid("a mode is 0 or 1");
  }
  return value;
}

/** A batch: a list of 1 to `most` items, held to `IMPORT_BATCH_BYTES` by `bytes`. */
function batch(value: unknown, most: number, bytes: (items: unknown[]) => number): unknown[] {
  if (!Array.isArray(value) || value.length === 0 || value.length > most) {
    throw invalid(`a call takes 1 to ${most}`);
  }
  if (bytes(value) > IMPORT_BATCH_BYTES) {
    throw invalid(`a call takes at most ${IMPORT_BATCH_BYTES} bytes`);
  }
  return value;
}

function rowsOf(value: unknown): ImportRow[] {
  return batch(value, IMPORT_ROWS, (rows) => utf8Bytes(JSON.stringify(rows))).map((row) => {
    if (!plain(row)) {
      throw invalid("a row is an object");
    }
    if (keys(row) === "document,kind" && row["kind"] === "policy") {
      return { kind: "policy", document: row["document"] };
    }
    if (keys(row) !== "body,collection,kind,private,row_id" || row["kind"] !== "collection"
        || typeof row["collection"] !== "string" || typeof row["row_id"] !== "string"
        || (row["private"] !== 0 && row["private"] !== 1)) {
      throw invalid("a row is the policy or a collection's row");
    }
    return { kind: "collection", collection: row["collection"], row_id: row["row_id"],
             private: row["private"], body: checkBody(row["body"]) };
  });
}

function entriesOf(value: unknown): ImportEntry[] {
  const size = (entries: unknown[]) => entries.reduce((sum: number, entry) => sum
    + (plain(entry) && entry["bytes"] instanceof Uint8Array ? entry["bytes"].byteLength : 0)
    + (plain(entry) && typeof entry["path"] === "string" ? utf8Bytes(entry["path"]) : 0), 0);
  return batch(value, IMPORT_FILES, size).map((entry): ImportEntry => {
    if (plain(entry) && keys(entry) === "kind,path" && entry["kind"] === "dir") {
      return { kind: "dir", path: importPath(entry["path"]) };
    }
    if (!plain(entry) || keys(entry) !== "bytes,content_id,kind,mode,path,size"
        || entry["kind"] !== "file" || !(entry["bytes"] instanceof Uint8Array)) {
      throw invalid("an entry is a folder, or an inline file and its bytes");
    }
    const bytes = entry["bytes"];
    if (entry["size"] !== bytes.byteLength || bytes.byteLength > INLINE_BYTES) {
      throw invalid(`an inline file is its size, at most ${INLINE_BYTES} bytes`);
    }
    if (entry["content_id"] !== inlineId(bytes)) {
      throw invalid("an inline file's bytes are not its content id");
    }
    return { kind: "file", path: importPath(entry["path"]), size: bytes.byteLength,
             mode: modeOf(entry["mode"]), content_id: entry["content_id"], bytes };
  });
}

/** A chunked file as `importFileBegin` takes it, its chunks held to its content id. */
function chunkedOf(r: Record<string, unknown>): ImportFileBeginRequest {
  const { size, chunks, content_id: contentId } = r;
  if (typeof size !== "number" || !Number.isSafeInteger(size) || size <= INLINE_BYTES) {
    throw invalid(`a chunked file is larger than ${INLINE_BYTES} bytes`);
  }
  const plan = chunkPlan(size);
  if (plan.length > MAX_INTENT_CHUNKS || !Array.isArray(chunks) || chunks.length !== plan.length
      || chunks.some((sha) => typeof sha !== "string" || !HEX64.test(sha))) {
    throw invalid("a chunked file names a 64-hex hash for each 4 MiB of it");
  }
  if (contentId !== chunkedId(size, chunks as string[])) {
    throw invalid("a chunked file's chunks are not its content id");
  }
  return { path: importPath(r["path"]), size, mode: modeOf(r["mode"]),
           content_id: contentId as string, chunks: chunks as string[] };
}

/**
 * In a unit: whether `want` is at `path` already (true), or nothing is (false); anything
 * else there is refused loudly, since on a pending owner only an earlier import of other
 * bytes can have put it there (`import_conflict`; the fix is a discard and a run again).
 */
function there(o: OwnerState, path: string,
               want: "dir" | { content_id: string; size: number; mode: number }): boolean {
  const entry = o.files.stat(path);
  if (entry === null) {
    return false;
  }
  if (want === "dir" || entry.kind === "dir") {
    if (want === "dir" && entry.kind === "dir") {
      return true;
    }
    throw conflict(`${path} holds a ${entry.kind} where the import has a ${
      want === "dir" ? "dir" : "file"}`);
  }
  const held = o.files.open(path);
  if (held.content_id !== want.content_id || entry.size !== want.size
      || entry.mode !== want.mode) {
    throw conflict(`${path} holds another file than the import's`);
  }
  return true;
}

/**
 * In a unit: whether the collection row is there already, equal by key and body (true), or
 * absent (false); any other row at its key is refused loudly. A placed body is compared by
 * its content's id, which names its bytes (`bodies.ts`).
 */
function rowThere(o: OwnerState, owner: string,
                  row: Extract<ImportRow, { kind: "collection" }>): boolean {
  const held = o.collections.get(owner, row.collection, row.row_id).row;
  if (held === null) {
    return false;
  }
  const bytes = new TextEncoder().encode(row.body);
  const same = held.private === (row.private === 1) && (held.content_id === undefined
    ? held.body === row.body
    : held.content_id === chunkedId(bytes.byteLength, cut(bytes).map((chunk) => chunk.sha256)));
  if (!same) {
    throw conflict(`the row ${row.row_id} of ${row.collection} holds another body`);
  }
  return true;
}

// -- THE DOORS: the `import` rows' handlers (`contract/doors.json`) --------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind (the platform's import binding,
// the owner pending), and the fields.

/** The `import` doors' handlers, by door. */
export const importDoors = Object.freeze({
  /** The policy, then collection rows, each in its own unit, a body placed past its size. */
  async importRows(o: OwnerState, door: Door<ImportRowsRequest>): Promise<Imported> {
    const rows = await o.door(() => rowsOf(door.check().r["rows"]));
    let written = 0;
    for (const row of rows) {
      if (row.kind === "policy") {
        const set = await o.synced(() => {
          door.check();
          return o.policy.imported(row.document, clock());
        });
        written += set ? 1 : 0;
        continue;
      }
      const wrote = await o.placing((): { body: unknown; writer: string } | { done: boolean } => {
        const { account } = door.check();
        return rowThere(o, account.owner, row) ? { done: false }
          : { body: row.body, writer: IMPORT_WRITER };
      }, (placed) => {
        const { account } = door.check();
        o.collections.write(account.owner, account.owner, row.collection, row.row_id, row.body,
                            "", row.private === 1, IMPORT_OP, placed, IMPORT_WRITER);
        return true;
      }, { synced: true });
      written += wrote ? 1 : 0;
    }
    return { written, skipped: rows.length - written };
  },
  /** Folders and inline files, in the caller's one unit, each created only where none is. */
  importFiles(o: OwnerState, { r }: Call<ImportFilesRequest>): Imported {
    const entries = entriesOf(r["entries"]);
    let written = 0;
    for (const entry of entries) {
      if (entry.kind === "dir") {
        if (!there(o, entry.path, "dir")) {
          o.files.mkdir(entry.path, { expected: "", parents: true, writer: IMPORT_WRITER });
          written += 1;
        }
        continue;
      }
      if (!there(o, entry.path, entry)) {
        o.files.writeSmall(entry.path, entry.bytes,
                           { expected: "", mode: entry.mode, writer: IMPORT_WRITER });
        written += 1;
      }
    }
    return { written, skipped: entries.length - written };
  },
  /**
   * A chunked file's upload: skipped when the file is there; else its open upload resumed, or
   * one begun; then its plan, the chunks it still needs, and the owner's namespace and wrapped
   * data key, which only `FILES` opens, for the doors to put its bytes with.
   */
  async importFileBegin(o: OwnerState, door: Door<ImportFileBeginRequest>):
      Promise<ImportUpload> {
    const begun = await o.filing(() => {
      const file = chunkedOf(door.check().r);
      if (there(o, file.path, file)) {
        return null;
      }
      const { open, same } = o.files.openUploads(file.size, file.chunks, IMPORT_WRITER);
      if (same === null && open >= IMPORT_INTENTS) {
        throw new Refusal("import_refused", TOO_MANY_UPLOADS);
      }
      const intentId = same ?? o.files.begin(file.size, file.chunks, IMPORT_WRITER).intent_id;
      return { intent_id: intentId, ...o.files.uploadPlan(intentId) };
    });
    if (begun === null) {
      return { skipped: true };
    }
    const { ns, key } = await o.files.space();
    return { skipped: false, namespace: ns, key, ...begun };
  },
  /** The doors' word that `FILES` took these chunks of the upload, each put by its hash. */
  importFileStored(o: OwnerState, { r }: Call<ImportFileStoredRequest>): { stored: number } {
    if (typeof r["intent_id"] !== "string" || !HEX32.test(r["intent_id"])) {
      throw invalid("an intent id is 32 lowercase hex characters");
    }
    return o.files.stored(r["intent_id"], r["chunks"]);
  },
  /**
   * The upload committed as its path, created there; skipped (its upload let go) when the file
   * is there already, which a delivery killed after its commit leaves.
   */
  importFileCommit(o: OwnerState, { r }: Call<ImportFileCommitRequest>): Imported {
    const path = importPath(r["path"]);
    const mode = modeOf(r["mode"]);
    const intentId = r["intent_id"];
    if (typeof intentId !== "string" || !HEX32.test(intentId)
        || typeof r["content_id"] !== "string" || !HEX64.test(r["content_id"])) {
      throw invalid("a commit names its upload and its content id");
    }
    const entry = o.files.stat(path);
    if (entry !== null && entry.kind === "file"
        && o.files.open(path).content_id === r["content_id"] && entry.mode === mode) {
      o.files.abort(intentId);
      return { written: 0, skipped: 1 };
    }
    const plan = o.files.uploadPlan(intentId);
    const size = plan.chunks.reduce((sum, chunk) => sum + chunk.size, 0);
    if (chunkedId(size, plan.chunks.map((chunk) => chunk.sha256)) !== r["content_id"]) {
      throw invalid("the upload is not the file its content id names");
    }
    if (there(o, path, { content_id: r["content_id"], size, mode })) {
      return { written: 0, skipped: 1 };
    }
    o.files.commit(intentId, path, { expected: "", mode, writer: IMPORT_WRITER, parents: true });
    return { written: 1, skipped: 0 };
  },
  /** The owner's one notice of the import, added once. */
  importNotice(o: OwnerState, { r }: Call<ImportNoticeRequest>): { added: boolean } {
    const detail = r["detail"];
    if (typeof detail !== "string" || detail === "" || [...detail].length > NOTICE_DETAIL) {
      throw invalid(`a notice is text of at most ${NOTICE_DETAIL} characters`);
    }
    if (o.notices.list().some((notice) => notice.type === IMPORT_NOTICE)) {
      return { added: false };
    }
    o.notices.add(IMPORT_NOTICE, detail, clock());
    return { added: true };
  },
});
