/**
 * Sandbox time, metered per owner (slice P, `sandbox-time.ts`): the sandbox Worker's log of
 * starts, pauses, resumes and kills summed per run and per UTC day, read by the object's own
 * `sandbox` lane. It limits nothing.
 */
import { runInDurableObject } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import { WriteGate } from "../src/gate";
import type { OwnerDO } from "../src/owner-do";
import { type RunReceipt, runMeter } from "../src/runs";
import { BILLED_KEPT, DAYS_KEPT, meterRead, SandboxTime } from "../src/sandbox-time";
import { rig } from "./ledger-support";
import { gated } from "./gateway-support";
import { ownerRun, recorder, runnerBinding } from "./runs-support";
import { drainDue, failure, sqlIn } from "./support";

const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: [] });
const S = 1000;
/** A day E2B billed nothing for: only the Worker's own log counted it. */
const UNBILLED = Object.freeze({ billed_seconds: 0, vcpu_seconds: 0, gb_seconds: 0,
                                 unverified_executions: 0 });
const DAY = 86_400 * S;

let made = 0;
/** A settled run row, as admission and settle would leave it, on `authority`'s sandbox. */
function settledRun(sql: SqlStorage, started: number | null, settled: number,
                    client: string | null = null): string {
  const id = `run_${(++made).toString(16).padStart(32, "0")}`;
  sql.exec("INSERT INTO runs (id, agent, conversation, source, credential, state, initiator, " +
             "client, unattended, created_at, started_at, settled_at) VALUES (?, 'main', ?, " +
             "'claude-code', 'api_key', 'succeeded', ?, ?, 0, ?, ?, ?)",
           id, `c_${"0".repeat(33)}`, client === null ? "owner" : "client", client,
           (started ?? settled) / S, started === null ? null : started / S, settled / S);
  return id;
}

const sandboxMs = (sql: SqlStorage, id: string) =>
  sql.exec<{ ms: number | null }>("SELECT sandbox_ms AS ms FROM runs WHERE id = ?", id).one().ms;

/** A fresh owner's migrated storage, the meter on it, in its own object. */
function onMeter<R>(act: (meter: SandboxTime, sql: SqlStorage) => R): Promise<R> {
  return gated().then((r) => runInDurableObject(r.stub, (_instance: OwnerDO, state) => {
    const gate = new WriteGate(state.storage);
    return gate.write(() => act(new SandboxTime(gate.storage, runMeter(gate.storage.sql)),
                                gate.storage.sql as SqlStorage));
  }));
}

describe("sandbox time", () => {
  it("sums a sandbox's running time across its pause, resume and kill, per run and per day",
     async () => {
    await onMeter((meter, sql) => {
      const t0 = 30 * DAY;
      // A run in the owner's sandbox from 5 s to 30 s, one from 2 s to 15 s, and a client's.
      const a = settledRun(sql, t0 + 5 * S, t0 + 30 * S);
      const b = settledRun(sql, t0 + 2 * S, t0 + 15 * S);
      const c = settledRun(sql, t0 + 1 * S, t0 + 50 * S, "f".repeat(64));
      const never = settledRun(sql, null, t0 + 3 * S);
      meter.apply([
        { seq: 1, authority: "owner", kind: "start", at: t0 },
        { seq: 2, authority: "owner", kind: "pause", at: t0 + 10 * S },
        { seq: 3, authority: "owner", kind: "resume", at: t0 + 20 * S },
        { seq: 4, authority: "owner", kind: "kill", at: t0 + 35 * S },
      ], 4, t0 + 60 * S);
      // Each run's seconds while its sandbox ran: (10 - 5) + (30 - 20); then (10 - 2), its
      // sandbox paused from 10 s past its settle at 15 s; none for the client's sandbox,
      // which never ran, nor for a run that never started.
      expect([sandboxMs(sql, a), sandboxMs(sql, b), sandboxMs(sql, c), sandboxMs(sql, never)])
        .toEqual([15 * S, 8 * S, 0, 0]);
      // The sandbox itself: 10 s, then 15 s, once however many runs it held.
      expect(meter.days(10)).toEqual([{ day: "19700131", seconds: 25, ...UNBILLED }]);
      expect(meter.cursor()).toMatchObject({ after: 4, read_at: t0 + 60 * S });
      // Every run is metered: no span is kept.
      expect(sql.exec("SELECT COUNT(*) AS n FROM sandbox_spans").one()).toEqual({ n: 0 });
    });
  });

  it("meters a run only once the log is read past its settle, and keeps what it needs",
     async () => {
    await onMeter((meter, sql) => {
      const t0 = 40 * DAY;
      const late = settledRun(sql, t0 + 1 * S, t0 + 100 * S);
      meter.apply([{ seq: 1, authority: "owner", kind: "resume", at: t0 }], 1, t0 + 50 * S);
      expect(sandboxMs(sql, late)).toBeNull();
      expect(meter.next(t0 / S + 50)).toBe(t0 / S + 50);
      // The pause comes in a later read, after the settle: the run gets 99 s.
      meter.apply([{ seq: 2, authority: "owner", kind: "pause", at: t0 + 120 * S }], 2,
                  t0 + 130 * S);
      expect(sandboxMs(sql, late)).toBe(99 * S);
      // An open span, read again a while after the last read; none, no work.
      meter.apply([{ seq: 3, authority: "owner", kind: "resume", at: t0 + 200 * S }], 3,
                  t0 + 210 * S);
      expect(meter.next(t0 / S + 211)).toBe(t0 / S + 210 + 900);
      meter.apply([{ seq: 4, authority: "owner", kind: "kill", at: t0 + 300 * S }], 4,
                  t0 + 310 * S);
      expect(meter.next(t0 / S + 311)).toBeNull();
      expect(meter.days(1)).toEqual([{ day: "19700210", seconds: 120 + 100, ...UNBILLED }]);
    });
  });

  it("splits a span across UTC days, and keeps the newest days only", async () => {
    await onMeter((meter, sql) => {
      const midnight = 50 * DAY;
      meter.apply([{ seq: 1, authority: "owner", kind: "start", at: midnight - 10 * S },
                   { seq: 2, authority: "owner", kind: "pause", at: midnight + 30 * S }],
                  2, midnight + 60 * S);
      expect(meter.days(5)).toEqual([{ day: "19700220", seconds: 30, ...UNBILLED },
                                     { day: "19700219", seconds: 10, ...UNBILLED }]);
      const events = [];
      for (let day = 0; day < DAYS_KEPT + 5; day++) {
        events.push({ seq: 3 + 2 * day, authority: "owner", kind: "resume" as const,
                      at: (100 + day) * DAY },
                    { seq: 4 + 2 * day, authority: "owner", kind: "pause" as const,
                      at: (100 + day) * DAY + S });
      }
      meter.apply(events, events.at(-1)!.seq, (100 + DAYS_KEPT + 6) * DAY);
      const kept = sql.exec<{ n: number }>("SELECT COUNT(*) AS n FROM sandbox_days").one().n;
      expect(kept).toBe(DAYS_KEPT);
    });
  });

  it("refuses a log out of shape, loudly", () => {
    const ok = { events: [{ seq: 3, authority: "owner", kind: "pause", at: 5 }], next: 3 };
    expect(meterRead(ok, 2).events).toHaveLength(1);
    for (const bad of [
      { events: [] }, { ...ok, next: 1 },
      { events: [{ ...ok.events[0], seq: 2 }], next: 3 },
      { events: [{ ...ok.events[0], kind: "sleep" }], next: 3 },
      { events: [{ ...ok.events[0], at: -1 }], next: 3 },
      { events: [{ ...ok.events[0], extra: 1 }], next: 3 },
    ]) {
      expect(() => meterRead(bad, 2)).toThrow("the sandbox's meter answered out of shape");
    }
  });

  it("is read by the object's own lane after a run settles, onto the run's receipt",
     async () => {
    const r = await gated();
    const sandbox = await recorder(r.stub);
    sandbox.meterFails = true;  // the first read fails: the lane backs off, nothing is lost
    const { run, start } = await ownerRun(r.stub, r.bind(), SIGNED_IN);
    await r.stub.runEvents(runnerBinding(r, start), { frames: [], end: { exit_code: 0 } });
    await drainDue(r.stub);
    const status = async () =>
      (await r.stub.agentStatus(r.bind(), { run_id: run.run_id }) as RunReceipt).sandbox_s;
    expect(await status()).toBeNull();
    const [row] = await sqlIn(r.stub, "SELECT started_at, settled_at FROM runs WHERE id = ?",
                              run.run_id);
    const [from, to] = [row!["started_at"] as number * S, row!["settled_at"] as number * S];
    sandbox.record("owner", "start", Math.floor(from) - 500);
    sandbox.record("owner", "pause", Math.floor(from) + 1);
    sandbox.record("owner", "resume", Math.floor(from) + 2);
    sandbox.meterFails = false;
    await sqlIn(r.stub, "UPDATE sandbox_meter SET retry_at = NULL");
    // Any door that changes a lane's rows re-derives the jobs.
    await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                       expected_etag: null });
    await drainDue(r.stub);
    const expected = Math.round((Math.floor(from) + 1 - from) + (to - (Math.floor(from) + 2)));
    expect(await status()).toBe(expected / 1000);
    // The sandbox itself: from its start to its pause, once (its resume is still open).
    expect((await r.stub.sandboxDays(r.bind(), {})).days).toEqual([
      { day: new Date(Math.floor(from) - 500).toISOString().slice(0, 10).replaceAll("-", ""),
        seconds: 0.501, ...UNBILLED }]);
    // The client's own call never reaches the meter's read.
    expect((await failure(() => r.stub.sandboxDays(
      r.bind({ origin: { client: "c".repeat(64), family: "mcp" } } as never), {}))).name)
      .toMatch(/Refused|Invalid/);
  });
});

describe("E2B's billed executions", () => {
  const event = (over: Record<string, unknown>) => ({
    id: crypto.randomUUID(), authority: "owner", kind: "paused",
    started_at: Date.now() - 60_000, execution_ms: 60_000, vcpu: 2, memory_mb: 1024,
    execution_id: "exec-1", sandbox_id: "sbx-1", at: Date.now(), ...over });
  const billed = async (r: Awaited<ReturnType<typeof rig>>) =>
    (await r.stub.sandboxDays(r.bind(null), {})).days;

  it("counts a paused sandbox's execution once, though its kill comes days later " +
     "(review N4)", async () => {
    const r = await rig();
    expect(await r.stub.sandboxBilled(event({}) as never)).toEqual({ counted: true });
    // A day and an hour pass: the window's ids are gone, the live sandbox's last is kept.
    await sqlIn(r.stub, "UPDATE sandbox_billed SET at = at - ?", 25 * 3_600_000);
    expect(await r.stub.sandboxBilled(event({ kind: "killed" }) as never))
      .toEqual({ counted: false });
    expect((await billed(r)).reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(60);
    // Killed: its sandbox's row is gone.
    expect(await sqlIn(r.stub, "SELECT COUNT(*) AS n FROM sandbox_live")).toEqual([{ n: 0 }]);
  });

  it("counts an execution past BILLED_KEPT anyway, and shows it unverified (review N5)",
     async () => {
    const r = await rig();
    await sqlIn(r.stub, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n " +
      "WHERE i < ?) INSERT INTO sandbox_billed (digest, at) SELECT randomblob(8), ? FROM n",
                BILLED_KEPT, Date.now());
    expect(await r.stub.sandboxBilled(event({ execution_id: "new-exec" }) as never))
      .toEqual({ counted: true });
    const days = await billed(r);
    expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(60);
    expect(days.reduce((sum, day) => sum + day.unverified_executions, 0)).toBe(1);
  });
});
