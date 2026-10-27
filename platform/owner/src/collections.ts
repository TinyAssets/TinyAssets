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
 * entries, and its history bytes count in every byte bound.
 *
 * Every change runs in one `transactionSync`, so a refusal or a thrown error leaves
 * nothing behind.
 */
import { createHash } from "node:crypto";

import { Invalid, Refused } from "./errors";
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
}

/**
 * Bounds on payload bytes and rows, each refused loudly. A writer other than the
 * owner gets a small share of one collection; one collection gets a slice of the
 * owner's total, so one room cannot starve another.
 */
export const LIMITS: Limits = Object.freeze({
  maxRow: 65536,
  maxWriterRows: 64,
  maxWriterBytes: 512 * 1024,
  maxRows: 1000,
  maxCollectionBytes: 8 * 1024 * 1024,
  maxOwnerRows: 10_000,
  maxOwnerBytes: 64 * 1024 * 1024,
  maxPull: 100,
  maxRevisions: 10,
});

/** The history ops a write never names. */
const ERASES: readonly string[] = Object.freeze(["delete", "clear"]);
const SIZE =
  "SELECT COUNT(*) AS n, COALESCE(SUM(length(CAST(body AS BLOB))), 0) AS bytes " +
  "FROM collection_rows";
const HISTORY_SIZE =
  "SELECT COALESCE(SUM(length(CAST(body_json AS BLOB))), 0) AS bytes FROM collection_history";
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
  readonly body: string;
}

export interface HistoryEntry {
  readonly revision: number;
  readonly op: string;
  readonly writer: string;
  readonly at: number;
  readonly body: string;
}

export interface Page {
  readonly cursor: number;
  readonly rows: Row[];
}

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
};

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
  };
}

function now(): number {
  return Date.now() / 1000;
}

export class Collections {
  readonly #storage: DurableObjectStorage;
  readonly #limits: Limits;

  constructor(storage: DurableObjectStorage, limits: Limits = LIMITS) {
    this.#storage = storage;
    this.#limits = limits;
  }

  get #sql(): SqlStorage {
    return this.#storage.sql;
  }

  /**
   * Refuse a write that would take its writer, collection or owner past a bound.
   *
   * Bytes are the rows' and their history's, measured after this write's prune; the
   * write adds its body twice, as the row and as its history entry.
   */
  #bound(owner: string, caller: string, collection: string, row: StoredRow | undefined,
         body: string): void {
    const limits = this.#limits;
    const size = utf8Bytes(body);
    const old = row === undefined ? 0 : utf8Bytes(row.body);
    const add = row === undefined ? 1 : 0;
    type Check = [string, string, unknown[], number, number, number, number];
    const checks: Check[] = [
      ["the owner's collections are full", "", [], limits.maxOwnerRows, limits.maxOwnerBytes,
        add, 2 * size - old],
      ["the collection is full", " WHERE collection = ?", [collection], limits.maxRows,
        limits.maxCollectionBytes, add, 2 * size - old],
    ];
    if (caller !== owner) {
      // Rows count against their creator, whoever takes them over later, so neither a
      // turn on a shared row nor a second account frees a share. Bytes count against
      // the current writer: a takeover charges the row's size.
      const mine = row !== undefined && row.writer === caller;
      const share = "this writer's share of the collection is full";
      checks.unshift(
        [share, " WHERE collection = ? AND creator = ?", [collection, caller],
          limits.maxWriterRows, limits.maxWriterBytes, add, 0],
        [share, " WHERE collection = ? AND writer = ?", [collection, caller],
          limits.maxWriterRows, limits.maxWriterBytes, 0, 2 * size - (mine ? old : 0)],
      );
    }
    for (const [reason, where, params, maxRows, maxBytes, moreRows, moreBytes] of checks) {
      const used = this.#sql.exec<{ n: number; bytes: number }>(SIZE + where, ...params).one();
      let bytes = used.bytes;
      if (moreBytes > 0) {
        bytes += this.#sql.exec<{ bytes: number }>(HISTORY_SIZE + where, ...params).one().bytes;
      }
      if ((moreRows && used.n + moreRows > maxRows) || (moreBytes > 0 && bytes + moreBytes > maxBytes)) {
        throw new Refused(reason);
      }
    }
  }

  /**
   * Commit `body` only while `expectedEtag` is the row's; `""` creates the row.
   *
   * A mismatch throws {@link Conflict} with the row as stored and changes nothing.
   * `historyOp` names the change in the row's history (the board's `claim`, say).
   */
  write(owner: string, caller: string, collection: string, rowId: string, body: unknown,
        expectedEtag: unknown, isPrivate: unknown, historyOp: unknown = "write"): Row {
    name(collection, "collection");
    name(rowId, "row_id");
    if (typeof body !== "string" || codePoints(body) > this.#limits.maxRow) {
      throw new Invalid(`a row body is text of at most ${this.#limits.maxRow} characters`);
    }
    if (LONE_SURROGATE.test(body)) {
      throw new Invalid("a row body is well-formed text");
    }
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
      // A row made again goes on past its deleted revisions, so none repeats.
      const revision = 1 + (row !== undefined ? row.revision : sql.exec<{ top: number }>(
        "SELECT COALESCE(MAX(revision), 0) AS top FROM collection_history " +
          "WHERE collection = ? AND key = ?", collection, rowId,
      ).one().top);
      sql.exec(
        "DELETE FROM collection_history WHERE collection = ? AND key = ? AND revision <= ?",
        collection, rowId, revision - this.#limits.maxRevisions,
      );
      this.#bound(owner, caller, collection, row, body);
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
          "private, body, updated_at, creator) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
          "ON CONFLICT (collection, row_id) DO UPDATE SET " +
          "revision = excluded.revision, seq = excluded.seq, etag = excluded.etag, " +
          "writer = excluded.writer, private = excluded.private, body = excluded.body, " +
          "updated_at = excluded.updated_at",
        collection, rowId, revision, seq, digest(`${seq}:${body}`), caller,
        isPrivate ? 1 : 0, body, at, caller,
      );
      sql.exec(
        "INSERT INTO collection_history VALUES (?, ?, ?, ?, ?, ?, ?)",
        collection, rowId, revision, historyOp, body, at, caller,
      );
      return shown(sql.exec<StoredRow>(
        "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ?", collection, rowId,
      ).one());
    });
  }

  /**
   * Rows past `after` this caller may see, in `seq` order, and the next cursor.
   *
   * The cursor is the collection's top when the page holds everything, so a reader
   * never rescans; otherwise it is the page's last `seq`.
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
    const rows = this.#sql.exec<StoredRow>(
      "SELECT * FROM collection_rows WHERE collection = ? AND seq > ? AND seq <= ? " +
        "AND (private = 0 OR writer = ?) ORDER BY seq LIMIT ?",
      collection, after, top, caller, limit,
    ).toArray();
    const last = rows.at(-1);
    return {
      cursor: rows.length === limit && last !== undefined ? last.seq : top,
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
    const entries = this.#sql.exec<{ revision: number; op: string; writer: string; at: number;
                                     body_json: string }>(
      "SELECT revision, op, writer, at, body_json FROM collection_history " +
        "WHERE collection = ? AND key = ? ORDER BY revision", collection, rowId,
    ).toArray();
    return {
      entries: entries.map((e) => ({
        revision: e.revision, op: e.op, writer: e.writer, at: e.at, body: e.body_json,
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
      sql.exec("INSERT INTO collection_history VALUES (?, ?, ?, ?, '', ?, ?)",
               collection, rowId, revision + 1, op, at, caller);
      sql.exec("DELETE FROM collection_rows WHERE collection = ? AND row_id = ?",
               collection, rowId);
    }
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
      "SELECT MAX(COALESCE((SELECT seq FROM collection_seqs WHERE collection = ?), 0), " +
        "COALESCE((SELECT MAX(seq) FROM collection_rows WHERE collection = ?), 0)) AS top",
      collection, collection,
    ).one().top;
  }
}
