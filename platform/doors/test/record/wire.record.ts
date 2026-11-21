/**
 * The wire recorder (`ci/record.ts`): refusals driven through every door
 * surface (each tool on the API, the app and MCP; busy, suspended and erasing owners; the
 * app's other routes; the sandbox's egress, run events and sync doors; MCP's events
 * extension), each answer one `REC` line, ids taken out (`clean`), so base and head print the
 * same lines for the same behaviour. Inbound hooks are `wire-reach.record.ts`'s.
 */
import { env } from "cloudflare:test";
import { beforeEach, describe, it } from "vitest";

import { slotBusy } from "../../../contract/src/slots";
import {
  EGRESS_ORIGIN, egressTokenOf, GENERATION_HEADER, generationOf, RUNNER_HEADER, SANDBOX_HEADER,
  SYNC_HEADER,
} from "../../../contract/src/egress";
import { PLATFORM_CALLER } from "../../../contract/src/binding";
import { ERASE_CONFIRM } from "../../../contract/src/owner";
import type { SandboxStart } from "../../../contract/src/sandbox";
import { toolTable } from "../../../contract/src/doors";
import {
  appPost, call, connect, doorsEnv, legacyPost, OPEN, refuseOnce, rpcBody, registerClient,
  serveFirstPartyDocument,
  setControl, url,
} from "../support";
import { clean, owned as ownedBy } from "./support";

let restore: () => void = () => undefined;
beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

const SCOPES = [{ universe: "cc", agent: "main", capability: "read_graph" }];

let counter = 0;
function fixedOwner(tag: string): string {
  counter += 1;
  const hex = (tag + counter.toString(16)).split("").map((c) => c.charCodeAt(0).toString(16))
    .join("").padEnd(32, "0").slice(0, 32);
  return `user_${hex}`;
}

async function rec(label: string, pending: Response | Promise<Response>): Promise<void> {
  let response: Response;
  try {
    response = await pending;
  } catch (error) {
    const e = error as Error;
    console.log(`REC\t${label}\tTHROW\t${clean(String(e?.name))}\t${clean(String(e?.message))
      .slice(0, 300)}`);
    return;
  }
  const text = await response.text();
  const ra = response.headers.get("Retry-After");
  const cc = response.headers.get("Cache-Control");
  const shown = response.status >= 200 && response.status < 300 && !/"isError":true|"error"/.test(text)
    ? "<ok>" : clean(text).slice(0, 600);
  console.log(`REC\t${label}\t${response.status}\tRA=${ra}\tCC=${cc}\t${shown}`);
}

function recRaw(label: string, value: unknown): void {
  console.log(`REC\t${label}\t${clean(JSON.stringify(value)).slice(0, 600)}`);
}

const owned = (tag: string) => ownedBy(fixedOwner(tag));

async function mint(token: string, session: string): Promise<string> {
  const response = await appPost("keys", token, session, { name: "laptop", scopes: SCOPES });
  return (await response.json<{ key: string }>()).key;
}

function api(key: string, tool: string, body: unknown): Promise<Response> {
  return call(new Request(url(`/api/tools/${tool}`), {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body) }));
}

function thrown(error: unknown): unknown {
  return { THROW: `${String((error as Error)?.name)}: ${String((error as Error)?.message)}` };
}

async function mcpTool(token: string, name: string, args: unknown): Promise<unknown> {
  return mcpToolInner(token, name, args).catch(thrown);
}

async function mcpRaw(token: string, message: unknown): Promise<unknown> {
  return mcpRawInner(token, message).catch(thrown);
}

async function mcpToolInner(token: string, name: string, args: unknown): Promise<unknown> {
  const steps: unknown[] = [
    { jsonrpc: "2.0", id: 0, method: "initialize", params: {
      protocolVersion: "2025-11-25", capabilities: {},
      clientInfo: { name: "review", version: "1" } } },
    { jsonrpc: "2.0", method: "notifications/initialized" },
    { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: args } },
  ];
  let last: Response | undefined;
  for (const step of steps) {
    last = await legacyPost(token, step);
    if (last.status !== 200 && last.status !== 202) {
      return { http: last.status, ra: last.headers.get("Retry-After"), body: await last.text() };
    }
  }
  const body = await rpcBody(last!);
  const result = body["result"] as { isError?: boolean } | undefined;
  if (result !== undefined && result.isError !== true) {
    return "<ok>";
  }
  return body;
}

async function mcpRawInner(token: string, message: unknown): Promise<unknown> {
  await legacyPost(token, { jsonrpc: "2.0", id: 0, method: "initialize", params: {
    protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "review", version: "1" } } });
  const response = await legacyPost(token, message);
  return { http: response.status, body: response.status === 200 ? await rpcBody(response)
    : await response.text() };
}

type Schema = { type?: unknown; properties?: Record<string, Schema>; anyOf?: Schema[];
                oneOf?: Schema[]; enum?: unknown[] };

function sample(schema: Schema | undefined, flavour: number): unknown {
  if (schema === undefined) {
    return "nope";
  }
  if (schema.enum !== undefined && schema.enum.length > 0) {
    return flavour === 2 ? "not_in_enum" : schema.enum[0];
  }
  const union = schema.anyOf ?? schema.oneOf;
  const types = Array.isArray(schema.type) ? schema.type as string[]
    : typeof schema.type === "string" ? [schema.type]
    : union !== undefined ? union.flatMap((each) => Array.isArray(each.type) ? each.type
                                          : typeof each.type === "string" ? [each.type] : [])
    : ["string"];
  const type = types.find((each) => each !== "null") ?? "null";
  switch (type) {
    case "string":
      return flavour === 0 ? "nope" : flavour === 1 ? "/workspace/none.md" : "x".repeat(5000);
    case "integer":
    case "number":
      return flavour === 2 ? -1 : 1;
    case "boolean":
      return true;
    case "array":
      return flavour === 2 ? ["nope"] : [];
    case "object":
      return flavour === 2 ? { a: 1 } : {};
    default:
      return null;
  }
}

function shapes(input: Schema): [string, unknown][] {
  const props = input.properties ?? {};
  const fill = (flavour: number) => Object.fromEntries(Object.entries(props)
    .map(([key, value]) => [key, sample(value, flavour)]));
  return [["empty", {}], ["extra", { __x: 1 }], ["fill0", fill(0)], ["fill1", fill(1)],
          ["fill2", fill(2)], ["notobj", [1]]];
}

const inject = (owner: string) => refuseOnce("outsideAgents", owner, slotBusy());

const TOOLS = toolTable().map((tool) => tool.name).sort();

describe("wire: tools on every JSON surface", () => {
  for (const name of TOOLS) {
    it(`tool ${name}`, async () => {
      const { who, app } = await owned(`t${name}`);
      const key = await mint(app.access_token, app.session);
      const tokens = await connect(await registerClient(), { owner: who });
      const tool = toolTable().find((row) => row.name === name)!;
      for (const [shape, args] of shapes(tool.input as Schema)) {
        await rec(`api ${name} ${shape}`, api(key, name, args));
        await rec(`app ${name} ${shape}`, appPost(`tools/${name}`, app.access_token,
                                                        app.session, args));
        recRaw(`mcp ${name} ${shape}`, await mcpTool(tokens.access_token, name, args));
      }
    });
  }
});

describe("wire: busy, suspended and erasing owners", () => {
  it("busy on each surface", async () => {
    const { who, app } = await owned("busy");
    const key = await mint(app.access_token, app.session);
    const tokens = await connect(await registerClient(), { owner: who });
    await inject(who);
    await rec("busy api agents_list", api(key, "agents_list", {}));
    await inject(who);
    await rec("busy app agents_list", appPost("tools/agents_list", app.access_token,
                                                    app.session, {}));
    await inject(who);
    recRaw("busy mcp agents_list", await mcpTool(tokens.access_token, "agents_list", {}));
    await inject(who);
    recRaw("busy mcp tools/list", await mcpRaw(tokens.access_token,
                                               { jsonrpc: "2.0", id: 5, method: "tools/list" }));
  });

  for (const reason of ["billing", "abuse"]) {
    it(`suspended for ${reason}`, async () => {
      const { who, app } = await owned(`s${reason}`);
      const key = await mint(app.access_token, app.session);
      const tokens = await connect(await registerClient(), { owner: who });
      await doorsEnv.OWNER.getByName(who).admissionSet(
        { owner: who, caller: PLATFORM_CALLER, origin: null }, { reason, suspended: true });
      for (const name of ["agents_list", "write_page", "read_page", "converse", "get_status"]) {
        await rec(`susp-${reason} api ${name}`, api(key, name, {}));
        await rec(`susp-${reason} app ${name}`, appPost(`tools/${name}`, app.access_token,
                                                              app.session, {}));
        recRaw(`susp-${reason} mcp ${name}`, await mcpTool(tokens.access_token, name, {}));
      }
      recRaw(`susp-${reason} mcp tools/list`, await mcpRaw(tokens.access_token,
        { jsonrpc: "2.0", id: 5, method: "tools/list" }));
      await rec(`susp-${reason} app agent/submit`, appPost("agent/submit",
        app.access_token, app.session, { message: "go", client_send_id: "s-1" }));
    });
  }

  it("erasing", async () => {
    const { who, app } = await owned("erase");
    const key = await mint(app.access_token, app.session);
    const tokens = await connect(await registerClient(), { owner: who });
    await rec("erase app account/erase", appPost("account/erase", app.access_token,
      app.session, { confirm: ERASE_CONFIRM }));
    for (const name of ["agents_list", "write_page", "get_status"]) {
      await rec(`erasing api ${name}`, api(key, name, {}));
      await rec(`erasing app ${name}`, appPost(`tools/${name}`, app.access_token,
                                                     app.session, {}));
      recRaw(`erasing mcp ${name}`, await mcpTool(tokens.access_token, name, {}));
    }
    recRaw("erasing mcp tools/list", await mcpRaw(tokens.access_token,
      { jsonrpc: "2.0", id: 5, method: "tools/list" }));
    // A fresh MCP sign-in for an erasing owner (the authorize door's erased refusal).
    try {
      await connect(await registerClient(), { owner: who });
      recRaw("erasing oauth connect", "connected");
    } catch (error) {
      recRaw("erasing oauth connect", String((error as Error).message));
    }
  });
});

describe("wire: the app's other routes", () => {
  it("routes with malformed and refused bodies", async () => {
    const { app } = await owned("approutes");
    const routes = ["keys", "keys/revoke", "keys/delete", "hooks/create", "hooks/revoke",
                    "agent/submit", "connections/key", "connections/revoke", "connections/list",
                    "files/upload/begin", "account/erase", "grants/revoke", "policy/propose",
                    "signins/start", "conversations/list", "runs/list", "tickets"];
    for (const route of routes) {
      for (const [shape, body] of [["empty", {}], ["junk", { zz: 1 }], ["arr", [1]],
                                   ["long", { name: "x".repeat(5000), agent: "nope",
                                              key_id: "nope", connection: "nope" }]] as const) {
        await rec(`approute ${route} ${shape}`, appPost(route, app.access_token,
                                                              app.session, body));
      }
    }
    // The upload bytes door with a nonexistent intent.
    await rec("appbytes unknown intent", call(new Request(
      url(`/app/api/files/upload/${"0".repeat(32)}/0`), {
        method: "PUT", body: new Uint8Array([1, 2, 3]),
        headers: { "Content-Type": "application/octet-stream", Origin: url(""),
                   "Sec-Fetch-Site": "same-origin", Authorization: `Bearer ${app.access_token}`,
                   Cookie: `__Host-ta-owner=${app.session}` } })));
  });
});

describe("wire: egress, run events and sync doors", () => {
  async function leased() {
    const { who, app, binding } = await owned("lease");
    await doorsEnv.OWNER.getByName(who).fileWrite(binding, {
      path: "/workspace/notes.md", expected_etag: null, content: new TextEncoder().encode("hi") });
    await appPost("agent/submit", app.access_token, null, { message: "go", client_send_id: "s-1" });
    // The owner's fake sandbox Worker records each start, by owner (T1a's
    // `ci/fake-sandbox.ts`, bound to the doors' tests as `SANDBOX_LOG`).
    const [start] = (await env.SANDBOX_LOG.recorded(who)).starts as SandboxStart[];
    return { who, start: start!, sync: start!.tokens!.sync_token,
             inside: { [SANDBOX_HEADER]: egressTokenOf(start!.network!.network)!,
                       [GENERATION_HEADER]: String(generationOf(start!.network!.network)) },
             runner: start!.tokens!.runner_token };
  }

  function post(path: string, body: unknown, headers: Record<string, string>): Promise<Response> {
    return call(new Request(`${EGRESS_ORIGIN}${path}`, {
      method: "POST", body: typeof body === "string" ? body : JSON.stringify(body),
      headers: { "Content-Type": "application/json", ...headers } }));
  }

  it("sync doors", async () => {
    const { start, sync, inside } = await leased();
    const as = { [SYNC_HEADER]: sync, ...inside };
    const lease = { lease_id: start.lease.lease_id, epoch: start.lease.epoch };
    const cases: [string, unknown][] = [
      ["read", { path: "/workspace/none.md" }], ["read", { path: "/workspace" }],
      ["read", { path: "relative" }], ["read", {}], ["read", { path: "/workspace/notes.md/x" }],
      ["list", { dir: "/workspace/notes.md", limit: 10 }], ["list", { dir: "/nope", limit: 10 }],
      ["list", { dir: "/workspace", limit: -1 }], ["changes", { after: -1, limit: 10 }],
      ["changes", { after: 0, limit: 100000 }],
      ["commit", { lease_id: "0".repeat(32), epoch: 1, ops: [] }],
      ["commit", { ...lease, ops: [{ op: "write", path: "/workspace/notes.md", base: null,
                                     inline: btoa("x") }] }],
      ["commit", { ...lease, ops: [{ op: "remove", path: "/workspace/none.md", base: null }] }],
      ["commit", { ...lease, ops: [{ op: "bogus" }] }], ["commit", { ...lease }],
      ["upload_begin", { ...lease, size: 5000, chunks: ["zz"] }],
      ["upload_begin", { lease_id: "0".repeat(32), epoch: 1, size: 5000, chunks: ["0".repeat(64)] }],
      ["upload_chunks", { ...lease, intent_id: "0".repeat(32), chunks: [] }],
      ["upload_abort", { ...lease, intent_id: "0".repeat(32) }],
      ["upload_commit", { ...lease, intent_id: "0".repeat(32), path: "/workspace/x" }],
    ];
    for (const [door, body] of cases) {
      await rec(`sync ${door} ${clean(JSON.stringify(body)).slice(0, 80)}`,
                await post(`/sync/${door}`, body, as));
    }
    await rec("sync read notjson", post("/sync/read", "{", as));
  });

  it("run events door", async () => {
    const { start, inside, runner } = await leased();
    const as = { ...inside, [RUNNER_HEADER]: runner };
    for (const body of [{}, { frames: "x" }, { frames: [] }, { frames: [{ kind: "nope" }] },
                        { frames: [{ seq: 1, kind: "ta.refused", at: 1 }] }, [1],
                        { frames: [], end: { state: "bogus" } }]) {
      await rec(`events ${clean(JSON.stringify(body))}`,
                await post(`/r/${start.run}/events`, body, as));
    }
    await rec("events wrong runner", post(`/r/${start.run}/events`, { frames: [] },
                                                 { ...inside, [RUNNER_HEADER]: "0".repeat(64) }));
    await rec("events other run", post(`/r/run_${"0".repeat(32)}/events`, { frames: [] }, as));
  });

  it("model and mcp doors", async () => {
    const { start, inside } = await leased();
    await rec("model unknown connection", call(new Request(
      `${EGRESS_ORIGIN}/r/${start.run}/c/c${"0".repeat(24)}/v1/messages`, {
        method: "POST", body: "{}", headers: { "Content-Type": "application/json",
                                                "Content-Length": "2", ...inside } })));
    await rec("model get unknown connection", call(new Request(
      `${EGRESS_ORIGIN}/r/${start.run}/c/c${"0".repeat(24)}/v1/models`, { headers: inside })));
    await rec("signin door junk", post("/signin", { zz: 1 }, inside));
    await rec("signin events unknown", post(`/signin/si_${"0".repeat(32)}/events`,
                                                  { frames: [] }, inside));
  });
});

describe("wire: MCP events extension", () => {
  it("events/list, subscribe, unsubscribe", async () => {
    const { who } = await owned("events");
    const tokens = await connect(await registerClient(), { owner: who });
    for (const [method, params] of [
      ["events/list", {}], ["events/list", { cursor: "x" }],
      ["events/subscribe", {}], ["events/subscribe", { name: "nope" }],
      ["events/subscribe", { name: "nope", arguments: {}, delivery: { mode: "webhook",
                                                                      url: "https://x.test/h" } }],
      ["events/unsubscribe", {}], ["events/unsubscribe", { name: "nope", arguments: {} }],
    ] as const) {
      recRaw(`events ${method} ${JSON.stringify(params)}`,
             await mcpRaw(tokens.access_token, { jsonrpc: "2.0", id: 9, method, params }));
    }
  });
});
