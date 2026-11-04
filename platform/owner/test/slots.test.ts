/**
 * The isolate's pool of units (`slots.ts`): taken while held, waited for first come first
 * served within a hold's size class up to a deadline, refused `busy` past it or past the
 * waiters it counts, a one-unit hold never behind a larger one nor a larger one starved by
 * them, each class capped apart, a holder growing wait-die (the archive's move), and released
 * whatever happens, once.
 */
import { describe, expect, it } from "vitest";

import {
  OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, OWNER_SMALL_WAITERS, OWNER_WAITERS,
} from "../src/holds";
import {
  BUSY, isolatePool, type Slot, SlotBusy, slotted, SlotPool,
} from "../src/slots";

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
/** The owner's pool, with a shorter wait. */
const owners = (wait: number) => new SlotPool(OWNER_POOL_UNITS, wait, OWNER_WAITERS,
                                              OWNER_RESERVE_UNITS, OWNER_SMALL_WAITERS);

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("a pool of units", () => {
  it("grants what is free, queues the rest first come first served in its class, and " +
     "drains on release", async () => {
    const pool = new SlotPool(3, 1000, 4);
    const a = await pool.acquire(2);
    const b = await pool.acquire(1);
    const order: string[] = [];
    const c = pool.acquire(2).then((slot) => { order.push("c"); return slot; });
    const d = pool.acquire(3).then((slot) => { order.push("d"); return slot; });
    await tick();
    expect(pool.state()).toMatchObject({ held: 3, waiting: 2 });
    b.release();
    await tick();
    // The head (two units) waits for two; the later three-unit call waits behind it.
    expect(order).toEqual([]);
    a.release();
    const held = await c;
    expect(order).toEqual(["c"]);
    held.release();
    (await d).release();
    expect(order).toEqual(["c", "d"]);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0, peak: 3 });
  });

  it("never holds a one-unit call behind larger ones: they leave the reserve free",
     async () => {
    const pool = new SlotPool(7, 1000, 8, 1);
    // Larger holds fill all but the reserve, and another waits for room.
    const large = [await pool.acquire(3), await pool.acquire(3)];
    const waitingLarge = pool.acquire(3);
    await tick();
    expect(pool.state()).toMatchObject({ held: 6, waiting: 1 });
    // A one-unit hold takes the reserve at once, ahead of the larger waiter.
    const small = await pool.acquire(1);
    expect(pool.state()).toMatchObject({ held: 7, waiting: 1 });
    // Another waits only for the one-unit hold before it, never for the larger ones.
    const second = pool.acquire(1);
    await tick();
    small.release();
    (await second).release();
    for (const each of large) {
      each.release();
    }
    (await waitingLarge).release();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    expect(() => pool.acquire(7)).toThrow(/takes 1 to 6 units, not 7/);
    expect(() => new SlotPool(2, 1000, 2, 2)).toThrow(/at least one unit/);
  });

  // Review rvmem4 (M-b), flipped: a stream of one-unit holds once starved a larger one.
  it("lets a five-unit append land amid eight cycling one-unit holds", async () => {
    const pool = owners(1_500);
    let on = true;
    const cyclers = Array.from({ length: 8 }, async () => {
      while (on) {
        const slot = await pool.acquire(1);
        await sleep(20);
        slot.release();
      }
    });
    await sleep(50);
    const outcome = await pool.acquire(5).then((slot) => {
      slot.release();
      return "granted";
    }, (error: Error) => error.message);
    on = false;
    await Promise.all(cyclers);
    expect(outcome).toBe("granted");
  });

  // Review rvmem4 (L-a), flipped: larger waiters once filled a cap one-unit holds shared.
  it("never refuses a small read for larger holds waiting: each class is capped apart",
     async () => {
    const pool = owners(1_500);
    const held = [await pool.acquire(3), await pool.acquire(3), await pool.acquire(3),
                  await pool.acquire(2), await pool.acquire(1)];
    const waiting = Array.from({ length: OWNER_WAITERS }, () =>
      pool.acquire(3).then((slot) => slot.release(), () => undefined));
    // One more larger hold is refused at once; a small read waits its turn instead.
    expect(await pool.acquire(3).catch((error: Error) => error.message)).toMatch(/^busy:/);
    const small = pool.acquire(1);
    held.at(-1)!.release();
    (await small).release();
    for (const slot of held.slice(0, -1)) {
      slot.release();
    }
    await Promise.all(waiting);
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
    expect(() => pool.acquire(2)).toThrow(/takes 1 to 1 units/);
    expect(() => new SlotPool(0)).toThrow(/at least one unit/);
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

  it("lets a holder grow before every queue, the others taking nothing past the reserve " +
     "while it waits", async () => {
    const pool = new SlotPool(7, 1000, 8, 1);
    const table = await pool.acquire(1, true);  // a move's table: the larger class
    const edit = await pool.acquire(3);
    const order: string[] = [];
    // A larger call already waiting for room.
    const queued = pool.acquire(3).then((slot) => { order.push("queued"); return slot; });
    await tick();
    expect(pool.state()).toMatchObject({ held: 4, waiting: 1 });
    // The holder grows by three: it fits now beside the reserve, ahead of the queue.
    const pass = await pool.grow(table, 2);
    expect(pass.age).toBe(table.age);
    pass.release();
    // Growing past what is free, it waits; a one-unit read takes the reserve only.
    const growing = pool.grow(table, 4).then((slot) => { order.push("grown"); return slot; });
    await tick();
    const read = await pool.acquire(1);
    const second = pool.acquire(1).then((slot) => { order.push("read"); return slot; });
    await tick();
    expect(order).toEqual([]);
    edit.release();
    // The edit's three free: the grower takes them first, never the queued larger call.
    const grown = await growing;
    expect(order).toEqual(["grown"]);
    grown.release();
    read.release();
    (await second).release();
    (await queued).release();
    table.release();
    expect(order).toEqual(["grown", "queued", "read"]);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("refuses a younger holder's growth while an older waits, and an older's refuses the " +
     "younger waiting (wait-die)", async () => {
    const pool = new SlotPool(7, 1000, 8, 1);
    const older = await pool.acquire(2);
    const younger = await pool.acquire(2);
    // The older waits to grow by three (two are free beside the reserve).
    const waiting = pool.grow(older, 3);
    await tick();
    // The younger asks too: refused at once, so it lets go of what it holds.
    expect(await pool.grow(younger, 1).catch((error: Error) => error.message)).toBe(BUSY);
    younger.release();
    (await waiting).release();
    // A younger holder waits first, and an older one's asking refuses it.
    const young = await pool.acquire(3);
    const first = pool.grow(young, 2).catch((error: Error) => error.message);
    await tick();
    const old = pool.grow(older, 1);
    expect(await first).toBe(BUSY);
    young.release();
    (await old).release();
    older.release();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("refuses a growth past its deadline, and never one past the larger class", async () => {
    const pool = new SlotPool(4, 20, 8, 1);
    const held = await pool.acquire(2);
    const other = await pool.acquire(1, true);
    expect(await pool.grow(held, 1).catch((error: Error) => error.message)).toBe(BUSY);
    expect(() => pool.grow(held, 2)).toThrow(/grows to at most 3 units/);
    other.release();
    (await pool.grow(held, 1)).release();
    held.release();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("is one pool an isolate, its capacity and reserve its Worker's one figure", () => {
    const pool = isolatePool(7, 1, 7);
    expect(isolatePool(7, 1, 7)).toBe(pool);
    expect(() => isolatePool(8, 1)).toThrow(/holds 7 units \(1 reserved\), not 8 \(1\)/);
  });
});
