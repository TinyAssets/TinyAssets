/**
 * The owner's admission and erasure (tasks 5, F1 and S4-S5; ADR-034), carried over from the
 * deleted thread tests (`drops.test.ts`, `scheduled.test.ts`) to what remains: a suspension
 * refuses every door until each reason is lifted, and only the platform's reserved caller
 * sets it; the erasure marks the owner (every door and lane refusing), ends every run,
 * then wipes the owner whole, leaving the erased admission row alone, resumed after a
 * crash at each step; and a stale `getByName` of an erased owner is refused forever.
 *
 * The erasure runs as production runs it, through its doors and its own job
 * (`erasure-support.ts`): begun at `ownerErase`, its outside record confirmed as the doors
 * confirm it, its put grace (a day) and retry interval (a minute) passed on the object's
 * clock by a fake `Date`, and its job run by the object's alarm.
 */
import {
  runDurableObjectAlarm, runInDurableObject,
} from "cloudflare:test";
import { env } from "cloudflare:workers";
import { Lifecycle } from "agents/lifecycle";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  OWNER_ERASED, OWNER_SUSPENDED, PLATFORM_ONLY, PLATFORM_RESERVED, tombstoned, writeTombstone,
} from "../src/admission";
import { type Binding, PLATFORM_CALLER } from "../../contract/src/binding";
import { ERASE_RETRY_MS, erasureAnswer, PUT_GRACE_MS, RECOVERY_WINDOW_S } from "../src/erasure";
import { ERASING } from "../../contract/src/refusal";
import { WriteGate } from "../src/gate";
import { Ledger } from "../src/ledger";
import { clock } from "../src/clock";
import { OwnerDO } from "../src/owner-do";
import { doorTable } from "../../contract/src/doors";
import {
  eraseBegun, erasedWhole, erasuresSent, finished, outsideConfirmed, pastGrace, tables,
  until, wipedWhole,
} from "./erasure-support";
import { ledgerParts, MCP, MUSE, rig as ledgerRig, signedIn } from "./ledger-support";
import { later, recorder } from "./runs-support";
import { admittedOwner, alarmOf, failure, gateFor, sqlIn, TIERS } from "./support";

afterEach(() => {
  vi.useRealTimers();
});

/** An owner of its own, admitted and signed in, with a row. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  await admittedOwner(owner);
  // A fresh stub at each use: a crash breaks the stub that reached the aborted instance.
  const stub = (): DurableObjectStub<OwnerDO> => env.OWNER.getByName(owner);
  const at = signedIn();
  const bind = (origin: typeof MUSE | null, extra: Partial<Binding> = {}): Binding =>
    ({ owner, caller: owner, origin, tiers: TIERS,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }), ...extra });
  const { session: cookie } = await stub().sessionOpen(bind(null, { authenticated_at: at }));
  const session = (): Binding => bind(null, { session: cookie });
  await stub().collectionWrite(bind(null), { collection: "board", row_id: "t", body: "1",
                                             expected_etag: "", private: false });
  const platform: Binding = { owner, caller: PLATFORM_CALLER, origin: null };
  return {
    owner, stub, bind, session, at, platform,
    admit: (origin: typeof MUSE) => stub().outsideAgents(bind(origin)),
    in: <R>(act: (instance: OwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub(), act),
  };
}
type Rig = Awaited<ReturnType<typeof rig>>;

/** The cutover's import doors: the platform's, refusing an owner's binding before its state. */
const IMPORT_DOORS = doorTable().filter((row) => row.class === "import").map((row) => row.door);
/** Every door of the object's, but the platform's. */
const doors = () => Object.getOwnPropertyNames(OwnerDO.prototype)
  .filter((door) => !["constructor", "alarm", "admissionSet", "tierSet", "fetch",
                      "webSocketMessage", "webSocketClose", "webSocketError",
                      "sandboxRung", "sandboxBilled", "dataKeyRewrap", "gatewayReseal", "erasureOutside", "erasureOutsideDone",
                      ...IMPORT_DOORS].includes(door));
const call = (r: Rig, door: string, binding: Binding) =>
  (r.stub() as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>)[door]!(
    binding, {});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** What a failing `disableAlarms` rejects with: the erasure's last step before the wipe. */
const ALARMS_FAILED = "the alarm could not be disabled (the test's)";

/**
 * Lifecycle's `disableAlarms` failing its next `times` calls, as a storage fault would, so
 * the erasure stops after its second step; `undo` puts it back, and `failed` is how many
 * calls it failed. Called inside the object.
 */
function failing(times: number): { undo: () => void; failed: () => number } {
  const prototype = Lifecycle.prototype as unknown as Record<string, unknown>;
  const original = prototype["disableAlarms"] as (...args: unknown[]) => Promise<void>;
  let left = times;
  let failed = 0;
  prototype["disableAlarms"] = function (this: unknown, ...args: unknown[]) {
    if (left > 0) {
      left--;
      failed++;
      return Promise.reject(new Error(ALARMS_FAILED));
    }
    return original.apply(this, args);
  };
  return {
    undo: () => {
      prototype["disableAlarms"] = original;
    },
    failed: () => failed,
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

/** The step the erasure recorded (`owner_erasure`), or null with no mark. */
async function stepOf(r: Rig): Promise<number | null> {
  const [row] = await sqlIn(r.stub(), "SELECT step FROM owner_erasure");
  return row === undefined ? null : row["step"] as number;
}

/** When the erasure's job is due (ms), or null with none. */
async function jobAt(r: Rig): Promise<number | null> {
  const [job] = await sqlIn(r.stub(), "SELECT time FROM cf_agents_jobs WHERE id = " +
                                      "'owner-erasure/erase'");
  return job === undefined ? null : job["time"] as number;
}

/**
 * The erasure begun and stopped at `step` before a crash: at 1, its sandboxes' erase refused
 * at the SANDBOX gate, so it never reached its second step; at 2, waiting out its put grace,
 * which is then passed on the object's clock. Its outside record is not confirmed yet.
 */
async function stopped(r: Rig, step: 1 | 2): Promise<void> {
  const sandbox = gateFor(env.SANDBOX_GATE, r.owner);
  if (step === 1) {
    await sandbox.arm("erase", { refuse: { code: "busy",
                                           message: "busy: the sandbox Worker is full" } });
  }
  await eraseBegun(r.stub(), r.session());
  await until(async () => step === 1
    ? (await sandbox.calls()).some((made) => made.method === "erase")
    : await stepOf(r) === 2 && (await jobAt(r) ?? 0) > Date.now() + ERASE_RETRY_MS,
              `the erasure stopped at step ${step}`);
  expect(await stepOf(r)).toBe(step);
  pastGrace();
}

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
  });

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
      expect((await failure(() => r.stub().admissionSet(r.platform, bad as never))).code)
        .toBe("invalid");
    }
    const named = env.OWNER.getByName(PLATFORM_CALLER);
    const platformOwner = { owner: PLATFORM_CALLER, caller: PLATFORM_CALLER, origin: null };
    expect((await failure(() => named.policyRead(platformOwner))).message)
      .toBe(PLATFORM_RESERVED);
    expect((await failure(() => named.admissionSet(platformOwner, set))).message)
      .toBe(PLATFORM_RESERVED);
    // Still admitted: nothing above set it.
    expect((await r.stub().policyRead(r.bind(null))).generation).toBeGreaterThan(0);
  });

  it("holds the wakes lane's claims while the owner is not admitted", async () => {
    const r = await ledgerRig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    const due = clock() + 3600;
    const out = await runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
      const [storage, grants, , ...rest] = ledgerParts(state.storage);
      const wake = (admitted: boolean) => {
        const ledger = new Ledger(storage, grants, () => admitted, ...rest);
        return { next: ledger.nextDue(clock(), admitted),
                 claim: ledger.claim(ledger.dueOpen(due, 1)[0]!.wake_id, due).outcome };
      };
      return [wake(false), wake(true)];
    });
    expect(out[0]).toEqual({ next: null, claim: "held" });
    expect(out[1]).toMatchObject({ claim: "claimed" });
  });
});

describe("the owner's erasure", () => {
  it("marks the owner, every door and lane refusing, then wipes it whole, leaving only the " +
     "erased admission row", async () => {
    const r = await rig();
    await eraseBegun(r.stub(), r.session());
    // The owner's rows are all still there while it is marked.
    expect(await tables(r.stub())).toContainEqual({ name: "collection_rows" });
    // Every door refuses: erasing (the put grace, a day, is not up, so no refused door wipes).
    const answers = new Map<string, string>();
    for (const door of doors()) {
      answers.set(door, (await failure(() => call(r, door, r.session()))).message);
    }
    expect([...answers].filter(([, answer]) => answer !== ERASING)).toEqual([]);
    await finished(r.stub(), r.owner);
    // Every sandbox of the owner killed and the sandbox Worker's state of it deleted, in the
    // erasure's step 2 (slice S review H-6).
    expect((await recorder(r.stub())).erasures).toEqual([r.owner]);
    // A ring of the line reaches an erased owner and does nothing.
    expect(await r.stub().sandboxRung()).toEqual({ waiting: 0 });
    expect(await tables(r.stub())).toEqual([{ name: "owner_admission" }]);
    expect(await sqlIn(r.stub(), "SELECT state, reasons FROM owner_admission"))
      .toEqual([{ state: "erased", reasons: "[]" }]);
    expect((await failure(() => r.stub().collectionGet(r.bind(null), {
      collection: "board", row_id: "t" }))).message).toBe(OWNER_ERASED);
  });

  it("holds its mark until the put grace is up, its job waking it then, never an in-memory " +
     "wait (review rvlim N5)", async () => {
    const r = await rig();
    await eraseBegun(r.stub(), r.session());
    await outsideConfirmed(r.stub(), r.owner);
    // Confirmed, it waits out the grace alone: its job is due a day after the mark.
    const [{ started_at: marked }] = await sqlIn(r.stub(), "SELECT started_at FROM " +
                                                           "owner_erasure") as [{ started_at: number }];
    await until(async () => await jobAt(r) === marked * 1000 + PUT_GRACE_MS,
                "the job at the grace's end");
    expect(await stepOf(r)).toBe(2);
    await runDurableObjectAlarm(r.stub());
    expect(await tables(r.stub())).toContainEqual({ name: "collection_rows" });
    // The grace up on the object's clock: the job, run by the alarm, wipes the owner.
    pastGrace();
    await runDurableObjectAlarm(r.stub());
    await until(() => wipedWhole(r.stub()), "the job's wipe");
  });

  it("resumes after a crash at each step, at the object's next start", async () => {
    for (const step of [1, 2] as const) {
      const r = await rig();
      await stopped(r, step);
      const sent = await erasuresSent(r.owner);
      await crash(r);
      // The new instance's start resumes it from the step it recorded: it reaches its second
      // step, and past the grace it tells the doors again; every door refuses meanwhile.
      await until(async () => await stepOf(r) === 2 && await erasuresSent(r.owner) > sent,
                  `the start's resume after step ${step}`);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
      await finished(r.stub(), r.owner);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(OWNER_ERASED);
    }
  });

  it("resumes at the object's start alone: no door, no job", async () => {
    for (const step of [1, 2] as const) {
      const r = await rig();
      await stopped(r, step);
      // The job lost: only the start can resume it.
      await sqlIn(r.stub(), "DELETE FROM cf_agents_jobs WHERE id = 'owner-erasure/erase'");
      const sent = await erasuresSent(r.owner);
      await crash(r);
      // Read straight from the storage and the queue, never through a door.
      await until(async () => await stepOf(r) === 2 && await erasuresSent(r.owner) > sent,
                  `the start's resume after step ${step}`);
    }
  });

  it("resumes by its own job alone: no restart, no door", async () => {
    const r = await rig();
    const fault = await r.in(() => failing(1));
    await eraseBegun(r.stub(), r.session());
    await outsideConfirmed(r.stub(), r.owner);
    pastGrace();
    // A door's resume past the grace: its attempt stops at its last step, the alarm kept.
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await until(() => fault.failed() === 1, "the attempt's failed alarm step");
    expect(await stepOf(r)).toBe(2);
    expect(await alarmOf(r.stub())).not.toBeNull();
    expect(await tables(r.stub())).toContainEqual({ name: "collection_rows" });
    // Its job, due on the object's clock, finishes it.
    await runDurableObjectAlarm(r.stub());
    await until(() => wipedWhole(r.stub()), "the job's wipe");
    fault.undo();
  });

  it("is tried again later when its job fails through Lifecycle's attempts", async () => {
    const r = await rig();
    const fault = await r.in(() => failing(Infinity));
    try {
      await eraseBegun(r.stub(), r.session());
      await outsideConfirmed(r.stub(), r.owner);
      pastGrace();
      const before = Date.now();
      await runDurableObjectAlarm(r.stub()).catch(() => false);
      // The job's failure moves it a retry interval on, never dropped, never due at once.
      await until(async () => (await jobAt(r) ?? 0) >= before + ERASE_RETRY_MS,
                  "the job rescheduled");
      expect(await tables(r.stub())).toContainEqual({ name: "collection_rows" });
    } finally {
      fault.undo();
    }
  });

  it("resumes from a refused door, at most once a retry interval", async () => {
    const r = await rig();
    const fault = await r.in(() => failing(1));
    await eraseBegun(r.stub(), r.session());
    await outsideConfirmed(r.stub(), r.owner);
    pastGrace();
    // The first refused door past the grace resumes it, and that attempt fails.
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await until(() => fault.failed() === 1, "the first attempt's failure");
    fault.undo();
    // Within its interval, a refused door resumes nothing.
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await sleep(200);
    expect(await tables(r.stub())).toContainEqual({ name: "collection_rows" });
    // An interval on, on the object's clock, the next refused door resumes it.
    later(ERASE_RETRY_MS / 1000 + 1);
    expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
    await until(() => wipedWhole(r.stub()), "the door's resume");
  });

  it("disables Lifecycle's alarm before it wipes the owner", async () => {
    const r = await rig();
    const seen = await r.in((_instance, state) => {
      const original = state.storage.deleteAll.bind(state.storage);
      let alarm: number | null | undefined;
      Object.defineProperty(state.storage, "deleteAll", {
        configurable: true,
        value: async (...args: Parameters<DurableObjectStorage["deleteAll"]>) => {
          alarm = await state.storage.getAlarm();
          return original(...args);
        },
      });
      return () => alarm;
    });
    await erasedWhole(r.stub(), r.owner, r.session());
    expect(seen()).toBeNull();
    expect(await tables(r.stub())).toEqual([{ name: "owner_admission" }]);
  });

  it("resumes its wipe at the next start after a wipe that failed or hung, and never counts " +
     "itself erased before the wipe", async () => {
    for (const how of ["fails", "hangs"] as const) {
      const r = await rig();
      await r.in((_instance, state) => {
        Object.defineProperty(state.storage, "deleteAll", {
          configurable: true,
          value: how === "fails" ? () => Promise.reject(new Error("the wipe failed (the test's)"))
            : () => new Promise(() => undefined),
        });
      });
      await eraseBegun(r.stub(), r.session());
      await outsideConfirmed(r.stub(), r.owner);
      pastGrace();
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message).toBe(ERASING);
      // Its attempt reached the wipe: every run ended and recorded so, the alarm disabled.
      await until(async () => await stepOf(r) === 2 && await alarmOf(r.stub()) === null,
                  "the alarm's end");
      // The owner's data still there, and it still counts itself erasing, not erased.
      expect(await sqlIn(r.stub(), "SELECT 1 FROM collection_rows")).toHaveLength(1);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(ERASING);
      await crash(r);
      await until(() => wipedWhole(r.stub()), `the wipe that ${how}`);
      expect((await failure(() => r.stub().policyRead(r.bind(null)))).message)
        .toBe(OWNER_ERASED);
    }
  });
});

describe("the erasure's tombstone", () => {
  it("leaves exactly one erased admission row; a stale getByName is then refused, migrating " +
     "nothing and starting no Lifecycle, across restarts", async () => {
    const r = await rig();
    await erasedWhole(r.stub(), r.owner, r.session());
    expect(await sqlIn(r.stub(), "SELECT only, state, reasons FROM owner_admission"))
      .toEqual([{ only: 1, state: "erased", reasons: "[]" }]);
    for (let round = 0; round < 2; round++) {
      await crash(r);
      for (const refused of [() => r.stub().policyRead(r.bind(null)),
                             () => r.stub().sessionOpen(r.bind(null, { authenticated_at: r.at })),
                             () => r.stub().admissionSet(r.platform, { reason: "billing",
                                                                       suspended: false })]) {
        expect((await failure(refused)).message).toBe(OWNER_ERASED);
      }
      expect(await tables(r.stub())).toEqual([{ name: "owner_admission" }]);
      expect(await sqlIn(r.stub(), "SELECT name FROM sqlite_master WHERE name GLOB " +
                                     "'cf_agents_*'")).toEqual([]);
      expect(await alarmOf(r.stub())).toBeNull();
      expect(await runDurableObjectAlarm(r.stub())).toBe(false);
    }
  });

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
  });

  it("leaves another owner admitted, made fresh as ever", async () => {
    const r = await rig();
    await erasedWhole(r.stub(), r.owner, r.session());
    const other = await rig();
    expect((await other.stub().policyRead(other.bind(null))).generation).toBeGreaterThan(0);
    expect(await sqlIn(other.stub(), "SELECT state FROM owner_admission"))
      .toEqual([{ state: "admitted" }]);
  });
});

describe("the account-erase answer", () => {
  it("names when erasure is complete: the mark, its put grace, then 30 days of recovery",
     () => {
    expect(RECOVERY_WINDOW_S).toBe(30 * 86_400);
    const grace = PUT_GRACE_MS / 1000;
    // Answered at the mark, before the wipe: not erased yet, complete at its earliest.
    expect(erasureAnswer(1_000, null)).toMatchObject({
      erased: false, complete_at: 1_000 + grace + 30 * 86_400 });
    // Wiped on time: the same moment.
    expect(erasureAnswer(1_000, 1_000 + grace)).toMatchObject({
      erased: true, complete_at: 1_000 + grace + 30 * 86_400 });
    // A retry that wiped later: later.
    expect(erasureAnswer(1_000, 1_000 + grace + 600).complete_at)
      .toBe(1_000 + grace + 600 + 30 * 86_400);
  });
});
