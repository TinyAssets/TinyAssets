/**
 * Where a sealed credential may go (`src/egress-scope.ts`): `allows`, on hand-written
 * endpoints. The source values sealed with a credential are the gateway's
 * (`gateway/test/credential.test.ts`), on the records it seals.
 */
import { describe, expect, it } from "vitest";

import { allows, type Endpoint } from "../src/egress-scope";

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
