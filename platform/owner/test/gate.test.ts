/**
 * The write gate (`gate.ts`): units run one at a time, each at once and synchronously, keep
 * their own atomicity, and the storage view refuses a write outside one.
 */
import { runInDurableObject } from "cloudflare:test";
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
    expect(await gate.write(() => null)).toBeNull();  // no value is a promise
    expect(await gate.write(() => undefined)).toBeUndefined();
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
    expect(gate.storage.sql.exec("SELECT 2 AS two;  ").one()).toEqual({ two: 2 });
    for (const statement of ["INSERT INTO gate_probe VALUES (4)", "UPDATE gate_probe SET n = 5",
                             "DELETE FROM gate_probe", "CREATE TABLE gate_other (n INTEGER)",
                             "WITH x AS (SELECT 1) INSERT INTO gate_probe SELECT * FROM x",
                             "SELECT 1; INSERT INTO gate_probe VALUES (6)",
                             "SELECT 1;DELETE FROM gate_probe"]) {
      expect(() => gate.storage.sql.exec(statement)).toThrow(OUTSIDE_GATE);
    }
    expect(() => gate.storage.transactionSync(() => 1)).toThrow(OUTSIDE_GATE);
    expect(rows(state)).toEqual([]);
  }));

  it("runs each unit at once, in the order asked: nothing to wait behind, the input gate's " +
     "hold included", () => withGate(async (gate, state) => {
    // pi's commits are its thread stores' own: no transaction of this database to queue on.
    const units = [1, 2, 3].map((n) => gate.write(() => {
      gate.storage.sql.exec("INSERT INTO gate_probe VALUES (?)", n);
      return n;
    }));
    // Each committed before any was awaited: a unit spans no await.
    expect(rows(state)).toEqual([1, 2, 3]);
    expect(await Promise.all(units)).toEqual([1, 2, 3]);
    await state.blockConcurrencyWhile(async () => {
      await gate.write(() => gate.storage.sql.exec("INSERT INTO gate_probe VALUES (4)"));
    });
    expect(rows(state)).toEqual([1, 2, 3, 4]);
  }));
});
