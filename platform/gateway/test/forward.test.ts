/**
 * Raw passthrough for sandbox egress (research §5.4), as a free call takes it: inbound
 * credentials stripped, the sealed one injected, the sealed endpoints enforced, a stalled
 * answer cut, and the secret redacted from what comes back. And the connection's setup check
 * (`verify`), and the reads of what its key can pay for (`credit`) and of how strong its
 * vendor lists the models it serves a key (`listing`).
 */
import { describe, expect, it, onTestFinished, vi } from "vitest";

import { until } from "../../ci/workerd";

import { isModelType } from "@earendil-works/pi-ai/models";
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";

import { builtinCatalog, modelInfos, recordOf } from "../src/catalog";
import { checkedRecord } from "../../contract/src/model-record";
import { apiRules } from "../../contract/src/api-rules";
import { Refusal } from "../../contract/src/refusal";
import { EGRESS_DEADLINE_S } from "../../contract/src/limits";
import { serviceSource, type Source } from "../../contract/src/egress-scope";
import {
  credit, forwardMetered, forwardRead, listing, verify,
} from "../src/forward";
import { redaction } from "../src/redact";
import { seal, type Binding } from "../src/keyring";
import { steer } from "./fake-upstream";
import { ALICE, ANTHROPIC, OPENAI, ring, rotate, sealed } from "./support";

const SECRET = "sk-alice-forward-0123456789";
/** A secret every encoding writes another way: `/`, `+`, `=`, and what only a form encodes. */
const ODD = "sk-a/b+c=d~e!f(0123456789)";

/** Anthropic, with its token count sealed: the one free call that carries a body. */
const COUNTING: Source = Object.freeze({ ...ANTHROPIC, endpoints: [...ANTHROPIC.endpoints, {
  origin: "https://api.anthropic.com", paths: ["/v1/messages/count_tokens"],
  methods: ["POST"] }] });
const COUNT = "https://egress/v1/messages/count_tokens";

/** The fake upstream echoes credential headers as SHA-256 hex (fake-upstream.ts). */
async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function call(blob: string, request: Request, env = ring("k1"),
                    bound: Binding = ALICE): Promise<Response> {
  return (await forwardRead(env, bound, blob, request, new ReadableStream())).response;
}

describe("forward", () => {
  it("strips every inbound credential, injects the sealed one, and sends only the headers " +
     "its rules name", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request(
      "https://egress.tinyassets.io/v1/models?key=placeholder&api_key=p&KEY=p&client_version=0.162.1", {
        headers: {
          authorization: "Bearer ta_ph_c1_placeholder", "x-api-key": "ta_ph_c1_placeholder",
          "x-goog-api-key": "ta_ph", "api-key": "ta_ph", cookie: "session=s",
          "proxy-authorization": "Basic x", "x-ta-envelope": "forged", "x-ta-sandbox": "jwt",
          "cf-connecting-ip": "10.0.0.1", "x-forwarded-for": "10.0.0.1",
          // Headers that change what a call does or bills, and an engine's own telemetry.
          "x-goog-fieldmask": "candidates", "x-goog-user-project": "other",
          "openai-project": "proj_other", "openai-beta": "responses=v1",
          "x-stainless-lang": "js",
          accept: "application/json", "content-type": "application/json",
          ...steer({ mode: "echo" }),
        },
      }), env);
    expect(response.status).toBe(200);
    const echo = await response.json() as { digests: Record<string, string>; method: string;
                                            host: string; path: string; query: string;
                                            headers: Record<string, string> };
    expect(echo.method).toBe("GET");
    expect(echo.host).toBe("api.openai.com");
    expect(echo.path).toBe("/v1/models");
    expect(echo.query).toBe("?client_version=0.162.1");
    expect(echo.digests["authorization"]).toBe(await sha256(`Bearer ${SECRET}`));
    for (const dropped of ["x-api-key", "x-goog-api-key", "api-key", "cookie",
                           "proxy-authorization", "x-ta-envelope", "x-ta-sandbox",
                           "cf-connecting-ip", "x-forwarded-for", "x-goog-fieldmask",
                           "x-goog-user-project", "openai-project", "openai-beta",
                           "x-stainless-lang"]) {
      expect(echo.headers[dropped], dropped).toBeUndefined();
    }
    expect(echo.headers).toMatchObject({ accept: "application/json",
                                         "content-type": "application/json" });
    expect(response.headers.get("x-upstream")).toBe("fake");
    expect(response.headers.get("cf-ray")).toBeNull();
  });

  it("replaces a placeholder sent in the sealed header itself, whatever its name", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, {
      ...OPENAI, upstream: { ...OPENAI.upstream, auth: { header: "x-custom-key", scheme: "" } } });
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-custom-key": "ta_ph_c1_placeholder", ...steer({ mode: "echo" }) } }), env);
    const echo = await response.json() as { headers: Record<string, string> };
    // Only the sealed secret is sent, which the answer redacts: no placeholder beside it.
    expect(echo.headers["x-custom-key"]).toBe("[redacted]");
  });

  it("injects by the sealed template, a bare header for Anthropic's, and streams the body",
     async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, COUNTING);
    const response = await call(blob, new Request(COUNT, {
      method: "POST", headers: { authorization: "Bearer ph", "x-api-key": "ph",
                                 ...steer({ mode: "echo" }) }, body: '{"messages":[]}' }), env);
    const echo = await response.json() as { digests: Record<string, string>; host: string;
                                            body: string };
    expect(echo.host).toBe("api.anthropic.com");
    expect(echo.body).toBe('{"messages":[]}');
    expect(echo.digests["x-api-key"]).toBe(await sha256(SECRET));
    expect(echo.digests["authorization"]).toBeUndefined();
  });

  it("sends an API's own headers with a value its rule takes, and a beta on a free call",
     async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, COUNTING);
    const sent = async (headers: Record<string, string>) => {
      const response = await call(blob, new Request(COUNT, {
        method: "POST", headers: { ...headers, ...steer({ mode: "echo" }) }, body: "{}" }), env);
      return ((await response.json()) as { headers: Record<string, string> }).headers;
    };
    const kept = await sent({ "anthropic-version": "2023-06-01",
                              "anthropic-beta": "token-counting-2024-11-01, context-1m",
                              "content-type": "application/json; charset=utf-8" });
    expect(kept).toMatchObject({ "anthropic-version": "2023-06-01",
                                 "anthropic-beta": "token-counting-2024-11-01, context-1m",
                                 "content-type": "application/json; charset=utf-8" });
    // A version the meter was not written against, and a body the provider would read
    // otherwise than the gateway did, are not sent.
    const dropped = await sent({ "anthropic-version": "2099-01-01",
                                 "content-type": "application/json; charset=utf-16" });
    expect(dropped["anthropic-version"]).toBeUndefined();
    expect(dropped["content-type"]).toBeUndefined();
  });

  it("sends a free call's coded body with its coding, as it came", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, COUNTING);
    const coded = new Uint8Array(await new Response(new Blob(['{"messages":[]}']).stream()
      .pipeThrough(new CompressionStream("gzip"))).arrayBuffer());
    const response = await call(blob, new Request(COUNT, {
      method: "POST", body: coded,
      headers: { "content-encoding": "gzip", ...steer({ mode: "echo" }) } }), env);
    const echo = await response.json() as { headers: Record<string, string>; body: string };
    expect(echo.headers["content-encoding"]).toBe("gzip");
    expect(echo.body).toBe(new TextDecoder().decode(coded));
  });

  it("names the request headers it dropped, never a value, when the provider refuses the " +
     "call as sent, and logs nothing when it serves it or fails itself", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const logged = vi.spyOn(console, "warn");
    onTestFinished(() => logged.mockRestore());
    const answered = async (status: number) => (await call(blob, new Request(
      "https://egress/v1/models", { headers: {
        authorization: "Bearer ta_ph_placeholder", "openai-project": "proj_secret_value",
        "x-stainless-lang": "js-lang-value", [`x-${"n".repeat(64)}`]: "long-name-value",
        "cf-ray": "edge-value",
        ...steer({ mode: "say", status: String(status) }) } }), env)).status;
    expect(await answered(200)).toBe(200);
    expect(await answered(503)).toBe(503);
    // A rate limit is the provider's own state, not a refusal of the call as sent.
    expect(await answered(429)).toBe(429);
    expect(logged).not.toHaveBeenCalled();
    expect(await answered(400)).toBe(400);
    expect(logged).toHaveBeenCalledTimes(1);
    // The placeholder's header was replaced by the sealed one, and Cloudflare's is not the
    // sandbox's, so neither is a drop; a name past the bound is counted, not shown.
    expect(logged.mock.calls[0]).toEqual([
      "a sealed openai-responses call its provider refused (400) had 3 request headers " +
      "dropped: openai-project x-stainless-lang ..."]);
    expect(JSON.stringify(logged.mock.calls)).not.toMatch(/proj_secret|placeholder|value/);
  });

  it.each([
    ["a path not sealed", "POST", "https://egress/v1/files"],
    ["a method not sealed", "DELETE", "https://egress/v1/responses"],
    ["an encoded separator", "POST", "https://egress/v1/responses%2F..%2Ffiles"],
    ["an encoded dot", "POST", "https://egress/v1/%2e%2e/admin"],
    ["a prefix of a sealed path", "POST", "https://egress/v1/responses-admin"],
    ["a path naming another host", "POST", "https://egress//evil.test/v1/responses"],
    ["a query parameter its API's rules do not allow", "GET", "https://egress/v1/models?stream=true"],
    ["an allowed parameter's name spelled another way", "GET",
     "https://egress/v1/models?Client_Version=0.162.1"],
    ["a value its parameter's rule does not take", "GET",
     "https://egress/v1/models?client_version=latest"],
    ["an allowed parameter beside one not allowed", "GET",
     "https://egress/v1/models?client_version=0.162.1&include=all"],
  ])("refuses %s, before anything is sent", async (_what, method, url) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request(url, {
      method, headers: steer({ mode: "echo" }), ...(method === "GET" ? {} : { body: "{}" }) }), env);
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: { code: "endpoint_refused",
                                                     message: "endpoint_refused" } });
  });

  it("sends a credential sealed for one provider only to that provider", async () => {
    // Sealed for Anthropic's endpoints, a request naming OpenAI's host and path goes to
    // Anthropic's origin or nowhere: here nowhere, since the path is not sealed there.
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const response = await call(blob, new Request("https://api.openai.com/v1/responses", {
      method: "POST", headers: steer({ mode: "echo" }), body: "{}" }), env);
    expect(response.status).toBe(403);
  });

  it("forwards only to the base URL's origin, even when another origin is sealed", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, {
      ...OPENAI,
      endpoints: [...OPENAI.endpoints,
                  { origin: "https://api.anthropic.com", paths: ["/*"], methods: ["GET"] }],
    });
    const response = await call(blob, new Request("https://egress//api.anthropic.com/v1/x", {
      headers: steer({ mode: "echo" }) }), env);
    expect(response.status).toBe(403);
  });

  it("refuses the blob under another owner, connection or version", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    for (const bound of [{ ...ALICE, owner: "bob" }, { ...ALICE, connection: "c9" },
                         { ...ALICE, version: 3 }]) {
      const response = await call(blob, new Request("https://egress/v1/models",
        { headers: steer({ mode: "echo" }) }), env, bound);
      expect(response.status).toBe(403);
      expect(await response.text()).toBe(JSON.stringify(
        { error: { code: "credential_refused", message: "credential_refused" } }));
    }
  });

  it("leaves an answer its provider goes silent on to its caller, whose leaving ends it",
     async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let ended: string | undefined;
    try {
      const response = await call(blob, new Request("https://egress/v1/models", {
        headers: steer({ mode: "stall", id: "idle" }) }), env);
      expect(response.status).toBe(200);
      const reader = response.body!.getReader();
      // The bytes it sent come through; then it says nothing more.
      expect((await reader.read()).value!.byteLength).toBeGreaterThan(0);
      const next = reader.read().then(() => "read on", (error: Error) => error.message);
      void next.then((how) => {
        ended = how;
      });
      // An hour of silence, past every engine's own cut: the gateway cuts nothing.
      await vi.advanceTimersByTimeAsync(60 * 60 * 1000);
      expect(ended).toBeUndefined();
      // The engine gives up: its leaving reaches the provider.
      await reader.cancel();
    } finally {
      vi.useRealTimers();
    }
    await until(async () => (await (await fetch("https://status.test/cancelled?id=idle"))
      .json() as { cancelled: boolean | null }).cancelled === true, "the provider's request cut");
  });

  it("ends a call its provider never answers at the forward deadline, and not before",
     async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      let answered = false;
      const pending = call(blob, new Request("https://egress/v1/models", {
        headers: steer({ mode: "hang" }) }), env);
      void pending.then(() => {
        answered = true;
      });
      // The deadline's timer is the call's only one: set once its request is on its way.
      await vi.waitFor(() => expect(vi.getTimerCount()).toBe(1));
      await vi.advanceTimersByTimeAsync(EGRESS_DEADLINE_S * 1000 - 1_000);
      expect(answered).toBe(false);
      await vi.advanceTimersByTimeAsync(1_000);
      const response = await pending;
      expect([response.status, await response.json()])
        .toEqual([504, { error: { code: "deadline", message: "deadline" } }]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never follows a redirect", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "redirect" }) }), env);
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://evil.test/steal");
  });

  it("redacts the secret from the provider's answer, and drops forged x-ta headers", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "quote" }) }), env);
    expect(response.status).toBe(401);
    const text = await response.text();
    expect(text).not.toContain(SECRET);
    expect(text).toBe('{"error":"bad key [redacted] and again [redacted]"}');
    expect(response.headers.get("x-ta-resealed")).toBeNull();
  });

  it("reports a reseal when the blob's kid is retiring", async () => {
    const old = ring("k1");
    const blob = await sealed(old, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "echo" }) }), rotate(old, "k2", ["k2"]));
    expect(response.status).toBe(200);
    expect(JSON.parse(response.headers.get("x-ta-resealed")!)).toMatchObject({ version: 2, kid: "k2" });
  });

  it("redacts the secret from the provider's response headers", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "hdr" }) }), env);
    expect(response.headers.get("x-echo-key")).toBe("[redacted]");
  });

  it("redacts a JSON-escaped spelling of the secret", async () => {
    const env = ring("k1");
    const slashed = "sk-a/b/c-0123456789";
    const blob = await sealed(env, ALICE, slashed, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "jsonesc" }) }), env);
    expect(await response.text()).toBe('{"k":"[redacted]"}');
  });

  it.each(["base64", "base64url", "uri", "uri-lower", "form", "plain"])(
    "redacts the secret written back as %s, wherever it starts, from a JSON body and a Location",
    async (encoding) => {
      const env = ring("k1");
      const blob = await sealed(env, ALICE, ODD, OPENAI);
      // Each start in a base64 text: the secret after 0, 1 and 2 bytes, and after `Bearer `.
      for (const before of ["", "x", "xy", "Bearer "]) {
        const response = await call(blob, new Request("https://egress/v1/models", {
          headers: steer({ mode: "encoded", encoding, before }) }), env);
        for (const said of [await response.text(), response.headers.get("location")!]) {
          expect(said.split("[redacted]"), `${encoding} after "${before}": ${said}`)
            .toHaveLength(2);
          expect(said).not.toContain(ODD);
        }
      }
    });

  it("redacts the refresh token and a client secret, not only the access token", async () => {
    const env = ring("k1");
    const plaintext = {
      kind: "oauth2" as const, upstream: OPENAI.upstream, endpoints: OPENAI.endpoints, sealed_at: 1,
      oauth: { client_id: "c", client_auth: { method: "client_secret_post" as const, secret: "cs-xyz-1" },
               token_endpoint: "https://auth.test/token",
               access: "at-xyz-1", refresh: "rt-xyz-1", expires_at: null },
    };
    const blob = (await seal(env, ALICE, plaintext)).blob;
    for (const said of ["rt-xyz-1", "cs-xyz-1", "at-xyz-1"]) {
      const response = await call(blob, new Request("https://egress/v1/models", {
        headers: steer({ mode: "say", say: `got ${said}` }) }), env);
      expect(await response.text()).toBe("got [redacted]");
    }
  });

  it("strips a method override, so a sealed POST cannot act as another method", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, COUNTING);
    const response = await call(blob, new Request(COUNT, {
      method: "POST", body: "{}",
      headers: { ...steer({ mode: "echo" }), "x-http-method-override": "DELETE",
                 "x-http-method": "DELETE", "x-method-override": "DELETE" } }), env);
    const echo = await response.json() as { headers: Record<string, string> };
    for (const name of ["x-http-method-override", "x-http-method", "x-method-override"]) {
      expect(echo.headers[name]).toBeUndefined();
    }
  });

  it("asks for plain bytes, and drops the content encoding of a body it rewrote", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const echoed = await call(blob, new Request("https://egress/v1/models", {
      headers: { ...steer({ mode: "echo" }), "accept-encoding": "gzip, br" } }), env);
    expect(((await echoed.json()) as { headers: Record<string, string> })
      .headers["accept-encoding"]).toBe("identity");
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: steer({ mode: "decoded" }) }), env);
    expect(response.headers.get("content-encoding")).toBeNull();
    expect(await response.text()).toBe("plain text, decoded on the way");
  });

  it("fails loudly with no usable keyring", async () => {
    const blob = await sealed(ring("k1"), ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models"),
                                { GATEWAY_KEYRING: "{}" });
    expect(response.status).toBe(500);
    expect(((await response.json()) as { error: { code: string } }).error.code)
      .toBe("keyring_unavailable");
  });
});

describe("redact", () => {
  async function through(chunks: string[], secrets: string[]): Promise<string> {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    });
    return new Response(redaction(secrets).stream(body)).text();
  }

  it("redacts a secret split across every chunk boundary", async () => {
    const text = `a ${SECRET} b ${SECRET}`;
    for (let size = 1; size <= 9; size++) {
      const chunks: string[] = [];
      for (let at = 0; at < text.length; at += size) {
        chunks.push(text.slice(at, at + size));
      }
      expect(await through(chunks, [SECRET])).toBe("a [redacted] b [redacted]");
    }
  });

  it("redacts each of several secrets, and leaves a near miss alone", async () => {
    expect(await through(["access-1 refresh-22 access-", "1x refresh-2"],
                         ["access-1", "refresh-22"]))
      .toBe("[redacted] [redacted] [redacted]x refresh-2");
  });

  it("redacts a shorter secret held back at the very end of the stream", async () => {
    expect(await through(["head ", "short"], ["a-much-longer-secret-value", "short"]))
      .toBe("head [redacted]");
    expect(await through(["short"], ["a-much-longer-secret-value", "short"])).toBe("[redacted]");
  });

  it("redacts each encoded spelling split across every chunk boundary", async () => {
    const bytes = (text: string) => String.fromCharCode(...new TextEncoder().encode(text));
    for (const written of [btoa(bytes(`xy${ODD}|tail`)), encodeURIComponent(`x${ODD}`),
                           new URLSearchParams({ v: ODD }).toString(),
                           JSON.stringify(btoa(bytes(`x${ODD}`))).split("/").join("\\/")]) {
      const text = `a ${written} b`;
      const whole = await through([text], [ODD]);
      expect(whole.split("[redacted]"), written).toHaveLength(2);
      for (let size = 1; size <= 9; size++) {
        const chunks: string[] = [];
        for (let at = 0; at < text.length; at += size) {
          chunks.push(text.slice(at, at + size));
        }
        expect(await through(chunks, [ODD]), `${written} in chunks of ${size}`).toBe(whole);
      }
    }
  });

  it("passes a body with no secret through unchanged", async () => {
    expect(await through(["plain ", "body"], [])).toBe("plain body");
    expect(await through(["plain ", "body"], ["absent"])).toBe("plain body");
  });
});

describe("verify", () => {
  it("passes a working key and lists pi-ai's catalog models for its provider, with " +
     "their metadata", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-good", ANTHROPIC);
    const verified = await verify(env, ALICE, blob, builtinCatalog());
    const catalog = builtinCatalog().provider("anthropic")!.getModels()
      .filter((model) => model.api === "anthropic-messages");
    expect(verified.models.map((model) => model.id)).toEqual(catalog.map((model) => model.id));
    expect(verified.models.length).toBeGreaterThan(0);
    // Where sandbox egress may forward it: the sealed base URL and endpoints, no secret.
    // A source of one API is handed as the owner's version before took it (`handed`).
    expect(verified.egress).toEqual({ api: "anthropic-messages",
                                      base_url: "https://api.anthropic.com",
                                      endpoints: ANTHROPIC.endpoints, meter: {
                                        method: "POST", path: "/v1/messages",
                                        model_field: ["model"], output_field: ["max_tokens"] } });
    expect(JSON.stringify(verified)).not.toContain("sk-good");
    // Each model's whole pi-ai record, but for what is the gateway's: where and how it
    // speaks to the upstream.
    verified.models.forEach((record, index) => {
      const { api: _a, provider: _p, baseUrl: _b, headers: _h, compat: _c, type: _t,
              samplingParams: _s, samplingParamsByThinkingLevel: _l, ...kept } = catalog[index]!;
      expect(record).toEqual(kept);
    });
    expect(verified.models.some((record) => record.thinkingLevelMap?.xhigh !== undefined))
      .toBe(true);
    expect(verified.models.some((record) => record.inputLimits?.images?.resize !== undefined))
      .toBe(true);
  });

  it("hands a source of several APIs as one scope, each API's meter learned, and each model " +
     "naming its API", async () => {
    const env = ring("k1");
    const both: Source = {
      upstream: { provider: "openrouter",
                  apis: [{ api: "openai-completions", base_url: "https://openrouter.ai/api/v1" },
                         { api: "anthropic-messages", base_url: "https://openrouter.ai/api" }],
                  auth: { header: "authorization", scheme: "Bearer" },
                  verify_url: "https://openrouter.ai/api/v1/models/user" },
      endpoints: [{ origin: "https://openrouter.ai",
                    paths: ["/api/v1/chat/completions", "/api/v1/messages", "/api/v1/models/user"],
                    methods: ["GET", "POST"] }] };
    const verified = await verify(env, ALICE, await sealed(env, ALICE, "sk-good", both),
                                  builtinCatalog());
    expect(verified.egress).toMatchObject({ apis: [
      { api: "openai-completions", base_url: "https://openrouter.ai/api/v1",
        meter: { method: "POST", path: "/api/v1/chat/completions" } },
      { api: "anthropic-messages", base_url: "https://openrouter.ai/api",
        meter: { method: "POST", path: "/api/v1/messages" } }], endpoints: both.endpoints });
    const apiOf = new Map(verified.models.map((model) => [model.id, model.api]));
    expect(apiOf.get("anthropic/claude-opus-5.5")).toBe("anthropic-messages");
    expect(apiOf.get("openrouter/auto")).toBe("openai-completions");
    expect(new Set(apiOf.values())).toEqual(new Set(["openai-completions", "anthropic-messages"]));
  });

  it("lists records every one of which a declared endpoint could declare, for every " +
     "built-in catalog", () => {
    let records = 0;
    for (const provider of builtinProviders()) {
      // A model of an API the gateway runs: none other is sealed, so none is listed.
      for (const model of provider.getModels().filter((each) => apiRules(each.api) !== null)) {
        if (isModelType(model, "chat")) {
          expect(checkedRecord(JSON.parse(JSON.stringify(recordOf(model))))).toEqual(recordOf(model));
          records += 1;
        }
      }
    }
    expect(records).toBeGreaterThan(1_000);
  });

  it("lists a catalog's chat models only, whatever else it serves on the same API", () => {
    const chat = builtinCatalog().provider("anthropic")!.getModels()
      .find((model) => model.api === "anthropic-messages")!;
    const image = { ...chat, id: "an-image-model", type: "image", output: ["image"] };
    const catalog = { provider: () => ({ ...builtinCatalog().provider("anthropic")!,
                                         getModels: () => [chat, image] }) as never };
    expect(modelInfos(catalog, ANTHROPIC.upstream).map((model) => model.id)).toEqual([chat.id]);
  });

  it("answers a refused key with a fixed code", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-bad", ANTHROPIC);
    const error = await verify(env, ALICE, blob, builtinCatalog()).catch((caught) => caught);
    expect(error).toBeInstanceOf(Refusal);
    expect((error as Refusal).message).toBe("upstream_unauthorized");
  });

  it("never follows a redirect from the setup check", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-redirect", ANTHROPIC);
    const error = await verify(env, ALICE, blob, builtinCatalog()).catch((caught) => caught);
    expect((error as Refusal).message).toBe("upstream_error");
    const evil = await (await fetch("https://status.test/evil")).json() as unknown[];
    expect(JSON.stringify(evil)).not.toContain("sk-redirect");
  });

  it("refuses a source with no setup check", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    await expect(verify(env, ALICE, blob, builtinCatalog())).rejects.toThrow("unsupported_source");
  });
});

describe("credit", () => {
  /** OpenAI's calls, with a key's limits read at `credit.test` sealed beside them. */
  const LIMITED: Source = Object.freeze({ ...OPENAI, endpoints: [...OPENAI.endpoints, {
    origin: "https://credit.test", paths: ["/key"], methods: ["GET"] }] });
  /** OpenRouter's read, as its source record has it. */
  const READ = { url: "https://credit.test/key", none_when: [
    { path: ["data", "is_free_tier"], equals: true },
    { path: ["data", "limit_remaining"], at_most: 0 }] };
  const read = async (key: string, record: Source = LIMITED, asked: unknown = READ) => {
    const env = ring("k1");
    return credit(env, ALICE, await sealed(env, ALICE, key, record), asked)
      .catch((caught: unknown) => caught);
  };

  it("reads whether a key pays for a priced model where its source reports it", async () => {
    expect(await read("sk-free")).toEqual({ pays: false });
    expect(await read("sk-dry")).toEqual({ pays: false });
    expect(await read("sk-paid")).toEqual({ pays: true });
  });

  it("answers a refused key, an answer of another shape and a redirect with fixed codes, " +
     "never following the redirect", async () => {
    expect(await read("sk-bad")).toMatchObject({ code: "upstream_unauthorized" });
    expect(await read("sk-odd")).toMatchObject({ code: "upstream_error" });
    expect(await read("sk-redirect")).toMatchObject({ code: "upstream_error" });
    const evil = await (await fetch("https://status.test/evil")).json() as unknown[];
    expect(JSON.stringify(evil)).not.toContain("sk-redirect");
  });

  it("sends the key only to a GET its sealed endpoints allow, and takes only a read",
     async () => {
    // A blob sealed before its record named the read: nothing is sent.
    expect(await read("sk-free", OPENAI)).toMatchObject({ code: "endpoint_refused" });
    expect(await read("sk-free", LIMITED, { ...READ, url: "https://evil.test/key" }))
      .toMatchObject({ code: "endpoint_refused" });
    const evil = await (await fetch("https://status.test/evil")).json() as unknown[];
    expect(JSON.stringify(evil)).not.toContain("sk-free");
    for (const bad of [{ ...READ, extra: 1 }, { ...READ, none_when: [] },
                       { ...READ, url: "http://credit.test/key" },
                       { ...READ, url: "https://credit.test/key?key=1" },
                       { ...READ, none_when: [{ path: ["data"], equals: "yes" }] }]) {
      expect(await read("sk-free", LIMITED, bad), JSON.stringify(bad))
        .toMatchObject({ code: "invalid_request" });
    }
  });
});

describe("listing", () => {
  /** OpenAI's calls, with a vendor's model listing at `models.test` sealed beside them. */
  const LISTED: Source = Object.freeze({ ...OPENAI, endpoints: [...OPENAI.endpoints, {
    origin: "https://models.test", paths: ["/models"], methods: ["GET"] }] });
  /** OpenRouter's read, as its source record has it. */
  const READ = { url: "https://models.test/models", rows: ["data"], id: ["id"],
                 score: ["benchmarks", "artificial_analysis", "intelligence_index"] };
  const { score: _score, ...UNSCORED } = READ;
  const read = async (key: string, record: Source = LISTED, asked: unknown = READ) => {
    const env = ring("k1");
    return listing(env, ALICE, await sealed(env, ALICE, key, record), asked)
      .catch((caught: unknown) => caught);
  };

  it("reads each model its vendor serves the key, with its score where it lists one",
     async () => {
    expect(await read("sk-listed")).toEqual({ models: [
      { id: "nvidia/nemotron-3.5-super-vl:free", score: 13.6 },
      { id: "cohere/north-mini-code:free", score: null },
      { id: "google/gemma-4-26b-a4b-it:free", score: null }] });
    // A vendor that gives no score: each model served, none scored.
    expect(await read("sk-unscored", LISTED, UNSCORED)).toEqual({ models: [
      { id: "cohere/north-mini-code:free", score: null }] });
  });

  it("answers a refused key and an answer of another shape, empty, or with no score where the " +
     "read names one, with fixed codes", async () => {
    expect(await read("sk-bad")).toMatchObject({ code: "upstream_unauthorized" });
    for (const key of ["sk-odd", "sk-empty", "sk-noid", "sk-unscored"]) {
      expect(await read(key), key).toMatchObject({ code: "upstream_error" });
    }
    for (const key of ["sk-odd", "sk-empty", "sk-noid"]) {
      expect(await read(key, LISTED, UNSCORED), key).toMatchObject({ code: "upstream_error" });
    }
  });

  it("sends the key only to a GET its sealed endpoints allow, and takes only a read",
     async () => {
    expect(await read("sk-listed", OPENAI)).toMatchObject({ code: "endpoint_refused" });
    for (const bad of [{ ...READ, extra: 1 }, { ...READ, rows: [] },
                       { ...READ, url: "https://models.test/models?all=1" },
                       { ...READ, score: ["benchmarks", "a.b"] }]) {
      expect(await read("sk-listed", LISTED, bad), JSON.stringify(bad))
        .toMatchObject({ code: "invalid_request" });
    }
  });
});

describe("a service's key", () => {
  const URL_SEALED = "https://mcp.example.com/mcp/";
  /** A key sealed for the MCP server at `URL_SEALED`, sent as `x-api-key` with no scheme. */
  const service = (env: ReturnType<typeof ring>) => {
    const { upstream, endpoints } = serviceSource(URL_SEALED, { header: "x-api-key",
                                                                 scheme: "" });
    return seal(env, ALICE, { kind: "api_key", secret: SECRET, upstream, endpoints,
                              sealed_at: Date.now() }).then((made) => made.blob);
  };

  it("is put in by its own header on its own URL alone, on each of the transport's methods, " +
     "and redacted from the answer", async () => {
    const env = ring("k1");
    const blob = await service(env);
    for (const method of ["POST", "GET", "DELETE"]) {
      const response = await call(blob, new Request("https://egress/mcp/", {
        method, headers: { authorization: "Bearer ph", ...steer({ mode: "echo" }) },
        ...(method === "POST" ? { body: "{}" } : {}) }), env);
      expect(response.status).toBe(200);
      const echo = await response.json() as { host: string; path: string; method: string;
                                              digests: Record<string, string> };
      expect(echo).toMatchObject({ host: "mcp.example.com", path: "/mcp/", method });
      expect(echo.digests["x-api-key"]).toBe(await sha256(SECRET));
      expect(echo.digests["authorization"]).toBeUndefined();
    }
    const quoted = await call(blob, new Request("https://egress/mcp/", {
      headers: steer({ mode: "hdr" }) }), env);
    expect(quoted.headers.get("x-echo-key")).not.toContain(SECRET);
  });

  it("is sent with the transport's own headers and no other", async () => {
    const env = ring("k1");
    const transport = {
      accept: "application/json, text/event-stream", "content-type": "application/json",
      "mcp-protocol-version": "2026-07-28", "mcp-session-id": "s-1", "last-event-id": "e-1",
      "mcp-method": "tools/call", "mcp-name": "search", "mcp-param-region": "eu",
    };
    const response = await call(await service(env), new Request("https://egress/mcp/", {
      method: "POST", body: "{}",
      headers: { ...transport, "x-auth-token": "the sandbox's own",
                 "anthropic-version": "2023-06-01", "mcp-protocol-version-x": "1",
                 ...steer({ mode: "echo" }) } }), env);
    const { headers } = await response.json() as { headers: Record<string, string> };
    expect(headers).toMatchObject(transport);
    for (const dropped of ["x-auth-token", "anthropic-version", "mcp-protocol-version-x"]) {
      expect(headers[dropped], dropped).toBeUndefined();
    }
  });

  it.each([
    ["another path at its host", "POST", "https://egress/admin"],
    ["a path below its own", "POST", "https://egress/mcp/x"],
    ["its path but for its slash", "POST", "https://egress/mcp"],
    ["a method the transport never sends", "PUT", "https://egress/mcp/"],
    ["a path naming another host", "POST", "https://egress//evil.test/mcp/"],
    ["its URL with a query", "POST", "https://egress/mcp/?session=1"],
  ])("is never sent to %s", async (_what, method, url) => {
    const env = ring("k1");
    const response = await call(await service(env), new Request(url, {
      method, headers: steer({ mode: "echo" }), body: "{}" }), env);
    expect(response.status).toBe(403);
    expect(((await response.json()) as { error: { code: string } }).error.code)
      .toBe("endpoint_refused");
  });

  it("is never metered: a metered forward of it is refused before anything is sent",
     async () => {
    const env = ring("k1");
    const reserve = vi.fn();
    const { response } = await forwardMetered(env, ALICE, await service(env), new Request(
      "https://egress/mcp/", { method: "POST", headers: steer({ mode: "echo" }),
                               body: "{}" }), reserve, new ReadableStream(), builtinCatalog());
    expect(((await response.json()) as { error: { code: string } }).error.code)
      .toBe("unmetered");
    expect(reserve).not.toHaveBeenCalled();
  });

  it("opens to its scope at the setup check, with no models and nothing sent", async () => {
    const env = ring("k1");
    const verified = await verify(env, ALICE, await service(env), builtinCatalog());
    expect(verified).toEqual({ models: [], egress: {
      kind: "service", base_url: URL_SEALED,
      endpoints: [{ origin: "https://mcp.example.com", paths: ["/mcp/"],
                    methods: ["POST", "GET", "DELETE"] }] } });
  });
});
