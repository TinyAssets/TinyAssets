/**
 * The one revision primitive (S34), in the owner's own object (ADR-032).
 *
 * Ported from `tinyassets/broker/conversations.py` (branch `s5b-board`), whose rules
 * it keeps. Rows are keyed by `(collection, row_id)`. Each write is a compare-and-swap
 * on the row's etag, and each commit is stamped with the collection's next `seq`, so
 * a reader pulls after a cursor, or gets one row by its key. The board is
 * `collection = "board"`; a Tier A app room is `collection = "room:<app>:<room>"`.
 * Payloads are stored and returned verbatim. A private row is visible to, and
 * writable by, its writer alone. The rows are bounded by what they take in the object, laid
 * out as SQLite lays them, names and index entries included (`maxRowsCharge`), and an
 * outside writer (a client, or a visiting owner) by the owner's own share of it; past a
 * bound a write is refused. Only the owner deletes a row, or clears a collection or one
 * writer's rows in it (the object enforces that at its RPC door).
 *
 * `collection_history` is the primitive's own record: each write appends the revision
 * it committed (its writer, op and body), and a delete or clear appends one body-less
 * entry and drops the row's earlier bodies, so a row made again under that key goes on
 * from the next revision and never repeats one. The history is bounded by what it takes,
 * not by rows times revisions: its
 * entries as SQLite lays them out (`maxHistoryCharge`), an erased key's last entry (its
 * tombstone) among them, the oldest pruned first; a key whose every entry was pruned goes
 * on past the newest revision a prune let go, so none repeats. It is a retention, never
 * refused, and its placed bodies are the first room made at the files byte quota
 * (`reclaim`).
 *
 * Where a body lives is the placement rule (ADR-035, `bodies.ts`): a body past
 * `PLACE_BYTES` is placed in R2 before its write (the door's `Bodies.begin`), and the row
 * and its history entry name its content, counted in the writer's files scope; the tier's
 * storage bounds those bytes.
 *
 * Every change runs in one `transactionSync`, so a refusal or a thrown error leaves
 * nothing behind.
 */
import { createHash } from "node:crypto";

import { type Adopt, adopted, BODY_BYTES_MAX, type Placed, READ_PAGE_BYTES } from "./bodies";
import { Invalid, Refused } from "./errors";
import type { OwnerStorage } from "./gate";
import { codePoints, name, utf8Bytes } from "./text";

export interface Limits {
  /** A row body, in UTF-8 bytes: one call through the object whole (`BODY_BYTES_MAX`). */
  readonly maxRowBytes: number;
  /**
   * The rows as SQLite lays them out (`ROW_CHARGE`): each row with its four index entries,
   * each collection's seq row and each outside writer's counter, in bytes, whatever their
   * names. The owner-bound guard's share of the 10 GB for them.
   */
  readonly maxRowsCharge: number;
  /** Rows one pull, or history entries one history read, returns: a page. */
  readonly maxPull: number;
  /** The history's entries, laid out as SQLite lays them (`HISTORY_CHARGE`), in bytes. */
  readonly maxHistoryCharge: number;
}

/**
 * The collections' bounds: the bytes their rows and their history take in the object, which
 * the owner-bound guard holds within its half of the 10 GB, and a body's own size. Bodies
 * past `PLACE_BYTES` are in R2, held by the tier's storage. An outside writer (a client, or
 * a visiting owner) holds at most the owner's own `client_share` of the rows' bytes, and
 * all of them together what `owner_reserve` leaves (`Shares`), as in the owner's files.
 */
export const LIMITS: Limits = Object.freeze({
  maxRowBytes: BODY_BYTES_MAX,
  maxRowsCharge: 256 * 1024 * 1024,
  maxPull: 100,
  maxHistoryCharge: 128 * 1024 * 1024,
});

/** The owner's own shares (its files policy's `client_share` and `owner_reserve`). */
export interface Shares {
  readonly client_share: number;
  readonly owner_reserve: number;
}

/** The files policy's defaults, for a primitive built without the owner's policy. */
const DEFAULT_SHARES: Shares = Object.freeze({ client_share: 25, owner_reserve: 25 });
/** The owner's own files scope (`manifest.ts`'s `OWNER_SCOPE`). */
export const OWNER_FILER = "owner";

/** The history ops a write never names. */
const ERASES: readonly string[] = Object.freeze(["delete", "clear"]);
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

/** A row's kept history, oldest first, and the cursor to read the older entries by. */
export interface History {
  readonly entries: HistoryEntry[];
  /** Whether older entries are kept that this page did not return. */
  readonly more: boolean;
  /** The revision to read before (`before_revision`) for them; null when none are left. */
  readonly before_revision: number | null;
}

export interface Page {
  readonly cursor: number;
  readonly rows: Row[];
}

/**
 * A row body as a write takes it, or refused: text of at most `maxRowBytes` UTF-8 bytes, and
 * well-formed. The door checks it before it places the body (ADR-035).
 */
export function checkBody(body: unknown, limits: Limits = LIMITS): string {
  if (typeof body !== "string" || utf8Bytes(body) > limits.maxRowBytes) {
    throw new Invalid(`a row body is text of at most ${limits.maxRowBytes} bytes`);
  }
  if (LONE_SURROGATE.test(body)) {
    throw new Invalid("a row body is well-formed text");
  }
  return body;
}

/**
 * The rows of the writers a files scope names: any (`*`), every outside writer
 * (`client:*`), or one. Bound three times; no bound `GLOB` pattern (a Durable Object's
 * SQLite takes one of at most 50 bytes, and a writer is 71).
 */
export const FILERS = "(? = '*' OR (? = 'client:*' AND filer GLOB 'client:*') OR filer = ?)";

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
  filer: string;
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
  /** The owner's shares, read at each write. */
  readonly #shares: () => Shares;

  constructor(storage: OwnerStorage, limits: Limits = LIMITS, adopt?: Adopt,
              shares: () => Shares = () => DEFAULT_SHARES) {
    this.#storage = storage;
    this.#limits = limits;
    this.#adopt = adopt;
    this.#shares = shares;
  }

  get #sql(): OwnerStorage["sql"] {
    return this.#storage.sql;
  }

  /** The rows' charge in all, and an outside writer's own and all outside writers'. */
  #charges(filer: string): { owner: number; mine: number; outside: number } {
    const sql = this.#sql;
    const usage = (who: string) => sql.exec<{ charge: number }>(
      "SELECT charge FROM collection_usage WHERE who = ?", who).toArray()[0]?.charge ?? 0;
    return { owner: sql.exec<{ rows_charge: number }>(
               "SELECT rows_charge FROM collection_space").one().rows_charge,
             mine: filer === OWNER_FILER ? 0 : usage(filer), outside: usage("*") };
  }

  /**
   * Refuse a write that took the rows' charge past its bound, or an outside writer past its
   * share of it or all outside writers past what the owner's reserve leaves. A write that
   * leaves a charge no higher passes, so a writer over a lowered share can still shrink.
   * The history is bounded on its own, pruned rather than refused.
   */
  #bound(before: { owner: number; mine: number; outside: number }, filer: string): void {
    const most = this.#limits.maxRowsCharge;
    const after = this.#charges(filer);
    const { client_share: share, owner_reserve: reserve } = this.#shares();
    const over = (key: "owner" | "mine" | "outside", bound: number) =>
      after[key] > before[key] && after[key] > bound;
    if (over("owner", most)) {
      throw new Refused("the owner's collections are full");
    }
    if (filer !== OWNER_FILER && over("mine", Math.floor(most * share / 100))) {
      throw new Refused("this writer's share of the owner's collections is full");
    }
    if (filer !== OWNER_FILER && over("outside", Math.floor(most * (100 - reserve) / 100))) {
      throw new Refused("outside writers' share of the owner's collections is full; the " +
                        "rest is kept for the owner");
    }
  }

  /**
   * Commit `body` only while `expectedEtag` is the row's; `""` creates the row.
   *
   * A mismatch throws {@link Conflict} with the row as stored and changes nothing.
   * `historyOp` names the change in the row's history (the board's `claim`, say). `filer` is
   * the writer's files scope (the owner's, or an outside writer's `client:<id>`): the row's
   * bytes count in its share, and a body past `PLACE_BYTES` comes `placed` (ADR-035), its
   * content adopted in this unit in that scope, the row and its entry naming it.
   */
  write(owner: string, caller: string, collection: string, rowId: string, body: unknown,
        expectedEtag: unknown, isPrivate: unknown, historyOp: unknown = "write",
        placed: Placed | null = null, filer = OWNER_FILER): Row {
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
          "(SELECT revision_floor FROM collection_space)) AS top", collection, rowId,
      ).one().top);
      const bytes = utf8Bytes(body as string);
      const before = this.#charges(filer);
      return adopted(body as string, placed, filer, this.#adopt, (contentId) => {
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
            "private, body, updated_at, creator, body_bytes, content_id, filer) " +
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
            "ON CONFLICT (collection, row_id) DO UPDATE SET " +
            "revision = excluded.revision, seq = excluded.seq, etag = excluded.etag, " +
            "writer = excluded.writer, private = excluded.private, body = excluded.body, " +
            "updated_at = excluded.updated_at, body_bytes = excluded.body_bytes, " +
            "content_id = excluded.content_id, filer = excluded.filer",
          collection, rowId, revision, seq, digest(`${seq}:${body}`), caller,
          isPrivate ? 1 : 0, kept, at, caller, bytes, contentId, filer,
        );
        sql.exec(
          "INSERT INTO collection_history (collection, key, revision, op, body_json, at, " +
            "writer, body_bytes, content_id, filer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
          collection, rowId, revision, historyOp, kept, at, caller, bytes, contentId, filer,
        );
        this.#prune();
        this.#bound(before, filer);
        const stored = sql.exec<StoredRow>(
          "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ?", collection,
          rowId,
        ).one();
        // The writer has the body it wrote: shown whole, as an inline one is.
        const { content_id: _placed, ...written } = shown(stored);
        return { ...written, body: body as string };
      });
    });
  }

  /**
   * The history within its bounds: past `maxHistoryCharge`, its oldest entries go,
   * tombstones among them, the newest revision they held kept as the floor a key with no
   * entries left goes on past.
   */
  #prune(): void {
    const sql = this.#sql;
    const over = () => sql.exec<{ charge: number }>("SELECT charge FROM collection_space")
      .one().charge > this.#limits.maxHistoryCharge;
    while (over()) {
      const oldest = sql.exec<{ collection: string; key: string; revision: number }>(
        "SELECT collection, key, revision FROM collection_history ORDER BY at, rowid LIMIT ?",
        PRUNE_BATCH).toArray();
      if (oldest.length === 0) {
        throw new Error("the collection history is over its bounds with nothing to prune");
      }
      for (const entry of oldest) {
        this.#let(entry);
        if (!over()) {
          return;
        }
      }
    }
  }

  /** One history entry let go, its revision kept as the floor. */
  #let(entry: { collection: string; key: string; revision: number }): void {
    this.#sql.exec("DELETE FROM collection_history WHERE collection = ? AND key = ? " +
                     "AND revision = ?", entry.collection, entry.key, entry.revision);
    this.#sql.exec("UPDATE collection_space SET revision_floor = MAX(revision_floor, ?)",
                   entry.revision);
  }

  /**
   * In a change at the files byte quota (`Files.#fit`, ADR-035): the oldest history entry
   * of the writers `filer` names (`FILERS`) whose placed body no live row
   * also names, let go, so its bytes can go; false when none is left.
   */
  readonly reclaim = (filer: string): boolean => {
    const entry = this.#sql.exec<{ collection: string; key: string; revision: number }>(
      "SELECT collection, key, revision FROM collection_history h WHERE content_id IS NOT " +
        `NULL AND ${FILERS} AND NOT EXISTS (SELECT 1 FROM collection_rows r WHERE ` +
        "r.collection = h.collection AND r.row_id = h.key AND r.content_id = h.content_id) " +
        "ORDER BY at, rowid LIMIT 1", filer, filer, filer).toArray()[0];
    if (entry === undefined) {
      return false;
    }
    this.#let(entry);
    return true;
  };

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
   * A row's kept history, oldest first, before `before_revision` when it is given; none for
   * a row `get` would not show. A page holds the newest entries, at most `maxPull`, whose
   * bodies fit `READ_PAGE_BYTES`, at least one; `more` and the cursor name the older ones it
   * left.
   */
  history(caller: string, collection: string, rowId: string, before: unknown = null):
      History {
    if (before !== null && (typeof before !== "number" || !Number.isSafeInteger(before)
                            || before < 1)) {
      throw new Invalid("before_revision is a revision");
    }
    if (this.#visible(caller, collection, rowId) === undefined) {
      return { entries: [], more: false, before_revision: null };
    }
    const found = this.#sql.exec<{ revision: number; op: string; writer: string; at: number;
                                   body_json: string; body_bytes: number;
                                   content_id: string | null }>(
      "SELECT revision, op, writer, at, body_json, body_bytes, content_id FROM " +
        "collection_history WHERE collection = ? AND key = ? AND revision < ? " +
        "ORDER BY revision DESC LIMIT ?", collection, rowId, before ?? Number.MAX_SAFE_INTEGER,
      this.#limits.maxPull + 1,
    ).toArray();
    const page = paged(found.slice(0, this.#limits.maxPull));
    const more = page.length < found.length;
    return {
      entries: page.reverse().map((e) => ({
        revision: e.revision, op: e.op, writer: e.writer, at: e.at, body: e.body_json,
        ...(e.content_id === null ? {} : { content_id: e.content_id }),
      })),
      more,
      before_revision: more ? page[0]!.revision : null,
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
                 "writer, body_bytes, filer) VALUES (?, ?, ?, ?, '', ?, ?, 0, ?)",
               collection, rowId, revision + 1, op, at, caller, OWNER_FILER);
      sql.exec("DELETE FROM collection_rows WHERE collection = ? AND row_id = ?",
               collection, rowId);
    }
    // A collection left with no row gives its seq to the floor and goes, so the seqs kept
    // are the collections with rows.
    if (sql.exec("SELECT 1 FROM collection_rows WHERE collection = ? LIMIT 1", collection)
          .toArray().length === 0) {
      sql.exec("UPDATE collection_space SET seq_floor = MAX(seq_floor, ?)",
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
        "(SELECT seq_floor FROM collection_space)), " +
        "COALESCE((SELECT MAX(seq) FROM collection_rows WHERE collection = ?), 0)) AS top",
      collection, collection,
    ).one().top;
  }
}
