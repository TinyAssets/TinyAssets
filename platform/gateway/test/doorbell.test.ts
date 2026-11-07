/**
 * The doorbell's secrets and sends (slice W, ruling H1): a wake's secret is sealed here and
 * opened only here, sent only to the URL it was sealed with, with its record's auth; an MCP
 * Events callback is verified by a signed single-use challenge before it is sealed.
 */
import { createHmac } from "node:crypto";

import { describe, expect, it } from "vitest";

import {
  doorbellSecret, ECHO_MAX_BYTES, ringDoorbell, sealDoorbell, type Send, verifyDoorbell,
} from "../src/doorbell";
import { Refusal } from "../../contract/src/refusal";
import { open } from "../src/keyring";
import { ring, rotate } from "./support";

const OWNER = "alice";
const SUB = "sub_" + "a".repeat(32);
const KEY = new Uint8Array(32).map((_, n) => n + 1);
const WHSEC = "whsec_" + btoa(String.fromCharCode(...KEY));
const HOOK = "https://hooks.example.com/tinyassets/events";
const FIRE = "https://api.anthropic.com/v1/claude_code/routines/trig_01ABC/fire";
const TOKEN = "sk-ant-oat01-" + "x".repeat(40);
const CHATGPT = { record: "chatgpt_mcp_events", url: HOOK, secret: WHSEC };
const CLAUDE = { record: "claude_routine_fire", url: FIRE, secret: TOKEN };
const REQUEST = { headers: { "Content-Type": "application/json", "X-MCP-Subscription-Id": SUB },
                  body: '{"eventId":"wake_1"}', event_id: "wake_1" };

/** A send that records each request and answers from a script. */
function recorder(answers: (Response | Error | ((request: Request) => Promise<Response>))[] = []) {
  const sent: { url: string; redirect: string; headers: Record<string, string>; body: string }[] = [];
  const send: Send = async (request) => {
    sent.push({ url: request.url, redirect: request.redirect,
                headers: Object.fromEntries(request.headers), body: await request.clone().text() });
    const answer = answers.shift() ?? new Response(null, { status: 202 });
    if (answer instanceof Error) {
      throw answer;
    }
    return typeof answer === "function" ? answer(request) : answer;
  };
  return { sent, send };
}

function signed(id: string, stamp: string, body: string, key: Uint8Array = KEY): string {
  return "v1," + createHmac("sha256", key).update(`${id}.${stamp}.${body}`).digest("base64");
}

async function code(run: () => Promise<unknown>): Promise<string> {
  const error = await run().then(() => undefined, (caught: unknown) => caught);
  return error instanceof Refusal ? error.code : String(error);
}

describe("the doorbell's seal", () => {
  it("seals a pasted token with its URL and its record's auth, never in the clear", async () => {
    const env = ring("k1");
    const { blob, kid } = await sealDoorbell(env, OWNER, SUB, 1, CLAUDE);
    expect(kid).toBe("k1");
    expect(blob).not.toContain(TOKEN);
    expect(blob).not.toContain("anthropic");
    // A doorbell blob never opens as a model credential, nor under another subscription.
    expect(await code(() => open(env, { owner: OWNER, connection: `wake.${SUB}`, version: 1 },
                                 blob))).toBe("credential_refused");
    const answer = await ringDoorbell(env, OWNER, "sub_" + "b".repeat(32), 1, blob, null,
                                      REQUEST, recorder().send);
    expect(answer).toEqual({ state: "refused" });
  });

  it("refuses a token or URL its record does not admit, before any seal", async () => {
    const env = ring("k1");
    for (const input of [{ ...CLAUDE, url: "https://evil.example/v1/claude_code/routines/t/fire" },
                         { ...CLAUDE, url: "http://api.anthropic.com/v1/claude_code/routines/t/fire" },
                         { ...CLAUDE, secret: "short" },
                         { ...CHATGPT, secret: "whsec_" + btoa("too short") },
                         { ...CHATGPT, url: "https://localhost/hook" },
                         { ...CHATGPT, url: "https://mcp.tinyassets.io/hook" },
                         { ...CHATGPT, url: "https://x.workers.dev/hook" },
                         { ...CHATGPT, url: "https://metadata.google.internal/hook" },
                         { ...CHATGPT, url: "https://10.0.0.1/hook" },
                         { record: "muse_recurring_task", url: HOOK, secret: TOKEN },
                         { ...CLAUDE, extra: true }]) {
      expect(await code(() => sealDoorbell(env, OWNER, SUB, 1, input)), JSON.stringify(input))
        .toBe("invalid_request");
    }
    expect(() => doorbellSecret({ kind: "doorbell" })).toThrow("credential_refused");
  });
});

describe("a ring", () => {
  it("sends a bearer token to the sealed URL alone, never following a redirect", async () => {
    const env = ring("k1");
    const { blob } = await sealDoorbell(env, OWNER, SUB, 1, CLAUDE);
    const { sent, send } = recorder([new Response("quoted body", {
      status: 429, headers: { "Retry-After": "259200" } })]);
    const answer = await ringDoorbell(env, OWNER, SUB, 1, blob, null,
                                      { ...REQUEST, headers: { "Content-Type": "application/json" } },
                                      send);
    expect(answer).toEqual({ state: "answered", status: 429, retry_after: "259200" });
    expect(sent).toEqual([{ url: FIRE, redirect: "manual",
                            headers: { "content-type": "application/json",
                                       authorization: `Bearer ${TOKEN}` },
                            body: REQUEST.body }]);
  });

  it("signs Standard Webhooks with the key, and both keys inside a rotation", async () => {
    const env = ring("k1");
    const first = await sealDoorbell(env, OWNER, SUB, 1, CHATGPT);
    const next = new Uint8Array(32).map((_, n) => 200 - n);
    const refreshed = await sealDoorbell(env, OWNER, SUB, 1, {
      ...CHATGPT, secret: "whsec_" + btoa(String.fromCharCode(...next)) });
    const { sent, send } = recorder();
    await ringDoorbell(env, OWNER, SUB, 1, first.blob, null, REQUEST, send);
    await ringDoorbell(env, OWNER, SUB, 1, refreshed.blob, { version: 1, blob: first.blob },
                       REQUEST, send);
    const [one, both] = sent;
    const stamp = one!.headers["webhook-timestamp"]!;
    expect(one!.headers["webhook-id"]).toBe("wake_1");
    expect(one!.headers["x-mcp-subscription-id"]).toBe(SUB);
    expect(one!.headers["webhook-signature"]).toBe(signed("wake_1", stamp, REQUEST.body));
    const later = both!.headers["webhook-timestamp"]!;
    expect(both!.headers["webhook-signature"]).toBe(
      `${signed("wake_1", later, REQUEST.body, next)} ${signed("wake_1", later, REQUEST.body)}`);
  });

  it("is unknown when the connection fails after the request may have left", async () => {
    const env = ring("k1");
    const { blob } = await sealDoorbell(env, OWNER, SUB, 1, CHATGPT);
    expect(await ringDoorbell(env, OWNER, SUB, 1, blob, null, REQUEST,
                              recorder([new Error("reset")]).send)).toEqual({ state: "unknown" });
  });

  it("refuses a request that names its own auth", async () => {
    const env = ring("k1");
    const { blob } = await sealDoorbell(env, OWNER, SUB, 1, CHATGPT);
    expect(await code(() => ringDoorbell(env, OWNER, SUB, 1, blob, null,
      { ...REQUEST, headers: { Authorization: "Bearer stolen" } }, recorder().send)))
      .toBe("invalid_request");
  });

  it("reseals a blob under a retired kid at the next version", async () => {
    const old = ring("k1");
    const { blob } = await sealDoorbell(old, OWNER, SUB, 1, CHATGPT);
    const answer = await ringDoorbell(rotate(old, "k2", ["k2"]), OWNER, SUB, 1, blob, null, REQUEST,
                                      recorder().send);
    expect(answer).toMatchObject({ state: "answered", status: 202,
                                   resealed: { kid: "k2", version: 2 } });
  });
});

describe("a callback's verification", () => {
  /** A receiver that echoes the challenge it was sent, after checking the signature. */
  const echo = async (request: Request): Promise<Response> => {
    const body = await request.text();
    const id = request.headers.get("webhook-id")!;
    const stamp = request.headers.get("webhook-timestamp")!;
    if (request.headers.get("webhook-signature") !== signed(id, stamp, body)
        || !/^msg_verification_[0-9a-f]{32}$/.test(id)
        || request.headers.get("x-mcp-subscription-id") !== SUB) {
      return new Response(null, { status: 401 });
    }
    return Response.json({ challenge: (JSON.parse(body) as { challenge: string }).challenge });
  };

  it("seals the callback only once it echoes a fresh, signed, single-use challenge", async () => {
    const env = ring("k1");
    const { sent, send } = recorder([echo, echo]);
    const first = await verifyDoorbell(env, OWNER, SUB, 1, CHATGPT, send);
    const second = await verifyDoorbell(env, OWNER, SUB, 1, CHATGPT, send);
    expect([first.verified, second.verified]).toEqual([true, true]);
    const challenges = sent.map((s) => (JSON.parse(s.body) as { type: string; challenge: string }));
    expect(challenges.map((c) => c.type)).toEqual(["verification", "verification"]);
    expect(challenges[0]!.challenge).not.toBe(challenges[1]!.challenge);
    expect(sent[0]!.redirect).toBe("manual");
    if (!first.verified) {
      throw new Error("expected verified");
    }
    // The verified blob rings the same callback.
    const rung = recorder();
    await ringDoorbell(env, OWNER, SUB, 1, first.sealed.blob, null, REQUEST, rung.send);
    expect(rung.sent[0]!.url).toBe(HOOK);
  });

  it("answers a failure as a category, never the endpoint's body", async () => {
    const env = ring("k1");
    const timeout = Object.assign(new Error("slow"), { name: "TimeoutError" });
    const cases: [Response | Error | ((r: Request) => Promise<Response>), string][] = [
      [Response.json({ challenge: "not it" }), "challenge_failed"],
      [new Response("x".repeat(ECHO_MAX_BYTES + 1)), "challenge_failed"],
      [new Response("not json"), "challenge_failed"],
      [new Response("secret page", { status: 404 }), "http_4xx"],
      [new Response(null, { status: 302, headers: { Location: "https://evil.example/" } }),
       "http_4xx"],
      [new Response("down", { status: 503 }), "http_5xx"],
      [timeout, "timeout"],
      [new Error("refused"), "connection_refused"],
    ];
    for (const [answer, reason] of cases) {
      expect(await verifyDoorbell(env, OWNER, SUB, 1, CHATGPT, recorder([answer]).send))
        .toEqual({ verified: false, reason });
    }
    // A kind whose callback the owner pastes is never verified this way.
    expect(await code(() => verifyDoorbell(env, OWNER, SUB, 1, CLAUDE, recorder().send)))
      .toBe("invalid_request");
  });
});
