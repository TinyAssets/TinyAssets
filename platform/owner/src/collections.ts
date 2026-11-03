/**
 * The one revision primitive (S34), in the owner's own object (ADR-032).
 *
 * Ported from `tinyassets/broker/conversations.py` (branch `s5b-board`), whose rules
 * it keeps. Rows are keyed by `(collection, row_id)`. Each write is a compare-and-swap
 * on the row's etag, and each commit is stamped with the collection's next `seq`, so
 * a reader pulls after a cursor, or gets one row by its key. The board is
 * `collection = "board"`; a Tier A app room is `collection = "room:<app>:<room>"`.
 * Payloads are stored and returned verbatim. A private row is visible to, and
 * writable by, its writer alone. Rows and payload bytes are bounded per writer (other
 * than the owner) in a collection, per collection and per owner; past a bound a write
 * is refused. Only the owner deletes a row, or clears a collection or one writer's
 * rows in it (the object enforces that at its RPC door).
 *
 * `collection_history` is the primitive's own record: each write appends the revision
 * it committed (its writer, op and body), and a delete or clear appends one body-less
 * entry and drops the row's earlier bodies, so a row made again under that key goes on
 * from the next revision and never repeats one. A row keeps its last `maxRevisions`
 * entries. The history is bounded by what it takes, not by rows times revisions: its
 * entries as SQLite lays them out (`maxHistoryCharge`) and their bodies wherever placed
 * (`maxHistoryBytes`), the oldest pruned first past either; a key whose every entry was
 * pruned goes on past the newest revision a prune let go, so none repeats. It is a
 * retention, never refused.
 *
 * Where a body lives is the placement rule (ADR-035, `bodies.ts`): a body past
 * `PLACE_BYTES` is placed in R2 before its write (the door's `Bodies.place`), and the row
 * and its history entry name its content; the byte bounds count every body's bytes
 * wherever it lives.
 *
 * Every change runs in one `transactionSync`, so a refusal or a thrown error leaves
 * nothing behind.
 */
import { createHash } from "node:crypto";

import { type Adopt, adopted, type Placed, READ_PAGE_BYTES } from "./bodies";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { codePoints, name, utf8Bytes } from "./text";

export interface Limits {
  /** A row body, in characters (code points). */
  readonly maxRow: number;
  /** Rows a writer other than the owner may create in one collection. */
  readonly maxWriterRows: number;
  /** Payload bytes a writer other than the owner may hold in one collection. */
  readonly maxWriterBytes: number;
  readonly maxRows: number;
  readonly maxCollectionBytes: number;
  readonly maxOwnerRows: number;
  readonly maxOwnerBytes: number;
  readonly maxPull: number;
  readonly maxRevisions: number;
  /** The history's entries, laid out as SQLite lays them (`HISTORY_CHARGE`), in bytes. */
  readonly maxHistoryCharge: number;
  /** The history's bodies, wherever placed, in bytes. */
  readonly maxHistoryBytes: number;
}

/**
 * Bounds on payload bytes and rows, each refused loudly. A writer other than the
 * owner gets a small share of one collection; one collection gets a slice of the
 * owner's total, so one room cannot starve another.
 */
export const LIMITS: Limits = Object.freeze({
  maxRow: 1024 * 1024,
  maxWriterRows: 10_000,
  maxWriterBytes: 1024 * 1024 * 1024,
  maxRows: 100_000,
  maxCollectionBytes: 4 * 1024 * 1024 * 1024,
  maxOwnerRows: 100_000,
  maxOwnerBytes: 16 * 1024 * 1024 * 1024,
  maxPull: 100,
  maxRevisions: 100,
  maxHistoryCharge: 128 * 1024 * 1024,
  maxHistoryBytes: 16 * 1024 * 1024 * 1024,
});

/** The history ops a write never names. */
const ERASES: readonly string[] = Object.freeze(["delete", "clear"]);
/** One scope's counters (`collection_usage`, kept by the schema's triggers). */
const USAGE =
  "SELECT rows, row_bytes AS bytes FROM collection_usage " +
  "WHERE scope = ? AND collection = ? AND who = ?";
/** Python's `str.isidentifier()`. */
const IDENTIFIER = /^[\p{XID_Start}_]\p{XID_Continue}*$/u;
const LONE_SURROGATE = /\p{Cs}/u;

export interface Row {
  readonly row_id: string;
  readonly revision: number;
  readonly etag: string;
  readonly seq: number;
  readonly writer: string;
  readonly private: boolean;
  /** The body; empty here while `content_id` names it placed, until the door reads it. */
  readonly body: string;
  /** A placed body's content (ADR-035), which the door reads into `body` and drops. */
  readonly content_id?: string;
}

export interface HistoryEntry {
  readonly revision: number;
  readonly op: string;
  readonly writer: string;
  readonly at: number;
  readonly body: string;
  readonly content_id?: string;
}

export interface Page {
  readonly cursor: number;
  readonly rows: Row[];
}

/**
 * A row body as a write takes it, or refused: text of at most `maxRow` characters, and
 * well-formed. The door checks it before it places the body (ADR-035).
 */
export function checkBody(body: unknown, limits: Limits = LIMITS): string {
  if (typeof body !== "string" || codePoints(body) > limits.maxRow) {
    throw new Invalid(`a row body is text of at most ${limits.maxRow} characters`);
  }
  if (LONE_SURROGATE.test(body)) {
    throw new Invalid("a row body is well-formed text");
  }
  return body;
}

/** History entries one prune step reads, oldest first. */
const PRUNE_BATCH = 64;

/** The expected etag is not the row's; `current` is the row as stored, or null. */
export class Conflict extends Error {
  override readonly name = "Conflict";
  constructor(readonly current: Row | null) {
    super("conflict");
  }
}

type StoredRow = {
  collection: string;
  row_id: string;
  revision: number;
  seq: number;
  etag: string;
  writer: string;
  private: number;
  body: string;
  updated_at: number;
  creator: string;
  body_bytes: number;
  content_id: string | null;
};

/** A page's rows: the first always, then each while the bodies fit `READ_PAGE_BYTES`. */
function paged<T extends { body_bytes: number }>(rows: readonly T[]): T[] {
  const page: T[] = [];
  let bytes = 0;
  for (const row of rows) {
    bytes += row.body_bytes;
    if (page.length > 0 && bytes > READ_PAGE_BYTES) {
      break;
    }
    page.push(row);
  }
  return page;
}

export function digest(body: string): string {
  return createHash("sha256").update(body, "utf8").digest("hex");
}

function shown(row: StoredRow): Row {
  return {
    row_id: row.row_id,
    revision: row.revision,
    etag: row.etag,
    seq: row.seq,
    writer: row.writer,
    private: Boolean(row.private),
    body: row.body,
    ...(row.content_id === null ? {} : { content_id: row.content_id }),
  };
}

function now(): number {
  return Date.now() / 1000;
}

export class Collections {
  readonly #storage: OwnerStorage;
  readonly #limits: Limits;
  /** How a writing unit adopts a placed body's content (`Bodies.adopt`); none, none placed. */
  readonly #adopt: Adopt | undefined;

  constructor(storage: OwnerStorage, limits: Limits = LIMITS, adopt?: Adopt) {
    this.#storage = storage;
    this.#limits = limits;
    this.#adopt = adopt;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** A scope's rows and payload bytes (its rows' and its history's), read by key. */
  #usage(scope: "owner" | "collection" | "writer" | "creator", collection: string,
         who: string): { rows: number; bytes: number } {
    return this.#sql.exec<{ rows: number; bytes: number }>(USAGE, scope, collection, who)
      .toArray()[0] ?? { rows: 0, bytes: 0 };
  }

  /**
   * Refuse a write that would take its writer, collection or owner past a bound.
   *
   * Bytes are the rows' bodies, wherever placed; the history is bounded on its own, and
   * pruned rather than refused. Each bound reads its scope's counters, which the schema's
   * triggers keep in this same transaction.
   */
  #bound(owner: string, caller: string, collection: string, row: StoredRow | undefined,
         size: number): void {
    const limits = this.#limits;
    const old = row === undefined ? 0 : row.body_bytes;
    const add = row === undefined ? 1 : 0;
    type Check = [string, { rows: number; bytes: number }, number, number, number, number];
    const checks: Check[] = [
      ["the owner's collections are full", this.#usage("owner", "", ""), limits.maxOwnerRows,
        limits.maxOwnerBytes, add, size - old],
      ["the collection is full", this.#usage("collection", collection, ""), limits.maxRows,
        limits.maxCollectionBytes, add, size - old],
    ];
    if (caller !== owner) {
      // Rows count against their creator, whoever takes them over later, so neither a
      // turn on a shared row nor a second account frees a share. Bytes count against
      // the current writer: a takeover charges the row's size.
      const mine = row !== undefined && row.writer === caller;
      const share = "this writer's share of the collection is full";
      checks.unshift(
        [share, this.#usage("creator", collection, caller), limits.maxWriterRows,
          limits.maxWriterBytes, add, 0],
        [share, this.#usage("writer", collection, caller), limits.maxWriterRows,
          limits.maxWriterBytes, 0, size - (mine ? old : 0)],
      );
    }
    for (const [reason, used, maxRows, maxBytes, moreRows, moreBytes] of checks) {
      if ((moreRows && used.rows + moreRows > maxRows)
          || (moreBytes > 0 && used.bytes + moreBytes > maxBytes)) {
        throw new Refused(reason);
      }
    }
  }

  /**
   * Commit `body` only while `expectedEtag` is the row's; `""` creates the row.
   *
   * A mismatch throws {@link Conflict} with the row as stored and changes nothing.
   * `historyOp` names the change in the row's history (the board's `claim`, say). A body
   * past `PLACE_BYTES` comes `placed` (ADR-035): its content is adopted in this unit, as
   * `writer`'s in the files plane, and the row and its entry name it.
   */
  write(owner: string, caller: string, collection: string, rowId: string, body: unknown,
        expectedEtag: unknown, isPrivate: unknown, historyOp: unknown = "write",
        placed: Placed | null = null, writer = "owner"): Row {
    name(collection, "collection");
    name(rowId, "row_id");
    checkBody(body, this.#limits);
    if (typeof expectedEtag !== "string" || typeof isPrivate !== "boolean") {
      throw new Invalid("invalid collection write");
    }
    if (typeof historyOp !== "string" || codePoints(historyOp) > 32
        || !IDENTIFIER.test(historyOp) || ERASES.includes(historyOp)) {
      throw new Invalid("history_op is a name of at most 32 characters, not an erase");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = sql.exec<StoredRow>(
        "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ?", collection, rowId,
      ).toArray()[0];
      if (row !== undefined && row.private && row.writer !== caller) {
        throw new Refused("a private row is its writer's own");
      }
      if (row !== undefined && Boolean(row.private) !== isPrivate) {
        throw new Invalid("a row keeps the privacy it was created with");
      }
      if ((row === undefined ? "" : row.etag) !== expectedEtag) {
        throw new Conflict(row === undefined ? null : shown(row));
      }
      // A row made again goes on past its deleted revisions, so none repeats: past its
      // kept entries, or, with none kept, past every revision a prune let go.
      const revision = 1 + (row !== undefined ? row.revision : sql.exec<{ top: number }>(
        "SELECT COALESCE((SELECT MAX(revision) FROM collection_history " +
          "WHERE collection = ? AND key = ?), " +
          "(SELECT revision_floor FROM collection_history_space)) AS top", collection, rowId,
      ).one().top);
      sql.exec(
        "DELETE FROM collection_history WHERE collection = ? AND key = ? AND revision <= ?",
        collection, rowId, revision - this.#limits.maxRevisions,
      );
      const bytes = utf8Bytes(body as string);
      this.#bound(owner, caller, collection, row, bytes);
      const contentId = adopted(body as string, placed, writer, this.#adopt);
      const kept = contentId === null ? body : "";
      const seq = this.top(collection) + 1;
      sql.exec(
        "INSERT INTO collection_seqs VALUES (?, ?) ON CONFLICT (collection) " +
          "DO UPDATE SET seq = excluded.seq", collection, seq,
      );
      const at = now();
      // An upsert on the row's key alone: a seq collision fails loudly on
      // UNIQUE (collection, seq) instead of replacing another row.
      sql.exec(
        "INSERT INTO collection_rows (collection, row_id, revision, seq, etag, writer, " +
          "private, body, updated_at, creator, body_bytes, content_id) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
          "ON CONFLICT (collection, row_id) DO UPDATE SET " +
          "revision = excluded.revision, seq = excluded.seq, etag = excluded.etag, " +
          "writer = excluded.writer, private = excluded.private, body = excluded.body, " +
          "updated_at = excluded.updated_at, body_bytes = excluded.body_bytes, " +
          "content_id = excluded.content_id",
        collection, rowId, revision, seq, digest(`${seq}:${body}`), caller,
        isPrivate ? 1 : 0, kept, at, caller, bytes, contentId,
      );
      sql.exec(
        "INSERT INTO collection_history (collection, key, revision, op, body_json, at, " +
          "writer, body_bytes, content_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        collection, rowId, revision, historyOp, kept, at, caller, bytes, contentId,
      );
      this.#prune();
      const stored = sql.exec<StoredRow>(
        "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ?", collection, rowId,
      ).one();
      // The writer has the body it wrote: shown whole, as an inline one is.
      const { content_id: _placed, ...written } = shown(stored);
      return { ...written, body: body as string };
    });
  }

  /**
   * The history within its bounds: past `maxHistoryCharge` or `maxHistoryBytes`, its oldest
   * entries go, the newest revision they held kept as the floor a key with no entries left
   * goes on past.
   */
  #prune(): void {
    const sql = this.#sql;
    const limits = this.#limits;
    const over = () => {
      const space = sql.exec<{ charge: number; bytes: number }>(
        "SELECT charge, bytes FROM collection_history_space").one();
      return space.charge > limits.maxHistoryCharge || space.bytes > limits.maxHistoryBytes;
    };
    while (over()) {
      const oldest = sql.exec<{ collection: string; key: string; revision: number }>(
        "SELECT collection, key, revision FROM collection_history ORDER BY at, rowid LIMIT ?",
        PRUNE_BATCH).toArray();
      if (oldest.length === 0) {
        throw new Error("the collection history is over its bounds with nothing to prune");
      }
      for (const entry of oldest) {
        sql.exec("DELETE FROM collection_history WHERE collection = ? AND key = ? " +
                   "AND revision = ?", entry.collection, entry.key, entry.revision);
        sql.exec("UPDATE collection_history_space SET revision_floor = " +
                   "MAX(revision_floor, ?)", entry.revision);
        if (!over()) {
          return;
        }
      }
    }
  }

  /**
   * Rows past `after` this caller may see, in `seq` order, and the next cursor.
   *
   * The cursor is the collection's top when the page holds everything, so a reader
   * never rescans; otherwise it is the page's last `seq`. A page holds at most `limit`
   * rows and, past its first, no more than `READ_PAGE_BYTES` of bodies (a placed body is
   * read by the door into its row).
   */
  pull(caller: string, collection: string, after: unknown, limit: unknown): Page {
    name(collection, "collection");
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Invalid("after is a collection seq");
    }
    if (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit <= 0
        || limit > this.#limits.maxPull) {
      throw new Invalid(`limit is 1 to ${this.#limits.maxPull}`);
    }
    const top = this.top(collection);
    const found = this.#sql.exec<StoredRow>(
      "SELECT * FROM collection_rows WHERE collection = ? AND seq > ? AND seq <= ? " +
        "AND (private = 0 OR writer = ?) ORDER BY seq LIMIT ?",
      collection, after, top, caller, limit,
    ).toArray();
    const rows = paged(found);
    const last = rows.at(-1);
    return {
      cursor: (rows.length === limit || rows.length < found.length) && last !== undefined
        ? last.seq : top,
      rows: rows.map(shown),
    };
  }

  #visible(caller: string, collection: string, rowId: string): StoredRow | undefined {
    name(collection, "collection");
    name(rowId, "row_id");
    return this.#sql.exec<StoredRow>(
      "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ? " +
        "AND (private = 0 OR writer = ?)", collection, rowId, caller,
    ).toArray()[0];
  }

  /** One row by its key, as `pull` would show it to this caller, or null. */
  get(caller: string, collection: string, rowId: string): { row: Row | null } {
    const row = this.#visible(caller, collection, rowId);
    return { row: row === undefined ? null : shown(row) };
  }

  /**
   * A row's kept history (at most `maxRevisions` entries), oldest first; none for a row
   * `get` would not show.
   */
  history(caller: string, collection: string, rowId: string): { entries: HistoryEntry[] } {
    if (this.#visible(caller, collection, rowId) === undefined) {
      return { entries: [] };
    }
    // The newest entries whose bodies fit a page, shown oldest first.
    const entries = paged(this.#sql.exec<{ revision: number; op: string; writer: string;
                                           at: number; body_json: string; body_bytes: number;
                                           content_id: string | null }>(
      "SELECT revision, op, writer, at, body_json, body_bytes, content_id FROM " +
        "collection_history WHERE collection = ? AND key = ? ORDER BY revision DESC",
      collection, rowId,
    ).toArray()).reverse();
    return {
      entries: entries.map((e) => ({
        revision: e.revision, op: e.op, writer: e.writer, at: e.at, body: e.body_json,
        ...(e.content_id === null ? {} : { content_id: e.content_id }),
      })),
    };
  }

  /** Delete the rows `where` matches; each keeps one body-less `op` history entry. */
  #erase(caller: string, collection: string, op: string, where: string,
         params: unknown[]): number {
    const sql = this.#sql;
    const rows = sql.exec<{ row_id: string; revision: number }>(
      "SELECT row_id, revision FROM collection_rows WHERE collection = ?" + where,
      collection, ...params,
    ).toArray();
    // The high-water seq stays where the rows had it, so no cursor goes back.
    sql.exec(
      "INSERT INTO collection_seqs VALUES (?, ?) ON CONFLICT (collection) " +
        "DO UPDATE SET seq = excluded.seq", collection, this.top(collection),
    );
    const at = now();
    for (const { row_id: rowId, revision } of rows) {
      sql.exec("DELETE FROM collection_history WHERE collection = ? AND key = ?",
               collection, rowId);
      sql.exec("INSERT INTO collection_history (collection, key, revision, op, body_json, at, " +
                 "writer, body_bytes) VALUES (?, ?, ?, ?, '', ?, ?, 0)",
               collection, rowId, revision + 1, op, at, caller);
      sql.exec("DELETE FROM collection_rows WHERE collection = ? AND row_id = ?",
               collection, rowId);
    }
    // A collection left with no row gives its seq to the floor and goes, so the seqs kept
    // are the collections with rows.
    if (sql.exec("SELECT 1 FROM collection_rows WHERE collection = ? LIMIT 1", collection)
          .toArray().length === 0) {
      sql.exec("UPDATE collection_history_space SET seq_floor = MAX(seq_floor, ?)",
               this.top(collection));
      sql.exec("DELETE FROM collection_seqs WHERE collection = ?", collection);
    }
    this.#prune();
    return rows.length;
  }

  /**
   * Delete one row only while `expectedEtag` is its etag (the owner's act alone).
   *
   * A mismatch throws {@link Conflict} with the row as `get` shows it, and changes
   * nothing. Like a clear, a delete never lowers the collection's `seq` and a pull
   * never shows it.
   */
  delete(caller: string, collection: string, rowId: string, expectedEtag: unknown):
      { deleted: number } {
    if (typeof expectedEtag !== "string") {
      throw new Invalid("invalid collection delete");
    }
    name(rowId, "row_id");
    name(collection, "collection");
    return this.#storage.transactionSync(() => {
      if (!this.#erase(caller, collection, "delete",
                       " AND row_id = ? AND etag = ? AND (private = 0 OR writer = ?)",
                       [rowId, expectedEtag, caller])) {
        throw new Conflict(this.get(caller, collection, rowId).row);
      }
      return { deleted: 1 };
    });
  }

  /**
   * Delete a collection's rows, or every row `writer` created or last wrote.
   *
   * The owner's act alone. Matching creator or writer removes an abuser's rows even
   * after another account took them over.
   */
  clear(caller: string, collection: string, writer: unknown): { deleted: number } {
    name(collection, "collection");
    if (writer !== null) {
      name(writer, "writer");
    }
    const [where, params] = writer === null
      ? ["", []]
      : [" AND (writer = ? OR creator = ?)", [writer, writer]];
    return this.#storage.transactionSync(() => ({
      deleted: this.#erase(caller, collection, "clear", where, params),
    }));
  }

  /**
   * The collection's high-water seq: never below a stored row's, never lowered by a
   * clear.
   */
  protected top(collection: string): number {
    return this.#sql.exec<{ top: number }>(
      "SELECT MAX(COALESCE((SELECT seq FROM collection_seqs WHERE collection = ?), " +
        "(SELECT seq_floor FROM collection_history_space)), " +
        "COALESCE((SELECT MAX(seq) FROM collection_rows WHERE collection = ?), 0)) AS top",
      collection, collection,
    ).one().top;
  }
}
