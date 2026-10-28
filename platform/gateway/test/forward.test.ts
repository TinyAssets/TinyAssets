/**
 * Raw passthrough for sandbox egress (research §5.4): inbound credentials stripped, the
 * sealed one injected, the sealed endpoints enforced, the deadline, and the secret
 * redacted from what comes back. And the connection's setup check (`verify`).
 */
import { describe, expect, it } from "vitest";

import { builtinCatalog } from "../src/catalog";
import { Failure } from "../src/failure";
import { forward, redact, verify } from "../src/forward";
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

  it("passes a body with no secret through unchanged", async () => {
    expect(await through(["plain ", "body"], [])).toBe("plain body");
    expect(await through(["plain ", "body"], ["absent"])).toBe("plain body");
  });
});

describe("verify", () => {
  it("passes a working key and lists pi-ai's catalog models for its provider", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-good", ANTHROPIC);
    const verified = await verify(env, ALICE, blob, builtinCatalog());
    const catalog = builtinCatalog().provider("anthropic")!.getModels()
      .filter((model) => model.api === "anthropic-messages").map((model) => model.id);
    expect(verified.models).toEqual(catalog);
    expect(verified.models.length).toBeGreaterThan(0);
  });

  it("answers a refused key with a fixed code", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, "sk-bad", ANTHROPIC);
    const error = await verify(env, ALICE, blob, builtinCatalog()).catch((caught) => caught);
    expect(error).toBeInstanceOf(Failure);
    expect((error as Failure).message).toBe("upstream_unauthorized");
  });

  it("refuses a source with no setup check", async () => {
    const env = ring("k1");
    const blob = await sealed(env, ALICE, SECRET, OPENAI);
    await expect(verify(env, ALICE, blob, builtinCatalog())).rejects.toThrow("unsupported_source");
  });
});
