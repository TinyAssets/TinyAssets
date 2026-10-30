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
import {
  AI_TELEMETRY_SCHEMA, type Api, getAttributionHeaders, mergeClientIdentity, type Model,
  normalizeContext, type TranscriptContext,
} from "@earendil-works/pi-ai";
import { getSupportedThinkingLevels } from "@earendil-works/pi-ai/models";
import { estimateContextTokens } from "@earendil-works/pi-ai/utils/estimate";
import { DEFAULT_HARNESS_IDENTITY, LiveDoc, ProviderDoc } from "@earendil-works/pi-durable";
import { describe, expect, it } from "vitest";

import { envelope } from "../../gateway/src/envelope";
import { IDENTITY } from "../src/agent";
import { blockingThreshold, compactionFor, ranked } from "../src/auto";
import type { ModelInfo } from "../src/connections";
import { NO_RUN } from "../src/origin";
import {
  AUTO_MODEL, envelopeHeader, GATEWAY_BASE_URL, outcomeOf,
} from "../src/gateway-provider";
import type { LiveEvent } from "../src/live-tee";
import { DEFAULT_POLICY_JSON } from "../src/policy";
import type { Source } from "../../gateway/src/credential";
import {
  ANTHROPIC, asked, catalog, direct, type Gated, gated, OPENAI, textOf, until,
} from "./gateway-support";
import { MUSE } from "./ledger-support";
import { failure } from "./support";
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

/** A random id the fake's `cmd:overflow-<id>` takes. */
function freshId(): string {
  return crypto.randomUUID().replaceAll("-", "").slice(0, 16);
}

/**
 * Two of the source's catalog models, the stronger first by `auto`'s ranking, each with
 * the context window and output limit given.
 */
async function pair(windows: readonly [number, number]): Promise<[ModelInfo, ModelInfo]> {
  const models = (await catalog(ANTHROPIC)).slice(0, 2);
  const [stronger, weaker] = ranked([{ connection: "c", source: "anthropic",
                                       provider: "anthropic", display_name: "c", created_at: 0,
                                       models }]).map((candidate) => candidate.model);
  return [{ ...stronger!, contextWindow: windows[0], maxTokens: 4_000 },
          { ...weaker!, contextWindow: windows[1], maxTokens: 4_000 }];
}

/** Connect a pasted key from inside the object, while a turn runs there. */
async function connectWithin(instance: GatedOwnerDO, r: Gated, key: string,
                             models: ModelInfo[]): Promise<string> {
  const prepared = await instance.connectionPrepare(r.session(), {
    source: "anthropic", kind: "api_key", display_name: "Within", provider: "anthropic",
    revocable: false });
  const sealed = await env.GATEWAY.seal(r.name, prepared.connection, 1, key, ANTHROPIC);
  await instance.connectionCommit(r.session(), { connection: prepared.connection, version: 1,
                                                 blob: sealed.blob, kid: sealed.kid, models });
  return prepared.connection;
}

/** Text of about `tokens` estimated tokens (pi-ai estimates 3.5 characters a token). */
function sized(tokens: number): string {
  return "word ".repeat(Math.ceil(tokens * 3.5 / 5));
}

describe("a context too long for its model", () => {
  it("is compacted by pi and retried once when the provider refuses it through the gateway, " +
     "and the turn succeeds", async () => {
    const r = await gated();
    await r.connect("sk-overflow");
    const id = freshId();
    const out = await r.in(async (instance) => {
      const pi = instance.agent.harness;
      await pi.prompt("cmd:echo");
      // Past the 20,000 recent tokens a compaction keeps verbatim, so the first exchange
      // can be summarized.
      await pi.prompt(`${sized(24_000)} cmd:echo`);
      const answer = await pi.prompt(`cmd:overflow-${id}`);
      const entries = await pi.messages();
      return { answer, kinds: entries.map((entry) => entry.kind),
               failed: entries.flatMap((entry) => entry.model ?? [])
                 .filter((message) => message.role === "assistant"
                         && message.stopReason === "error")
                 .map((message) => (message as { errorMessage?: string }).errorMessage) };
    });
    expect(out.answer.status).toBe("done");
    expect(out.failed).toEqual(["context_length_exceeded"]);
    expect(out.kinds).toContain("pi.compaction");
    const { receipts } = await r.stub.usageReceipts(r.bind());
    // Newest first: the retry, the summary, the refusal (at no cost) and the two before.
    expect(receipts.map((receipt) => [receipt.state, receipt.state === "failed" ? receipt.cost
      : null])).toEqual([["succeeded", null], ["succeeded", null], ["failed", 0],
                         ["succeeded", null], ["succeeded", null]]);
  }, 60_000);

  it("is held to a smaller target's own threshold when auto continues there: pi compacts, " +
     "and the retry stays on it", async () => {
    const r = await gated();
    const [strong, small] = await pair([1_000_000, 64_000]);
    // The smaller is the strongest connected when the turn starts; the stronger is
    // connected while its tool runs, so the turn's continuation stays on the smaller.
    const smallConnection = await r.connect("sk-small", ANTHROPIC, "Small", [small]);
    const target = `${smallConnection}/${small.id}`;
    expect(blockingThreshold(small)).toBe(56_000);
    const out = await r.in(async (instance) => {
      let release!: () => void;
      instance.probes.holdGate = new Promise((resolve) => { release = resolve; });
      // Two tool rounds of about 14,000 tokens each (under pi's cut of a tool's output).
      // With the 30,000 and more the first answer was billed for, the first round's
      // continuation fits the smaller's threshold; the second's passes it, far inside the
      // stronger's. A compaction keeps 20,000 recent tokens, so it cuts before the second.
      instance.probes.holdOutputs = [`${sized(14_000)} cmd:measured-hold`,
                                     `${sized(14_000)} cmd:echo`];
      const { operationId } = await instance.agent.harness.submit(
        `${sized(30_000)} cmd:measured-hold`);
      await until(async () => instance.probes.holds === 1, "the hold to start");
      await connectWithin(instance, r, "sk-strong", [strong]);
      expect(instance.parts.provider.getModels()[1]!.id).not.toBe(target);
      release();
      const answer = await instance.agent.harness.wait(operationId);
      const entries = await instance.agent.harness.messages();
      return { answer, kinds: entries.map((entry) => entry.kind),
               answers: entries.flatMap((entry) => entry.model ?? [])
                 .filter((message) => message.role === "assistant")
                 .map((message) => [(message as { model: string }).model,
                                    (message as { errorMessage?: string }).errorMessage ?? null]) };
    });
    expect(out.answer.status).toBe("done");
    expect(out.kinds).toContain("pi.compaction");
    // Kept past the summary: the second tool call, the refusal at the smaller's threshold,
    // and the retry, all on it.
    expect(out.answers).toEqual([[target, null], [target, "context_length_exceeded"],
                                 [target, null]]);
    // The smaller was sent the two tool rounds and the retry, each under its threshold;
    // the stronger, the summary.
    const sent = await envelopes(r);
    expect(sent.map((call) => call.envelope.binding.connection === smallConnection))
      .toEqual([true, true, false, true]);
  }, 60_000);

  it("is refused at the routed target's threshold and not below it, nothing reserved or sent",
     async () => {
    const r = await gated();
    const [strong, small] = await pair([1_000_000, 64_000]);
    const smallConnection = await r.connect("sk-small2", ANTHROPIC, "Small", [small]);
    const target = `${smallConnection}/${small.id}`;
    await r.connect("sk-strong2", ANTHROPIC, "Strong", [strong]);
    /** A continuation on the smaller whose context is estimated at `tokens`. */
    const continuing = (tokens: number) => {
      const tail = (pad: number): TranscriptContext => normalizeContext({ messages: [
        { role: "user", content: "x".repeat(pad), timestamp: 1 },
        { role: "assistant", content: [{ type: "toolCall", id: "t1", name: "read",
                                         arguments: {} }],
          api: "pi-messages", provider: "gateway", model: target, stopReason: "toolUse",
          usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                   cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
          timestamp: 2 },
        { role: "toolResult", toolCallId: "t1", toolName: "read",
          content: [{ type: "text", text: "cmd:echo" }], isError: false, timestamp: 3 }] });
      let pad = Math.floor(tokens * 3.5) - 64;
      while (estimateContextTokens(tail(pad)).tokens < tokens) {
        pad += 1;
      }
      expect(estimateContextTokens(tail(pad)).tokens).toBe(tokens);
      return tail(pad);
    };
    const threshold = blockingThreshold(small);
    const answers = await r.in(async (instance) => {
      const [auto] = instance.parts.provider.getModels();
      const call = async (context: TranscriptContext, model = auto!) => instance.parts.provider
        .streamSimple(model, context, await direct(instance)).result();
      const physical = instance.parts.provider.getModels().find((model) => model.id === target)!;
      return [await call(continuing(threshold + 1)), await call(continuing(threshold)),
              // The smaller named directly is pi's to hold to its own threshold.
              await call(continuing(threshold + 1), physical)];
    });
    expect(answers.map((answer) => [answer.model, answer.errorMessage ?? null])).toEqual([
      [target, "context_length_exceeded"], [target, null], [target, null]]);
    const { receipts } = await r.stub.usageReceipts(r.bind());
    expect(receipts).toHaveLength(2);
    expect(await envelopes(r)).toHaveLength(2);
  });

  it.each([[80_000, 70_000], [64_000, 56_000]])(
    "is not refused on a routed target whose threshold is no lower than the strongest's, " +
    "which pi already held it to (a window of %d)", async (window, threshold) => {
    const r = await gated();
    // The stronger by rank has the smaller window, or the same: `auto` mirrors it, so
    // pi's threshold is already as low.
    const [strong, weak] = await pair([64_000, window]);
    await r.connect("sk-low", ANTHROPIC, "Strong", [strong]);
    const weakConnection = await r.connect("sk-high", ANTHROPIC, "Weak", [weak]);
    const target = `${weakConnection}/${weak.id}`;
    const answer = await r.in(async (instance) => {
      const [auto] = instance.parts.provider.getModels();
      const context = normalizeContext({ messages: [
        { role: "user", content: `${"x".repeat(75_000 * 4)} cmd:echo`, timestamp: 1 },
        { role: "assistant", content: [{ type: "text", text: "ok" }], api: "pi-messages",
          provider: "gateway", model: target, stopReason: "error", errorMessage: "context_length_exceeded",
          usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                   cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
          timestamp: 2 }] });
      return instance.parts.provider.streamSimple(auto!, context, await direct(instance)).result();
    });
    // Past the routed target's own threshold, but not refused: sent, and answered.
    expect(blockingThreshold(weak)).toBe(threshold);
    expect([answer.model, answer.errorMessage ?? null]).toEqual([target, null]);
  });
});

describe("a connected model's whole record", () => {
  it("offers xhigh and max only for a model whose thinking map has them, and auto the " +
     "strongest's", async () => {
    const r = await gated();
    const records = await catalog(ANTHROPIC);
    const mapped = records.find((model) => model.thinkingLevelMap?.xhigh !== undefined
                                && model.thinkingLevelMap.max !== undefined)!;
    const { thinkingLevelMap: _map, ...plain } =
      records.find((model) => model.reasoning && model.id !== mapped.id)!;
    const connection = await r.connect("sk-levels", ANTHROPIC, "Key", [mapped, plain]);
    const listed = await r.in((instance) => instance.parts.provider.getModels());
    const levels = (id: string) => getSupportedThinkingLevels(
      listed.find((model) => model.id === id)! as Model<Api>);
    expect(levels(`${connection}/${mapped.id}`)).toEqual(expect.arrayContaining(["xhigh", "max"]));
    expect(levels(`${connection}/${plain.id}`)).toEqual(["off", "minimal", "low", "medium", "high"]);
    const strongest = listed[1]!;
    expect(levels(AUTO_MODEL)).toEqual(levels(strongest.id));
    // The rest of the record rides with it: input limits for `read`, the cache lifetimes.
    const whole = listed.find((model) => model.id === `${connection}/${mapped.id}`)!;
    expect(whole.inputLimits).toEqual(mapped.inputLimits);
    expect(whole.promptCache).toEqual(mapped.promptCache);
    expect(whole.thinkingLevelMap).toEqual(mapped.thinkingLevelMap);
  });

  it("reserves a call at its long-context tier by pi-ai's calculateCost, and settles what " +
     "the gateway's pi-ai adapter priced at that tier", async () => {
    const r = await gated();
    // A declared endpoint, so the gateway's own record carries the tier too.
    const tiered = {
      id: "tiered-1", name: "Tiered", reasoning: false, input: ["text"] as ("text")[],
      contextWindow: 200_000, maxTokens: 4_000,
      cost: { input: 3, output: 15, cacheRead: 0.3, cacheWrite: 3.75,
              tiers: [{ inputTokensAbove: 10, input: 6, output: 30, cacheRead: 0.6,
                        cacheWrite: 7.5 }] } };
    const declared: Source = { upstream: { ...ANTHROPIC.upstream, provider: "custom",
                                           models: [tiered] },
                               endpoints: ANTHROPIC.endpoints };
    const listed = await catalog(declared);
    expect(listed).toEqual([tiered]);
    await r.connect("sk-tier", declared, "Key", listed);
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels()[0]!, asked(`${"a".repeat(350)} cmd:echo`),
      await direct(instance)).result());
    expect(answer.stopReason).toBe("stop");
    const { receipts: [receipt] } = await r.stub.usageReceipts(r.bind());
    // The fake bills 11 input tokens, past the tier's 10, and 7 output: the tier's rates
    // for the whole request, 11 at 6 and 7 at 30, not the base 11 at 3 and 7 at 15.
    expect(Math.abs(receipt!.cost! - (11 * 6 + 7 * 30))).toBeLessThanOrEqual(1);
    expect(receipt!.cost).toBe(Math.ceil(answer.usage.cost.total * 1_000_000));
    // The estimate: its input past the tier too, and the reserved output at the tier's 30.
    const estimated = receipt!.est_tokens - receipt!.max_output_tokens;
    expect(estimated).toBeGreaterThan(10);
    expect(Math.abs(receipt!.est_cost - (estimated * 6 + receipt!.max_output_tokens * 30)))
      .toBeLessThanOrEqual(1);
  });

  it("settles what the gateway's pi-ai adapter priced, not the record stored at connect " +
     "time", async () => {
    const r = await gated();
    const [first] = await catalog(ANTHROPIC);
    // The record as stored before a pi-ai upgrade changed the prices.
    const stale = { ...first!, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
    await r.connect("sk-stale", ANTHROPIC, "Key", [stale]);
    const answer = await r.in(async (instance) => instance.parts.provider.streamSimple(
      instance.parts.provider.getModels()[0]!, asked("cmd:echo"), await direct(instance))
      .result());
    expect(answer.stopReason).toBe("stop");
    const { receipts: [receipt] } = await r.stub.usageReceipts(r.bind());
    // The stored record estimated nothing; the gateway priced 11 input and 7 output tokens
    // by its own catalog.
    expect(receipt!.est_cost).toBe(0);
    expect(Math.abs(receipt!.cost! - (11 * first!.cost.input + 7 * first!.cost.output)))
      .toBeLessThanOrEqual(1);
    expect(receipt!.cost).toBeGreaterThan(0);
  });

  it("gives pi each listed model's own compaction thresholds, auto's the strongest's",
     async () => {
    const r = await gated();
    const [strong, small] = await pair([1_000_000, 32_000]);
    const connection = await r.connect("sk-thresholds", ANTHROPIC, "Key", [strong, small]);
    const compaction = await r.in((instance) => instance.parts.provider.compaction());
    expect(compaction).toEqual({ modelOverrides: {
      "gateway/auto": compactionFor(strong),
      [`gateway/${connection}/${strong.id}`]: compactionFor(strong),
      [`gateway/${connection}/${small.id}`]: { reserveTokens: 4_000, keepRecentTokens: 8_000,
                                               backgroundTokens: 4_000 },
    } });
    expect(compactionFor(strong)).toEqual({ reserveTokens: 16_384, keepRecentTokens: 20_000,
                                            backgroundTokens: 32_768 });
    // A connection made later is in pi's next read.
    const later = await r.connect("sk-later", ANTHROPIC, "Later", [small]);
    expect(Object.keys((await r.in((instance) => instance.parts.provider.compaction()))
      .modelOverrides!)).toContain(`gateway/${later}/${small.id}`);
  });

  it("is compacted at its own thresholds, which pi reads, not at pi's defaults", async () => {
    const r = await gated();
    const [only] = await pair([40_000, 40_000]);
    await r.connect("sk-own", ANTHROPIC, "Key", [only]);
    // Its own: blocking past 35,000, in the background past 30,000. pi's defaults would
    // block past 23,616 and start one in the background at any size a summary can cut.
    expect(compactionFor(only)).toEqual({ reserveTokens: 5_000, keepRecentTokens: 10_000,
                                          backgroundTokens: 5_000 });
    const compacted = await r.in(async (instance) => {
      const pi = instance.agent.harness;
      const kinds = async () => (await pi.messages()).map((entry) => entry.kind);
      // A first exchange a summary could cut, under pi's defaults too.
      await pi.prompt("cmd:measured-echo");
      await pi.prompt(`${sized(12_000)} cmd:measured-echo`);
      // About 25,000 with the first answer's bill: under its own thresholds.
      await pi.prompt(`${sized(10_000)} cmd:measured-echo`);
      const under = (await kinds()).includes("pi.compaction");
      // About 36,000: past them.
      await pi.prompt(`${sized(10_000)} cmd:measured-echo`);
      await until(async () => (await kinds()).includes("pi.compaction"), "the compaction");
      return under;
    });
    expect(compacted).toBe(false);
  }, 60_000);
});

describe("the owner's usage view", () => {
  it("keeps pi's and pi-ai's metrics and pi's own usage, for the owner alone", async () => {
    const r = await gated();
    await r.connect("sk-metrics");
    const answer = await r.in((instance) => instance.agent.harness.prompt("cmd:echo"));
    expect(answer.status).toBe("done");
    const view = await r.stub.usageMetrics(r.session());
    const names = new Set(view.metrics.series.map((series) => series.name));
    for (const name of [...Object.keys(AI_TELEMETRY_SCHEMA.metrics),
                        "pi.durable.phase.duration", "pi.durable.tasks.active"]) {
      expect(names).toContain(name);
    }
    const tokens = view.metrics.series.filter((series) => series.name === "pi.ai.tokens");
    expect(tokens.find((series) => series.attributes["kind"] === "input")!.sum).toBe(11);
    expect(tokens.find((series) => series.attributes["kind"] === "output")!.sum).toBe(7);
    const cost = view.metrics.series.find((series) => series.name === "pi.ai.cost")!;
    const { receipts: [receipt] } = await r.stub.usageReceipts(r.bind());
    expect(Math.ceil(cost.sum * 1_000_000)).toBe(receipt!.cost);
    expect(view.metrics.dropped).toBe(0);
    // pi's own ledger, by the physical model that answered.
    const [key, used] = Object.entries(view.models)[0]!;
    expect(key).toBe(`gateway/${receipt!.connection}/${receipt!.model}`);
    expect(used).toMatchObject({ input: 11, output: 7 });
    expect((await failure(() => r.stub.usageMetrics(r.bind({ origin: MUSE, source: {
      kind: "mcp", ref: "conn-1" } })))).message).toBe("only the owner may read its model usage");
  });
});
