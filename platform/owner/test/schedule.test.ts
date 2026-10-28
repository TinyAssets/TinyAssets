/**
 * The one schedule on the Agents SDK Lifecycle job queue (tasks 3.6): each lane's job is
 * derived from its rows, a run ends by setting its own job, a failed lane backs off, and
 * the object holds a Lifecycle without its runtime handlers.
 *
 * The lane tests run a real Lifecycle (agents 0.31.0) over a fresh object's own storage,
 * with its alarm writes recorded instead of set, so workerd never fires them and each test
 * drives `alarm()` itself.
 */
import { runInDurableObject } from "cloudflare:test";
import { Lifecycle, LifecycleCapability } from "agents/lifecycle";
import { describe, expect, it } from "vitest";

import type { OwnerDO } from "../src/owner-do";
import {
  inTime, type Lane, LANE_HUNG_S, LANE_PASS_S, LANE_RETRY_S, laneJob, Schedule, SCHEDULE,
} from "../src/schedule";
import { WORK_HANDOFF_MS } from "../src/scheduled";
import { MUSE, rig } from "./ledger-support";
import { alarmOf, failure, laneJobs, ownerObject, sqlIn } from "./support";

/** A lane over a script: its next work, and what each run does. */
class ScriptedLane implements Lane {
  due: number | null = null;
  runs = 0;
  /** Runs left that throw, and what they throw. */
  failing = 0;
  error: Error = new Error("the pass failed");
  /** Run inside each run, after its work: a door acting meanwhile. */
  during: (() => Promise<void>) | null = null;
  /** The next work a run leaves: null for none. */
  after: number | null = null;
  /** Ignore `now`, as a lane that breaks its contract would. */
  fixed = false;
  /** How long each run takes, and the most runs seen at once. */
  sleepMs = 0;
  #running = 0;
  mostAtOnce = 0;
  /** The wall-clock budget each run was given, in ms. */
  readonly untils: number[] = [];

  next(now: number): number | null {
    return this.due === null ? null : this.fixed ? this.due : Math.max(this.due, now);
  }

  async run(_now: number, until: number): Promise<void> {
    this.untils.push(until);
    this.runs++;
    this.#running++;
    this.mostAtOnce = Math.max(this.mostAtOnce, this.#running);
    try {
      await this.#work();
    } finally {
      this.#running--;
    }
  }

  async #work(): Promise<void> {
    if (this.sleepMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, this.sleepMs));
    }
    if (this.failing > 0) {
      this.failing--;
      throw this.error;
    }
    this.due = this.after;
    await this.during?.();
  }
}

interface Probe {
  readonly schedule: Schedule;
  readonly lifecycle: Lifecycle;
  /** Each alarm write Lifecycle made, in ms, null for a delete. */
  readonly alarms: (number | null)[];
  readonly lanes: Record<string, ScriptedLane>;
  /** The schedule's clock, in seconds; tests move it. */
  now: number;
  /** The schedule's jobs by lane: due time, and the lane work time each was set for. */
  jobs(): Record<string, { time: number; due: unknown }>;
  /** A statement on the object's own storage, as a direct change would make. */
  sql(statement: string, ...params: SqlStorageValue[]): void;
  /** Rows read from the object's own storage. */
  read(statement: string, ...params: SqlStorageValue[]): Record<string, SqlStorageValue>[];
}

/** A Schedule of scripted lanes `names`, installed on a Lifecycle over `state`'s storage. */
function probe(state: DurableObjectState, names: string[]): Probe {
  const alarms: (number | null)[] = [];
  const bound = (target: object, key: string | symbol) => {
    const value = Reflect.get(target, key) as unknown;
    return typeof value === "function" ? (value as (...a: unknown[]) => unknown).bind(target)
      : value;
  };
  const storage = new Proxy(state.storage, {
    get: (target, key) => key === "setAlarm" ? async (at: number) => { alarms.push(at); }
      : key === "deleteAlarm" ? async () => { alarms.push(null); }
      : bound(target, key),
  });
  const ctx = new Proxy(state, {
    get: (target, key) => key === "storage" ? storage : bound(target, key),
  });
  const lanes = Object.fromEntries(names.map((name) => [name, new ScriptedLane()]));
  const p: Probe = {
    alarms, lanes, now: Date.now() / 1000,
    sql: (statement, ...params) => void state.storage.sql.exec(statement, ...params).toArray(),
    read: (statement, ...params) => state.storage.sql.exec(statement, ...params).toArray(),
    schedule: undefined as unknown as Schedule,
    lifecycle: undefined as unknown as Lifecycle,
    jobs: () => Object.fromEntries(state.storage.sql.exec<{ id: string; time: number;
                                                            payload: string | null }>(
      "SELECT id, time, payload FROM cf_agents_jobs WHERE capability = ? ORDER BY id", SCHEDULE,
    ).toArray().map((row) => [row.id.slice(SCHEDULE.length + 1), {
      time: row.time,
      due: row.payload === null ? null : (JSON.parse(row.payload) as { due?: unknown }).due,
    }])),
  };
  const schedule = new Schedule(lanes, () => p.now);
  const host = { ctx, constructor: { name: "ProbeHost" } } as unknown as OwnerDO;
  return Object.assign(p, { schedule, lifecycle: new Lifecycle(host).use(schedule) });
}

/** Run `act` on a probe over a fresh owner's object. */
function withProbe<R>(names: string[], act: (p: Probe) => Promise<R>): Promise<R> {
  const { stub } = ownerObject();
  return runInDurableObject(stub, (_i: OwnerDO, state) => act(probe(state, names)));
}

const ms = (seconds: number) => Math.ceil(seconds * 1000);

/** A job set at `time` for work at `due` (both in seconds). */
const at = (time: number, due = time) => ({ time: ms(time), due: ms(due) });

describe("a lane's job", () => {
  it("is set at the lane's next work, cancelled with none, and left alone when in place",
     () => withProbe(["a", "b"], async (p) => {
    const t = p.now;
    p.lanes["a"]!.due = t + 100;
    await p.schedule.sync();
    expect(p.jobs()).toEqual({ a: at(t + 100) });
    expect(p.alarms).toEqual([ms(t + 100)]);  // Lifecycle armed it; b has no work, no job
    await p.schedule.sync();
    expect(p.alarms).toHaveLength(1);  // already in place: no row and no alarm written
    p.lanes["b"]!.due = t + 50;
    await p.schedule.sync();
    expect(p.jobs()).toEqual({ a: at(t + 100), b: at(t + 50) });
    expect(p.alarms.at(-1)).toBe(ms(t + 50));  // the earliest job
    p.lanes["a"]!.due = null;
    p.lanes["b"]!.due = null;
    await p.schedule.sync();
    expect(p.jobs()).toEqual({});
    expect(p.alarms.at(-1)).toBeNull();  // no job: the alarm is cleared
  }));

  it("is never set before now, nor before the lane's work", () => withProbe(["a"], async (p) => {
    p.lanes["a"]!.due = p.now - 600;
    await p.schedule.sync();
    expect(p.jobs()["a"]!.time).toBe(ms(p.now));
    const due = Math.floor(p.now) + 100.0004;  // work due part-way through a millisecond
    p.lanes["a"]!.due = due;
    await p.schedule.sync();
    expect(p.jobs()["a"]!.time / 1000).toBeGreaterThanOrEqual(due);
  }));

  it("is kept where Lifecycle moved it later, unless the rows have earlier work",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now + 100;
    await p.schedule.sync();
    // The memory-limit breaker backs the job off, around the schedule.
    p.sql("UPDATE cf_agents_jobs SET time = time + 600000");
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 700, p.now + 100));
    a.due = p.now + 300;  // the rows' work moved later, still before the backoff: kept
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 700, p.now + 100));
    a.due = p.now + 50;  // earlier work than the job was set for
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 50));
  }));

  it("never shares an id with another capability's job", () =>
    withProbe(["pi-wake"], async (p) => {
      const pi = new (class extends LifecycleCapability {
        constructor() {
          super("pi-harness");
        }
        wake() {
          return this.lifecycle.jobs.push({ id: "pi-wake", fn: "wake", time: Date.now() + 1000 });
        }
      })();
      p.lifecycle.use(pi);
      await pi.wake();
      p.lanes["pi-wake"]!.due = p.now + 100;
      await p.schedule.sync();
      expect(Object.keys(p.jobs())).toEqual(["pi-wake"]);
      expect(laneJob("pi-wake")).toBe("owner-schedule/pi-wake");
    }));
});

describe("a lane's run", () => {
  it("is one bounded pass per alarm, and ends by setting its job from the rows",
     () => withProbe(["a", "b"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.after = p.now + 500;
    p.lanes["b"]!.due = p.now + 900;
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect([a.runs, p.lanes["b"]!.runs]).toEqual([1, 0]);  // only the due lane ran
    expect(p.jobs()["a"]).toEqual(at(p.now + 500));
    // A run that leaves more work due now sets its job at once; the next alarm takes it.
    a.due = p.now - 10;
    a.after = p.now - 10;
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect(a.runs).toBe(2);
    expect(p.jobs()["a"]!.time).toBe(ms(p.now));
    a.after = null;
    await p.lifecycle.alarm();
    expect([a.runs, p.jobs()["a"]]).toEqual([3, undefined]);  // no work left: no job
  }));

  it("keeps a door's change made while it runs", () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.after = null;
    a.during = async () => {
      a.due = p.now + 40;  // a door adds work while the run awaits its sends
      await p.schedule.sync();
    };
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect(p.jobs()["a"]).toEqual(at(p.now + 40));
  }));

  it("keeps its job when its next work is where the job already was",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.fixed = true;
    a.due = p.now - 10;
    a.after = p.now - 10;
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect(p.jobs()["a"]!.time).toBe(ms(p.now - 10));
  }));

  it("is single-flight: an overlapping alarm skips a lane while it runs",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.sleepMs = 200;
    await p.schedule.sync();
    await Promise.all([p.lifecycle.alarm(), p.lifecycle.alarm()]);
    expect([a.runs, a.mostAtOnce]).toEqual([1, 1]);
  }));

  it("gives each pass a wall-clock budget its single-flight hung timeout covers (review N2)",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    await p.schedule.sync();
    const [job] = p.read("SELECT singleflight, hung_timeout_seconds FROM cf_agents_jobs " +
                         "WHERE id = ?", laneJob("a"));
    expect([Boolean(job!["singleflight"]), job!["hung_timeout_seconds"]]).toEqual([true, 60]);
    const before = Date.now();
    await p.lifecycle.alarm();
    expect(a.untils).toHaveLength(1);
    expect(a.untils[0]!).toBeGreaterThanOrEqual(before + LANE_PASS_S * 1000);
    expect(a.untils[0]!).toBeLessThanOrEqual(Date.now() + LANE_PASS_S * 1000);
    // A row's outside call fits in what the budget leaves of the hung timeout.
    expect(LANE_HUNG_S).toBe(60);
    expect(WORK_HANDOFF_MS).toBeLessThanOrEqual((LANE_HUNG_S - LANE_PASS_S) * 1000);
    expect(await inTime(new Promise(() => undefined), 10)).toBe("late");
    expect(await inTime(Promise.resolve("done"), Infinity)).toBe("done");
  }));

  it("that fails through Lifecycle's retries backs its lane off, and a door honours it",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.failing = 99;
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect(a.runs).toBe(3);  // Lifecycle's in-process attempts, then the lane's backoff
    const held = p.now + LANE_RETRY_S;
    expect(p.jobs()["a"]).toEqual(at(held, p.now));
    await p.schedule.sync();  // a door: the work is due now, but the lane is held
    expect(p.jobs()["a"]).toEqual(at(held, p.now));
    a.due = p.now + 2 * LANE_RETRY_S;  // work later than the backoff keeps its own time
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 2 * LANE_RETRY_S));
  }));

  it("that succeeds clears its lane's backoff", () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.failing = 3;
    await p.schedule.sync();
    await p.lifecycle.alarm();
    expect(p.jobs()["a"]).toEqual(at(p.now + LANE_RETRY_S, p.now));
    p.sql("UPDATE cf_agents_jobs SET time = 0");  // the backoff's time has come
    a.after = p.now + 900;
    await p.lifecycle.alarm();
    expect([a.runs, p.jobs()["a"]]).toEqual([4, at(p.now + 900)]);
  }));

  it("that hits a platform failure keeps its job for a fresh invocation, unbacked-off",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.failing = 1;
    a.error = new Error("Durable Object reset because its code was updated.");
    await p.schedule.sync();
    const before = p.jobs()["a"];
    expect((await failure(() => p.lifecycle.alarm())).message).toMatch(/code was updated/);
    expect([a.runs, p.jobs()["a"]]).toEqual([1, before]);
  }));

  it("of a job no lane names completes it without running a lane",
     () => withProbe(["a"], async (p) => {
    p.lanes["a"]!.due = p.now + 900;
    await p.schedule.sync();
    p.sql("INSERT INTO cf_agents_jobs (id, capability, fn, time) VALUES " +
            "(?, ?, 'stray', 0), ('a', ?, 'a', 0), (?, ?, 'a', 0)",
          laneJob("stray"), SCHEDULE, SCHEDULE, laneJob("b"), SCHEDULE);
    await p.lifecycle.alarm();
    expect(p.lanes["a"]!.runs).toBe(0);
    expect(Object.keys(p.jobs())).toEqual(["a"]);
  }));
});

describe("the object's schedule", () => {
  it("is a fixed set of lane jobs, armed at the earliest", async () => {
    const r = await rig();
    const muse = await r.register(MUSE, "Muse", "mcp_event");
    await r.post(null, `dm:${muse}`);
    await r.stub.fileUploadBegin(r.bind(null), { size: 5000, chunks: ["e".repeat(64)] });
    const t = Date.now() / 1000;
    await r.inLedger({}, (ledger) => {
      const claim = ledger.claim(ledger.dueOpen(t + 120, 1)[0]!.wake_id, t + 120);
      expect(claim.outcome).toBe("claimed");
    });
    await r.stub.grantRevoke(r.bind(null), { client: "f".repeat(64) });  // any change syncs
    const jobs = await laneJobs(r.stub);
    expect(Object.keys(jobs)).toEqual(["files", "wakes"]);
    expect(await sqlIn(r.stub, "SELECT capability, COUNT(*) AS n FROM cf_agents_jobs " +
                                 "GROUP BY capability")).toEqual([{ capability: SCHEDULE, n: 2 }]);
    const earliest = Math.min(jobs["files"]!.time, jobs["wakes"]!.time);
    expect(earliest).toBe(jobs["wakes"]!.time);  // the claim's lease, before the upload's expiry
    expect(await alarmOf(r.stub)).toBe(earliest / 1000);
  });

  it("is driven by the object's alarm alone: no fetch or WebSocket handler is installed",
     async () => {
    const { stub, as, name } = ownerObject();
    await stub.policyRead(as(name));
    // The base class's own two, and none of Lifecycle's handlers (fetch, alarm, webSocket*).
    expect(await runInDurableObject(stub, (instance: OwnerDO) =>
      Object.getOwnPropertyNames(instance).sort())).toEqual(["ctx", "env"]);
    expect((await failure(() => stub.fetch("https://owner.example/"))).message)
      .toMatch(/does not define a `fetch\(\)` method/);
  });
});
