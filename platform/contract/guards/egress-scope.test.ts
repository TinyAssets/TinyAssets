/**
 * Where a sealed credential may go (`src/egress-scope.ts`): `allows`, on hand-written
 * endpoints, and a service's key bound to its one URL (`serviceSource`, `egressScope`). The
 * source values sealed with a credential are the gateway's (`gateway/test/credential.test.ts`),
 * on the records it seals.
 */
import { describe, expect, it } from "vitest";

import {
  allows, egressHeaders, egressScope, egressTarget, type Endpoint, keySource, type ModelScope,
  modelApi, scopeApi, serviceSource, source,
} from "../src/egress-scope";

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

describe("a service's key", () => {
  const AUTH = { header: "authorization", scheme: "Bearer" };

  it("is sealed to its one URL, and its scope reads back whole", () => {
    const sealed = serviceSource("https://mcp.example.com/mcp/", AUTH);
    expect(sealed.endpoints).toEqual([{ origin: "https://mcp.example.com", paths: ["/mcp/"],
                                        methods: ["POST", "GET", "DELETE"] }]);
    const scope = { kind: "service", base_url: sealed.upstream.base_url,
                    endpoints: sealed.endpoints };
    expect(egressScope(JSON.parse(JSON.stringify(scope)))).toEqual(scope);
    expect(keySource(JSON.parse(JSON.stringify(sealed)))).toEqual(sealed);
  });

  it.each([
    ["an IP address", "https://192.168.0.1/mcp"],
    ["an IPv6 address", "https://[::1]/mcp"],
    ["a name ending in a number", "https://mcp.example.123/mcp"],
    ["a query", "https://mcp.example.com/mcp?k=v"],
    ["plain http", "http://mcp.example.com/mcp"],
  ])("refuses a URL with %s", (_what, url) => {
    expect(() => serviceSource(url, AUTH)).toThrow("invalid_source");
  });

  it("refuses a scope or a seal whose URL is not exactly its one endpoint", () => {
    const endpoints = [{ origin: "https://mcp.example.com", paths: ["/mcp/"],
                         methods: ["POST"] }];
    for (const [base, sealed] of [
      ["https://mcp.example.com/other", endpoints],
      ["https://evil.example.com/mcp/", endpoints],
      ["https://mcp.example.com/mcp/",
       [...endpoints, { ...endpoints[0]!, origin: "https://evil.example.com" }]],
      ["https://mcp.example.com/mcp/", [{ ...endpoints[0]!, paths: ["/mcp/", "/admin"] }]],
    ] as const) {
      expect(() => egressScope({ kind: "service", base_url: base, endpoints: sealed }))
        .toThrow("invalid_source");
      expect(() => keySource({ upstream: { kind: "service", base_url: base, auth: AUTH },
                               endpoints: sealed })).toThrow("invalid_source");
    }
  });
});

describe("a model source of several APIs", () => {
  const AUTH = { header: "authorization", scheme: "Bearer" };
  const endpoints = [{ origin: "https://router.example.com",
                       paths: ["/api/v1/chat/completions", "/api/v1/messages", "/api/v1/models"],
                       methods: ["GET", "POST"] }];
  const apis = [{ api: "openai-completions", base_url: "https://router.example.com/api/v1" },
                { api: "anthropic-messages", base_url: "https://router.example.com/api" }];
  const meter = (path: string) => ({ method: "POST", path, model_field: ["model"],
                                     output_field: ["max_tokens"] });
  const scope = egressScope({ apis: [
    { ...apis[0]!, meter: meter("/api/v1/chat/completions") },
    { ...apis[1]!, meter: meter("/api/v1/messages") }], endpoints }) as ModelScope;

  it("is sealed with each API at its base, all at one origin, no API twice", () => {
    expect(source({ upstream: { provider: "router", apis, auth: AUTH }, endpoints }, false)
      .upstream.apis).toEqual(apis);
    for (const bad of [
      [apis[0], { ...apis[1]!, base_url: "https://other.example.com/api" }],
      [apis[0], { ...apis[1]!, api: "openai-completions" }],
      [apis[0], { ...apis[1]!, api: "bedrock-converse-stream" }], []]) {
      expect(() => source({ upstream: { provider: "router", apis: bad, auth: AUTH }, endpoints },
                          false)).toThrow("invalid_source");
    }
  });

  it("reads a blob or a scope of one API spelled before upstreams named their APIs", () => {
    expect(source({ upstream: { provider: "router", ...apis[0]!, auth: AUTH }, endpoints },
                  false).upstream).toEqual({ provider: "router", apis: [apis[0]], auth: AUTH });
    expect(egressScope({ ...apis[0]!, endpoints, meter: null }))
      .toEqual({ apis: [{ ...apis[0]!, meter: null }], endpoints });
  });

  it("holds a model call to the API whose call it is, and a read to every API's rules", () => {
    expect(scopeApi(scope, "POST", "/api/v1/messages")?.api).toBe("anthropic-messages");
    expect(scopeApi(scope, "POST", "/api/v1/chat/completions")?.api).toBe("openai-completions");
    expect(scopeApi(scope, "GET", "/api/v1/models")).toBeNull();
    // `beta=true` is the Messages API's query, never chat completions'.
    expect(egressTarget(scope, "POST", "/api/v1/messages?beta=true")).not.toBeNull();
    expect(egressTarget(scope, "POST", "/api/v1/chat/completions?beta=true")).toBeNull();
    expect(egressTarget(scope, "GET", "/api/v1/models?beta=true")).not.toBeNull();
    const sent = new Headers({ "anthropic-version": "2023-06-01" });
    expect(egressHeaders(scope, "POST", "/api/v1/messages", false, sent)
      .get("anthropic-version")).toBe("2023-06-01");
    expect(egressHeaders(scope, "POST", "/api/v1/chat/completions", false, sent)
      .has("anthropic-version")).toBe(false);
  });

  it("says which of its APIs a model speaks: the one it names, or a scope's one", () => {
    expect(modelApi(scope, { api: "anthropic-messages" })?.base_url)
      .toBe("https://router.example.com/api");
    expect(modelApi(scope, {})).toBeNull();
    expect(modelApi(scope, { api: "openai-responses" })).toBeNull();
    const one = egressScope({ ...apis[0]!, endpoints, meter: null }) as ModelScope;
    expect(modelApi(one, {})?.api).toBe("openai-completions");
  });
});
