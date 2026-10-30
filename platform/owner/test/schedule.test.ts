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
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { Lifecycle, LifecycleCapability } from "agents/lifecycle";
import { describe, expect, it } from "vitest";

import { type Write, WriteGate } from "../src/gate";
import type { Ledger } from "../src/ledger";
import { type LaneDeps, type OwnerDO, ownerLanes } from "../src/owner-do";
import { FILE_DEFAULTS } from "../src/policy";
import {
  inTime, type Lane, LANE_HUNG_S, LANE_PASS_S, LANE_RETRY_S, laneJob, Schedule, SCHEDULE,
} from "../src/schedule";
import { type Scheduled, WORK_HANDOFF_MS, WORK_PER_PASS, type WorkRunner } from "../src/scheduled";
import { WAKE_SEND_MS, type WakeSender, WAKES_PER_ALARM } from "../src/wakes";
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
  /** The object's own storage, as the probe found it. */
  readonly storage: DurableObjectStorage;
  /** One alarm, as the object runs it: Lifecycle's. */
  alarm(): Promise<void>;
  /** Each alarm write Lifecycle made, in ms, null for a delete. */
  readonly alarms: (number | null)[];
  /** Set to make each alarm write fail, as a storage fault would. */
  alarmFault: Error | null;
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
    get: (target, key) => key === "setAlarm" ? async (at: number) => {
      if (p.alarmFault !== null) {
        throw p.alarmFault;
      }
      alarms.push(at);
    }
      : key === "deleteAlarm" ? async () => { alarms.push(null); }
      : bound(target, key),
  });
  const ctx = new Proxy(state, {
    get: (target, key) => key === "storage" ? storage : bound(target, key),
  });
  const lanes = Object.fromEntries(names.map((name) => [name, new ScriptedLane()]));
  const p: Probe = {
    alarms, alarmFault: null, lanes, storage: state.storage, now: Date.now() / 1000,
    alarm: () => p.lifecycle.alarm(),
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
  const schedule = new Schedule(lanes, () => p.now, new WriteGate(state.storage).write);
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

  it("fails a set whose alarm cannot be armed, rather than leave its job unarmed",
     () => withProbe(["a"], async (p) => {
    p.lanes["a"]!.due = p.now + 100;
    p.alarmFault = new Error("the alarm could not be set");
    expect((await failure(() => p.schedule.sync())).message).toBe("the alarm could not be set");
    p.alarmFault = null;
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
    await p.alarm();
    expect([a.runs, p.lanes["b"]!.runs]).toEqual([1, 0]);  // only the due lane ran
    expect(p.jobs()["a"]).toEqual(at(p.now + 500));
    // A run that leaves more work due now sets its job at once; the next alarm takes it.
    a.due = p.now - 10;
    a.after = p.now - 10;
    await p.schedule.sync();
    await p.alarm();
    expect(a.runs).toBe(2);
    expect(p.jobs()["a"]!.time).toBe(ms(p.now));
    a.after = null;
    await p.alarm();
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
    await p.alarm();
    expect(p.jobs()["a"]).toEqual(at(p.now + 40));
  }));

  it("keeps its job when its next work is where the job already was",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.fixed = true;
    a.due = p.now - 10;
    a.after = p.now - 10;
    await p.schedule.sync();
    await p.alarm();
    expect(p.jobs()["a"]!.time).toBe(ms(p.now - 10));
  }));

  it("is single-flight: an overlapping alarm skips a lane while it runs",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.sleepMs = 200;
    await p.schedule.sync();
    await Promise.all([p.alarm(), p.alarm()]);
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
    await p.alarm();
    expect(a.untils).toHaveLength(1);
    expect(a.untils[0]!).toBeGreaterThanOrEqual(before + LANE_PASS_S * 1000);
    expect(a.untils[0]!).toBeLessThanOrEqual(Date.now() + LANE_PASS_S * 1000);
    // A row's outside call fits in what the budget leaves of the hung timeout.
    expect(LANE_HUNG_S).toBe(60);
    expect(WORK_HANDOFF_MS).toBeLessThanOrEqual((LANE_HUNG_S - LANE_PASS_S) * 1000);
    expect(await inTime(new Promise(() => undefined), 10)).toBe("late");
    const slow = new Promise((resolve) => setTimeout(() => resolve("done"), 20));
    expect(await inTime(slow, Infinity)).toBe("done");  // unbounded waits it out
  }));

  it("is set inside a unit of the write gate: never while a pi transaction holds the queue",
     () => withProbe(["a"], async (p) => {
    p.lanes["a"]!.due = p.now + 900;
    // A transaction of the queue pi's commits use, held open as a pi commit holds it.
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    const open = new DurableObjectSqliteDatabase(p.storage).transaction(() => held);
    const setting = p.schedule.sync();
    for (let i = 0; i < 20; i++) {
      await Promise.resolve();
    }
    expect(p.jobs()).toEqual({});  // the job row waits for the queue
    release();
    await Promise.all([open, setting]);
    expect(p.jobs()["a"]).toEqual(at(p.now + 900));
  }));

  it("that fails through Lifecycle's retries backs its lane off, and a door honours it",
     () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.failing = 99;
    await p.schedule.sync();
    await p.alarm();
    expect(a.runs).toBe(3);  // Lifecycle's in-process attempts, then the lane's backoff
    const held = p.now + LANE_RETRY_S;
    expect(p.jobs()["a"]).toEqual(at(held, p.now));
    await p.schedule.sync();  // a door: the work is due now, but the lane is held
    expect(p.jobs()["a"]).toEqual(at(held, p.now));
    a.due = held;  // work at the hold's own time: the job is now set for that work
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(held));
    a.due = p.now + 10;  // so earlier work moves it, the hold spent
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 10));
    a.due = p.now + 2 * LANE_RETRY_S;  // work later than the backoff keeps its own time
    await p.schedule.sync();
    expect(p.jobs()["a"]).toEqual(at(p.now + 2 * LANE_RETRY_S));
  }));

  it("that succeeds clears its lane's backoff", () => withProbe(["a"], async (p) => {
    const a = p.lanes["a"]!;
    a.due = p.now - 10;
    a.failing = 3;
    await p.schedule.sync();
    await p.alarm();
    expect(p.jobs()["a"]).toEqual(at(p.now + LANE_RETRY_S, p.now));
    p.sql("UPDATE cf_agents_jobs SET time = 0");  // the backoff's time has come
    a.after = p.now + 900;
    await p.alarm();
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
    expect((await failure(() => p.alarm())).message).toMatch(/code was updated/);
    expect([a.runs, p.jobs()["a"]]).toEqual([1, before]);
  }));

  it("of a job no lane names completes it without running a lane",
     () => withProbe(["a"], async (p) => {
    p.lanes["a"]!.due = p.now + 900;
    await p.schedule.sync();
    p.sql("INSERT INTO cf_agents_jobs (id, capability, fn, time) VALUES " +
            "(?, ?, 'stray', 0), ('a', ?, 'a', 0), (?, ?, 'a', 0)",
          laneJob("stray"), SCHEDULE, SCHEDULE, laneJob("b"), SCHEDULE);
    await p.alarm();
    expect(p.lanes["a"]!.runs).toBe(0);
    expect(Object.keys(p.jobs())).toEqual(["a"]);
  }));
});

describe("the object's lanes", () => {
  /** Recording stand-ins for the stores and passes the lanes run over. */
  function recorded(sender: () => WakeSender | null, runner: () => WorkRunner | null) {
    const seen: unknown[][] = [];
    const ledger = { nextDue: (now: number, ringing: boolean) => {
      seen.push(["ledger.nextDue", now, ringing]);
      return now + 1;
    } } as unknown as Ledger;
    const work = { nextDue: (now: number, running: boolean) => {
      seen.push(["work.nextDue", now, running]);
      return now + 2;
    } } as unknown as Scheduled;
    const files = {
      nextAlarm: (now: number) => {
        seen.push(["files.nextAlarm", now]);
        return now + 3;
      },
      collect: async (now: number, until: number) => {
        seen.push(["files.collect", now, until]);
      },
    };
    const connections = {
      nextDue: (now: number) => {
        seen.push(["connections.nextDue", now]);
        return now + 4;
      },
      run: async (now: number, until: number) => {
        seen.push(["connections.run", now, until]);
      },
    };
    const threads = {
      nextDue: (now: number) => {
        seen.push(["threads.nextDue", now]);
        return now + 5;
      },
      run: async (now: number, until: number) => {
        seen.push(["threads.run", now, until]);
      },
    };
    const clock = () => 42;
    const write = (() => undefined) as unknown as Write;
    const deps: LaneDeps = {
      ledger, files, work, connections, threads, sender, runner, clock, write,
      runWakes: async (...args) => {
        seen.push(["runWakes", ...args]);
        return 0;
      },
      runWork: async (...args) => {
        seen.push(["runWork", ...args]);
        return 0;
      },
    };
    return { seen, deps, ledger, work, clock, write };
  }

  it("hand each pass its until (slice 5 N1)", async () => {
    const { seen, deps, ledger, work, clock, write } = recorded(() => null, () => null);
    const lanes = ownerLanes(deps);
    expect(Object.keys(lanes)).toEqual(["wakes", "files", "work", "connections", "threads"]);
    for (const lane of Object.values(lanes)) {
      await lane.run(100, 123_456);
    }
    expect(seen).toEqual([
      ["runWakes", ledger, null, clock, write, WAKES_PER_ALARM, WAKE_SEND_MS, 123_456],
      ["files.collect", 100, 123_456],
      ["runWork", work, null, clock, write, WORK_PER_PASS, WORK_HANDOFF_MS, 123_456],
      ["connections.run", 100, 123_456],
      ["threads.run", 100, 123_456],
    ]);
  });

  it("count open rows as work only with the doorbell and runner they read at each use " +
     "(review N3)", () => {
    let sender: WakeSender | null = null;
    let runner: WorkRunner | null = null;
    const { seen, deps } = recorded(() => sender, () => runner);
    const lanes = ownerLanes(deps);
    for (const ringing of [false, true]) {
      expect([lanes.wakes.next(7), lanes.files.next(7), lanes.work.next(7),
              lanes.connections.next(7), lanes.threads.next(7)]).toEqual([8, 10, 9, 11, 12]);
      expect(seen).toEqual([["ledger.nextDue", 7, ringing], ["files.nextAlarm", 7],
                            ["work.nextDue", 7, ringing], ["connections.nextDue", 7],
                            ["threads.nextDue", 7]]);
      seen.length = 0;
      void Promise.all(Object.values(lanes).map((lane) => lane.run(1, 2)));
      expect(seen.map((call) => call[2])).toEqual([sender, 2, runner, 2, 2]);
      seen.length = 0;
      // The object gains its doorbell and runner after the lanes were made.
      sender = {} as WakeSender;
      runner = {} as WorkRunner;
    }
  });
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

  it("writes its doors' and its alarm's file steps through the write gate", async () => {
    const r = await rig();
    const path = "/workspace/one.bin";
    const bytes = new Uint8Array(5000).fill(7);
    // Each step a door writes around its FILES call is a unit of the gate: one written
    // outside a unit would be refused by the storage view the object's modules hold.
    const written = await r.stub.fileWrite(r.bind(null), { path, expected_etag: null,
                                                           content: bytes });
    const version = (written as { entry: { version: number } }).entry.version;
    expect((await r.stub.fileReadChunks(r.bind(null), { path, version, first: 0, count: 1 }))
      .chunks).toEqual([bytes]);
    await r.stub.fileRemove(r.bind(null), { path, expected_etag: null, recursive: false });
    await r.stub.fileHistoryPurge(r.bind(null), { path });
    const [{ sha256 }] = await sqlIn(r.stub, "SELECT sha256 FROM file_chunks") as
      [{ sha256: string }];
    // The alarm's files pass deletes the garbage chunk, each of its writes a unit; a pass
    // that wrote outside one would throw, and the chunk would stay.
    await sqlIn(r.stub, "UPDATE file_chunks SET gc_after = 1");
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/d", expected_etag: null });
    // The door re-derived the files job: due now.
    expect((await laneJobs(r.stub))["files"]!.time).toBeLessThanOrEqual(Date.now());
    await runInDurableObject(r.stub, (instance: OwnerDO) => instance.alarm());
    expect(await sqlIn(r.stub, "SELECT sha256 FROM file_chunks WHERE sha256 = ?", sha256))
      .toEqual([]);
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
