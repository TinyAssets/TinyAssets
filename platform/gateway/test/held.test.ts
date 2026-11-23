/**
 * A metered call as the gateway holds it (`held.ts`): held by its weight, its slots taken as
 * its bytes arrive and let go at its stop or its deadline, keys past ASCII and a path-named
 * model's body `model` refused, any throw before the fetch settled `failed`, a body shorter
 * than its length refused; and the isolate's pool under load, wait-die.
 */
import { createExecutionContext } from "cloudflare:test";
import { exports } from "cloudflare:workers";
import { env } from "cloudflare:workers";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Refusal } from "../../contract/src/refusal";
import { aliased, apiRules } from "../../contract/src/api-rules";
import { EGRESS_BODY_MAX, SLOT_WAIT_MS } from "../../contract/src/limits";
import {
  GATEWAY_POOL_SLOTS, gatewaySlots, Hold, heldDeadlineMs, readHeld, TEXT_COST, Tokens,
} from "../src/held";
import { ConnectGateway, EgressGateway } from "../src/index";
import { UNIT_BYTES } from "../../contract/src/slots";
import { until } from "../../ci/workerd";
import { steer } from "./fake-upstream";
import { C1, GOOGLE, granting, meterable, OPENAI, outcome, posted } from "./support";
import type { Reserve } from "../../contract/src/gateway";

/** The doors' connect entrypoint (the doors and owner suites reach it over a real binding). */
const connect = new ConnectGateway(createExecutionContext(), env);

/** An OpenAI Responses model whose output cap pi-ai sets, so its calls can be metered. */
const responsesModel = () => meterable(OPENAI, "sk-fwd");

describe("a key past ASCII, or a body model where the path names it", () => {
  it("refuses max_tokenſ, ſtream and ſervice_tier, which Go's folding reads as ASCII", () => {
    const completions = apiRules("openai-completions")!;
    expect(aliased(completions, { model: "m", MAX_TOKENS: 9 })).toBe(true);
    expect(aliased(completions, { model: "m", max_tokens: 10, "max_tokenſ": 999_999 }))
      .toBe(true);
    expect(aliased(completions, { model: "m", "max_completion_tokenſ": 999_999 })).toBe(true);
    expect(aliased(completions, { model: "m", "ſervice_tier": "priority" })).toBe(true);
    expect(aliased(completions, { model: "m", stream: true, "ſtream_options":
      { include_usage: false } })).toBe(true);
  });

  it("never sends the long-s cap: the call is refused before any reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", C1, 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi", max_output_tokens: 900_000,
                       ["max_output_tokenſ"]: 900_000 }), steer({ mode: "echo" })),
      granting(asked), new ReadableStream());
    expect([answer.response.status, await answer.response.json()]).toEqual(
      [400, { error: { code: "egress_aliased_key", message: "egress_aliased_key" } }]);
    expect(asked).toEqual([]);
    expect(await outcome(answer)).toEqual({ state: "failed" });
  });

  it("refuses a Google call whose body names a model beside its path's", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", GOOGLE)).blob;
    const { id, meter } = await meterable(GOOGLE, "sk-fwd");
    const path = meter.path.replace("{model}", id);
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", C1, 1, blob, posted(path, JSON.stringify({
      model: "models/some-dearer-model", contents: [{ role: "user", parts: [{ text: "hi" }] }] }),
      steer({ mode: "echo" })), granting(asked), new ReadableStream());
    expect(await answer.response.json()).toMatchObject({ error: { code: "egress_aliased_key" } });
    expect(asked).toEqual([]);
  });
});

describe("a held call's length, weight and holds", () => {
  it("refuses a body shorter than its Content-Length, before any reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const text = JSON.stringify({ model: model.id, input: "hi" });
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body: new Blob([text]).stream(),
                                        headers: { "content-length": String(text.length + 1000),
                                                   ...steer({ mode: "echo" }) } }),
      granting(asked), new ReadableStream());
    expect(await answer.response.json()).toMatchObject({ error: { code: "invalid_request" } });
    expect(asked).toEqual([]);
    expect(gatewaySlots().state().held).toBe(0);
  });

  it("refuses a body whose tree would weigh more than the pool, before its parse", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    // 4 MiB of empty objects: 11.67 times its bytes once parsed, measured.
    const items = "{},".repeat(Math.floor(4 * 1024 * 1024 / 3));
    const text = `{"model":${JSON.stringify(model.id)},"input":[${items}{}]}`;
    const asked: unknown[] = [];
    const answer = await egress.forward("alice", C1, 1, blob, posted("/v1/responses", text),
                                        granting(asked), new ReadableStream());
    expect([answer.response.status, await answer.response.json()]).toEqual(
      [413, { error: { code: "egress_too_large", message: "egress_too_large" } }]);
    expect(asked).toEqual([]);
    expect(gatewaySlots().state().held).toBe(0);
  });

  it("lets every slot go at the caller's stop of a stalled body, another owner's call landing",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const mallory = (await connect.seal("mallory", C1, 1, "sk-fwd", OPENAI)).blob;
    const pool = gatewaySlots();
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
    // One sandbox declares the bound and sends 10 bytes, then nothing.
    const stalled = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"model":"'));
    } });
    const stop = new IdentityTransformStream();
    const first = egress.forward("mallory", C1, 1, mallory, new Request(
      "https://egress/v1/responses", { method: "POST", body: stalled,
                                        headers: { "content-length": String(EGRESS_BODY_MAX) } }),
      granting(), stop.readable);
    // It holds only what its bytes need: one slot, not the pool.
    await until(() => pool.state().held === 1, "the unit held", 1500);
    const small = JSON.stringify({ model: model.id, input: "hi" });
    const other = await egress.forward("alice", C1, 1, blob, posted("/v1/responses", small,
      steer({ mode: "echo" })), granting(), new ReadableStream());
    expect(other.response.status).toBe(200);
    await other.response.body?.cancel();
    // The caller (the door) stops the call: its read ends, and its slot goes.
    await stop.writable.getWriter().close();
    const stopped = await first;
    expect([stopped.response.status, await stopped.response.json()]).toEqual(
      [499, { error: { code: "aborted", message: "aborted" } }]);
    expect(await outcome(stopped)).toEqual({ state: "failed" });
    await until(() => pool.state().held === 0, "the unit let go", 1500);
  });

  it("refuses a body stalled past its deadline, its slots let go", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const stalled = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('{"model":"'));
    } });
    // On the fake clock, once the body's first bytes hold their unit: refused at the
    // sender's deadline, never before it.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    let said: unknown;
    let answer: Awaited<ReturnType<EgressGateway["forward"]>> | undefined;
    try {
      const asked = egress.forward("alice", C1, 1, blob, new Request(
        "https://egress/v1/responses", { method: "POST", body: stalled,
                                          headers: { "content-length": "100" } }),
        granting(), new ReadableStream()).then(async (answered) => {
        answer = answered;
        said = await answered.response.json();
      });
      await until(() => gatewaySlots().state().held > 0, "the body's unit");
      await vi.advanceTimersByTimeAsync(SLOT_WAIT_MS - 1);
      expect(said).toBeUndefined();
      await vi.advanceTimersByTimeAsync(heldDeadlineMs(100) - SLOT_WAIT_MS + 1);
      await asked;
    } finally {
      vi.useRealTimers();
    }
    expect(said).toMatchObject({ error: { code: "invalid_request" } });
    expect(await outcome(answer!)).toEqual({ state: "failed" });
    expect(gatewaySlots().state().held).toBe(0);
  });

  it("stops a reserve its caller stopped, its slots let go, nothing sent", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const stop = new IdentityTransformStream();
    let asked = false;
    const answer = egress.forward("alice", C1, 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi" }), steer({ mode: "echo" })),
      () => {
        asked = true;
        return new Promise(() => undefined);  // the owner's object never answers
      }, stop.readable);
    await until(() => asked, "the reserve asked", 1500);
    await stop.writable.getWriter().close();
    const stopped = await answer;
    expect(await stopped.response.json()).toMatchObject({ error: { code: "aborted" } });
    expect(await outcome(stopped)).toEqual({ state: "failed" });
    expect(gatewaySlots().state().held).toBe(0);
  });

  it("settles `failed`, never `unknown`, a call it threw on after the reserve", async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    // The reserve's RPC failed after the owner's commit: the gateway sent nothing.
    const reserve: Reserve = async () => {
      throw new Error("the RPC to the owner's object failed after its commit");
    };
    const answer = await egress.forward("alice", C1, 1, blob, posted("/v1/responses",
      JSON.stringify({ model: model.id, input: "hi" })), reserve, new ReadableStream());
    expect(answer.response.status).toBe(500);
    expect(await outcome(answer)).toEqual({ state: "failed" });
  });
});

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

describe("a held call's sender, and its weight past ASCII", () => {
  it("weighs a body of `\\u` escapes as the two-byte text its parse makes", () => {
    const tokens = new Tokens();
    tokens.feed(new TextEncoder().encode('{"model":"m","input":"\\u2014 dash"}'));
    expect(tokens.ascii).toBe(false);
    const plain = new Tokens();
    plain.feed(new TextEncoder().encode('{"model":"m","input":"a \\n line"}'));
    expect(plain.ascii).toBe(true);
  });

  it("lets every one of 16 silent calls go at its unit's deadline: another owner lands",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const alice = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    const mallory = (await connect.seal("mallory", C1, 1, "sk-fwd", OPENAI)).blob;
    const model = await responsesModel();
    const pool = gatewaySlots();
    // On the fake clock: the units' deadlines pass on it, not on the host.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      const hogs = Array.from({ length: GATEWAY_POOL_SLOTS }, () => egress.forward(
        "mallory", C1, 1, mallory, new Request("https://egress/v1/responses", {
          method: "POST", headers: { "content-length": String(EGRESS_BODY_MAX) },
          body: new ReadableStream<Uint8Array>({ start() { /* sends nothing */ } }),
        }), granting(), new ReadableStream()));
      await until(() => pool.state().held === GATEWAY_POOL_SLOTS, "the pool held", 1500);
      // Alice's ordinary call (one unit), a moment later, waits while mallory's silent calls
      // hold the pool, up to their units' deadline; at it they go, and she lands.
      await vi.advanceTimersByTimeAsync(1);
      const text = JSON.stringify({ model: model.id, input: "hi" });
      const asked = egress.forward("alice", C1, 1, alice, posted("/v1/responses", text,
        steer({ mode: "echo" })), granting(), new ReadableStream());
      await until(() => pool.state().waiting === 1, "alice's wait", 1500);
      await vi.advanceTimersByTimeAsync(SLOT_WAIT_MS - 2);
      expect(pool.state()).toMatchObject({ held: GATEWAY_POOL_SLOTS, waiting: 1 });
      await vi.advanceTimersByTimeAsync(1);
      await until(() => pool.state().waiting === 0, "alice's unit", 1500);
      const answer = await asked;
      expect(answer.response.status).toBe(200);
      await answer.response.body?.cancel();
      for (const hog of await Promise.all(hogs)) {
        expect(await hog.response.json()).toMatchObject({ error: { code: "invalid_request" } });
      }
    } finally {
      vi.useRealTimers();
    }
    await until(() => pool.state().held === 0, "the pool let go", 1500);
  });

  it("never counts a wait for a unit against the sender", async () => {
    const pool = gatewaySlots();
    const blocker = await pool.acquire(GATEWAY_POOL_SLOTS - 1, "x");
    const bytes = new TextEncoder().encode(JSON.stringify({ input: "a".repeat(2 * 1024 * 1024) }));
    // The body is the test's to feed: up to the first byte past one unit's, then the rest.
    const first = Math.floor(UNIT_BYTES / TEXT_COST) + 1;
    let feed!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(controller) { feed = controller; } });
    const hold = new Hold(pool, "a", new AbortController().signal);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    try {
      const read = readHeld(new Request("https://egress/x", { method: "POST", body,
        headers: { "content-length": String(bytes.byteLength) } }), bytes.byteLength, hold,
        new AbortController().signal);
      feed.enqueue(bytes.slice(0, first));
      // Its second unit waits on the blocker, on the fake clock to just short of the pool's
      // wait; granted, the sender has its whole deadline again before the rest comes, so the
      // rest arrives past the sender's deadline counted from the first unit's grant.
      await until(() => pool.state().waiting === 1, "the second unit's wait");
      await vi.advanceTimersByTimeAsync(SLOT_WAIT_MS - 1);
      blocker.release();
      await until(() => hold.units === 2, "the second unit");
      await vi.advanceTimersByTimeAsync(SLOT_WAIT_MS - 1);
      feed.enqueue(bytes.slice(first));
      feed.close();
      expect((await read).bytes).toBe(bytes.byteLength);
    } finally {
      vi.useRealTimers();
      hold.release();
    }
  });

  it("refuses a body that cannot fit as soon as its weight passes the pool, not after it",
     async () => {
    const egress = new EgressGateway(createExecutionContext(), env);
    const blob = (await connect.seal("alice", C1, 1, "sk-fwd", OPENAI)).blob;
    // 4 MiB declared, of which 3.5 MiB of empty objects is sent (past the pool's weight by
    // its 2.6th MiB), then nothing.
    const head = '{"model":"m","input":[' + "{},".repeat(3.5 * 1024 * 1024 / 3);
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(head));
    } });
    const started = Date.now();
    const answer = await egress.forward("alice", C1, 1, blob, new Request(
      "https://egress/v1/responses", { method: "POST", body,
                                        headers: { "content-length": String(4 * 1024 * 1024) } }),
      granting(), new ReadableStream());
    expect([answer.response.status, await answer.response.json()]).toEqual(
      [413, { error: { code: "egress_too_large", message: "egress_too_large" } }]);
    expect(Date.now() - started).toBeLessThan(5_000);
    expect(gatewaySlots().state().held).toBe(0);
  });
});

describe("the isolate's pool under load, wait-die", () => {
  // On the fake clock: each call's reads and holds take their time on it, and the pool's
  // wait is its own; nothing here waits on the host. The pool is the gateway's own.
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** One call: its read grows a unit at a time to `read`, then it tops up to `weight`. */
  async function call(owner: string, read: number, weight: number,
                      holdMs: number): Promise<"done" | "busy"> {
    const hold = new Hold(gatewaySlots(), owner, new AbortController().signal);
    try {
      for (let units = 1; units <= read; units++) {
        await hold.grow(units);
        await sleep(20);
      }
      await hold.grow(weight);
      await sleep(holdMs);
      return "done";
    } catch (error) {
      if (error instanceof Refusal && error.code === "busy") {
        return "busy";
      }
      throw error;
    } finally {
      hold.release();
    }
  }

  it("lets a burst through without waiting out the pool's wait: the oldest grows, the others " +
     "give way", async () => {
    const pool = gatewaySlots();
    let ended = 0;
    const ends = Promise.all(["a", "b", "c", "d"].map((owner) =>
      call(owner, 4, 8, 50).finally(() => { ended++; })));
    // Every call ends before the pool's wait could refuse one.
    await vi.advanceTimersByTimeAsync(SLOT_WAIT_MS - 1);
    expect(ended).toBe(4);
    expect((await ends).filter((end) => end === "done").length).toBeGreaterThanOrEqual(1);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });

  it("keeps a closed loop of 8 owners at least as productive as one call at a time",
     async () => {
    const pool = gatewaySlots();
    const end = Date.now() + 4000;
    let done = 0;
    let busy = 0;
    const loops = Promise.all(Array.from({ length: 8 }, async (_, n) => {
      while (Date.now() < end) {
        if (await call(`o${n}`, 5, 9, 50) === "done") {
          done++;
        } else {
          busy++;
          await sleep(20);
        }
      }
    }));
    // Four seconds on the fake clock, then the calls still going end.
    await vi.advanceTimersByTimeAsync(4000);
    await vi.runAllTimersAsync();
    await loops;
    // One at a time (a read of about 0.1 s, a hold of 0.05 s): about 25 in 4 s.
    expect(done).toBeGreaterThanOrEqual(25);
    expect(pool.state()).toMatchObject({ held: 0, waiting: 0 });
  });
});
