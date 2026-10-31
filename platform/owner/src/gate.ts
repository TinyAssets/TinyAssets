/**
 * The owner object's one write gate: every owner write is one synchronous unit (ADR-032;
 * tasks 5, S3).
 *
 * pi no longer shares the object's database: each agent thread's Session lives in a store
 * of its own (`thread-store.ts`), so no pi transaction ever opens in the owner's SQLite,
 * and the gate keeps no queue. It holds the one rule every owner write keeps: a unit is a
 * synchronous body run in one `transactionSync`, so it reads what it decides on itself and
 * writes before anything else runs in the object, and it never spans an outside call (a
 * store's RPC, `FILES`, the gateway): a door or a lane pass writes through it a unit at a
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
  /** The storage as the object's modules see it: writes only inside a unit. */
  readonly storage: OwnerStorage;

  constructor(storage: DurableObjectStorage) {
    this.#raw = storage;
    const sql = storage.sql;
    const exec = ((query: string, ...bindings: SqlStorageValue[]) => {
      if (!READ.test(query)) {
        this.#held();
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

  #held(): void {
    if (!this.#holding) {
      throw new Error(OUTSIDE_GATE);
    }
  }

  /**
   * The owner's erasure's last step (`erase.ts`): the whole database deleted at once, every
   * table, key and job of it. Never inside a unit; nothing writes after it.
   */
  wipe(): Promise<void> {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    return this.#raw.deleteAll();
  }

  /**
   * Run `unit` now, in a `transactionSync` of its own. A unit inside another throws at once;
   * one that returns a promise is rolled back.
   */
  readonly write: Write = <T>(unit: () => T): Promise<T> => {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    let outcome: Outcome<T>;
    try {
      outcome = this.#raw.transactionSync((): Outcome<T> => {
        this.#holding = true;
        let value: T;
        try {
          value = unit();
        } catch (error) {
          return { error };  // committed as far as the unit wrote, then thrown below
        } finally {
          this.#holding = false;
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
}
