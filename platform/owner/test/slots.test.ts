/**
 * The isolate's chunk slots (`slots.ts`): taken while held, waited for first come first
 * served up to a deadline, refused `busy` past it or past the waiters it counts, and
 * released whatever happens, once.
 */
import { describe, expect, it } from "vitest";

import {
  BUSY, isolatePool, type Slot, SlotBusy, slotted, SlotPool,
} from "../src/slots";

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("a slot pool", () => {
  it("grants what is free, queues the rest first come first served, and drains on release",
     async () => {
    const pool = new SlotPool(2, 1000);
    const a = await pool.acquire(1);
    const b = await pool.acquire(1);
    const order: string[] = [];
    const c = pool.acquire(2).then((slot) => { order.push("c"); return slot; });
    const d = pool.acquire(1).then((slot) => { order.push("d"); return slot; });
    await tick();
    expect(pool.state()).toMatchObject({ held: 2, waiting: 2 });
    a.release();
    await tick();
    // The head (two slots) waits for both; the later one-slot call waits behind it.
    expect(order).toEqual([]);
    b.release();
    const held = await c;
    expect(order).toEqual(["c"]);
    held.release();
    (await d).release();
    expect(order).toEqual(["c", "d"]);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0, peak: 2 });
  });

  it("refuses busy past its deadline and past its waiters, and a release twice is once",
     async () => {
    const pool = new SlotPool(1, 50, 1);
    const held = await pool.acquire(1);
    const waiting = pool.acquire(1);
    const over = await pool.acquire(1).catch((error: Error) => error);
    expect(over).toBeInstanceOf(SlotBusy);
    expect([(over as Error).name, (over as Error).message]).toEqual(["Refused", BUSY]);
    expect((await waiting.catch((error: Error) => error) as Error).message).toBe(BUSY);
    expect(pool.state()).toMatchObject({ held: 1, waiting: 0 });
    held.release();
    held.release();
    expect(pool.state().held).toBe(0);
    expect(() => pool.acquire(2)).toThrow(/takes 1 to 1 slots/);
    expect(() => new SlotPool(0)).toThrow(/at least one slot/);
  });

  it("lets cap + 1 holders all land: the extra one waits, then holds", async () => {
    const pool = new SlotPool(3, 5000);
    let most = 0;
    const done = await Promise.all(Array.from({ length: 4 }, (_, n) =>
      slotted(pool, 1, async () => {
        most = Math.max(most, pool.state().held);
        await new Promise((resolve) => setTimeout(resolve, 20));
        return n;
      })));
    expect(done).toEqual([0, 1, 2, 3]);
    expect([most, pool.state().held, pool.state().peak]).toEqual([3, 0, 3]);
  });

  it("releases whatever the work does", async () => {
    const pool = new SlotPool(1);
    const failed = await slotted(pool, 1, () => Promise.reject(new Error("boom")))
      .catch((error: Error) => error.message);
    expect([failed, pool.state().held]).toEqual(["boom", 0]);
    const slot: Slot = await pool.acquire(1);
    slot.release();
  });

  it("is one pool an isolate, its capacity its Worker's one figure", () => {
    const pool = isolatePool(7);
    expect(isolatePool(7)).toBe(pool);
    expect(() => isolatePool(8)).toThrow(/holds 7 slots, not 8/);
  });
});
