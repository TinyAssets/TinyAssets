/**
 * A model turn's counters live exactly as long as its work (migration 10, M3): each pi
 * record that owns a turn (an open input submission, every steer joined to its run beside
 * it, or a live compaction task) is kept by the thread host as it commits, in a unit at
 * once, and all of them again as a Session opens; a turn's row comes with its first owner
 * and goes with its last, or with its thread. A call of a turn with no row is refused
 * `turn_ended`, so no counter ever starts over; a thread holds at most `THREAD_INPUTS`
 * open inputs.
 */
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { fauxAssistantMessage, fauxToolCall, type Message } from "@earendil-works/pi-ai";
import { ProviderDoc, ROOT_CONVERSATION_ID } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { originContext } from "../src/origin";
import { THREAD_INPUTS, THREAD_INPUTS_FULL, turnOwners } from "../src/thread-host";
import { TURN_ENDED } from "../src/usage";
import { asked, gated, type Gated, provider } from "./gateway-support";
import { failure } from "./support";
import { type FauxOwnerDO, says } from "./worker";

const BG = BACKGROUND_CONTEXT;
const THREAD = `thread_${"a".repeat(32)}`;

function agentObject() {
  const stub = env.AGENT.getByName(`owner-${crypto.randomUUID()}`);
  return {
    stub,
    in: <R>(act: (instance: FauxOwnerDO, state: DurableObjectState) => R | Promise<R>) =>
      runInDurableObject(stub, act),
  };
}

/** A model answer that calls the test's `hold` tool through `ta`. */
const holding = () => fauxAssistantMessage([fauxToolCall("ta", { op: "call", name: "hold",
                                                                  arguments: {} })],
                                           { stopReason: "toolUse" });

function gate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}

async function until(check: () => Promise<boolean>, what: string): Promise<void> {
  for (let i = 0; i < 400; i++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** Write a models table into the owner's policy, in its session. */
async function models(r: Gated, table: Record<string, unknown>): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind());
  await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                          policy: { ...policy, models: table } });
}

/** Rows of a query in the object's own storage, from inside it. */
const rowsIn = (state: DurableObjectState, query: string) =>
  state.storage.sql.exec<Record<string, SqlStorageValue>>(query).toArray();
const owners = (state: DurableObjectState) =>
  rowsIn(state, "SELECT owner, turn FROM model_turn_owners ORDER BY owner");

describe("a turn's owners, as the thread host sees them commit", () => {
  it("are each open input and live compaction, with its turn, and none once it ends", () => {
    const input = (id: number, status: string, trace?: Record<string, string>) =>
      ({ type: "submission", value: { id, type: "input", status, conversationId: 0,
                                      ...(trace === undefined ? {} : { trace }) } });
    const task = (id: number, kind: string, status: string) =>
      ({ type: "task", value: { id, kind, state: { status } } });
    expect(turnOwners(THREAD, [
      input(1, "queued", { "ta.origin": "input:a" }),
      input(2, "placed", { "ta.origin": "input:a", "ta.joined": "1" }),
      input(3, "done", { "ta.origin": "input:b" }),
      input(4, "unanswered", { "ta.origin": "input:c" }),
      input(5, "queued"),
      { type: "submission", value: { id: 6, type: "write", status: "queued", conversationId: 0,
                                     trace: { "ta.origin": "input:d" } } },
      task(7, "pi.compaction", "pending"),
      task(8, "pi.compaction", "completing"),
      task(9, "pi.compaction", "terminal"),
      task(10, "pi.generation", "running"),
    ] as never)).toEqual([
      { owner: "s:1", turn: "input:a", joined: false },
      { owner: "s:2", turn: "input:a", joined: true },
      { owner: "s:3", turn: null },
      { owner: "s:4", turn: null },
      { owner: "t:7", turn: `task:${THREAD}:7` },
      { owner: "t:8", turn: `task:${THREAD}:8` },
      { owner: "t:9", turn: null },
    ]);
  });

  it("hold each open input's turn, a steer's its own, and let them go once answered",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      instance.script = Array.from({ length: 12 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done")
          : JSON.stringify(messages.at(-1)).includes("work") ? holding() : says("noted"));
      const held = gate();
      instance.probes.holdGate = held.promise;
      const { operationId } = await instance.harness.submit("work");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const during = owners(state);
      // pi marks a steer's own origin before it links the steer to the run: its turn is
      // its own, and the run's calls stay the run's.
      const steer = await instance.harness.submit("steer it", { whenBusy: "steer" });
      const steered = owners(state);
      held.release();
      await instance.harness.wait(operationId);
      await instance.harness.wait(steer.operationId);
      return { during, steered, after: owners(state),
               turns: rowsIn(state, "SELECT turn FROM model_turns") };
    });
    expect(out.during).toHaveLength(1);
    expect(out.steered).toHaveLength(2);
    expect(new Set(out.steered.map((row) => row["turn"])).size).toBe(2);
    expect(out.after).toEqual([]);
    expect(out.turns).toEqual([]);
  });

  it("are healed when the Session reopens after units were lost between commit and unit",
     async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      instance.script = Array.from({ length: 12 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done")
          : JSON.stringify(messages.at(-1)).includes("work") ? holding() : says("noted"));
      const thread = await instance.harness.thread();
      const owned = instance.parts.turns.owned;
      // The object dies after each store commit and before its unit: nothing is kept.
      instance.parts.turns.owned = async () => undefined;
      const held = gate();
      instance.probes.holdGate = held.promise;
      // Cut off by the close, the hold outlasts it, so its run is still open when it reopens.
      instance.probes.holdLinger = 200;
      const { operationId } = await instance.harness.submit("work");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      const lost = owners(state);
      // A stale owner a lost settle left behind, of an input long answered.
      await owned(thread, [{ owner: "s:999999", turn: "input:stale" }]);
      instance.parts.turns.owned = owned;
      const reconciled = instance.parts.turns.reconciled;
      let healed: Record<string, SqlStorageValue>[] = [];
      instance.parts.turns.reconciled = async (at, open) => {
        await reconciled(at, open);
        healed = owners(state);  // as the Session resumes
      };
      await instance.agent.host.close(thread);
      await instance.agent.host.pi(thread);
      held.release();
      await instance.harness.wait(operationId);
      return { lost, healed, after: owners(state), holds: instance.probes.holds };
    });
    expect(out.lost).toEqual([]);
    expect(out.healed).toHaveLength(1);
    expect(out.healed[0]!["owner"]).toMatch(/^s:[0-9]+$/);
    expect(out.healed[0]!["turn"]).toMatch(/^input:/);
    expect(out.after).toEqual([]);
  }, 30_000);

  it("go with their thread, in its drop's unit", async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      const thread = (await instance.agent.thread("main", null)).thread_id;
      await instance.parts.turns.owned(thread, [{ owner: "s:1", turn: "input:dropped" },
                                                { owner: "t:2", turn: `task:${thread}:2` }]);
      const kept = rowsIn(state, "SELECT turn FROM model_turns ORDER BY turn").length;
      await instance.parts.write(() => state.storage.sql.exec(
        "DELETE FROM agent_threads WHERE thread_id = ?", thread));
      return { kept, after: owners(state), turns: rowsIn(state, "SELECT turn FROM model_turns") };
    });
    expect(out).toEqual({ kept: 2, after: [], turns: [] });
  });

  it("are refused past THREAD_INPUTS open inputs a thread, at once and loudly", async () => {
    const r = agentObject();
    const out = await r.in(async (instance, state) => {
      instance.script = Array.from({ length: 40 }, () => (messages: Message[]) =>
        messages.at(-1)?.role === "toolResult" ? says("done")
          : JSON.stringify(messages.at(-1)).includes("work") ? holding() : says("noted"));
      const held = gate();
      instance.probes.holdGate = held.promise;
      const first = await instance.harness.submit("work");
      await until(async () => instance.probes.holds === 1, "the hold to start");
      for (let i = 1; i < THREAD_INPUTS; i++) {
        await instance.harness.submit(`more ${i}`);
      }
      const refused = await failure(() => instance.harness.submit("one too many"));
      // The same input again is pi's duplicate, never refused.
      const again = await instance.harness.submit("work", { operationId: first.operationId });
      held.release();
      await instance.harness.wait(first.operationId);
      return { refused: refused.message, again: again.accepted,
               open: (owners(state)).length };
    });
    expect(out.refused).toBe(THREAD_INPUTS_FULL);
    expect(out.again).toBe(false);
  }, 60_000);
});

describe("a turn through the gateway", () => {
  it("is refused turn_ended once its input was answered, its counters never made again",
     async () => {
    const r = await gated();
    await r.connect("sk-ended");
    const out = await r.in(async (instance, state) => {
      expect((await instance.harness.prompt("cmd:echo")).status).toBe("done");
      const turn = rowsIn(state, "SELECT turn FROM model_reservations")[0]!["turn"] as string;
      const pi = await instance.harness.pi();
      const root = await pi.snapshot(ProviderDoc, ROOT_CONVERSATION_ID, BG);
      const auto = instance.parts.provider.getModels()[0]!;
      const late = await (await provider(instance)).streamSimple(auto, asked("cmd:echo"), {
        sessionId: root!.sessionId, telemetry: originContext(turn) }).result();
      return { late: late.errorMessage, turns: rowsIn(state, "SELECT turn FROM model_turns") };
    });
    expect(out.late).toBe(`model_budget: ${TURN_ENDED}`);
    expect(out.turns).toEqual([]);
    const rows = await r.sql("SELECT state, rule FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => [row["state"], row["rule"]]))
      .toEqual([["succeeded", null], ["refused", TURN_ENDED]]);
  }, 30_000);

  it("ends a run's turn with the run, and gives a steer that outlives it a turn of its own",
     async () => {
    const r = await gated();
    await r.connect("sk-steer");
    await models(r, { turn_requests: 1 });
    const out = await r.in(async (instance, state) => {
      // The run's one call streams on while the steer comes; the run then answers, and the
      // steer, left behind its last boundary, starts a run of its own on the same turn.
      const { operationId } = await instance.harness.submit("cmd:drip");
      await until(async () => rowsIn(state, "SELECT 1 FROM model_reservations WHERE state = 'dispatched'")
                    .length > 0,
                  "the run's call to be out");
      const steer = await instance.harness.submit("cmd:echo", { whenBusy: "steer" });
      return [await instance.harness.wait(operationId),
              await instance.harness.wait(steer.operationId)];
    });
    expect(out.map((answer) => answer.status)).toEqual(["done", "done"]);
    // At one request a turn, the steer's run is its own turn's first, never the run's second.
    const rows = await r.sql("SELECT turn, state, rule FROM model_reservations ORDER BY seq");
    expect(rows.map((row) => [row["state"], row["rule"]]))
      .toEqual([["succeeded", null], ["succeeded", null]]);
    expect(rows[1]!["turn"]).not.toBe(rows[0]!["turn"]);
    expect(await r.sql("SELECT turn FROM model_turns")).toEqual([]);
  }, 30_000);
});
