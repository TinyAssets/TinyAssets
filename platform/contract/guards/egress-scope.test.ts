/**
 * Where a sealed credential may go (`src/egress-scope.ts`): `allows`, on hand-written
 * endpoints, and a service's key bound to its one URL (`serviceSource`, `egressScope`). The
 * source values sealed with a credential are the gateway's (`gateway/test/credential.test.ts`),
 * on the records it seals.
 */
import { describe, expect, it } from "vitest";

import {
  allows, egressScope, type Endpoint, keySource, serviceSource,
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
    // A URL parser takes it; the egress table's own rule (`checkDestination`) does not.
    ["a label past 63 characters", `https://${"a".repeat(64)}.example.com/mcp`],
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
