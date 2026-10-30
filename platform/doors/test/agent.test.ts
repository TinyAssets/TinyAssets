/**
 * The agent's tools at the doors (agent research §8; tasks 5, A4): `converse`,
 * `run_graph`, `run_status` and `run_cancel` on `/mcp` and `/api/tools/*`, held to their
 * scopes and to the credential's own owner, and the owner's own `/app/api/agent/*`, which
 * refuses an outside credential. The doors' object is production's, on the gateway
 * provider with nothing connected, so a turn settles `no_model`: the door path, not the
 * model, is what is tested here.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it } from "vitest";

import { OUTSIDE_CREDENTIAL } from "../src/app";
import { toolNamed } from "../src/tools";
import {
  appPost, call, connect, OPEN, registerClient, serveFirstPartyDocument, setControl,
  signInApp, sqlIn, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

async function mcpClient(tokens: Tokens): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

function api(key: string, tool: string, body: unknown): Promise<Response> {
  return call(new Request(url(`/api/tools/${tool}`), {
    method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
}

type Answer = { operation_id: string; thread_id: string; status: string; reason?: string };

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("the agent's tools on /mcp", () => {
  it("converse, run_status, run_graph and run_cancel reach the token's own owner only",
     async () => {
    const alice = owner();
    const bob = owner();
    const mine = await mcpClient(await connect(await registerClient(), { owner: alice }));
    const theirs = await mcpClient(await connect(await registerClient("Other"), { owner: bob }));
    const said = await mine.callTool({ name: "converse", arguments: {
      message: "hello", client_send_id: "c-1", wait_ms: 10_000 } });
    expect(said.isError).toBeFalsy();
    const answer = said.structuredContent as Answer;
    // Nothing is connected: the door ran, and the turn settled loudly.
    expect(answer).toMatchObject({ status: "unanswered", reason: "no_model" });
    expect(answer.operation_id.startsWith(`${answer.thread_id}.`)).toBe(true);
    const read = await mine.callTool({ name: "run_status",
                                       arguments: { operation_id: answer.operation_id } });
    expect(read.structuredContent).toEqual(answer);
    // Bob's token reaches Bob's object, where Alice's operation is no one's.
    for (const name of ["run_status", "run_cancel"]) {
      const refused = await theirs.callTool({ name,
                                              arguments: { operation_id: answer.operation_id } });
      expect(refused.isError).toBe(true);
      expect(refused.content).toEqual([{ type: "text", text: "no such operation" }]);
    }
    expect(await sqlIn(alice, "SELECT kind FROM agent_threads")).toEqual([{ kind: "converse" }]);
    expect(await sqlIn(bob, "SELECT kind FROM agent_threads")).toEqual([]);
    const launched = await mine.callTool({ name: "run_graph",
                                           arguments: { agent: "main", input: "go" } });
    expect(launched.structuredContent).toMatchObject({ status: "queued" });
    const cancelled = await mine.callTool({ name: "run_cancel", arguments: {
      operation_id: (launched.structuredContent as Answer).operation_id } });
    expect(cancelled.isError).toBeFalsy();
    expect(Object.keys(cancelled.structuredContent!)).toEqual(["operation_id", "cancelled"]);
    const forged = await mine.callTool({ name: "run_status", arguments: { operation_id: "1" } });
    expect(forged.isError).toBe(true);
    await mine.close();
    await theirs.close();
  }, 60_000);

  it("refuses a field the door does not take", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }));
    const refused = await client.callTool({ name: "converse", arguments: {
      message: "hi", client_send_id: "x", owner: "bob" } });
    expect(refused.isError).toBe(true);
    await client.close();
  });
});

describe("the agent's tools on /api", () => {
  const AGENT = { command_center_id: "cc", display_name: "Laptop", delivery_method: "pull",
                  recipe_id: null };

  async function keyed(capabilities: string[]) {
    const who = owner();
    const app = await signInApp({ owner: who });
    const minted = await appPost("keys", app.access_token, app.session, {
      name: "laptop",
      scopes: capabilities.map((capability) => ({ universe: "cc", agent: "main", capability })),
    });
    const { key, key_id } = await minted.json<{ key: string; key_id: string }>();
    expect((await appPost("outside-agents", app.access_token, app.session,
                          { key_id, ...AGENT })).status).toBe(200);
    return { who, key };
  }

  it("refuses a key scoped without converse or run_graph, before any thread", async () => {
    const { who, key } = await keyed(["read_graph"]);
    for (const [tool, body] of [
      ["converse", { message: "hi", client_send_id: "a" }],
      ["run_graph", { agent: "main", input: "go" }],
    ] as const) {
      const refused = await api(key, tool, body);
      expect(refused.status).toBe(403);
      expect(await refused.json()).toEqual({ error: "outside client capability not granted" });
    }
    expect(await sqlIn(who, "SELECT thread_id FROM agent_threads")).toEqual([]);
  }, 60_000);

  it("serves a key scoped with them, stamped where its agent is", async () => {
    const { who, key } = await keyed(["converse", "run_graph"]);
    const said = await api(key, "converse", { message: "hi", client_send_id: "a",
                                              wait_ms: 10_000 });
    expect(said.status).toBe(200);
    const answer = await said.json<Answer>();
    expect(answer).toMatchObject({ status: "unanswered", reason: "no_model" });
    const status = await api(key, "run_status", { operation_id: answer.operation_id });
    expect(await status.json()).toEqual(answer);
    expect((await api(key, "run_graph", { agent: "main", input: "go" })).status).toBe(200);
    expect(await sqlIn(who, "SELECT universe, agent_id, kind FROM agent_threads " +
                            "ORDER BY created_at")).toEqual([
      { universe: "cc", agent_id: "main", kind: "converse" },
      { universe: "cc", agent_id: "main", kind: "run" }]);
  }, 60_000);
});

describe("the owner's /app/api/agent/*", () => {
  it("serves the owner's own threads, and refuses an outside credential", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const said = await appPost("agent/submit", app.access_token, null,
                               { message: "hi", client_send_id: "o-1", wait_ms: 10_000 });
    expect(said.status).toBe(200);
    const answer = await said.json<Answer>();
    expect(answer).toMatchObject({ status: "unanswered", reason: "no_model" });
    const status = await appPost("agent/status", app.access_token, null,
                                 { operation_id: answer.operation_id });
    expect(await status.json()).toEqual(answer);
    const run = await (await appPost("agent/run", app.access_token, null,
                                     { agent: "main", input: "go" })).json<Answer>();
    expect(run.status).toBe("queued");
    const cancel = await appPost("agent/cancel", app.access_token, null,
                                 { operation_id: run.operation_id });
    expect(cancel.status).toBe(200);
    expect(await sqlIn(who, "SELECT client, kind FROM agent_threads ORDER BY created_at"))
      .toEqual([{ client: null, kind: "converse" }, { client: null, kind: "run" }]);
    // An MCP client's token for the same owner is an outside credential.
    const tokens = await connect(await registerClient(), { owner: who });
    const refused = await appPost("agent/submit", tokens.access_token, null,
                                  { message: "hi", client_send_id: "o-2" });
    expect(refused.status).toBe(403);
    expect(await refused.json()).toEqual({ error: OUTSIDE_CREDENTIAL });
    expect(await sqlIn(who, "SELECT COUNT(*) AS n FROM agent_threads")).toEqual([{ n: 2 }]);
  }, 60_000);

  it("is the same doors the outside tools call", () => {
    expect(["converse", "run_graph", "run_status", "run_cancel"]
      .map((name) => toolNamed(name)!.door))
      .toEqual(["agentSubmit", "agentRun", "agentStatus", "agentCancel"]);
  });
});
