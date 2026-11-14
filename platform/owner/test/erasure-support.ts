/**
 * The owner's erasure as the tests drive it, through its doors and its own job alone (B23
 * question 2): begun at `ownerErase`; its outside record confirmed by the test as the doors
 * confirm it (`erasureOutsideDone`) once the erasure queue took the object's message (the
 * fake consumer, `fake-erasures.ts`); and the put grace (`PUT_GRACE_MS`) and the retry
 * interval (`ERASE_RETRY_MS`) passed on the object's clock by a fake `Date` (`later`), its job
 * then run by its alarm. A test that uses these makes the clock real again after it.
 */
import { runDurableObjectAlarm } from "cloudflare:test";
import { env } from "cloudflare:workers";

import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { ERASE_CONFIRM, PUT_GRACE_MS } from "../src/erasure";
import type { OwnerDO } from "../src/owner-do";
import { later } from "./runs-support";
import { sqlIn } from "./support";

type Stub = DurableObjectStub<OwnerDO>;

/** How many messages `owner`'s object sent the erasure queue, as its consumer took them. */
export async function erasuresSent(owner: string): Promise<number> {
  return (await env.ERASURES_SENT.of(owner)).length;
}

/** Wait until `check` holds. */
export async function until(check: () => Promise<boolean> | boolean, what: string,
                            tries = 500): Promise<void> {
  for (let i = 0; i < tries; i++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** The owner's own tables: none of the runtime's (`_cf_`). */
export function tables(stub: Stub) {
  return sqlIn(stub, "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT " +
                     "LIKE '\\_cf\\_%' ESCAPE '\\' ORDER BY name");
}

/** Whether `stub`'s database is wiped to its tombstone: its erased admission row alone. */
export async function wipedWhole(stub: Stub): Promise<boolean> {
  return JSON.stringify(await tables(stub)) === '[{"name":"owner_admission"}]';
}

/** The owner's erasure begun at its door, in its session; resolves once the mark stands. */
export function eraseBegun(stub: Stub, session: Binding): Promise<{ erasing: true }> {
  return stub.ownerErase(session, { confirm: ERASE_CONFIRM });
}

/**
 * The outside record confirmed, as the doors' consumer confirms it once it has made it, after
 * the object's message reached the queue.
 */
export async function outsideConfirmed(stub: Stub, owner: string): Promise<void> {
  await until(async () => await erasuresSent(owner) > 0, "the erasure queue's message");
  await stub.erasureOutsideDone({ owner, caller: PLATFORM_CALLER, origin: null });
}

/** The object's clock moved past the put grace of a mark made now (and a minute). */
export function pastGrace(): void {
  later(PUT_GRACE_MS / 1000 + 60);
}

/**
 * An erasure under way finished: the outside record confirmed, the grace passed on the
 * object's clock, and the job's alarm run until the owner is wiped to its tombstone.
 */
export async function finished(stub: Stub, owner: string): Promise<void> {
  await outsideConfirmed(stub, owner);
  pastGrace();
  await until(async () => {
    if (await wipedWhole(stub)) {
      return true;
    }
    await runDurableObjectAlarm(stub).catch(() => false);
    return false;
  }, "the erasure's wipe");
}

/** The owner erased whole through its doors (`eraseBegun`, then `finished`). */
export async function erasedWhole(stub: Stub, owner: string, session: Binding):
    Promise<void> {
  await eraseBegun(stub, session);
  await finished(stub, owner);
}
