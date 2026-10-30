/**
 * The agent's `gateway` provider (tasks 4 G2, 5 A1), in workerd: a real turn through the
 * gateway Worker run beside the tests, against the gateway's own fake upstream; `no_model`
 * with nothing connected; the live tee against pi's one-second commits; an abort reaching
 * the gateway; a provider's refusal settled at no cost; a budget refusal; a call with no
 * run; a dispatch or settle that cannot be kept; the envelope the gateway reads.
 */
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { getAttributionHeaders, mergeClientIdentity } from "@earendil-works/pi-ai";
import { DEFAULT_HARNESS_IDENTITY, LiveDoc, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { envelope } from "../../gateway/src/envelope";
import { IDENTITY } from "../src/agent";
import { NO_RUN } from "../src/origin";
import {
  AUTO_MODEL, envelopeHeader, GATEWAY_BASE_URL, outcomeOf,
} from "../src/gateway-provider";
import type { LiveEvent } from "../src/live-tee";
import { DEFAULT_POLICY_JSON } from "../src/policy";
import {
  ANTHROPIC, asked, catalog, direct, type Gated, gated, OPENAI, textOf, until,
} from "./gateway-support";
import type { GatedOwnerDO, Spawned } from "./worker";

const BG = BACKGROUND_CONTEXT;

/** The envelopes of the object's gateway fetches, as the gateway's own parser reads them. */
async function envelopes(r: Gated) {
  const calls = await r.in((instance) => instance.calls.filter((call) => call.method === "fetch"));
  return calls.map((call) => {
    const sent = JSON.parse(call.text) as { url: string; headers: Record<string, string>;
                                            body: string };
    return { url: sent.url, body: JSON.parse(sent.body) as { model: string },
             envelope: envelope(sent.headers["x-ta-envelope"] ?? null, Date.now()) };
  });
}

/** Write a models table into the owner's policy, in its session. */
async function models(r: Gated, table: Record<string, unknown>): Promise<void> {
  const { generation, policy } = await r.stub.policyRead(r.bind());
  await r.stub.policyWrite(r.session(), { expected_generation: generation,
                                          policy: { ...policy, models: table } });
}

describe("a turn through the gateway Worker", () => {
  it("runs on the owner's connected model, reserved, enveloped and settled", async () => {
    const r = await gated();
    const connection = await r.connect("sk-turn");
    const answer = await r.in(async (instance) => instance.agent.harness.prompt("cmd:echo"));
    expect(answer.status).toBe("done");
    // The fake upstream echoes what it was sent: the sealed key, in the provider's header.
    const echoed = JSON.parse(answer.text!) as { auth: Record<string, string | null>;
                                                 max: number; path: string };
    expect(echoed.auth["x-api-key"]).toBe("sk-turn");
    expect(echoed.path).toBe("/v1/messages");
    const [sent, ...more] = await envelopes(r);
    expect(more).toEqual([]);
    expect(sent!.url).toBe(`${GATEWAY_BASE_URL}/messages`);
    expect(sent!.envelope.binding).toEqual({ owner: r.name, connection, version: 1 });
    expect(sent!.envelope.policy_generation).toBe(1);
    // The model goes upstream as the provider's own id; the answer names the physical one.
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts).toHaveLength(1);
    const [receipt] = receipts;
    expect(sent!.body.model).toBe(receipt!.model);
    expect(sent!.envelope.reservation).toEqual({ id: receipt!.reservation,
                                                 max_output_tokens: receipt!.max_output_tokens });
    expect(echoed.max).toBe(receipt!.max_output_tokens);
    // Counted under the connection's source record, which its source ceilings are keyed by.
    expect(receipt).toMatchObject({ connection, source: "anthropic", state: "succeeded",
                                    tokens: 18 });
    expect(receipt!.cost).toBeGreaterThan(0);
    expect(receipt!.cost).toBeLessThanOrEqual(receipt!.est_cost);
    const transcript = await r.in(async (instance) => (await instance.agent.harness.messages())
      .flatMap((entry) => entry.model ?? []));
    const last = transcript.at(-1) as { provider: string; model: string };
    expect([last.provider, last.model]).toEqual(["gateway", `${connection}/${receipt!.model}`]);
  });

  it("settles a turn unanswered: no_model when nothing is connected", async () => {
    const r = await gated();
    const answer = await r.in(async (instance) => {
      expect(instance.parts.provider.getModels()).toEqual([]);
      return instance.agent.harness.prompt("hello");
    });
    expect([answer.status, answer.reason]).toEqual(["unanswered", "no_model"]);
    expect(await r.in((instance) => instance.calls)).toEqual([]);
  });

  it("lists auto with the strongest connected model's metadata, and each model", async () => {
    const r = await gated();
    const connection = await r.connect("sk-list");
    const listed = await r.in((instance) => instance.parts.provider.getModels());
    const [auto, strongest] = listed;
    expect(auto!.id).toBe(AUTO_MODEL);
    expect(strongest!.id.startsWith(`${connection}/`)).toBe(true);
    for (const field of ["contextWindow", "maxTokens", "cost", "reasoning", "input"] as const) {
      expect(auto![field]).toEqual(strongest![field]);
    }
    expect(listed.every((model) => model.api === "pi-messages"
                        && model.provider === "gateway"
                        && model.baseUrl === GATEWAY_BASE_URL)).toBe(true);
  });

  it("names TinyAssets to providers, not pi", () => {
    const merged = mergeClientIdentity(DEFAULT_HARNESS_IDENTITY, IDENTITY);
    const headers = getAttributionHeaders(
      { provider: "openrouter", baseUrl: "https://openrouter.ai/api/v1" }, merged);
    expect(JSON.stringify(headers)).toContain("tinyassets.io");
    expect(JSON.stringify(headers).toLowerCase()).not.toContain("pi.dev");
  });
});

describe("the live tee", () => {
  it("gets every delta as it streams while pi commits the answer once a second", async () => {
    const r = await gated();
    await r.connect("sk-tee");
    const out = await r.in(async (instance) => {
      const pi = await instance.agent.harness.pi();
      const root = await pi.root(BG);
      const { sessionId } = (await pi.snapshot(ProviderDoc, root.id, BG))!;
      const seen: LiveEvent[] = [];
      const unsubscribe = instance.parts.tee.subscribe(sessionId, (event) => seen.push(event));
      const committed = new Set<string>();
      let streaming = true;
      const watching = (async () => {
        while (streaming) {
          const live = await pi.snapshot(LiveDoc, root.id, BG);
          const partial = live?.generation?.message;
          if (partial !== undefined) {
            committed.add(JSON.stringify(partial.content));
          }
          await new Promise((resolve) => setTimeout(resolve, 25));
        }
      })();
      const answer = await instance.agent.harness.prompt("cmd:drip");
      streaming = false;
      await watching;
      unsubscribe();
      return { answer, seen, committed: committed.size };
    });
    expect(out.answer.status).toBe("done");
    const deltas = out.seen.filter((event) => event.type === "text_delta");
    expect(deltas.map((event) => event.delta).join("")).toBe(out.answer.text);
    expect(deltas.length).toBeGreaterThanOrEqual(12);
    expect(out.seen.at(-1)).toEqual({ type: "done", reason: "stop" });
    // 1.8 s of streaming: pi committed its partial at most once a second.
    expect(out.committed).toBeGreaterThanOrEqual(1);
    expect(out.committed).toBeLessThanOrEqual(3);
    expect(out.committed).toBeLessThan(deltas.length);
  });
});

describe("an abort", () => {
  it("reaches the gateway, which cancels the upstream; the call settles unknown", async () => {
    const r = await gated();
    await r.connect("sk-abort");
    const id = crypto.randomUUID().replace(/-/g, "").slice(0, 12);
    const cancelled = async () => ((await (await fetch(`https://status.test/cancelled?id=${id}`))
      .json()) as { cancelled: boolean | null }).cancelled;
    const settled = await r.in(async (instance: GatedOwnerDO) => {
      const { operationId } = await instance.agent.harness.submit(`cmd:slow-${id}`);
      await until(async () => (await cancelled()) === false, "the upstream stream to open");
      expect(await instance.agent.harness.abort({ operationId })).toBe(true);
      return instance.agent.harness.wait(operationId);
    });
    expect(settled.status).not.toBe("done");
    await until(async () => (await cancelled()) === true, "the upstream to be cancelled");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => receipt.state)).toEqual(["unknown"]);
  });
});

/** Hold the queue every unit waits on until `release` is called. */
function holdQueue(state: DurableObjectState): { release: () => void; done: Promise<void> } {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const done = new DurableObjectSqliteDatabase(state.storage).transaction(async () => {
    await held;
  });
  return { release, done };
}

describe("a call never sent", () => {
  it("is not sent once aborted before its dispatch", async () => {
    const r = await gated();
    await r.connect("sk-aborted");
    const answer = await r.in(async (instance) => {
      const auto = instance.parts.provider.getModels()[0]!;
      const stop = new AbortController();
      stop.abort();
      return instance.parts.provider.streamSimple(auto, asked("cmd:echo"),
                                                  { ...await direct(instance), signal: stop.signal })
        .result();
    });
    expect([answer.stopReason, answer.errorMessage])
      .toEqual(["aborted", "model_call_not_sent: aborted"]);
    expect(await envelopes(r)).toEqual([]);
    expect((await r.stub.usageReceipts(r.bind())).receipts.map((receipt) =>
      [receipt.state, receipt.rule])).toEqual([["not_sent", "aborted"]]);
  });

  it("is not sent when its connection is revoked between its reserve and its dispatch",
     async () => {
    const r = await gated();
    const connection = await r.connect("sk-raced");
    const answer = await r.in(async (instance, state) => {
      const auto = instance.parts.provider.getModels()[0]!;
      const options = await direct(instance);
      const queue = holdQueue(state);
      const call = instance.parts.provider.streamSimple(auto, asked("cmd:echo"), options)
        .result();
      for (let i = 0; i < 50; i++) {
        await Promise.resolve();
      }
      // The call's reserve is queued; the revoke lands behind it, before the dispatch.
      const revoked = instance.connectionRevoke(r.session(), { connection });
      queue.release();
      await queue.done;
      await revoked;
      return call;
    });
    expect(answer.errorMessage).toBe("model_call_not_sent: connection_changed");
    expect(await envelopes(r)).toEqual([]);
  });

  it("is refused when an OAuth connection is revoked before its reserve, its blob kept " +
     "only for the provider revoke", async () => {
    const r = await gated();
    const connection = await r.connectOAuth();
    const answer = await r.in(async (instance, state) => {
      const auto = instance.parts.provider.getModels()[0]!;
      const options = await direct(instance);
      const queue = holdQueue(state);
      const revoked = instance.connectionRevoke(r.session(), { connection });
      const call = instance.parts.provider.streamSimple(auto, asked("cmd:echo"), options)
        .result();
      queue.release();
      await queue.done;
      await revoked;
      return call;
    });
    expect(answer.errorMessage).toBe("connection_unavailable");
    expect(await envelopes(r)).toEqual([]);
    // The blob was kept for the provider revoke, which the lane may have sent already.
    expect((await r.stub.connectionList(r.session())).connections[0])
      .toMatchObject({ status: "revoked" });
  });

  it("settles failed, holding nothing, when the gateway refuses it before the provider",
     async () => {
    const r = await gated();
    const models = await catalog(ANTHROPIC);
    const missing = { ...models[0]!, id: "no-such-model", name: "None" };
    const connection = await r.connect("sk-missing", ANTHROPIC, "Key", [missing]);
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels().find((model) =>
        model.id === `${connection}/no-such-model`)!, asked("hello"),
          await direct(instance)).result());
    expect(answer.stopReason).toBe("error");
    expect(answer.errorMessage).toBe("unknown_model");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.cost])).toEqual([["failed", 0]]);
    expect(await r.sql("SELECT SUM(cost) AS cost FROM model_spend WHERE period LIKE 'd:%'"))
      .toEqual([{ cost: 0 }]);
  });
});

describe("the reservation's output cap", () => {
  it("is what the gateway clamps the call to, under the model's own limit", async () => {
    const r = await gated();
    await r.connect("sk-cap");
    await models(r, { turn_cost: 200_000 });
    const answer = await r.in((instance) => instance.agent.harness.prompt("cmd:echo"));
    expect(answer.status).toBe("done");
    const { receipts: [receipt] } = await r.stub.usageReceipts(r.bind());
    const [sent] = await envelopes(r);
    const listed = await r.in((instance) => instance.parts.provider.getModels()[0]!.maxTokens);
    expect(receipt!.max_output_tokens).toBeLessThan(listed);
    expect(sent!.envelope.reservation.max_output_tokens).toBe(receipt!.max_output_tokens);
    expect((JSON.parse(answer.text!) as { max: number }).max).toBe(receipt!.max_output_tokens);
  });
});

describe("the owner's model budget", () => {
  it("refuses a call the caps leave no room for, as the turn's error, never sent", async () => {
    const r = await gated();
    await r.connect("sk-budget");
    await models(r, { turn_cost: 0 });
    const answer = await r.in((instance) => instance.agent.harness.prompt("hello"));
    expect([answer.status, answer.reason]).toEqual(["unanswered", "model_error"]);
    const failed = answer.messages.flatMap((entry) => entry.model ?? []).at(-1) as
      { role: string; stopReason: string; errorMessage: string };
    expect([failed.role, failed.stopReason, failed.errorMessage])
      .toEqual(["assistant", "error", "model_budget: turn_cost"]);
    expect(await envelopes(r)).toEqual([]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.rule]))
      .toEqual([["refused", "turn_cost"]]);
  });

  it("feeds pi the retry count from owner policy, re-read when the policy moves", async () => {
    const r = await gated();
    expect(await r.in((instance) => instance.parts.policy())).toEqual({});
    await models(r, { retries: 0 });
    expect(await r.in((instance) => instance.parts.policy()))
      .toEqual({ retry: { maxRetries: 0 } });
    await models(r, { retries: 2 });
    expect(await r.in((instance) => instance.parts.policy()))
      .toEqual({ retry: { maxRetries: 2 } });
    expect(JSON.parse(DEFAULT_POLICY_JSON)).not.toHaveProperty("models");
  });
});

describe("a provider's refusal before any output", () => {
  it("settles failed, at no cost, with the gateway's own code", async () => {
    const r = await gated();
    await r.connect("sk-429");
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:fail-429"), await direct(instance))
      .result());
    expect([answer.stopReason, answer.errorMessage]).toEqual(["error", "upstream_rate_limited"]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.cost])).toEqual([["failed", 0]]);
    expect(await r.sql("SELECT SUM(cost) AS cost FROM model_spend WHERE period LIKE 'd:%'"))
      .toEqual([{ cost: 0 }]);
  });

  it("is retried by pi as a rate limit, and its retries never spend a daily ceiling",
     async () => {
    const r = await gated();
    await r.connect("sk-429b");
    await models(r, { daily_cost: 20_000_000, retries: 2 });
    const answer = await r.in((instance) => instance.agent.harness.prompt("cmd:fail-429"));
    expect(answer.status).toBe("unanswered");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.cost]))
      .toEqual(Array(3).fill(["failed", 0]));
    expect(await r.sql("SELECT SUM(cost) AS cost FROM model_spend WHERE period LIKE 'd:%'"))
      .toEqual([{ cost: 0 }]);
  }, 60_000);

  // pi-ai decides by its own reading of the code (`isRetryableAssistantError`): each try
  // before any output settles failed, at no cost.
  it.each([
    [503, "upstream_overloaded", 2], [529, "upstream_overloaded", 2],
    [500, "upstream_server_error", 2], [502, "upstream_server_error", 2],
    [408, "upstream_timeout", 2], [429, "upstream_rate_limited", 2],
    [400, "upstream_rejected", 1], [401, "upstream_unauthorized", 1],
    [403, "upstream_unauthorized", 1], [404, "upstream_rejected", 1],
  ])("answers a %d as %s, tried %d times with one retry allowed", async (status, code, tries) => {
    const r = await gated();
    await r.connect(`sk-${status}`);
    await models(r, { retries: 1 });
    const answer = await r.in((instance) => instance.agent.harness.prompt(`cmd:fail-${status}`));
    expect(answer.status).toBe("unanswered");
    const failed = answer.messages.flatMap((entry) => entry.model ?? []).at(-1) as
      { errorMessage: string };
    expect(failed.errorMessage).toBe(code);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => [receipt.state, receipt.cost]))
      .toEqual(Array(tries).fill(["failed", 0]));
  }, 30_000);
});

describe("a call's outcome", () => {
  const usage = { input: 1, output: 2, cacheRead: 0, cacheWrite: 0, totalTokens: 3,
                  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0.000_012 } };
  const message = { role: "assistant" as const, content: [], api: "pi-messages",
                    provider: "gateway", model: "m", usage, stopReason: "stop" as const,
                    timestamp: 1 };

  it("is unknown for a stream lost without its terminal event, whatever the status", () => {
    expect(outcomeOf(undefined, {})).toEqual({ state: "unknown" });
    expect(outcomeOf(undefined, { status: 200 })).toEqual({ state: "unknown" });
    expect(outcomeOf({ type: "error", reason: "error",
                       error: { ...message, stopReason: "error" } }, { status: 200 }))
      .toEqual({ state: "unknown" });
  });

  it("is failed for a refusal's status, and succeeded with what a done used", () => {
    expect(outcomeOf(undefined, { status: 502 })).toEqual({ state: "failed" });
    expect(outcomeOf({ type: "done", reason: "stop", message }, { status: 200 }))
      .toEqual({ state: "succeeded", tokens: 3, cost: 12 });
  });
});

describe("background work a run starts", () => {
  it("counts against that run's caps, which refuse it, and never against a later run",
     async () => {
    const r = await gated();
    await r.connect("sk-background");
    await models(r, { turn_requests: 3 });
    const out = await r.in(async (instance) => {
      let release!: () => void;
      instance.probes.backgroundGate = new Promise<void>((resolve) => { release = resolve; });
      // The run calls the `background` tool through `ta`, then answers: two requests.
      const first = await instance.agent.harness.prompt("cmd:ta-background");
      const later = await instance.agent.harness.prompt("cmd:echo");
      // Its work, two requests more, starts only now: during no run.
      release();
      const pi = await instance.agent.harness.pi();
      const task = await pi.waitForTask(instance.probes.background as never, BG);
      const made = (task.state.outcome as { result: Spawned }).result;
      const work = await (await pi.submission(made.submission as never, BG))!.wait(BG);
      return { first: first.status, later: later.status, work: work.status };
    });
    expect(out).toEqual({ first: "done", later: "done", work: "unanswered" });
    const rows = await r.sql("SELECT turn, state, rule FROM model_reservations ORDER BY seq");
    const [spawning, , later] = rows.map((row) => row["turn"]);
    expect(later).not.toBe(spawning);
    expect(rows.map((row) => [row["turn"] === spawning, row["state"], row["rule"]])).toEqual([
      [true, "succeeded", null], [true, "succeeded", null], [false, "succeeded", null],
      // The work's first request is the spawning run's third; its second passes the cap.
      [true, "succeeded", null], [true, "refused", "turn_requests"]]);
    expect(await r.sql("SELECT turn, requests FROM model_turns ORDER BY requests"))
      .toEqual([{ turn: later, requests: 1 }, { turn: spawning, requests: 3 }]);
  }, 30_000);
});

describe("a call with no run", () => {
  it("is refused no_run, nothing reserved and nothing sent", async () => {
    const r = await gated();
    await r.connect("sk-norun");
    const answers = await r.in(async (instance) => {
      const auto = instance.parts.provider.getModels()[0]!;
      return [await instance.parts.provider.streamSimple(auto, asked("cmd:echo"),
                                                         { sessionId: "unknown" }).result(),
              await instance.parts.provider.streamSimple(auto, asked("cmd:echo")).result()];
    });
    expect(answers.map((answer) => answer.errorMessage)).toEqual([NO_RUN, NO_RUN]);
    expect(await r.sql("SELECT id FROM model_reservations")).toEqual([]);
    expect(await envelopes(r)).toEqual([]);
  });
});

describe("a call whose own unit cannot be kept", () => {
  it("is abandoned unknown, its answer still given, when its settle throws", async () => {
    const r = await gated();
    await r.connect("sk-settle");
    await r.sql("CREATE TRIGGER fail_settle BEFORE UPDATE OF tokens ON model_reservations " +
                "WHEN NEW.state = 'succeeded' BEGIN SELECT RAISE(ABORT, 'injected'); END");
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:echo"), await direct(instance)).result());
    expect(answer.stopReason).toBe("stop");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts.map((receipt) => receipt.state)).toEqual(["unknown"]);
    expect(receipts[0]!.est_cost).toBeGreaterThan(0);
  });

  it("is abandoned not sent, and never sent, when its dispatch throws", async () => {
    const r = await gated();
    await r.connect("sk-dispatch");
    await r.sql("CREATE TRIGGER fail_dispatch BEFORE UPDATE OF state ON model_reservations " +
                "WHEN NEW.state = 'dispatched' BEGIN SELECT RAISE(ABORT, 'injected'); END");
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:echo"), await direct(instance)).result());
    expect(answer.stopReason).toBe("error");
    expect(await envelopes(r)).toEqual([]);
    expect((await r.stub.usageReceipts(r.bind())).receipts.map((receipt) => receipt.state))
      .toEqual(["not_sent"]);
  });
});

describe("the envelope", () => {
  it("is what the gateway's own parser reads, an owner of any printable name", () => {
    const now = Date.now();
    const header = envelopeHeader({
      owner: "Ålice 🌍", connection: "c0123456789abcdef01234567", version: 3,
      blob: "v1.k2.AAAA.BBBB", reservation: { id: "r0123", max_output_tokens: 4096 },
      policy_generation: 7, deadline: now + 60_000 });
    expect(envelope(header, now)).toEqual({
      binding: { owner: "Ålice 🌍", connection: "c0123456789abcdef01234567", version: 3 },
      blob: "v1.k2.AAAA.BBBB", reservation: { id: "r0123", max_output_tokens: 4096 },
      policy_generation: 7, deadline: now + 60_000 });
  });

  it("carries the owner's object name and the policy generation of its reservation",
     async () => {
    const r = await gated();
    await r.connect("sk-generation", OPENAI);
    await models(r, { turn_requests: 50 });
    const answer = await r.in(async (instance) => {
      const auto = instance.parts.provider.getModels()[0]!;
      return instance.parts.provider.streamSimple(auto, asked("cmd:echo"), await direct(instance))
        .result();
    });
    expect(answer.stopReason).toBe("stop");
    expect(textOf(answer)).toContain("Bearer sk-generation");
    const [sent] = await envelopes(r);
    expect(sent!.envelope.binding.owner).toBe(r.name);
    expect(sent!.envelope.policy_generation).toBe(2);
    expect(env.TEST_KEYRING).not.toContain("sk-generation");
  });
});
