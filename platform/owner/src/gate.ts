/**
 * The owner object's one write gate: every owner write is one synchronous unit (ADR-032;
 * tasks 5, S3).
 *
 * No agent loop shares the object's database (agents run on unmodified engines, ADR-034),
 * so no outside transaction ever opens in the owner's SQLite, and the gate keeps no queue.
 * It holds the one rule every owner write keeps: a unit is a synchronous body run in one
 * `transactionSync`, so it reads what it decides on itself and writes before anything else
 * runs in the object, and it never spans an outside call (`FILES`, the gateway): a door or a lane pass writes through it a unit at a
 * time, awaiting outside calls only between units. A unit is never an async
 * `ctx.storage.transaction()`, which would hold the input gate across its awaits.
 *
 * The gate changes no unit's atomicity: a unit's own `transactionSync` keeps it, and what
 * a unit wrote before it threw stands, as it would without the gate, so a refusal receipt
 * written before the refusal is thrown is kept. Reads stay direct.
 *
 * The modules the object builds see its storage only through `storage`, a view that
 * refuses, loudly, any statement but a single `SELECT` (nothing after it) and any
 * `transactionSync` outside a unit (Fact 8: a write outside a unit is a crash, not a hope).
 * A unit inside another, and a unit that returns a promise, are refused the same way.
 * A unit that wrote anything is followed, in its own transaction, by `changed`: the owner is
 * marked changed for the nightly manifest (`backup.ts` `mark`). A `quiet` unit is one that
 * changes nothing a manifest holds: the export's and the manifest's own bookkeeping, and a
 * session's renewal; it marks nothing.
 */

/** What the object's modules use of its storage: SQL, and `transactionSync`. */
export interface OwnerStorage {
  readonly sql: Pick<SqlStorage, "exec">;
  transactionSync<T>(closure: () => T): T;
}

/** Run one synchronous write unit through the gate; resolves with what it returned. */
export type Write = <T>(unit: () => T) => Promise<T>;

/** A statement the view lets through outside a unit: one read, and no statement after it. */
const READ = /^\s*SELECT\b[^;]*;?\s*$/i;

export const OUTSIDE_GATE = "an owner write outside the write gate; run it as a gate unit";
export const NESTED_UNIT = "a write unit inside another; the outer unit writes it";
export const ASYNC_UNIT = "a write unit is synchronous; await outside calls before or after it";

function thenable(value: unknown): boolean {
  return (typeof value === "object" || typeof value === "function") && value !== null
    && typeof (value as { then?: unknown }).then === "function";
}

type Outcome<T> = { readonly value: T } | { readonly error: unknown };

export class WriteGate {
  readonly #raw: DurableObjectStorage;
  /** True only while a unit runs: units are synchronous, so nothing else runs meanwhile. */
  #holding = false;
  /** Whether the unit running has written: only a unit that did is marked `changed`. */
  #wrote = false;
  /** Whether the unit running is `quiet`: it writes nothing a manifest holds. */
  #quiet = false;
  /** Run, in the unit's own transaction, after a unit that wrote. */
  #changed: (sql: Pick<SqlStorage, "exec">) => void = () => {};
  readonly #sql: SqlStorage;
  /** The storage as the object's modules see it: writes only inside a unit. */
  readonly storage: OwnerStorage;

  constructor(storage: DurableObjectStorage) {
    this.#raw = storage;
    const sql = storage.sql;
    this.#sql = sql;
    const exec = ((query: string, ...bindings: SqlStorageValue[]) => {
      if (!READ.test(query)) {
        this.#held();
        this.#wrote = true;
      }
      return sql.exec(query, ...bindings);
    }) as SqlStorage["exec"];
    this.storage = Object.freeze({
      sql: Object.freeze({ exec }),
      transactionSync: <T>(closure: () => T): T => {
        this.#held();
        return storage.transactionSync(closure);
      },
    });
  }

  /** What a unit that wrote is followed by (set once, by the object). */
  onChange(changed: (sql: Pick<SqlStorage, "exec">) => void): void {
    this.#changed = changed;
  }

  #held(): void {
    if (!this.#holding) {
      throw new Error(OUTSIDE_GATE);
    }
  }

  /**
   * The owner's erasure's last step (`erase.ts`): the whole database deleted at once, every
   * table, key and job of it, then `tombstone` run on it in a transaction of its own (the
   * erased admission row, `admission.ts`), which writes only into a wiped database and only
   * once. A SQLite object's `deleteAll` wipes at once, so the tombstone lands in the same
   * synchronous turn, nothing else running between the two; it is run again once the wipe
   * resolves, so a wipe that took longer is tombstoned all the same, and one that failed is
   * not. A crash after the wipe is durable can still lose it (the window `erase.ts` names,
   * not measured in production).
   * Never inside a unit; nothing else writes after it.
   */
  wipe(tombstone: (sql: Pick<SqlStorage, "exec">) => void): Promise<void> {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    const wiped = this.#raw.deleteAll();
    const leave = () => this.#raw.transactionSync(() => tombstone(this.#raw.sql));
    leave();
    return wiped.then(leave);
  }

  /**
   * Run `unit` now, in a `transactionSync` of its own. A unit inside another throws at once;
   * one that returns a promise is rolled back.
   */
  readonly write: Write = <T>(unit: () => T): Promise<T> => {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    const sql = this.#sql;
    let outcome: Outcome<T>;
    try {
      outcome = this.#raw.transactionSync((): Outcome<T> => {
        this.#holding = true;
        this.#wrote = false;
        let value: T;
        try {
          value = unit();
        } catch (error) {
          return { error };  // committed as far as the unit wrote, then thrown below
        } finally {
          this.#holding = false;
          if (this.#wrote && !this.#quiet) {
            this.#changed(sql);
          }
        }
        if (thenable(value)) {
          throw new Error(ASYNC_UNIT);  // rolls the unit back
        }
        return { value };
      });
    } catch (error) {
      return Promise.reject(error);
    }
    return "error" in outcome ? Promise.reject(outcome.error) : Promise.resolve(outcome.value);
  };

  /**
   * A unit, as `write` runs it, that marks nothing changed: it writes only the export's or
   * the manifest's own bookkeeping, or a session's renewal, none of which a manifest holds.
   */
  readonly quiet: Write = <T>(unit: () => T): Promise<T> => {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    this.#quiet = true;
    try {
      return this.write(unit);
    } finally {
      this.#quiet = false;
    }
  };
}
