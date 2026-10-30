/**
 * Raw passthrough for sandbox egress (research §5.4): inbound credentials stripped, the
 * sealed one injected, the sealed endpoints enforced, the deadline, and the secret
 * redacted from what comes back. And the connection's setup check (`verify`).
 */
import { describe, expect, it } from "vitest";

import { isModelType } from "@earendil-works/pi-ai/models";
import { builtinProviders } from "@earendil-works/pi-ai/providers/all";

import { builtinCatalog } from "../src/catalog";
import { checkedRecord, recordOf } from "../src/model-record";
import { Failure } from "../src/failure";
import { forward, redact, verify } from "../src/forward";
import { seal } from "../src/keyring";
import { ALICE, ANTHROPIC, OPENAI, ring, rotate, sealed } from "./support";

const SECRET = "sk-alice-forward-0123456789";

/** The fake upstream echoes credential headers as SHA-256 hex (fake-upstream.ts). */
async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function call(blob: string, request: Request, env = ring("k1"), deadlineMs?: number) {
  return forward(env, ALICE, blob, request, deadlineMs);
}

describe("forward", () => {
  it("strips every inbound credential, injects the sealed one, and passes the rest", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request(
      "https://egress.tinyassets.io/v1/responses?key=placeholder&api_key=p&stream=true", {
        method: "POST",
        headers: {
          authorization: "Bearer ta_ph_c1_placeholder", "x-api-key": "ta_ph_c1_placeholder",
          "x-goog-api-key": "ta_ph", "api-key": "ta_ph", cookie: "session=s",
          "proxy-authorization": "Basic x", "x-ta-envelope": "forged", "x-ta-sandbox": "jwt",
          "cf-connecting-ip": "10.0.0.1", "x-forwarded-for": "10.0.0.1",
          "openai-beta": "responses=v1", "content-type": "application/json",
          "x-fake-mode": "echo",
        },
        body: JSON.stringify({ input: "hi" }),
      }), env);
    expect(response.status).toBe(200);
    const echo = await response.json() as { digests: Record<string, string>; method: string;
                                            host: string; path: string; query: string;
                                            headers: Record<string, string>; body: string };
    expect(echo.method).toBe("POST");
    expect(echo.host).toBe("api.openai.com");
    expect(echo.path).toBe("/v1/responses");
    expect(echo.query).toBe("?stream=true");
    expect(echo.body).toBe(JSON.stringify({ input: "hi" }));
    expect(echo.digests["authorization"]).toBe(await sha256(`Bearer ${SECRET}`));
    for (const stripped of ["x-api-key", "x-goog-api-key", "api-key", "cookie",
                            "proxy-authorization", "x-ta-envelope", "x-ta-sandbox",
                            "cf-connecting-ip", "x-forwarded-for"]) {
      expect(echo.headers[stripped]).toBeUndefined();
    }
    expect(echo.headers["openai-beta"]).toBe("responses=v1");
    expect(response.headers.get("x-upstream")).toBe("fake");
    expect(response.headers.get("cf-ray")).toBeNull();
  });

  it("injects by the sealed template: a bare header for Anthropic's convention", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, ANTHROPIC);
    const response = await call(blob, new Request("https://egress/v1/messages", {
      method: "POST", headers: { authorization: "Bearer ph", "x-api-key": "ph",
                                 "x-fake-mode": "echo" }, body: "{}" }), env);
    const echo = await response.json() as { digests: Record<string, string>; host: string; headers: Record<string, string> };
    expect(echo.host).toBe("api.anthropic.com");
    expect(echo.digests["x-api-key"]).toBe(await sha256(SECRET));
    expect(echo.digests["authorization"]).toBeUndefined();
  });

  it.each([
    ["a path not sealed", "POST", "https://egress/v1/files"],
    ["a method not sealed", "DELETE", "https://egress/v1/responses"],
    ["an encoded separator", "POST", "https://egress/v1/responses%2F..%2Ffiles"],
    ["an encoded dot", "POST", "https://egress/v1/%2e%2e/admin"],
    ["a prefix of a sealed path", "POST", "https://egress/v1/responses-admin"],
    ["a path naming another host", "POST", "https://egress//evil.test/v1/responses"],
  ])("refuses %s, before anything is sent", async (_what, method, url) => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request(url, {
      method, headers: { "x-fake-mode": "echo" }, ...(method === "GET" ? {} : { body: "{}" }) }), env);
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
      method: "POST", headers: { "x-fake-mode": "echo" }, body: "{}" }), env);
    expect(response.status).toBe(403);
  });

  it("forwards only to the base URL's origin, even when another origin is sealed", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, {
      ...OPENAI,
      endpoints: [...OPENAI.endpoints,
                  { origin: "https://api.anthropic.com", paths: ["/*"], methods: ["POST"] }],
    });
    const response = await call(blob, new Request("https://egress//api.anthropic.com/v1/x", {
      method: "POST", headers: { "x-fake-mode": "echo" }, body: "{}" }), env);
    expect(response.status).toBe(403);
  });

  it("refuses the blob under another owner, connection or version", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    for (const bound of [{ ...ALICE, owner: "bob" }, { ...ALICE, connection: "c9" },
                         { ...ALICE, version: 3 }]) {
      const response = await forward(env, bound, blob, new Request("https://egress/v1/models",
        { headers: { "x-fake-mode": "echo" } }));
      expect(response.status).toBe(403);
      expect(await response.text()).toBe(JSON.stringify(
        { error: { code: "credential_refused", message: "credential_refused" } }));
    }
  });

  it("cuts a request at its deadline", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const started = Date.now();
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "hang" } }), env, 300);
    expect(response.status).toBe(504);
    expect(((await response.json()) as { error: { code: string } }).error.code).toBe("deadline");
    expect(Date.now() - started).toBeLessThan(5000);
  });

  it("never follows a redirect", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "redirect" } }), env);
    expect(response.status).toBe(307);
    expect(response.headers.get("location")).toBe("https://evil.test/steal");
  });

  it("redacts the secret from the provider's answer, and drops forged x-ta headers", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "quote" } }), env);
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
      headers: { "x-fake-mode": "echo" } }), rotate(old, "k2", ["k2"]));
    expect(response.status).toBe(200);
    expect(JSON.parse(response.headers.get("x-ta-resealed")!)).toMatchObject({ version: 2, kid: "k2" });
  });

  it("redacts the secret from the provider's response headers", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "hdr" } }), env);
    expect(response.headers.get("x-echo-key")).toBe("[redacted]");
  });

  it("redacts a JSON-escaped spelling of the secret", async () => {
    const env = ring("k1");
    const slashed = "sk-a/b/c-0123456789";
    const blob = await sealed(env, ALICE, slashed, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "jsonesc" } }), env);
    expect(await response.text()).toBe('{"k":"[redacted]"}');
  });

  it("redacts the refresh token and a client secret, not only the access token", async () => {
    const env = ring("k1");
    const plaintext = {
      kind: "oauth2" as const, upstream: OPENAI.upstream, endpoints: OPENAI.endpoints, sealed_at: 1,
      oauth: { client_id: "c", client_auth: { method: "client_secret_post" as const, secret: "cs-xyz-1" },
               token_endpoint: "https://auth.test/token", refresh_with: "rfc6749" as const,
               access: "at-xyz-1", refresh: "rt-xyz-1", expires_at: null },
    };
    const blob = (await seal(env, ALICE, plaintext)).blob;
    for (const said of ["rt-xyz-1", "cs-xyz-1", "at-xyz-1"]) {
      const response = await call(blob, new Request("https://egress/v1/models", {
        headers: { "x-fake-mode": "say", "x-fake-say": `got ${said}` } }), env);
      expect(await response.text()).toBe("got [redacted]");
    }
  });

  it("strips a method override, so a sealed POST cannot act as another method", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    const response = await call(blob, new Request("https://egress/v1/responses", {
      method: "POST", body: "{}",
      headers: { "x-fake-mode": "echo", "x-http-method-override": "DELETE",
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
      headers: { "x-fake-mode": "echo", "accept-encoding": "gzip, br" } }), env);
    expect(((await echoed.json()) as { headers: Record<string, string> })
      .headers["accept-encoding"]).toBe("identity");
    const response = await call(blob, new Request("https://egress/v1/models", {
      headers: { "x-fake-mode": "decoded" } }), env);
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
    return new Response(redact(body, secrets)).text();
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

  it("lists records every one of which a declared endpoint could declare, for every " +
     "built-in catalog", () => {
    let records = 0;
    for (const provider of builtinProviders()) {
      for (const model of provider.getModels()) {
        if (isModelType(model, "chat")) {
          expect(checkedRecord(JSON.parse(JSON.stringify(recordOf(model))))).toEqual(recordOf(model));
          records += 1;
        }
      }
    }
    expect(records).toBeGreaterThan(1_000);
  });

  it("answers a refused key with a fixed code", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-bad", ANTHROPIC);
    const error = await verify(env, ALICE, blob, builtinCatalog()).catch((caught) => caught);
    expect(error).toBeInstanceOf(Failure);
    expect((error as Failure).message).toBe("upstream_unauthorized");
  });

  it("never follows a redirect from the setup check", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-redirect", ANTHROPIC);
    const error = await verify(env, ALICE, blob, builtinCatalog()).catch((caught) => caught);
    expect((error as Failure).message).toBe("upstream_error");
    const evil = await (await fetch("https://status.test/evil")).json() as unknown[];
    expect(JSON.stringify(evil)).not.toContain("sk-redirect");
  });

  it("refuses a source with no setup check", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    await expect(verify(env, ALICE, blob, builtinCatalog())).rejects.toThrow("unsupported_source");
  });
});
