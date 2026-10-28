/**
 * `POST /v1/messages` (research §2.3, §4): pi-ai's own pi-messages client on the owner's
 * side, the gateway in the middle, and pi-ai's real adapters on the far side, against
 * pi-ai's faux provider and against a fake upstream in each provider's wire format.
 */
import {
  type AssistantMessageEvent, createModels, fauxText, fauxThinking, fauxToolCall, type Model,
  type Api,
} from "@earendil-works/pi-ai";
import { describe, expect, it } from "vitest";

import { builtinCatalog } from "../src/catalog";
import type { Source } from "../src/credential";
import { encode, frame } from "../src/encode";
import { BODY_MAX, guardedFetch } from "../src/messages";
import {
  ALICE, ANTHROPIC, ask, client, drain, envelopeOf, FAUX_SOURCE, fauxAssistantMessage,
  fauxCatalog, gatewayHandler, GOOGLE, OPENAI, replay, ring, rotate, sealed, USER,
} from "./support";

const SECRET = "sk-alice-0123456789";

/** A catalog model of `provider` speaking `api`, chosen from pi-ai's catalog, never named. */
function catalogModel(provider: string, api: string,
                      pick: (model: Model<Api>) => boolean = () => true): Model<Api> {
  const found = builtinCatalog().provider(provider)!.getModels()
    .find((model) => model.api === api && pick(model));
  if (found === undefined) {
    throw new Error(`pi-ai's catalog has no ${provider} model for ${api}`);
  }
  return found;
}

/** The raw SSE text of one call. */
async function raw(handler: (request: Request) => Promise<Response>, header: string,
                   body: unknown): Promise<{ status: number; text: string; headers: Headers }> {
  const response = await handler(new Request("https://gateway.internal/v1/messages", {
    method: "POST", headers: { "x-ta-envelope": header, "content-type": "application/json" },
    body: JSON.stringify(body),
  }));
  return { status: response.status, text: await response.text(), headers: response.headers };
}

function echoOf(message: { content: { type: string; text?: string }[] }): Record<string, unknown> {
  const text = message.content.find((block) => block.type === "text")?.text ?? "";
  return JSON.parse(text) as Record<string, unknown>;
}

async function cancelledAt(id: string): Promise<boolean | null> {
  const answer = await fetch(`https://status.test/cancelled?id=${id}`);
  return ((await answer.json()) as { cancelled: boolean | null }).cancelled;
}

/** What an event says, without the live `partial` (pi-ai's own event fields only). */
function semantic(event: AssistantMessageEvent): Record<string, unknown> {
  const fields: Record<string, unknown> = { type: event.type };
  for (const key of ["contentIndex", "delta", "content", "toolCall", "reason"] as const) {
    if (key in event) {
      fields[key] = (event as Record<string, unknown>)[key];
    }
  }
  return fields;
}

/** A final message without the fields the owner's side fills from its own model. */
function strip(message: object): Record<string, unknown> {
  const { api: _a, provider: _p, model: _m, timestamp: _t, durationMs: _d, ...rest } =
    message as Record<string, unknown>;
  return rest;
}

const SCRIPT = () => [fauxAssistantMessage(
  [fauxThinking("Need the tool first."), fauxText("Here it is, in a few more words."),
   fauxToolCall("echo", { text: "x", n: 2, nested: { list: [1, "two"] } }, { id: "call-1" })],
  { stopReason: "toolUse", responseId: "resp-1" })];

describe("the encoding", () => {
  it("is the exact inverse of pi-ai's pi-messages client, event by event", async () => {
    // pi-ai's own events, encoded as the gateway encodes them, and played into pi-ai's
    // own pi-messages client: it rebuilds every event pi-ai emitted and the same final
    // message. (A partial is live on both sides, not a per-event snapshot, so it is held
    // by the events that build it and the message it ends as.)
    const { faux } = fauxCatalog(SCRIPT());
    const models = createModels();
    models.setProvider(faux.provider);
    const direct: AssistantMessageEvent[] = [];
    let frames = "";
    let final;
    for await (const event of models.streamSimple(faux.getModel(), USER, {})) {
      direct.push(event);
      if (event.type === "error") {
        throw new Error("the faux script does not fail");
      }
      if (event.type === "done") {
        final = structuredClone(event.message);
      }
      frames += frame(encode(event));
    }
    const replayed = await drain(replay(frames).stream(USER));
    expect(replayed.events.map(semantic)).toEqual(direct.map(semantic));
    expect(strip(replayed.message)).toEqual(strip(final!));
  });

  it("round-trips through the gateway exactly as pi-ai streams it", async () => {
    const env = ring("k1");
    const direct = fauxCatalog(SCRIPT());
    const models = createModels();
    models.setProvider(direct.faux.provider);
    const expected = await drain(models.streamSimple(direct.faux.getModel(), USER, {}));

    const { catalog } = fauxCatalog(SCRIPT());
    const blob = await sealed(env, ALICE, SECRET, FAUX_SOURCE);
    const owner = client(gatewayHandler(env, catalog), envelopeOf({ blob }), "faux-1");
    const got = await drain(owner.stream(USER));
    expect(got.events.map(semantic)).toEqual(expected.events.map(semantic));
    expect(strip(got.message)).toEqual(strip(expected.message));
    expect(got.message.stopReason).toBe("toolUse");
    expect(got.message.responseId).toBe("resp-1");
    expect(got.message.content.map((block) => block.type)).toEqual(["thinking", "text", "toolCall"]);
  });
});

describe("against pi-ai's faux provider", () => {

  it("clamps maxTokens to the reservation, on the request and on the model", async () => {
    const env = ring("k1");
    const seen = (requested: number | undefined, cap: number) => {
      const { catalog } = fauxCatalog([(_context, options, _state, model) =>
        fauxAssistantMessage(JSON.stringify({ maxTokens: options?.maxTokens,
                                              modelMax: model.maxTokens, key: options?.apiKey,
                                              base: model.baseUrl }))]);
      return { catalog, requested, cap };
    };
    for (const { catalog, requested, cap } of [seen(5000, 100), seen(50, 100), seen(undefined, 300)]) {
      const blob = await sealed(env, ALICE, SECRET, FAUX_SOURCE);
      const owner = client(gatewayHandler(env, catalog), envelopeOf({ blob, cap }), "faux-1");
      const { message } = await drain(owner.stream(USER, requested === undefined
        ? {} : { maxTokens: requested }));
      const echo = echoOf(message as never);
      expect(echo["maxTokens"]).toBe(Math.min(requested ?? cap, cap));
      expect(echo["modelMax"]).toBe(Math.min(4096, cap));
      expect(echo["key"]).toBe(SECRET);
      expect(echo["base"]).toBe("https://faux.test/v1");
    }
  });

  it("carries pi-ai's usage in done", async () => {
    const env = ring("k1");
    const { catalog } = fauxCatalog([fauxAssistantMessage("four words of reply")]);
    const blob = await sealed(env, ALICE, SECRET, FAUX_SOURCE);
    const { text } = await raw(gatewayHandler(env, catalog), envelopeOf({ blob }),
                               { model: "faux-1", context: USER, options: {} });
    const done = text.split("\n\n").filter(Boolean).map((frame) =>
      JSON.parse(frame.replace(/^data: /, "")) as Record<string, unknown>).at(-1)!;
    expect(done["type"]).toBe("done");
    expect(done["reason"]).toBe("stop");
    expect(done["usage"]).toMatchObject({ output: expect.any(Number), input: expect.any(Number),
                                          totalTokens: expect.any(Number),
                                          cost: expect.any(Object) });
    expect((done["usage"] as { output: number }).output).toBeGreaterThan(0);
  });

  it("ends the upstream call when the caller stops reading", async () => {
    const env = ring("k1");
    const { catalog, faux } = fauxCatalog([fauxAssistantMessage("x".repeat(4000))], 50);
    const blob = await sealed(env, ALICE, SECRET, FAUX_SOURCE);
    const response = await gatewayHandler(env, catalog)(new Request("https://g/v1/messages", {
      method: "POST", headers: { "x-ta-envelope": envelopeOf({ blob }) },
      body: JSON.stringify({ model: "faux-1", context: USER }),
    }));
    const reader = response.body!.getReader();
    await reader.read();
    await reader.cancel();
    expect(faux.state.callCount).toBe(1);
  });

  it("reports a reseal in a header when the blob's kid is retiring", async () => {
    const old = ring("k1");
    const blob = await sealed(old, ALICE, SECRET, FAUX_SOURCE);
    const env = rotate(old, "k2", ["k2"]);
    const { catalog } = fauxCatalog([fauxAssistantMessage("ok")]);
    const { status, headers } = await raw(gatewayHandler(env, catalog), envelopeOf({ blob }),
                                          { model: "faux-1", context: USER });
    expect(status).toBe(200);
    const resealed = JSON.parse(headers.get("x-ta-resealed")!) as { version: number; kid: string };
    expect(resealed).toMatchObject({ version: 2, kid: "k2" });
    expect(headers.get("x-ta-policy-generation")).toBe("7");
    expect(headers.get("x-ta-reservation")).toBe("r-1");
  });
});

describe("against a fake upstream, through pi-ai's real adapters", () => {
  const cases: [string, Source, string, string][] = [
    ["anthropic-messages", ANTHROPIC, "x-api-key", SECRET],
    ["openai-responses", OPENAI, "authorization", `Bearer ${SECRET}`],
    ["google-generative-ai", GOOGLE, "x-goog-api-key", SECRET],
  ];

  it.each(cases)("%s: sends the opened secret, only upstream, with the clamp", async (
      api, record, header, value) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, record);
    const model = catalogModel(record.upstream.provider, api);
    const owner = client(gatewayHandler(env), envelopeOf({ blob, cap: 321 }), model.id);
    const { events, message } = await drain(owner.stream(USER, { maxTokens: 5000 }));
    expect(message.stopReason).toBe("stop");
    const echo = echoOf(message as never);
    expect((echo["auth"] as Record<string, string | null>)[header]).toBe(value);
    for (const [other, seen] of Object.entries(echo["auth"] as Record<string, string | null>)) {
      if (other !== header) {
        expect(seen).toBeNull();
      }
    }
    expect(echo["envelope"]).toBeNull();
    expect(echo["max"]).toBe(321);
    expect(String(echo["path"])).toMatch(api === "google-generative-ai"
      ? /^\/v1beta\/models\/.+:streamGenerateContent$/ : /^\/v1\/(messages|responses)$/);
    expect(message.usage.input).toBe(11);
    expect(message.usage.output).toBe(7);
    expect(message.responseId).toMatch(/_fake$/);
    expect(events.map((event) => event.type)).toContain("text_delta");
    expect(events.at(-1)?.type).toBe("done");
    if (api === "openai-responses") {
      // The Responses adapter signs a text block with its item id; the signature, which
      // multi-turn replay needs, survives the wire.
      expect((message.content[0] as { textSignature?: string }).textSignature)
        .toContain("msg_fake");
    }
  });

  it.each(cases)("%s: catalog prices make pi-ai's cost in done", async (api, record) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, record);
    const model = catalogModel(record.upstream.provider, api, (entry) => entry.cost.output > 0);
    const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
    const { message } = await drain(owner.stream(USER));
    expect(message.usage.cost.output).toBeCloseTo(7 * model.cost.output / 1_000_000, 12);
    expect(message.usage.cost.total).toBeGreaterThan(0);
  });

  it("streams a declared custom endpoint through pi-ai's adapter for its API", async () => {
    const env = ring("k1");
    const custom: Source = {
      upstream: { provider: "custom", api: "anthropic-messages", base_url: "https://api.anthropic.com",
                  auth: { header: "x-api-key", scheme: "" },
                  models: [{ id: "declared-1", name: "Declared", reasoning: false, input: ["text"],
                             contextWindow: 100_000, maxTokens: 8000,
                             cost: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0 } }] },
      endpoints: ANTHROPIC.endpoints,
    };
    const blob = await sealed(env, ALICE, SECRET, custom);
    const owner = client(gatewayHandler(env), envelopeOf({ blob, cap: 500 }), "declared-1");
    const { message } = await drain(owner.stream(USER));
    const echo = echoOf(message as never);
    expect((echo["auth"] as Record<string, string>)["x-api-key"]).toBe(SECRET);
    expect(echo["max"]).toBe(500);
    expect(message.usage.cost.output).toBeCloseTo(7 * 2 / 1_000_000, 12);
  });

  it("holds a thinking budget inside the reservation too", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages",
      (entry) => entry.reasoning && (entry.compat as { forceAdaptiveThinking?: boolean } | undefined)
        ?.forceAdaptiveThinking !== true && entry.maxTokens > 4000);
    const owner = client(gatewayHandler(env), envelopeOf({ blob, cap: 3000 }), model.id);
    const { message } = await drain(owner.stream(USER, { maxTokens: 2000, reasoning: "high" }));
    expect(Number(echoOf(message as never)["max"])).toBeLessThanOrEqual(3000);
  });

  it("round-trips thinking, signatures and a tool call through pi-messages", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages");
    const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
    const { message } = await drain(owner.stream(ask("cmd:tool")));
    expect(message.stopReason).toBe("toolUse");
    expect(message.content).toEqual([
      { type: "thinking", thinking: "Look it up.", thinkingSignature: "sig-1" },
      { type: "toolCall", id: "toolu_1", name: "lookup", arguments: { q: "pi" } },
    ]);
    expect(message.responseId).toBe("msg_tool");
  });

  it.each([
    ["401", "upstream_unauthorized"], ["403", "upstream_unauthorized"],
    ["429", "upstream_rate_limited"], ["400", "upstream_rejected"],
    ["404", "upstream_rejected"],
  ])("answers an upstream %s with %s, and never the upstream body or the secret", async (
      status, code) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages");
    const header = envelopeOf({ blob });
    const { text } = await raw(gatewayHandler(env), header,
                               { model: model.id, context: ask(`cmd:fail-${status}`),
                                 options: { } });
    expect(text).not.toContain(SECRET);
    expect(text).not.toContain("bad key");
    const last = JSON.parse(text.trim().split("\n\n").at(-1)!.replace(/^data: /, ""));
    expect(last).toMatchObject({ type: "error", reason: "error", errorMessage: code });
    const owner = client(gatewayHandler(env), header, model.id);
    const { message } = await drain(owner.stream(ask(`cmd:fail-${status}`)));
    expect(message.stopReason).toBe("error");
    expect(message.errorMessage).toBe(code);
  });

  it("never follows a redirect, so the secret never leaves the sealed origin", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages");
    const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
    const { message } = await drain(owner.stream(ask("cmd:redirect")));
    // Followed, it would have reached evil.test, which the fake answers 599.
    expect(message.errorMessage).toBe("upstream_error");
  });

  it("refuses an adapter request to a path the credential is not sealed for", async () => {
    const env = ring("k1");
    const narrow: Source = { ...OPENAI, endpoints: [{ origin: "https://api.openai.com",
                                                      paths: ["/v1/models"], methods: ["GET"] }] };
    const blob = await sealed(env, ALICE, SECRET, narrow);
    const model = catalogModel("openai", "openai-responses");
    const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
    const { message } = await drain(owner.stream(USER));
    expect(message.errorMessage).toBe("endpoint_refused");
  });

  it("aborts the upstream stream when the owner's call is cancelled", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages");
    const owner = client(gatewayHandler(env), envelopeOf({ blob }), model.id);
    const abort = new AbortController();
    const stream = owner.stream(ask("cmd:slow-abort1"), { signal: abort.signal });
    const events: AssistantMessageEvent[] = [];
    for await (const event of stream) {
      events.push(event);
      if (event.type === "text_delta") {
        abort.abort();
      }
    }
    expect((await stream.result()).stopReason).toBe("aborted");
    await expect.poll(() => cancelledAt("abort1"), { timeout: 5000 }).toBe(true);
  });

  it("cuts the call at the envelope's deadline", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const model = catalogModel("anthropic", "anthropic-messages");
    const header = envelopeOf({ blob, deadline: Date.now() + 400 });
    const { text } = await raw(gatewayHandler(env), header,
                               { model: model.id, context: ask("cmd:slow-dead1") });
    const last = JSON.parse(text.trim().split("\n\n").at(-1)!.replace(/^data: /, ""));
    expect(last).toMatchObject({ type: "error", reason: "aborted", errorMessage: "deadline" });
    await expect.poll(() => cancelledAt("dead1"), { timeout: 5000 }).toBe(true);
  });
});

describe("refusals before the stream", () => {
  async function answer(header: string | null, body: unknown, env = ring("k1"),
                        method = "POST") {
    const headers: Record<string, string> = header === null ? {} : { "x-ta-envelope": header };
    const response = await gatewayHandler(env)(new Request("https://g/v1/messages", {
      method, headers, ...(method === "GET" ? {} : { body: JSON.stringify(body) }),
    }));
    return { status: response.status, body: await response.json() as { error: { code: string } } };
  }

  const model = () => catalogModel("anthropic", "anthropic-messages").id;

  it("refuses a call with no envelope, a malformed one, or one past its deadline", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const body = { model: model(), context: USER };
    expect(await answer(null, body, env)).toEqual(
      { status: 400, body: { error: { code: "invalid_envelope", message: "invalid_envelope" } } });
    expect((await answer("not-base64!", body, env)).body.error.code).toBe("invalid_envelope");
    expect((await answer(btoa("{}"), body, env)).body.error.code).toBe("invalid_envelope");
    expect((await answer(envelopeOf({ blob, deadline: Date.now() - 1 }), body, env)).status)
      .toBe(504);
    expect((await answer(envelopeOf({ blob, deadline: Date.now() + 3_600_000 }), body, env))
      .body.error.code).toBe("invalid_envelope");
    expect((await answer(envelopeOf({ blob, cap: 0 }), body, env)).body.error.code)
      .toBe("invalid_envelope");
  });

  it("refuses the blob under any other owner, connection or version", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    for (const bound of [{ ...ALICE, owner: "bob" }, { ...ALICE, connection: "c2" },
                         { ...ALICE, version: 2 }]) {
      const got = await answer(envelopeOf({ blob, bound }), { model: model(), context: USER }, env);
      expect(got).toEqual({ status: 403,
                            body: { error: { code: "credential_refused", message: "credential_refused" } } });
    }
  });

  it("refuses an unknown model, a bad body and a wrong method", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const header = envelopeOf({ blob });
    expect((await answer(header, { model: "no-such-model", context: USER }, env)).status).toBe(404);
    expect((await answer(header, { model: model() }, env)).body.error.code).toBe("invalid_request");
    expect((await answer(header, { model: model(), context: USER, extra: 1 }, env)).body.error.code)
      .toBe("invalid_request");
    expect((await answer(header, { model: model(), context: USER,
                                   options: { maxTokens: -1 } }, env)).body.error.code)
      .toBe("invalid_request");
    expect((await answer(header, { model: model(), context: USER,
                                   options: { headers: { a: "b" } } }, env)).body.error.code)
      .toBe("invalid_request");
    expect((await answer(header, null, env, "GET")).status).toBe(405);
  });

  it("refuses a body over the bound without reading it", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const response = await gatewayHandler(env)(new Request("https://g/v1/messages", {
      method: "POST", headers: { "x-ta-envelope": envelopeOf({ blob }),
                                 "content-length": String(BODY_MAX + 1) },
      body: "{}",
    }));
    expect(response.status).toBe(413);
  });

  it("fails loudly with no usable keyring, never with a default key", async () => {
    const blob = await sealed(ring("k1"), ALICE, SECRET, ANTHROPIC);
    const got = await answer(envelopeOf({ blob }), { model: model(), context: USER },
                             { GATEWAY_KEYRING: "" });
    expect(got).toEqual({ status: 500,
                          body: { error: { code: "keyring_unavailable", message: "keyring_unavailable" } } });
  });

  it("answers an exception it did not expect as internal, never with its text", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const throwing = { provider: () => { throw new Error(`provider prose quoting ${SECRET}`); } };
    const response = await gatewayHandler(env, throwing)(new Request("https://g/v1/messages", {
      method: "POST", headers: { "x-ta-envelope": envelopeOf({ blob }) },
      body: JSON.stringify({ model: model(), context: USER }),
    }));
    expect(response.status).toBe(500);
    expect(await response.text()).toBe(JSON.stringify(
      { error: { code: "internal", message: "internal" } }));
  });

  it("refuses a source pi-ai has no provider for", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, {
      ...ANTHROPIC, upstream: { ...ANTHROPIC.upstream, provider: "no-such-provider" } });
    expect((await answer(envelopeOf({ blob }), { model: model(), context: USER }, env)).body
      .error.code).toBe("unsupported_source");
  });
});

describe("the guarded fetch", () => {
  it("refuses an origin, a method or a path the credential is not sealed for", async () => {
    const observed = { status: undefined, refused: false, unreachable: false };
    const guarded = guardedFetch(ANTHROPIC.endpoints, observed);
    for (const [url, method] of [["https://api.openai.com/v1/messages", "POST"],
                                 ["https://api.anthropic.com/v1/files", "POST"],
                                 ["https://api.anthropic.com/v1/messages", "DELETE"],
                                 ["http://api.anthropic.com/v1/messages", "POST"],
                                 ["https://api.anthropic.com/v1/messages%2F..%2Ffiles", "POST"],
                                 ["https://u:p@api.anthropic.com/v1/messages", "POST"]] as const) {
      await expect(guarded(url, { method })).rejects.toThrow("endpoint_refused");
    }
    expect(observed.refused).toBe(true);
    const ok = await guarded("https://api.anthropic.com/v1/models", { method: "GET",
                                                                      headers: { "x-api-key": "sk-good" } });
    expect(ok.status).toBe(200);
    expect(observed.status).toBe(200);
  });
});

describe("the encoder", () => {
  it("refuses a deferred done, which pi-messages cannot carry", () => {
    const message = fauxAssistantMessage("x");
    expect(() => encode({ type: "done", reason: "deferred", message })).toThrow("unsupported_deferred");
  });
});
