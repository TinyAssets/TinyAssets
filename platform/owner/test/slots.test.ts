/**
 * The isolate's pool of units (`slots.ts`): taken while held, waited for first come first
 * served within a hold's size class up to a deadline, refused `busy` past it or past the
 * waiters it counts, a one-unit hold never behind a larger one nor a larger one starved by
 * them, each class capped apart, and released whatever happens, once.
 */
import { describe, expect, it } from "vitest";

import {
  OWNER_POOL_UNITS, OWNER_RESERVE_UNITS, OWNER_SMALL_WAITERS, OWNER_WAITERS,
} from "../src/holds";
import {
  BUSY, isolatePool, type Slot, SlotBusy, SlotPool, slotted,
} from "../../contract/src/slots";

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

  it("serves the owners waiting in turn, each owner's own calls first come first served",
     async () => {
    for (const units of [1, 2]) {
      const pool = new SlotPool(2, 5000);
      const holding = await pool.acquire(2, "a");
      const order: string[] = [];
      const wait = (owner: string, name: string) => pool.acquire(units, owner).then((slot) => {
        order.push(name);
        slot.release();
      });
      // Owner a queues three calls before owner b queues one: b is served second, not fourth.
      const calls = [wait("a", "a1"), wait("a", "a2"), wait("a", "a3"), wait("b", "b1")];
      await tick();
      holding.release();
      await Promise.all(calls);
      expect(order).toEqual(["a1", "b1", "a2", "a3"]);
    }
  });

  it("shares its waiters without a cap an owner: a newcomer takes the place of the owner " +
     "waiting most", async () => {
    const pool = new SlotPool(1, 5000, 3);
    const holding = await pool.acquire(1, "a");
    const flooded = [pool.acquire(1, "a"), pool.acquire(1, "a"), pool.acquire(1, "a")]
      .map((call) => call.then((slot) => { slot.release(); return "landed"; },
                               (error: Error) => error.message));
    await tick();
    // The waiters are full of a's: b's call takes the place of a's newest, which is busy.
    const other = pool.acquire(1, "b").then((slot) => { slot.release(); return "landed"; });
    await tick();
    expect(pool.state().waiting).toBe(3);
    // Another of a's, past the waiters, is busy itself: a already waits most.
    expect((await pool.acquire(1, "a").catch((error: Error) => error)) instanceof SlotBusy)
      .toBe(true);
    holding.release();
    expect(await Promise.all(flooded)).toEqual(["landed", "landed", BUSY]);
    expect(await other).toBe("landed");
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("lets only the oldest holder wait to grow (wait-die): a younger one gives everything back",
     async () => {
    const pool = new SlotPool(6, 5000);
    const older = pool.lease("a");
    const younger = pool.lease("b");
    await older.grow(2);
    await younger.grow(2);
    const fillers = [await pool.acquire(1, "c"), await pool.acquire(1, "c")];
    // The younger cannot grow at once: it dies, its units back, told busy.
    const died = await younger.grow(3).catch((error: Error) => error);
    expect(died).toBeInstanceOf(SlotBusy);
    expect([younger.units, pool.state().held]).toEqual([0, 4]);
    // The oldest waits, and while it waits a newcomer takes nothing it waits for.
    const refill = await pool.acquire(2, "c");
    const grown = older.grow(3);
    await tick();
    let came = false;
    const newcomer = pool.acquire(1, "d").then((slot) => { came = true; return slot; });
    await tick();
    expect(pool.state()).toMatchObject({ held: 6, waiting: 2 });
    // One unit back: the oldest holder's, not the newcomer's, though the newcomer fits.
    fillers[0]!.release();
    await grown;
    await tick();
    expect([older.units, came, pool.state().waiting]).toEqual([3, false, 1]);
    fillers[1]!.release();
    const late = await newcomer;
    older.release();
    late.release();
    refill.release();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("holds an owner at half the pool or more to what it holds while another owner waits",
     async () => {
    const pool = new SlotPool(4, 5000);
    const a1 = await pool.acquire(1, "a");
    const a2 = await pool.acquire(1, "a");
    const b1 = await pool.acquire(1, "b");
    // b waits for two; a, at half the pool, takes no more until b is served.
    const order: string[] = [];
    const b2 = pool.acquire(2, "b").then((slot) => { order.push("b"); return slot; });
    const a3 = pool.acquire(1, "a").then((slot) => { order.push("a"); return slot; });
    await tick();
    expect(order).toEqual([]);
    a1.release();
    b1.release();
    await b2;
    await a3;
    expect(order).toEqual(["b", "a"]);
    (await b2).release();
    a2.release();
    (await a3).release();
    // Alone, an owner may take the whole pool.
    const all = await Promise.all([1, 2, 3, 4].map(() => pool.acquire(1, "a")));
    expect(pool.state().held).toBe(4);
    all.forEach((slot) => slot.release());
  });

  it("takes a stopped wait out of the queue at once", async () => {
    const pool = new SlotPool(1, 5000);
    const held = await pool.acquire(1, "x");
    const stop = new AbortController();
    const waiting = pool.acquire(1, "a", stop.signal).catch((error: unknown) => error);
    await tick();
    expect(pool.state().waiting).toBe(1);
    stop.abort(new Error("stopped"));
    expect(((await waiting) as Error).message).toBe("stopped");
    expect(pool.state().waiting).toBe(0);
    held.release();
    expect(pool.state().held).toBe(0);
  });

  it("lets a larger lease hold one unit outside the reserve, grow a step and give it back, " +
     "still the oldest holder (the archive's move)", async () => {
    const pool = new SlotPool(7, 1000, 8, 1);
    const move = pool.lease("", true);
    await move.grow(1);  // its records': the larger class, never the reserve
    const edit = await pool.acquire(5);
    expect(pool.state()).toMatchObject({ held: 6 });
    // A one-unit read still takes the reserve: the move's unit is not in it.
    const read = await pool.acquire(1);
    read.release();
    // Its pass: grown past what is free, it waits as the oldest holder; a newcomer takes
    // nothing past the reserve meanwhile.
    const pass = move.grow(4);
    await tick();
    const order: string[] = [];
    const newcomer = pool.acquire(3).then((slot) => { order.push("newcomer"); return slot; });
    await tick();
    edit.release();
    await pass;
    expect([move.units, order]).toEqual([4, []]);
    // The pass given back: the records' unit kept, and the newcomer lands.
    move.shrink(1);
    expect(move.units).toBe(1);
    (await newcomer).release();
    expect(order).toEqual(["newcomer"]);
    // Still the oldest holder: growing past what is free it waits, and a younger lease's
    // growth meanwhile gives way, everything it held given back.
    const younger = pool.lease("", true);
    await younger.grow(2);
    const blocker = await pool.acquire(2);
    const growing = move.grow(4);
    await tick();
    expect(await younger.grow(4).catch((error: Error) => error.message)).toBe(BUSY);
    expect(younger.units).toBe(0);
    await growing;
    expect(move.units).toBe(4);
    blocker.release();
    move.release();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("is one pool an isolate, its capacity and reserve its Worker's one figure", () => {
    const isolate = { pool: null, tails: null };
    const pool = isolatePool(isolate, 7, 1, 7);
    expect(isolatePool(isolate, 7, 1, 7)).toBe(pool);
    expect(() => isolatePool(isolate, 8, 1)).toThrow(/holds 7 units \(1 reserved\), not 8 \(1\)/);
  });
});
