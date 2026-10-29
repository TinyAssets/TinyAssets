/**
 * The sealed source data and where a credential may go (research §1.3, §2.4), the
 * envelope (§1.4), and the fixed codes (`failure.ts`).
 */
import { describe, expect, it } from "vitest";

import { allows, credential, type Endpoint, source } from "../src/credential";
import { DEADLINE_MAX_MS, encodeEnvelope, envelope } from "../src/envelope";
import { failureResponse, upstreamCode } from "../src/failure";
import { ANTHROPIC, apiKey, GOOGLE, OPENAI } from "./support";

const ENDPOINTS: readonly Endpoint[] = Object.freeze([
  { origin: "https://api.example.com", paths: ["/v1/messages", "/v1/models/*"], methods: ["POST"] },
  { origin: "https://files.example.com", paths: ["/*"], methods: ["GET"] },
]);

describe("allows", () => {
  it.each([
    ["POST", "https://api.example.com/v1/messages", true],
    ["POST", "https://api.example.com/v1/messages?beta=1", true],
    ["POST", "https://api.example.com/v1/models/a:stream", true],
    ["POST", "https://api.example.com/v1/models/", true],
    ["POST", "https://api.example.com/v1/models", false],
    ["POST", "https://api.example.com/v1/messages/", false],
    ["POST", "https://api.example.com/v1/messagesx", false],
    ["GET", "https://api.example.com/v1/messages", false],
    ["POST", "http://api.example.com/v1/messages", false],
    ["POST", "https://api.example.com:8443/v1/messages", false],
    ["POST", "https://evil.example.com/v1/messages", false],
    ["POST", "https://api.example.com.evil.test/v1/messages", false],
    ["POST", "https://user:pass@api.example.com/v1/messages", false],
    ["POST", "https://api.example.com/v1/models/a%2F..%2F..%2Fadmin", false],
    ["POST", "https://api.example.com/v1/models/%2e%2e/admin", false],
    ["POST", "https://api.example.com/v1/models/a%5Cb", false],
    ["GET", "https://files.example.com/anything/at/all", true],
    ["GET", "https://files.example.com/", true],
    ["POST", "https://files.example.com/x", false],
  ])("%s %s is %s", (method, url, expected) => {
    expect(allows(ENDPOINTS, method, new URL(url))).toBe(expected);
  });

  it("reads a dotted path as the URL parser resolves it", () => {
    expect(allows(ENDPOINTS, "POST", new URL("https://api.example.com/v1/models/../messages")))
      .toBe(true);
    expect(allows(ENDPOINTS, "POST", new URL("https://api.example.com/v1/models/../../admin")))
      .toBe(false);
  });
});

describe("source", () => {
  it("accepts the source records the tests use", () => {
    for (const record of [ANTHROPIC, OPENAI, GOOGLE]) {
      expect(source(record, false)).toEqual(record);
    }
  });

  const bad = (patch: (record: Record<string, unknown>) => unknown) =>
    patch(structuredClone(OPENAI) as unknown as Record<string, unknown>);
  it.each([
    ["a base URL off the sealed origins", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), base_url: "https://evil.test" } }))],
    ["an http base URL", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), base_url: "http://api.openai.com" } }))],
    ["an IP-literal base URL", bad(() => ({ upstream: { ...OPENAI.upstream,
      base_url: "https://10.0.0.1" }, endpoints: [{ origin: "https://10.0.0.1", paths: ["/*"],
                                                     methods: ["POST"] }] }))],
    ["an IPv6 origin", bad(() => ({ upstream: { ...OPENAI.upstream, base_url: "https://[::1]" },
      endpoints: [{ origin: "https://[::1]", paths: ["/*"], methods: ["POST"] }] }))],
    ["a base URL with user info", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), base_url: "https://u:p@api.openai.com" } }))],
    ["an origin with a path", bad((r) => ({ ...r, endpoints: [{
      origin: "https://api.openai.com/v1", paths: ["/*"], methods: ["POST"] }] }))],
    ["a wildcard mid-path", bad((r) => ({ ...r, endpoints: [{
      origin: "https://api.openai.com", paths: ["/v1*"], methods: ["POST"] }] }))],
    ["a relative path", bad((r) => ({ ...r, endpoints: [{
      origin: "https://api.openai.com", paths: ["v1/messages"], methods: ["POST"] }] }))],
    ["an unknown method", bad((r) => ({ ...r, endpoints: [{
      origin: "https://api.openai.com", paths: ["/v1/messages"], methods: ["CONNECT"] }] }))],
    ["no endpoints", bad((r) => ({ ...r, endpoints: [] }))],
    ["an auth header with a space", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), auth: { header: "x api key", scheme: "" } } }))],
    ["a verify URL off the endpoints", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), verify_url: "https://api.openai.com/v1/files" } }))],
    ["models on a built-in provider", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), models: [] } }))],
    ["a custom provider with no models", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), provider: "custom" } }))],
    ["an extra field", bad((r) => ({ ...r, note: "x" }))],
    ["an OAuth block on a key", bad((r) => ({ ...r, oauth: {} }))],
    ...[["an empty query", "https://api.openai.com/v1?"], ["an empty fragment", "https://api.openai.com/v1#"],
        ["a query", "https://api.openai.com/v1?x=1"], ["a fragment", "https://api.openai.com/v1#x"],
        ["an upper-case host", "https://API.openai.com/v1"], ["a trailing-dot host", "https://api.openai.com./v1"],
        ["a dot segment", "https://api.openai.com/x/../v1"], ["a default port", "https://api.openai.com:443/v1"],
        ["an encoded slash", "https://api.openai.com/v1%2Fx"]]
      .map(([what, base]) => [`a base URL with ${what}`, bad((r) => ({ ...r, upstream: {
        ...(r["upstream"] as object), base_url: base } }))] as [string, unknown]),
    ["a trailing-dot host on both the base URL and its origin", bad((r) => ({
      upstream: { ...(r["upstream"] as object), base_url: "https://api.openai.com./v1" },
      endpoints: [{ origin: "https://api.openai.com.", paths: ["/*"], methods: ["POST"] }] }))],
    ["an origin with a trailing slash", bad((r) => ({ ...r, endpoints: [{
      origin: "https://api.openai.com/", paths: ["/*"], methods: ["POST"] }] }))],
    ["a verify URL with an empty query", bad((r) => ({ ...r, upstream: {
      ...(r["upstream"] as object), verify_url: "https://api.openai.com/v1/models?" } }))],
  ])("refuses %s", (_what, record) => {
    expect(() => source(record, false)).toThrow("invalid_source");
  });

  it("accepts a declared custom endpoint with its models", () => {
    const custom = {
      upstream: { provider: "custom", api: "openai-completions", base_url: "https://llm.example.com/v1",
                  auth: { header: "authorization", scheme: "Bearer" },
                  models: [{ id: "local-1", name: "Local", reasoning: false, input: ["text"],
                             contextWindow: 32000, maxTokens: 4096,
                             cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }] },
      endpoints: [{ origin: "https://llm.example.com", paths: ["/v1/chat/completions"],
                    methods: ["POST"] }],
    };
    expect(source(custom, false).upstream.models).toHaveLength(1);
  });
});

describe("credential", () => {
  it("refuses a plaintext of another shape as credential_refused", () => {
    const good = apiKey("sk", ANTHROPIC);
    expect(credential(good)).toEqual(good);
    for (const broken of [{ ...good, kind: "oauth2" }, { ...good, secret: "a b" },
                          { ...good, oauth: {} }, { ...good, sealed_at: -1 }, null, []]) {
      expect(() => credential(broken)).toThrow("credential_refused");
    }
  });
});

describe("envelope", () => {
  const now = 1_000_000;
  const fields = { owner: "alice", connection: "c1", version: 1, blob: "v1.k1.x.y",
                   reservation: { id: "r-1", max_output_tokens: 10 }, policy_generation: 0,
                   deadline: now + 1000 };

  it("reads what the owner object sends, owner names of any printable text included", () => {
    const read = envelope(encodeEnvelope({ ...fields, owner: "Ålice 🌊" }), now);
    expect(read.binding).toEqual({ owner: "Ålice 🌊", connection: "c1", version: 1 });
    expect(read.reservation).toEqual({ id: "r-1", max_output_tokens: 10 });
  });

  it.each([
    ["a missing field", (() => { const { policy_generation: _g, ...rest } = fields; return rest; })()],
    ["an extra field", { ...fields, model: "m" }],
    ["a zero cap", { ...fields, reservation: { id: "r-1", max_output_tokens: 0 } }],
    ["a fractional cap", { ...fields, reservation: { id: "r-1", max_output_tokens: 1.5 } }],
    ["an extra reservation field", { ...fields, reservation: { id: "r", max_output_tokens: 1, x: 1 } }],
    ["a bad owner", { ...fields, owner: " alice" }],
    ["a version of zero", { ...fields, version: 0 }],
    ["a negative generation", { ...fields, policy_generation: -1 }],
    ["a deadline too far ahead", { ...fields, deadline: now + DEADLINE_MAX_MS + 1 }],
    ["a blob that is not text", { ...fields, blob: 1 }],
    ["a reservation id with a space", { ...fields, reservation: { id: "r 1", max_output_tokens: 1 } }],
    ["a reservation id starting with a dot", { ...fields, reservation: { id: ".r", max_output_tokens: 1 } }],
    ["a header over its bound", { ...fields, blob: "b".repeat(96 * 1024) }],
  ])("refuses %s", (_what, value) => {
    expect(() => envelope(encodeEnvelope(value as typeof fields), now)).toThrow("invalid_envelope");
  });

  it("refuses a deadline that has passed as deadline", () => {
    expect(() => envelope(encodeEnvelope({ ...fields, deadline: now }), now)).toThrow("deadline");
  });

  it("refuses a header that is not base64url JSON", () => {
    for (const header of [null, "", "a", "!!!!", btoa("not json"), "x".repeat(100 * 1024)]) {
      expect(() => envelope(header, now)).toThrow("invalid_envelope");
    }
  });
});

describe("fixed codes", () => {
  it.each([
    [401, "upstream_unauthorized"], [403, "upstream_unauthorized"], [429, "upstream_rate_limited"],
    [408, "upstream_timeout"], [400, "upstream_rejected"], [404, "upstream_rejected"],
    [499, "upstream_rejected"], [503, "upstream_overloaded"], [529, "upstream_overloaded"],
    [500, "upstream_server_error"], [502, "upstream_server_error"],
    [504, "upstream_server_error"], [302, "upstream_error"],
  ] as const)("names upstream %d as %s", (status, code) => {
    expect(upstreamCode(status)).toBe(code);
  });

  it("answers with the code and nothing else", async () => {
    const response = failureResponse("endpoint_refused");
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: { code: "endpoint_refused",
                                                     message: "endpoint_refused" } });
  });
});
