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
 * Each write, and each delete's or clear's history entry, also takes the owner-wide
 * clock's next change (`collection_clock`), so one cursor covers many collections
 * (`changed`): a main agent's digests (slice M) read the board and every room in one call,
 * deletions included. A row keeps the privacy it was made with, so a row turns private
 * only by being deleted and made again, which its delete reports.
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
import { sha256Hex } from "../../contract/src/digest";

import {
  type Adopt, adopted, BODY_BYTES_MAX, bodyOffset, type Placed, READ_PAGE_BYTES,
} from "./bodies";
import { Refusal } from "../../contract/src/refusal";
import type { OwnerStorage } from "./gate";
import {
  bodied, conflictShown, contentsOf, fitPage, fittedRow, headChars, heads, headWindow, PAGE_BYTES,
  windowed, windowedAll,
} from "./pages";
import { codePoints, name, utf8Bytes } from "../../contract/src/text";
import type {
  Account, Call, Door, OwnerState,
} from "./owner-do";
import { clock } from "./clock";
import { jsonBytes } from "../../contract/src/encoded";
import { filerOf, MAIN_AGENT } from "./authority";
import type { Heads } from "./pages";

export interface Limits {
  /** A row body, in UTF-8 bytes: one call through the object whole (`BODY_BYTES_MAX`). */
  readonly maxRowBytes: number;
  /**
   * The rows as SQLite lays them out (the schema's triggers charge them): each row with its
   * five index entries,
   * each collection's seq row and each outside writer's counter, in bytes, whatever their
   * names. The owner-bound guard's share of the 10 GB for them.
   */
  readonly maxRowsCharge: number;
  /** Rows one pull, or history entries one history read, returns: a page. */
  readonly maxPull: number;
  /** The history's entries, laid out as SQLite lays them (the schema's triggers), in bytes. */
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

interface HistoryEntry {
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
    throw new Refusal("invalid", `a row body is text of at most ${limits.maxRowBytes} bytes`);
  }
  if (LONE_SURROGATE.test(body)) {
    throw new Refusal("invalid", "a row body is well-formed text");
  }
  return body;
}

/**
 * The rows of the writers a files scope names: any (`*`), every outside writer
 * (`client:*`), or one. Bound three times; no bound `GLOB` pattern (a Durable Object's
 * SQLite takes one of at most 50 bytes, and a writer is 71).
 */
const FILERS = "(? = '*' OR (? = 'client:*' AND filer GLOB 'client:*') OR filer = ?)";

/** History entries one prune step reads, oldest first. */
const PRUNE_BATCH = 64;

/**
 * A row as a digest shows it, where it lives and its owner-wide change; or a row's
 * deletion (a delete or a clear, by its writer), shown to whoever could see the row.
 */
export type Changed =
  | (Row & { readonly collection: string; readonly change: number; readonly deleted: false })
  | { readonly collection: string; readonly row_id: string; readonly change: number;
      readonly deleted: true; readonly op: string; readonly writer: string };

/**
 * Rows changed after an owner-wide cursor, and the next cursor; `reset` when a deletion past
 * the cursor was pruned from the history (`erasure_floor`): the reader reads its rows again
 * whole (a pull) and goes on from the cursor given.
 */
export interface Changes {
  readonly cursor: number;
  readonly rows: Changed[];
  readonly reset?: true;
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
  body_bytes: number;
  content_id: string | null;
  filer: string;
  change: number;
};

/**
 * A page's rows: the first always, then each while the bodies fit `bytes`: `PAGE_BYTES` for
 * a read that answers them whole (a body is at least its bytes as JSON, so no more could
 * fit the answer; the door fits it exactly, `pages.ts`), `READ_PAGE_BYTES` for a digest's
 * heads (the bucket reads it makes).
 */
function paged<T extends { body_bytes: number }>(rows: readonly T[], most: number): T[] {
  const page: T[] = [];
  let bytes = 0;
  for (const row of rows) {
    bytes += row.body_bytes;
    if (page.length > 0 && bytes > most) {
      break;
    }
    page.push(row);
  }
  return page;
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

/**
 * `changed`'s query: each part read by its change index (named, so a plan that would scan
 * a collection's rows instead fails loudly) from the cursor on, the rows written past it and
 * the deletions past it of rows the caller could see; then filtered by collection.
 */
export function changedSql(where: "collection" | "prefix"): string {
  const test = where === "collection" ? "collection = ?" : "collection >= ? AND collection < ?";
  return "SELECT collection, row_id, revision, seq, etag, writer, private, body, change, " +
    "body_bytes, content_id, 0 AS deleted, '' AS op FROM collection_rows " +
    "INDEXED BY collection_changes " +
    `WHERE change > ? AND ${test} AND (private = 0 OR writer = ?) ` +
    "UNION ALL SELECT collection, key, revision, 0, '', writer, 0, '', change, 0, NULL, 1, op " +
    "FROM collection_history INDEXED BY collection_erasures " +
    `WHERE change > ? AND ${test} AND (private_writer IS NULL OR private_writer = ?) ` +
    "ORDER BY change LIMIT ?";
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
      throw new Refusal("refused", "the owner's collections are full");
    }
    if (filer !== OWNER_FILER && over("mine", Math.floor(most * share / 100))) {
      throw new Refusal("refused", "this writer's share of the owner's collections is full");
    }
    if (filer !== OWNER_FILER && over("outside", Math.floor(most * (100 - reserve) / 100))) {
      throw new Refusal("refused",
                        "outside writers' share of the owner's collections is full; the " +
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
      throw new Refusal("invalid", "invalid collection write");
    }
    if (typeof historyOp !== "string" || codePoints(historyOp) > 32
        || !IDENTIFIER.test(historyOp) || ERASES.includes(historyOp)) {
      throw new Refusal("invalid", "history_op is a name of at most 32 characters, not an erase");
    }
    const sql = this.#sql;
    return this.#storage.transactionSync(() => {
      const row = sql.exec<StoredRow>(
        "SELECT * FROM collection_rows WHERE collection = ? AND row_id = ?", collection, rowId,
      ).toArray()[0];
      if (row !== undefined && row.private && row.writer !== caller) {
        throw new Refusal("refused", "a private row is its writer's own");
      }
      if (row !== undefined && Boolean(row.private) !== isPrivate) {
        throw new Refusal("invalid", "a row keeps the privacy it was created with");
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
        const change = this.#tick();
        // An upsert on the row's key alone: a seq collision fails loudly on
        // UNIQUE (collection, seq) instead of replacing another row.
        sql.exec(
          "INSERT INTO collection_rows (collection, row_id, revision, seq, etag, writer, " +
            "private, body, updated_at, creator, body_bytes, content_id, filer, change) " +
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
            "ON CONFLICT (collection, row_id) DO UPDATE SET " +
            "revision = excluded.revision, seq = excluded.seq, etag = excluded.etag, " +
            "writer = excluded.writer, private = excluded.private, body = excluded.body, " +
            "updated_at = excluded.updated_at, body_bytes = excluded.body_bytes, " +
            "content_id = excluded.content_id, filer = excluded.filer, " +
            "change = excluded.change",
          collection, rowId, revision, seq, sha256Hex(`${seq}:${body}`), caller,
          isPrivate ? 1 : 0, kept, at, caller, bytes, contentId, filer, change,
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

  /**
   * One history entry let go, its revision kept as the floor; a delete's or clear's entry
   * its change too (`erasure_floor`), so a feed reader behind it is told to read again.
   */
  #let(entry: { collection: string; key: string; revision: number }): void {
    const gone = this.#sql.exec<{ change: number | null }>(
      "DELETE FROM collection_history WHERE collection = ? AND key = ? AND revision = ? " +
        "RETURNING change", entry.collection, entry.key, entry.revision).toArray()[0];
    this.#sql.exec("UPDATE collection_space SET revision_floor = MAX(revision_floor, ?), " +
                     "erasure_floor = MAX(erasure_floor, ?)", entry.revision,
                   gone?.change ?? 0);
  }

  /**
   * In a change at the files byte quota (`Files.#fit`, ADR-035): the oldest history entry
   * of the writers `filer` names (`FILERS`) whose placed body no live row
   * also names, let go, so its bytes can go; false when none is left.
   */
  /**
   * The bytes of every collection body the owner stores, its rows' and their history's,
   * inline or placed (once a content). A read.
   */
  /**
   * A read for the cutover's check (`import.ts`, `importCheck`): `limit` rows from the
   * `offset`th, by collection and id: each one's privacy, and its body inline or its placed
   * content's id.
   */
  rowsPage(offset: number, limit: number): { collection: string; row_id: string; private: 0 | 1;
                                            body: string; content_id: string | null }[] {
    return this.#sql.exec<{ collection: string; row_id: string; private: number; body: string;
                            content_id: string | null }>(
      "SELECT collection, row_id, private, body, content_id FROM collection_rows " +
        "ORDER BY collection, row_id LIMIT ? OFFSET ?", limit, offset)
      .toArray().map((row) => ({ ...row, private: row.private === 1 ? 1 : 0 }));
  }

  storedBytes(): number {
    return this.#sql.exec<{ bytes: number }>(
      "SELECT coalesce((SELECT SUM(body_bytes) FROM collection_rows WHERE content_id IS NULL), " +
        "0) + coalesce((SELECT SUM(body_bytes) FROM collection_history WHERE content_id IS " +
        "NULL), 0) + coalesce((SELECT SUM(size) FROM file_contents WHERE content_id IN " +
        "(SELECT content_id FROM collection_rows WHERE content_id IS NOT NULL UNION SELECT " +
        "content_id FROM collection_history WHERE content_id IS NOT NULL)), 0) AS bytes")
      .one().bytes;
  }

  readonly reclaim = (filer: string): boolean => {
    // The oldest history entry that holds bytes no live row holds: an inline body (never
    // the row's own revision), or placed content the row no longer names.
    const entry = this.#sql.exec<{ collection: string; key: string; revision: number }>(
      "SELECT collection, key, revision FROM collection_history h WHERE body_bytes > 0 AND " +
        `${FILERS} AND NOT EXISTS (SELECT 1 FROM collection_rows r WHERE ` +
        "r.collection = h.collection AND r.row_id = h.key AND (r.revision = h.revision OR " +
        "r.content_id = h.content_id)) ORDER BY at, rowid LIMIT 1", filer, filer, filer)
      .toArray()[0];
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
   * rows and, past its first, no more than `PAGE_BYTES` of bodies (a placed body is read by
   * the door into its row, and the door fits the page to one answer, `pages.ts`).
   */
  pull(caller: string, collection: string, after: unknown, limit: unknown): Page {
    name(collection, "collection");
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Refusal("invalid", "after is a collection seq");
    }
    if (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit <= 0
        || limit > this.#limits.maxPull) {
      throw new Refusal("invalid", `limit is 1 to ${this.#limits.maxPull}`);
    }
    const top = this.top(collection);
    const found = this.#sql.exec<StoredRow>(
      "SELECT * FROM collection_rows WHERE collection = ? AND seq > ? AND seq <= ? " +
        "AND (private = 0 OR writer = ?) ORDER BY seq LIMIT ?",
      collection, after, top, caller, limit,
    ).toArray();
    const rows = paged(found, PAGE_BYTES);
    const last = rows.at(-1);
    return {
      cursor: (rows.length === limit || rows.length < found.length) && last !== undefined
        ? last.seq : top,
      rows: rows.map(shown),
    };
  }

  /**
   * Rows this caller may see, in `collection` or in every collection whose name starts
   * with `prefix`, whose latest write is past the owner-wide change `after`, oldest change
   * first; and the next cursor: the page's last change when it is full, else the owner's
   * latest, so a reader never rescans and its cursor never goes back. A page holds at most
   * `READ_PAGE_BYTES` of bodies past its first row, as a pull's (`paged`), and a placed body
   * (ADR-035) comes as its content id, for the door to read from R2 (`filled`).
   */
  changed(caller: string, where: { collection: string } | { prefix: string }, after: unknown,
          limit: unknown): Changes {
    if (typeof after !== "number" || !Number.isSafeInteger(after) || after < 0) {
      throw new Refusal("invalid", "after is a change cursor");
    }
    if (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit <= 0
        || limit > this.#limits.maxPull) {
      throw new Refusal("invalid", `limit is 1 to ${this.#limits.maxPull}`);
    }
    const space = this.#sql.exec<{ erasure_floor: number }>(
      "SELECT erasure_floor FROM collection_space").one();
    if (after > 0 && after < space.erasure_floor) {
      // A deletion this reader has not seen is gone from the history: it cannot be told,
      // so it is told to read again, loudly, never left holding a deleted row.
      const top = this.#sql.exec<{ change: number }>(
        "SELECT change FROM collection_clock").one().change;
      return { cursor: top, rows: [], reset: true };
    }
    // A prefix is the range from itself to the name just past every name it starts.
    const keys = "collection" in where ? [name(where.collection, "collection")]
      : [where.prefix, where.prefix.slice(0, -1) + String.fromCodePoint(
          where.prefix.codePointAt(where.prefix.length - 1)! + 1)];
    const found = this.#sql.exec<StoredRow & { deleted: number; op: string }>(
      changedSql("collection" in where ? "collection" : "prefix"),
      after, ...keys, caller, after, ...keys, caller, limit,
    ).toArray();
    const rows = paged(found, READ_PAGE_BYTES);
    const top = this.#sql.exec<{ change: number }>(
      "SELECT change FROM collection_clock").one().change;
    const last = rows.at(-1);
    const full = found.length === limit || rows.length < found.length;
    return {
      cursor: full && last !== undefined ? last.change : Math.max(after, top),
      rows: rows.map((row): Changed => row.deleted === 1
        ? { collection: row.collection, row_id: row.row_id, change: row.change, deleted: true,
            op: row.op, writer: row.writer }
        : { ...shown(row), collection: row.collection, change: row.change, deleted: false }),
    };
  }

  /**
   * Whether a row anyone may see in one of `collections` was written or deleted past the
   * owner-wide change `after` (slice W: which rooms a wake's subscription follows changed).
   * Each part read by its change index from the cursor on, so it reads only what changed.
   */
  touched(collections: readonly string[], after: number): boolean {
    const list = JSON.stringify(collections);
    return this.#sql.exec(
      "SELECT 1 FROM collection_rows INDEXED BY collection_changes WHERE change > ? " +
        "AND private = 0 AND collection IN (SELECT value FROM json_each(?)) " +
        "UNION ALL SELECT 1 FROM collection_history INDEXED BY collection_erasures " +
        "WHERE change > ? AND private_writer IS NULL " +
        "AND collection IN (SELECT value FROM json_each(?)) LIMIT 1",
      after, list, after, list,
    ).toArray().length > 0;
  }

  /** The owner-wide change cursor: the latest change any write took. A read of one row. */
  clock(): number {
    return this.#sql.exec<{ change: number }>("SELECT change FROM collection_clock").one().change;
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
   * bodies fit `PAGE_BYTES`, at least one (the door fits it to one answer); `more` and the cursor name the older ones it
   * left.
   */
  history(caller: string, collection: string, rowId: string, before: unknown = null):
      History {
    if (before !== null && (typeof before !== "number" || !Number.isSafeInteger(before)
                            || before < 1)) {
      throw new Refusal("invalid", "before_revision is a revision");
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
    const page = paged(found.slice(0, this.#limits.maxPull), PAGE_BYTES);
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
    const rows = sql.exec<{ row_id: string; revision: number; private: number;
                            writer: string }>(
      "SELECT row_id, revision, private, writer FROM collection_rows WHERE collection = ?" +
        where, collection, ...params,
    ).toArray();
    // The high-water seq stays where the rows had it, so no cursor goes back.
    sql.exec(
      "INSERT INTO collection_seqs VALUES (?, ?) ON CONFLICT (collection) " +
        "DO UPDATE SET seq = excluded.seq", collection, this.top(collection),
    );
    const at = now();
    for (const { row_id: rowId, revision, private: hidden, writer } of rows) {
      sql.exec("DELETE FROM collection_history WHERE collection = ? AND key = ?",
               collection, rowId);
      sql.exec("INSERT INTO collection_history (collection, key, revision, op, body_json, at, " +
                 "writer, body_bytes, filer, change, private_writer) " +
                 "VALUES (?, ?, ?, ?, '', ?, ?, 0, ?, ?, ?)",
               collection, rowId, revision + 1, op, at, caller, OWNER_FILER, this.#tick(),
               hidden ? writer : null);
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
      throw new Refusal("invalid", "invalid collection delete");
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

  /** The owner-wide clock's next change (`collection_clock`), in the caller's transaction. */
  #tick(): number {
    return this.#sql.exec<{ change: number }>(
      "UPDATE collection_clock SET change = change + 1 RETURNING change").one().change;
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

/** The board (`collections.ts`), and every room's prefix: `room:<app>:<room>`. */
export const BOARD = "board";

export const ROOM_PREFIX = "room:";

export function conflicted<T>(act: () => T): T | Conflicted {
  try {
    return act();
  } catch (error) {
    if (error instanceof Conflict) {
      return { conflict: error.current };
    }
    throw error;
  }
}

/** A written row as its write answers it: everything but its body, and the body's bytes. */
export type WrittenRow = Omit<Row, "body" | "content_id"> & { readonly body_bytes: number };

function writtenRow(row: Row): WrittenRow {
  const { body, content_id: _placed, ...rest } = row;
  return { ...rest, body_bytes: utf8Bytes(body) };
}

/** The board's rows changed past `after`. */
export interface BoardDigestRequest {
  readonly limit: number;
  readonly after: number;
  readonly head_chars?: number;
}
export interface WriteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly body: string;
  readonly expected_etag: string;
  readonly private: boolean;
  readonly history_op?: string;
}
export interface PullRequest {
  readonly collection: string;
  readonly after: number;
  readonly limit: number;
}
interface KeyRequest {
  readonly collection: string;
  readonly row_id: string;
}
/**
 * A row read: its key; with `body_offset`, its body from that byte (a `body_next_offset` an
 * answer gave), and with `etag`, refused when the row changed since that answer.
 */
export interface GetRequest extends KeyRequest {
  readonly body_offset?: number;
  readonly etag?: string;
}
/**
 * A history read: a row's key, and the revision to read before for older entries; with
 * `body_offset`, the page's newest entry's body from that byte.
 */
export interface HistoryRequest extends KeyRequest {
  readonly before_revision?: number;
  readonly body_offset?: number;
}
export interface DeleteRequest {
  readonly collection: string;
  readonly row_id: string;
  readonly expected_etag: string;
}
export interface ClearRequest {
  readonly collection: string;
  readonly writer: string | null;
}
/** A lost compare-and-swap is an answer carrying the row as it stands, not a refusal. */
export interface Conflicted {
  readonly conflict: Row | null;
}

// -- THE DOORS: the `collections` rows' handlers (`contract/doors.json`) --------------------------
//
// Each door's body, which the object's runner (`owner-do.ts`, `#run` and `#units`) hands
// the object's state once the row's steps held: the bind, the account, the scope and the
// fields.

/**
 * Ring the agents that follow `collection` for a change past `after` (slice W): only for
 * the owner's own act (an outside client's write, or a run's, rings no agent, so no two
 * agents wake each other without end, as slice ID's mail rings only the agent it
 * addresses), only for the board or a room, only for a change anyone may see, and only
 * agents with a live subscription following it whose grant reads their command center.
 * Each wake says what changed and the cursor before it, so its digest is just the change.
 */
function ring(o: OwnerState, account: Account, collection: string, after: number): void {
  const change = collection === BOARD ? "board"
    : collection.startsWith(ROOM_PREFIX) ? "rooms" : null;
  if (account.origin !== null || account.run !== null || change === null
      || !o.collections.touched([collection], after)) {
    return;
  }
  const agents = o.ledger.followers(collection, change === "board", clock())
    .filter((agent) => o.grants.reads(agent.client, {
      universe: agent.command_center_id, agent: MAIN_AGENT, capability: "read_graph" }));
  if (agents.length) {
    o.ledger.changed(change, after, agents.map((agent) => agent.agent_id));
  }
}

/** The `collections` doors' handlers, by door. */
export const collectionsDoors = Object.freeze({
  async boardDigest(o: OwnerState, door: Door<BoardDigestRequest>): Promise<Heads> {
    return o.reading(() => {
      const { account: { caller }, r } = door.check();
      const chars = headChars(r["head_chars"]);
      const changes = o.collections.changed(caller, { collection: BOARD }, r["after"],
                                                r["limit"]);
      return { value: { changes, chars }, contents: contentsOf(changes.rows),
               maxOf: () => headWindow(chars) };
    }, ({ changes, chars }, windows) => heads(changes, chars, windows));
  },
  async collectionGet(o: OwnerState, door: Door<GetRequest>): Promise<{ row: Row | null }> {
    return o.reading(() => {
      const { account: { caller }, r } = door.check();
      const offset = bodyOffset(r["body_offset"]);
      // A read on names the revision it reads (review L3), so two are never spliced.
      if (offset > 0 && r["etag"] === undefined) {
        throw new Refusal("invalid", "body_offset reads on a row's body: name its etag");
      }
      const got = o.collections.get(caller, r["collection"] as string, r["row_id"] as string);
      if (r["etag"] !== undefined && got.row?.etag !== r["etag"]) {
        throw new Refusal("row_changed",
                          "row_changed: the row is no longer the one at that etag: read it " +
                          "again from its start");
      }
      return { value: { got, offset }, contents: contentsOf(got.row === null ? [] : [got.row]),
               offsetOf: () => offset };
    }, ({ got, offset }, windows) => ({ row: got.row === null ? null
      : fittedRow(windowed(got.row, windows, offset), jsonBytes({ row: null }) - 4) as Row }));
  },
  async collectionPull(o: OwnerState, door: Door<PullRequest>): Promise<Page> {
    return o.reading(() => {
      const { account: { caller }, r } = door.check();
      const page = o.collections.pull(caller, r["collection"] as string, r["after"],
                                          r["limit"]);
      return { value: page, contents: contentsOf(page.rows) };
    }, (page, windows) => {
      const rows = page.rows.length === 0 ? [] : fitPage(
        jsonBytes({ cursor: page.cursor, rows: [] }), windowedAll(page.rows, windows),
        ({ item, window }, room, first) => bodied(item, window, room, first));
      return { cursor: rows.length === page.rows.length ? page.cursor : rows.at(-1)!.seq,
               rows: rows as Row[] };
    });
  },
  async collectionHistory(o: OwnerState, door: Door<HistoryRequest>): Promise<History> {
    return o.reading(() => {
      const { account: { caller }, r } = door.check();
      const offset = bodyOffset(r["body_offset"]);
      const kept = o.collections.history(caller, r["collection"] as string,
                                             r["row_id"] as string, r["before_revision"] ?? null);
      const newest = kept.entries.at(-1)?.content_id;
      return { value: { kept, offset }, contents: contentsOf(kept.entries),
               offsetOf: (contentId) => contentId === newest ? offset : 0 };
    }, ({ kept, offset }, windows) => {
      const fitted = kept.entries.length === 0 ? [] : fitPage(
        jsonBytes({ entries: [], more: true, before_revision: kept.before_revision }),
        windowedAll([...kept.entries].reverse(), windows, offset),
        ({ item, window }, room, first) => bodied(item, window, room, first));
      const entries = (fitted as HistoryEntry[]).reverse();
      return entries.length === kept.entries.length ? { ...kept, entries }
        : { entries, more: true, before_revision: entries[0]!.revision };
    });
  },
  async collectionWrite(o: OwnerState, door: Door<WriteRequest>):
      Promise<{ row: WrittenRow } | Conflicted> {
    return o.placing(() => {
      const { account, r } = door.check();
      return { body: checkBody(r["body"]), writer: filerOf(account) };
    }, (placed) => {
      const { account, r } = door.check();
      const collection = r["collection"] as string;
      const before = o.collections.clock();
      const written = conflicted(() => ({
        row: writtenRow(o.collections.write(account.owner, account.caller, collection,
                                                r["row_id"] as string, r["body"],
                                                r["expected_etag"], r["private"],
                                                "history_op" in r ? r["history_op"] : "write",
                                                placed, filerOf(account))),
      }));
      ring(o, account, collection, before);
      return written;
    }, { shown: conflictShown(), synced: true });
  },
  async collectionDelete(o: OwnerState, door: Door<DeleteRequest>):
      Promise<{ deleted: number } | Conflicted> {
    return o.reading(() => {
      const { account, r } = door.check();
      const before = o.collections.clock();
      const value = conflicted(() => o.collections.delete(
        account.caller, r["collection"] as string, r["row_id"] as string, r["expected_etag"],
      ));
      ring(o, account, r["collection"] as string, before);
      return { value, contents: conflictShown<typeof value>().contents(value) };
    }, conflictShown<{ deleted: number } | Conflicted>().fill, true);
  },
  collectionClear(o: OwnerState, { account, r }: Call<ClearRequest>): { deleted: number } {
    const before = o.collections.clock();
    const cleared = o.collections.clear(account.caller, r["collection"] as string,
                                            r["writer"]);
    ring(o, account, r["collection"] as string, before);
    return cleared;
  },
});
