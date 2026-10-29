/**
 * The agent's `gateway` provider (tasks 4 G2, 5 A1), in workerd: a real turn through the
 * gateway Worker run beside the tests, against the gateway's own fake upstream; `no_model`
 * with nothing connected; the live tee against pi's one-second commits; an abort reaching
 * the gateway; a budget refusal; the envelope the gateway reads.
 */
import { env } from "cloudflare:workers";
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import { DurableObjectSqliteDatabase } from "@earendil-works/chord/sqlite/cloudflare";
import { getAttributionHeaders, mergeClientIdentity } from "@earendil-works/pi-ai";
import { DEFAULT_HARNESS_IDENTITY, LiveDoc, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { envelope } from "../../gateway/src/envelope";
import { IDENTITY } from "../src/agent";
import { AUTO_MODEL, envelopeHeader, GATEWAY_BASE_URL, turnOf } from "../src/gateway-provider";
import type { LiveEvent } from "../src/live-tee";
import { DEFAULT_POLICY_JSON } from "../src/policy";
import {
  ANTHROPIC, asked, catalog, type Gated, gated, OPENAI, textOf, until,
} from "./gateway-support";
import type { GatedOwnerDO } from "./worker";

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
    expect(receipt).toMatchObject({ connection, state: "succeeded", tokens: 18 });
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
                                                  { signal: stop.signal }).result();
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
      const queue = holdQueue(state);
      const call = instance.parts.provider.streamSimple(auto, asked("cmd:echo")).result();
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
      const queue = holdQueue(state);
      const revoked = instance.connectionRevoke(r.session(), { connection });
      const call = instance.parts.provider.streamSimple(auto, asked("cmd:echo")).result();
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
    const answer = await r.in((instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels().find((model) =>
        model.id === `${connection}/no-such-model`)!, asked("hello")).result());
    expect(answer.stopReason).toBe("error");
    expect(answer.errorMessage).toContain("unknown_model");
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

describe("a turn's key", () => {
  it("is the conversation and its newest user message's time", () => {
    const at = (text: string, timestamp: number) => ({ role: "user" as const, content: text,
                                                       timestamp });
    const reply = { role: "toolResult" as const, toolCallId: "t", toolName: "read",
                    content: [], isError: false, timestamp: 9 };
    expect(turnOf("s1", [at("a", 5)])).toBe("s1:5");
    expect(turnOf("s1", [at("a", 5), reply])).toBe("s1:5");
    expect(turnOf("s1", [at("a", 5), reply, at("b", 7)])).toBe("s1:7");
    expect(turnOf("s2", [at("a", 5)])).toBe("s2:5");
    expect(turnOf(undefined, [at("a", 5)])).toBe("direct:5");
    expect(turnOf("s1", [])).toBe("s1:0");
    expect(turnOf("x".repeat(400), [at("a", 1)]).length).toBe(256 + 2);
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
      return instance.parts.provider.streamSimple(auto, asked("cmd:echo")).result();
    });
    expect(answer.stopReason).toBe("stop");
    expect(textOf(answer)).toContain("Bearer sk-generation");
    const [sent] = await envelopes(r);
    expect(sent!.envelope.binding.owner).toBe(r.name);
    expect(sent!.envelope.policy_generation).toBe(2);
    expect(env.TEST_KEYRING).not.toContain("sk-generation");
  });
});
