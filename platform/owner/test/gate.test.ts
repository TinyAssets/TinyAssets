/**
 * The write gate (`gate.ts`): units run one at a time on the queue pi's commits use, keep
 * their own atomicity, and the storage view refuses a write outside one.
 */
import { runInDurableObject } from "cloudflare:test";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { describe, expect, it } from "vitest";

import { ASYNC_UNIT, NESTED_UNIT, OUTSIDE_GATE, WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { failure, ownerObject } from "./support";

/** A gate on a fresh object's storage, with a table of its own to write. */
function withGate<R>(act: (gate: WriteGate, state: DurableObjectState) => Promise<R>): Promise<R> {
  return runInDurableObject(ownerObject().stub, async (_instance: OwnerDO, state) => {
    state.storage.sql.exec("CREATE TABLE gate_probe (n INTEGER)");
    return act(new WriteGate(state.storage), state);
  });
}

const rows = (state: DurableObjectState) =>
  state.storage.sql.exec<{ n: number }>("SELECT n FROM gate_probe ORDER BY rowid").toArray()
    .map((row) => row.n);

describe("the write gate", () => {
  it("runs a unit and answers what it returned", () => withGate(async (gate, state) => {
    expect(await gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (1)");
      return "done";
    })).toBe("done");
    expect(rows(state)).toEqual([1]);
  }));

  it("keeps a unit's atomicity its own: what it wrote before it threw stands, its " +
     "transactionSync rolls back", () => withGate(async (gate, state) => {
    const refused = await failure(() => gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (1)");
      gate.storage.transactionSync(() => {
        gate.storage.sql.exec("INSERT INTO gate_probe VALUES (2)");
        throw new RangeError("refused");
      });
    }));
    expect([refused.name, refused.message]).toEqual(["RangeError", "refused"]);
    expect(rows(state)).toEqual([1]);
  }));

  it("refuses a unit inside another and a unit that returns a promise, rolled back",
     () => withGate(async (gate, state) => {
    const nested = await failure(() => gate.write(() => gate.write(() => 1)));
    expect(nested.message).toBe(NESTED_UNIT);
    const later = await failure(() => gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (3)");
      return Promise.resolve(1);
    }));
    expect(later.message).toBe(ASYNC_UNIT);
    expect(rows(state)).toEqual([]);
  }));

  it("lets reads through its view anywhere, and no write outside a unit",
     () => withGate(async (gate, state) => {
    expect(gate.storage.sql.exec("SELECT COUNT(*) AS n FROM gate_probe").one()).toEqual({ n: 0 });
    expect(gate.storage.sql.exec("  select 1 AS one").one()).toEqual({ one: 1 });
    for (const statement of ["INSERT INTO gate_probe VALUES (4)", "UPDATE gate_probe SET n = 5",
                             "DELETE FROM gate_probe", "CREATE TABLE gate_other (n INTEGER)",
                             "WITH x AS (SELECT 1) INSERT INTO gate_probe SELECT * FROM x"]) {
      expect(() => gate.storage.sql.exec(statement)).toThrow(OUTSIDE_GATE);
    }
    expect(() => gate.storage.transactionSync(() => 1)).toThrow(OUTSIDE_GATE);
    expect(rows(state)).toEqual([]);
  }));

  it("never waits on the input gate: a startup holding it waits on the queue behind a unit",
     () => withGate(async (gate, state) => {
    // Lifecycle's startup holds the input gate (blockConcurrencyWhile) and awaits pi's
    // open, a transaction of this queue; a unit queued just before it must not need the
    // input gate to run, or each waits on the other until the runtime resets the object.
    const unit = gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (1)");
    });
    await state.blockConcurrencyWhile(() =>
      new DurableObjectSqliteDatabase(state.storage).transaction(async (tx) => {
        await tx.run("INSERT INTO gate_probe VALUES (2)");
      }));
    await unit;
    expect(rows(state)).toEqual([1, 2]);
  }));

  it("runs units one at a time, in order, behind a transaction of the same queue",
     () => withGate(async (gate, state) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const other = new DurableObjectSqliteDatabase(state.storage).transaction(async (tx) => {
      await tx.run("INSERT INTO gate_probe VALUES (1)");
      await held;
    });
    const units = [2, 3].map((n) => gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (?)", n);
    }));
    for (let i = 0; i < 20; i++) {
      await Promise.resolve();
    }
    expect(rows(state)).toEqual([]);  // the transaction has not begun or holds the queue
    release();
    await Promise.all([other, ...units]);
    expect(rows(state)).toEqual([1, 2, 3]);
  }));
});
