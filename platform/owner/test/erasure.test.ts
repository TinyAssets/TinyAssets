/**
 * The owner's admission and erasure (tasks 5, F1 and S4-S5; ADR-034), carried over from the
 * deleted thread tests (`drops.test.ts`, `scheduled.test.ts`) to what remains: a suspension
 * refuses every door until each reason is lifted, and only the platform's reserved caller
 * sets it; the erasure marks the owner (every door and lane refusing), ends every run,
 * then wipes the owner whole, leaving the erased admission row alone, resumed after a
 * crash at each step; and a stale `getByName` of an erased owner is refused forever.
 */
import {
  evictDurableObject, runDurableObjectAlarm, runInDurableObject,
} from "cloudflare:test";
import { env } from "cloudflare:workers";
import { Lifecycle } from "agents/lifecycle";
import { describe, expect, it } from "vitest";

import {
  OWNER_ERASED, OWNER_SUSPENDED, PLATFORM_CALLER, PLATFORM_ONLY, PLATFORM_RESERVED,
  tombstoned, writeTombstone,
} from "../src/admission";
import { ERASE_RETRY_MS, ERASING } from "../src/erasure";
import { WriteGate } from "../src/gate";
import { Grants } from "../src/grants";
import { clock, Ledger } from "../src/ledger";
import { type Binding, OwnerDO } from "../src/owner-do";
import { Policy } from "../src/policy";
import { MCP, MUSE, rig as ledgerRig, signedIn } from "./ledger-support";
import { alarmOf, failure, sqlIn } from "./support";
import type { FauxOwnerDO } from "./worker";

/** An owner of its own on the class whose erasure is in reach, signed in, with a row. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = (): DurableObjectStub<FauxOwnerDO> => env.AGENT.getByName(owner);
  const at = signedIn();
  const bind = (origin: typeof MUSE | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session: cookie } = await stub().sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => bind(null, { session: cookie });
  await stub().collectionWrite(bind(null), { collection: "board", row_id: "t", body: "1",
                                             expected_etag: "", private: false });
  const platform: Binding = { owner, caller: PLATFORM_CALLER, origin: null };
  return {
    owner, stub, bind, session, at, platform,
    admit: (origin: typeof MUSE) => stub().outsideAgents(bind(origin)),
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub(), act),
  };
}
type Rig = Awaited<ReturnType<typeof rig>>;

/** Every door of the object's, but the platform's. */
const doors = () => Object.getOwnPropertyNames(OwnerDO.prototype)
  .filter((door) => !["constructor", "alarm", "admissionSet", "tierSet", "fetch",
                      "webSocketMessage", "webSocketClose", "webSocketError",
                      "sandboxRung", "erasureOutside", "erasureOutsideDone"].includes(door));
const call = (r: Rig, door: string, binding: Binding) =>
  (r.stub() as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>)[door]!(
    binding, {});

/** The owner's own tables: none of the runtime's (`_cf_`). */
const tables = (r: Rig) => sqlIn(r.stub(), "SELECT name FROM sqlite_master WHERE " +
                                   "type = 'table' AND name NOT LIKE '\\_cf\\_%' ESCAPE " +
                                   "'\\' ORDER BY name");

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** What a failing `disableAlarms` rejects with: the erasure's last step before the wipe. */
const ALARMS_FAILED = "the alarm could not be disabled (the test's)";

/**
 * Lifecycle's `disableAlarms` failing its next `times` calls, as a storage fault would, so
 * the erasure stops after its second step; returns the undo. Called inside the object.
 */
function failing(times: number): () => void {
  const prototype = Lifecycle.prototype as unknown as Record<string, unknown>;
  const original = prototype["disableAlarms"] as (...args: unknown[]) => Promise<void>;
  let left = times;
  prototype["disableAlarms"] = function (this: unknown, ...args: unknown[]) {
    if (left > 0) {
      left--;
      return Promise.reject(new Error(ALARMS_FAILED));
    }
    return original.apply(this, args);
  };
  return () => {
    prototype["disableAlarms"] = original;
  };
}

/** Reset the object as a crash would, and wait until a new instance answers. */
async function crash(r: Rig): Promise<void> {
  await runInDurableObject(r.stub(), (_instance, state) => state.abort("crash"))
    .catch(() => undefined);
  for (let tries = 0; tries < 100; tries++) {
    if (await runInDurableObject(r.stub(), () => true).catch(() => false)) {
      return;
    }
    await sleep(10);
  }
  throw new Error("no new instance answered");
}

const until = async (check: () => Promise<boolean>, what: string) => {
  for (let i = 0; i < 500; i++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
};

describe("the owner's admission", () => {
  it("refuses every door while suspended, for either reason, until each is lifted", async () => {
    const r = await rig();
    expect(doors().length).toBeGreaterThan(55);
    await r.stub().admissionSet(r.platform, { reason: "billing", suspended: true });
    await r.stub().admissionSet(r.platform, { reason: "abuse", suspended: true });
    const answers = new Map<string, string>();
    for (const door of doors()) {
      answers.set(door, (await failure(() => call(r, door, r.session()))).message);
    }
    expect([...answers].filter(([, answer]) =>
      answer !== `${OWNER_SUSPENDED} (abuse, billing)`)).toEqual([]);
    expect(await r.stub().admissionSet(r.platform, { reason: "billing", suspended: false }))
      .toEqual({ state: "suspended", reasons: ["abuse"] });
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
      .toBe(`${OWNER_SUSPENDED} (abuse)`);
    expect(await r.stub().admissionSet(r.platform, { reason: "abuse", suspended: false }))
      .toEqual({ state: "admitted", reasons: [] });
    expect((await r.stub().policyRead(r.bind(null))).generation).toBeGreaterThan(0);
  }, 60_000);

  it("is set only by the platform's reserved caller, which reaches no other door, and no " +
     "owner is named so", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const set = { reason: "billing", suspended: true };
    expect((await failure(() => r.stub().admissionSet(r.bind(null), set))).message)
      .toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet(r.session(), set))).message)
      .toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet(
      { ...r.platform, origin: MUSE, source: MCP }, set))).message).toBe(PLATFORM_ONLY);
    expect((await failure(() => r.stub().admissionSet({ ...r.platform, session: "x" }, set)))
      .message).toBe(PLATFORM_ONLY);
    for (const door of ["policyRead", "collectionPull", "mailboxRead", "fileUsage"]) {
      expect((await failure(() => call(r, door, r.platform))).message).toBe(PLATFORM_RESERVED);
    }
    for (const bad of [{ reason: "fraud", suspended: true }, { reason: "billing" },
                       { reason: "billing", suspended: "yes" }, {}]) {
      expect((await failure(() => r.stub().admissionSet(r.platform, bad as never))).name)
        .toBe("Invalid");
    }
    const named = env.AGENT.getByName(PLATFORM_CALLER);
    const platformOwner = { owner: PLATFORM_CALLER, caller: PLATFORM_CALLER, origin: null };
    expect((await failure(() => named.policyRead(platformOwner))).message)
      .toBe(PLATFORM_RESERVED);
    expect((await failure(() => named.admissionSet(platformOwner, set))).message)
      .toBe(PLATFORM_RESERVED);
    // Still admitted: nothing above set it.
    expect((await r.stub().policyRead(r.bind(null))).generation).toBeGreaterThan(0);
  }, 30_000);

  it("holds the wakes lane's claims while the owner is not admitted", async () => {
    const r = await ledgerRig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    const due = clock() + 3600;
    const out = await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const grants = new Grants(state.storage, new Policy(state.storage));
      const wake = (admitted: boolean) => {
        const ledger = new Ledger(state.storage, grants, undefined, () => admitted);
        return { next: ledger.nextDue(clock(), admitted),
                 claim: ledger.claim(ledger.dueOpen(due, 1)[0]!.wake_id, due).outcome };
      };
      return [wake(false), wake(true)];
    });
    expect(out[0]).toEqual({ next: null, claim: "held" });
    expect(out[1]).toMatchObject({ claim: "claimed" });
  }, 30_000);
});

describe("the owner's erasure", () => {
  it("marks the owner, every door and lane refusing, then wipes it whole, leaving only the " +
     "erased admission row", async () => {
    const r = await rig();
    // The mark alone, as an erasure a crash cut short after its first step leaves it.
    await r.in((_instance, state) => state.storage.sql.exec(
      "INSERT INTO owner_erasure (only, started_at) VALUES (1, 1)"));
    // The owner's rows are all still there while it is marked.
    expect(await tables(r)).toContainEqual({ name: "collection_rows" });
    // Every door refuses: erasing, and once the erasure the first refusal resumed has wiped
    // the owner, erased.
    const answers = new Map<string, string>();
    for (const door of doors()) {
      answers.set(door, (await failure(() => call(r, door, r.session()))).message);
    }
    expect([...answers.values()][0]).toBe(ERASING);
    expect([...answers].filter(([, answer]) => answer !== ERASING && answer !== OWNER_ERASED))
      .toEqual([]);
    await r.in(async (instance) => {
      await instance.erasure.erase();
      expect(instance.erasure.erased).toBe(true);
      // Every sandbox of the owner's killed and the sandbox Worker's state of it deleted,
      // in the erasure's step 2 (slice S review H-6).
      expect(instance.recorder.erasures).toEqual([r.owner]);
    });
    // A ring of the line reaches an erased owner and does nothing.
    expect(await r.stub().sandboxRung()).toEqual({ waiting: 0 });
    expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
    expect(await sqlIn(r.stub(), "SELECT state, reasons FROM owner_admission"))
      .toEqual([{ state: "erased", reasons: "[]" }]);
    expect((await failure(() => r.stub().collectionGet(r.bind(null), {
      collection: "board", row_id: "t" }))).message).toBe(OWNER_ERASED);
  }, 30_000);

  it("holds its mark until the put grace is up, its job waking it then, never an in-memory " +
     "wait (review rvlim N5)", async () => {
    const r = await rig();
    // Marked just now, both earlier steps done: the grace (here none, so the mark's own
    // time) is ahead, an hour on.
    const due = Date.now() + 3_600_000;
    await r.in((_instance, state) => state.storage.sql.exec(
      "INSERT INTO owner_erasure (only, started_at, step) VALUES (1, ?, 2)", due / 1000));
    await r.in(async (instance) => {
      await instance.erasure.erase();
      expect(instance.erasure.erased).toBe(false);
    });
    expect(await tables(r)).toContainEqual({ name: "collection_rows" });
    const [job] = await sqlIn(r.stub(), "SELECT time FROM cf_agents_jobs WHERE id = " +
                                          "'owner-erasure/erase'");
    expect(job!["time"]).toBe(due);
  }, 30_000);

  it("resumes after a crash at each step, at the object's next start", async () => {
    for (const step of [1, 2]) {
      const r = await rig();
      await r.in((_instance, state) => state.storage.sql.exec(
        "INSERT INTO owner_erasure (only, started_at, step) VALUES (1, 1, ?)", step));
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
      await evictDurableObject(r.stub());
      // The new instance's start resumes it from the step it recorded, and refuses meanwhile
      // (erasing, or erased once the resumed wipe is done).
      expect([ERASING, OWNER_ERASED]).toContain(
        (await failure(() => r.stub().policyRead(r.bind(null)))).message);
      await until(async () => JSON.stringify(await tables(r)) ===
                    '[{"name":"owner_admission"}]', `the wipe after step ${step}`);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(OWNER_ERASED);
    }
  }, 60_000);

  it("resumes at the object's start alone: no door, no job", async () => {
    for (const step of [1, 2]) {
      const r = await rig();
      // A mark with no job beside it: only the start can resume it.
      await r.in((_instance, state) => state.storage.sql.exec(
        "INSERT INTO owner_erasure (only, started_at, step) VALUES (1, 1, ?)", step));
      await crash(r);
      // Read straight from the storage, never through a door's binding check.
      await until(async () => JSON.stringify(await tables(r)) ===
                    '[{"name":"owner_admission"}]', `the start's wipe after step ${step}`);
    }
  }, 60_000);

  it("resumes by its own job alone: no restart, no door", async () => {
    const r = await rig();
    const out = await r.in(async (instance, state) => {
      const restore = failing(1);
      const answer = await instance.erasure.erase().then(() => "wiped",
                                                         (error: Error) => error.message);
      restore();
      return { answer, alarm: await state.storage.getAlarm(),
               step: state.storage.sql.exec<{ step: number }>(
                 "SELECT step FROM owner_erasure").one().step };
    });
    expect(out).toMatchObject({ answer: ALARMS_FAILED, step: 2 });
    expect(out.alarm).not.toBeNull();
    await sqlIn(r.stub(), "UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-erasure/erase'");
    await runDurableObjectAlarm(r.stub());
    await until(async () => JSON.stringify(await tables(r)) === '[{"name":"owner_admission"}]',
                "the job's wipe");
  }, 30_000);

  it("is tried again later when its job fails through Lifecycle's attempts", async () => {
    const r = await rig();
    const restore = await r.in(async (instance) => {
      const undo = failing(Infinity);
      await instance.erasure.erase().catch(() => undefined);
      return undo;
    });
    await sqlIn(r.stub(), "UPDATE cf_agents_jobs SET time = 0 WHERE id = 'owner-erasure/erase'");
    const before = Date.now();
    await runDurableObjectAlarm(r.stub()).catch(() => false);
    // The job's failure moves it a retry interval on, never dropped, never due at once.
    await until(async () => {
      const [job] = await sqlIn(r.stub(), "SELECT time FROM cf_agents_jobs WHERE id = " +
                                          "'owner-erasure/erase'");
      return job !== undefined && Number(job["time"]) >= before + ERASE_RETRY_MS - 5_000;
    }, "the job rescheduled");
    await r.in(() => restore());
  }, 60_000);

  it("resumes from a refused door, at most once a retry interval", async () => {
    const r = await rig();
    await r.in(async (instance) => {
      const restore = failing(1);
      await instance.erasure.erase().catch(() => undefined);
      restore();
    });
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await sleep(200);
    expect(await r.in((instance) => instance.erasure.erased)).toBe(false);  // within its wait
    await sleep(500);
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await until(() => r.in((instance) => instance.erasure.erased), "the door's resume");
  }, 30_000);

  it("disables Lifecycle's alarm before it wipes the owner", async () => {
    const r = await rig();
    const seen = await r.in(async (instance, state) => {
      const original = state.storage.deleteAll.bind(state.storage);
      let alarm: number | null | undefined;
      Object.defineProperty(state.storage, "deleteAll", {
        configurable: true,
        value: async (...args: Parameters<DurableObjectStorage["deleteAll"]>) => {
          alarm = await state.storage.getAlarm();
          return original(...args);
        },
      });
      await instance.erasure.erase();
      return alarm;
    });
    expect(seen).toBeNull();
    expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
  }, 30_000);

  it("resumes its wipe at the next start after a wipe that failed or hung, and never counts " +
     "itself erased before the wipe", async () => {
    for (const how of ["fails", "hangs"] as const) {
      const r = await rig();
      const out = await r.in(async (instance, state) => {
        Object.defineProperty(state.storage, "deleteAll", {
          configurable: true,
          value: how === "fails" ? () => Promise.reject(new Error("the wipe failed (the test's)"))
            : () => new Promise(() => undefined),
        });
        const erasing = instance.erasure.erase().then(() => "wiped",
                                                     (error: Error) => error.message);
        const answer = how === "fails" ? await erasing : "pending";
        await until(async () => state.storage.sql.exec<{ step: number }>(
          "SELECT step FROM owner_erasure").toArray()[0]?.step === 2
          && (await state.storage.getAlarm()) === null, "the alarm's end");
        return { answer, erased: instance.erasure.erased };
      });
      expect(out).toEqual({ answer: how === "fails" ? "the wipe failed (the test's)" : "pending",
                            erased: false });
      // Every run ended and recorded so; the owner's data still there, no alarm left.
      expect(await sqlIn(r.stub(), "SELECT step FROM owner_erasure")).toEqual([{ step: 2 }]);
      expect(await sqlIn(r.stub(), "SELECT 1 FROM collection_rows")).toHaveLength(1);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(ERASING);
      await crash(r);
      await until(async () => JSON.stringify(await tables(r)) ===
                    '[{"name":"owner_admission"}]', `the wipe that ${how}`);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(OWNER_ERASED);
    }
  }, 60_000);
});

describe("the erasure's tombstone", () => {
  it("leaves exactly one erased admission row; a stale getByName is then refused, migrating " +
     "nothing and starting no Lifecycle, across evictions", async () => {
    const r = await rig();
    await r.in((instance) => instance.erasure.erase());
    expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
    expect(await sqlIn(r.stub(), "SELECT only, state, reasons FROM owner_admission"))
      .toEqual([{ only: 1, state: "erased", reasons: "[]" }]);
    for (let round = 0; round < 2; round++) {
      await evictDurableObject(r.stub());
      for (const refused of [() => r.stub().policyRead(r.bind(null)),
                             () => r.stub().sessionOpen(r.bind(null, { authenticated_at: r.at })),
                             () => r.stub().admissionSet(r.platform, { reason: "billing",
                                                                       suspended: false })]) {
        expect((await failure(refused)).message).toBe(OWNER_ERASED);
      }
      expect(await tables(r)).toEqual([{ name: "owner_admission" }]);
      expect(await sqlIn(r.stub(), "SELECT name FROM sqlite_master WHERE name GLOB " +
                                     "'cf_agents_*'")).toEqual([]);
      expect(await alarmOf(r.stub())).toBeNull();
      expect(await runDurableObjectAlarm(r.stub())).toBe(false);
    }
  }, 60_000);

  it("is written in the same turn as the wipe, only into a wiped database, and once", async () => {
    const r = await rig();
    const out = await r.in(async (_instance, state) => {
      // Not wiped (its erasure's mark-table is there): nothing is written.
      writeTombstone(state.storage.sql);
      const before = tombstoned(state.storage.sql);
      const gate = new WriteGate(state.storage);
      const wiping = gate.wipe(writeTombstone);
      const sameTurn = tombstoned(state.storage.sql);
      await wiping;
      writeTombstone(state.storage.sql);  // again: one row still
      return { before, sameTurn, rows: state.storage.sql.exec(
        "SELECT state FROM owner_admission").toArray() };
    });
    expect(out).toEqual({ before: false, sameTurn: true, rows: [{ state: "erased" }] });
  }, 30_000);

  it("leaves another owner admitted, made fresh as ever", async () => {
    const r = await rig();
    await r.in((instance) => instance.erasure.erase());
    const other = await rig();
    expect((await other.stub().policyRead(other.bind(null))).generation).toBeGreaterThan(0);
    expect(await sqlIn(other.stub(), "SELECT state FROM owner_admission"))
      .toEqual([{ state: "admitted" }]);
  }, 30_000);
});
