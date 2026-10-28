/**
 * The platform gates at the doors (research §7; checklist 11): the outside kill switch,
 * fail-closed, and the maintenance gate.
 */
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { outsideOpen, ownerOpen } from "../src/control";
import { parseControl } from "../src/directory";
import { mintKeyFor } from "./keys";
import {
  appPost, authorizeMcp, call, connect, doorsEnv, legacyPost, OPEN, reached, registerClient,
  serveFirstPartyDocument, setControl, signInApp, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

function listTools(token: string): Promise<Response> {
  return legacyPost(token, { jsonrpc: "2.0", id: 1, method: "tools/call",
                             params: { name: "agents_list", arguments: {} } });
}

function api(key: string, environment = doorsEnv): Promise<Response> {
  return call(new Request(url("/api/tools/agents_list"), {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: "{}" }), environment);
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("the control record", () => {
  it("is read exactly", () => {
    expect(parseControl(OPEN)).toEqual(OPEN);
    for (const bad of [null, [], "on", { ...OPEN, outside: "yes" }, { ...OPEN, ordinary: "most" },
                       { ...OPEN, inbound: "true" }, { ...OPEN, canary: "alice" },
                       { ...OPEN, canary: [" alice"] }, { ...OPEN, extra: 1 },
                       { outside: "on", ordinary: "all", canary: [] },
                       { ...OPEN, canary: Array.from({ length: 101 }, (_v, i) => `o${i}`) }]) {
      expect(parseControl(bad)).toBeNull();
    }
  });

  it("opens outside work only when it reads on, and the owner's when it does not refuse", () => {
    expect(outsideOpen(null, "a")).toBe(false);
    expect(outsideOpen({ ...OPEN, outside: "off" }, "a")).toBe(false);
    expect(outsideOpen(OPEN, "a")).toBe(true);
    expect(ownerOpen(null, "a")).toBe(true);
    expect(ownerOpen({ ...OPEN, outside: "off" }, "a")).toBe(true);
    expect(ownerOpen({ ...OPEN, ordinary: "none" }, "a")).toBe(false);
  });
});

describe.each([
  ["missing", null],
  ["switched off", { ...OPEN, outside: "off" }],
  ["malformed", { ...OPEN, outside: true }],
  ["in maintenance", { ...OPEN, ordinary: "none" }],
])("with the record %s", (_what, record) => {
  it("refuses an MCP call 503, before the object", async () => {
    const alice = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    await setControl(record);
    const response = await listTools(tokens.access_token);
    expect(response.status).toBe(503);
    expect(await reached(alice)).toBe(false);
  });

  it("refuses an /api call 503", async () => {
    const { key } = await mintKeyFor(owner());
    await setControl(record);
    expect((await api(key)).status).toBe(503);
  });

  it("refuses an outside client's authorization", async () => {
    const client = await registerClient();
    await setControl(record);
    await expect(authorizeMcp(client, { owner: owner() })).rejects.toThrow(
      "temporarily_unavailable");
  });
});

describe("the owner's own surface", () => {
  it.each([
    ["missing", null],
    ["switched off", { ...OPEN, outside: "off" }],
    ["malformed", { ...OPEN, outside: true }],
  ])("stays open with the record %s", async (_what, record) => {
    const app = await signInApp({ owner: owner() });
    await setControl(record);
    const response = await appPost("session/extend", app.access_token, app.session, {});
    expect(response.status).toBe(200);
  });

  it("is held by maintenance", async () => {
    const app = await signInApp({ owner: owner() });
    await setControl({ ...OPEN, ordinary: "none" });
    const response = await appPost("session/extend", app.access_token, app.session, {});
    expect(response.status).toBe(503);
  });
});

describe("the canary gate", () => {
  it("admits only the canary owners, outside and in", async () => {
    const canary = owner();
    const other = owner();
    const canaryKey = await mintKeyFor(canary);
    const otherKey = await mintKeyFor(other);
    await setControl({ ...OPEN, ordinary: "canary", canary: [canary] });
    expect((await api(canaryKey.key)).status).toBe(200);
    expect((await api(otherKey.key)).status).toBe(503);
    expect((await appPost("session/extend", canaryKey.token, canaryKey.session, {})).status)
      .toBe(200);
    expect((await appPost("session/extend", otherKey.token, otherKey.session, {})).status)
      .toBe(503);
  });
});

it("refuses outside work when the record cannot be read", async () => {
  const { key } = await mintKeyFor(owner());
  const real = env.DIRECTORY;
  const failing = {
    get: (name: string, options: never) => name === "c:control"
      ? Promise.reject(new Error("KV down")) : real.get(name, options),
  } as unknown as KVNamespace;
  expect((await api(key, { ...doorsEnv, DIRECTORY: failing })).status).toBe(503);
});
