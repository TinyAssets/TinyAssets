/**
 * The owner object's one write gate: every owner write goes through the SQLite queue pi's
 * commits use (ADR-032; pi-durable README, "Storage").
 *
 * pi-durable shares the object's SQLite database, under its own `pi_` tables, and commits
 * through chord's `DurableObjectSqliteDatabase`, whose `transaction()` holds one queue per
 * storage object and runs `ctx.storage.transaction()`. On a SQLite-backed object, anything
 * written through `ctx.storage` while that transaction is open becomes part of it, and a
 * `transactionSync` opened then is a savepoint inside it. pi's transaction awaits between
 * its statements, so a microtask of work already running in the object (a door's
 * continuation, a lane's pass) can run inside it, and its write would then commit with
 * pi's, or be rolled back with it after the door had answered. pi's own words: "direct
 * storage calls bypass the queue and may be rolled back with a Pi transaction".
 *
 * So the object holds one `DurableObjectSqliteDatabase` over its storage, which shares
 * pi's queue by construction (chord keys the queue by the storage object), and runs each
 * write unit as one turn of that queue: `write(unit)` waits for whatever the queue holds
 * (`close()`, chord's own host-query path) and then runs the unit, synchronously, in a
 * `transactionSync`. A unit is synchronous, our existing `transactionSync` bodies, so it
 * holds the queue only for its own SQL, never across an outside call; a door or a lane
 * pass writes through it a unit at a time, never for a whole pass. pi's transaction opens
 * only in a turn of its own after its queue turn begins, so a synchronous unit run once
 * the queue is clear never runs inside one.
 *
 * A unit is never an async `ctx.storage.transaction()` of its own: that holds the input
 * gate (`blockConcurrencyWhile`), and Lifecycle's startup holds the input gate while it
 * awaits pi's open on this queue, so a unit queued just before the open would wait on the
 * startup's gate while the startup waits on the unit, until the runtime resets the object.
 *
 * The gate changes no unit's atomicity: a unit's own `transactionSync` keeps it, and what
 * a unit wrote before it threw stands, as it would without the gate, so a refusal receipt
 * written before the refusal is thrown is kept. Reads stay direct.
 *
 * The modules the object builds see its storage only through `storage`, a view that
 * refuses, loudly, any statement but a single `SELECT` (nothing after it) and any
 * `transactionSync` outside a unit (Fact 8: a write that could land in pi's transaction is
 * a crash, not a hope). A unit
 * inside another, and a unit that returns a promise, are refused the same way. Units in
 * pi's own callbacks (a commit's `conversationCreated`) never come here: they write through
 * pi's transaction handle, and a unit queued from inside pi's transaction would wait on it
 * forever (pi-durable README: "calling `database` itself inside the callback never
 * settles").
 */
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";

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
  readonly #db: DurableObjectSqliteDatabase;
  readonly #raw: DurableObjectStorage;
  /** True only while a unit runs: units are synchronous, so nothing else runs meanwhile. */
  #holding = false;
  /** The storage as the object's modules see it: writes only inside a unit. */
  readonly storage: OwnerStorage;

  constructor(storage: DurableObjectStorage) {
    this.#db = new DurableObjectSqliteDatabase(storage);
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
   * Run `unit` on the queue pi's commits use, once whatever it holds is done. A unit inside
   * another throws at once; one that returns a promise is rolled back.
   */
  readonly write: Write = <T>(unit: () => T): Promise<T> => {
    if (this.#holding) {
      throw new Error(NESTED_UNIT);
    }
    return this.#db.close().then(() => {
      const outcome = this.#raw.transactionSync((): Outcome<T> => {
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
      if ("error" in outcome) {
        throw outcome.error;
      }
      return outcome.value;
    });
  };
}
