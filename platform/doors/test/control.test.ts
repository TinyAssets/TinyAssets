/**
 * The platform gates at the doors (research §7; checklist 11): the outside kill switch,
 * fail-closed, and the maintenance gate, which a record that is there but malformed or
 * unreadable also closes.
 */
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { outsideOpen, ownerOpen } from "../src/control";
import type { DoorsEnv } from "../src/config";
import { type Control, parseControl } from "../src/directory";
import { mintKeyFor } from "./keys";
import {
  appCallback, appPost, authorizeMcp, call, connect, doorsEnv, legacyPost, OPEN, reached,
  registerClient, serveFirstPartyDocument, setControl, signInApp, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

function listTools(token: string, environment: DoorsEnv = doorsEnv): Promise<Response> {
  return legacyPost(token, { jsonrpc: "2.0", id: 1, method: "tools/call",
                             params: { name: "agents_list", arguments: {} } }, {}, environment);
}

function api(key: string, environment: DoorsEnv = doorsEnv): Promise<Response> {
  return call(new Request(url("/api/tools/agents_list"), {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: "{}" }), environment);
}

/** The doors with the directory's control record unreadable (an outage). */
function unreadable(): DoorsEnv {
  const real = env.DIRECTORY;
  const failing = {
    get: (name: string, options: never) => name === "c:control"
      ? Promise.reject(new Error("KV down")) : real.get(name, options),
    put: real.put.bind(real), delete: real.delete.bind(real), list: real.list.bind(real),
  } as unknown as KVNamespace;
  return { ...doorsEnv, DIRECTORY: failing };
}

/** A record state, set up, and the env the doors then run with. */
type State = readonly [string, () => Promise<DoorsEnv>];

const record = (value: unknown): (() => Promise<DoorsEnv>) => async () => {
  await setControl(value);
  return doorsEnv;
};

const MALFORMED: State = ["malformed", record({ ...OPEN, outside: true })];
const UNREADABLE: State = ["unreadable", async () => unreadable()];
const NOT_JSON: State = ["not JSON", async () => {
  await env.DIRECTORY.put("c:control", "{outside");
  return doorsEnv;
}];
const MISSING: State = ["missing", record(null)];

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

  it("opens outside work only when read on; the owner's when missing or admitting", () => {
    const read = (control: Control) => ({ kind: "read", control }) as const;
    for (const kind of ["missing", "malformed", "unreadable"] as const) {
      expect(outsideOpen({ kind }, "a")).toBe(false);
    }
    expect(outsideOpen(read({ ...OPEN, outside: "off" }), "a")).toBe(false);
    expect(outsideOpen(read(OPEN), "a")).toBe(true);
    expect(ownerOpen({ kind: "missing" }, "a")).toBe(true);
    expect(ownerOpen({ kind: "malformed" }, "a")).toBe(false);
    expect(ownerOpen({ kind: "unreadable" }, "a")).toBe(false);
    expect(ownerOpen(read({ ...OPEN, outside: "off" }), "a")).toBe(true);
    expect(ownerOpen(read({ ...OPEN, ordinary: "none" }), "a")).toBe(false);
  });
});

describe.each([
  MISSING, MALFORMED, UNREADABLE, NOT_JSON,
  ["switched off", record({ ...OPEN, outside: "off" })],
  ["in maintenance", record({ ...OPEN, ordinary: "none" })],
] as const)("the outside gate, with the record %s", (_what, state) => {
  it("refuses an MCP call 503, before the object", async () => {
    const alice = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    const response = await listTools(tokens.access_token, await state());
    expect(response.status).toBe(503);
    expect(await reached(alice)).toBe(false);
  });

  it("refuses an /api call 503", async () => {
    const { key } = await mintKeyFor(owner());
    expect((await api(key, await state())).status).toBe(503);
  });

  it("refuses an outside client's authorization", async () => {
    const client = await registerClient();
    await expect(authorizeMcp(client, { owner: owner() }, await state())).rejects.toThrow(
      "temporarily_unavailable");
  });
});

describe.each([
  [MISSING, true], [MALFORMED, false], [UNREADABLE, false], [NOT_JSON, false],
  [["switched off", record({ ...OPEN, outside: "off" })] as State, true],
  [["in maintenance", record({ ...OPEN, ordinary: "none" })] as State, false],
] as const)("the owner gate, with the record %s", ([_what, state], open) => {
  it(`${open ? "admits" : "refuses"} the owner's surface`, async () => {
    const app = await signInApp({ owner: owner() });
    const response = await call(new Request(url("/app/api/session/extend"), {
      method: "POST", headers: { "Content-Type": "application/json",
                                 Origin: "https://tinyassets.io",
                                 Authorization: `Bearer ${app.access_token}`,
                                 Cookie: `__Host-ta-owner=${app.session}` }, body: "{}" }),
      await state());
    expect(response.status).toBe(open ? 200 : 503);
  });

  it(`${open ? "admits" : "refuses"} the owner's sign-in`, async () => {
    const { back } = await appCallback({ owner: owner() }, await state());
    const redirect = new URL(back.headers.get("Location")!);
    expect(redirect.searchParams.get("error")).toBe(open ? null : "temporarily_unavailable");
    expect(back.headers.getSetCookie().some((line) => line.startsWith("__Host-ta-owner=")))
      .toBe(open);
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

  it("checks the MCP call's own owner", async () => {
    const canary = owner();
    const other = owner();
    const client = await registerClient();
    const canaryTokens = await connect(client, { owner: canary });
    const otherTokens = await connect(client, { owner: other });
    await setControl({ ...OPEN, ordinary: "canary", canary: [canary] });
    expect((await listTools(canaryTokens.access_token)).status).toBe(200);
    expect((await listTools(otherTokens.access_token)).status).toBe(503);
  });

  it("checks the owner's own sign-in", async () => {
    const canary = owner();
    await setControl({ ...OPEN, ordinary: "canary", canary: [canary] });
    const admitted = await appCallback({ owner: canary });
    expect(new URL(admitted.back.headers.get("Location")!).searchParams.get("code")).toBeTruthy();
    const refused = await appCallback({ owner: owner() });
    expect(new URL(refused.back.headers.get("Location")!).searchParams.get("error"))
      .toBe("temporarily_unavailable");
  });
});
