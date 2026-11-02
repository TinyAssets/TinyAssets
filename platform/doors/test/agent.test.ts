/**
 * The agent's tools are absent until the runs slice (R) brings them back as run doors
 * (ADR-034; AGENTS.md Fact 8: an absent tool, never a stub that answers falsely):
 * `converse`, `run_graph`, `run_status` and `run_cancel` are not listed on `/mcp`, are no
 * tool on `/api/tools/*`, and the owner's `/app/api/agent/*` routes are not found.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it } from "vitest";

import { toolNamed } from "../src/tools";
import {
  appPost, call, connect, OPEN, registerClient, serveFirstPartyDocument, setControl,
  signInApp, type Tokens, url,
} from "./support";

const AGENT_TOOLS: readonly string[] = Object.freeze(["converse", "run_graph", "run_status",
                                                      "run_cancel"]);

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

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("the agent's tools, until runs", () => {
  it("are no tool of the table", () => {
    expect(AGENT_TOOLS.map((name) => toolNamed(name))).toEqual([undefined, undefined,
                                                                 undefined, undefined]);
  });

  it("are not listed on /mcp, and a call of one is refused as no tool", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }));
    const { tools } = await client.listTools();
    expect(tools.length).toBeGreaterThan(10);
    expect(tools.filter((tool) => AGENT_TOOLS.includes(tool.name))).toEqual([]);
    const called = await client.callTool({ name: "converse", arguments: {
      message: "hello", client_send_id: "c-1" } }).then(
      (result) => `result ${result.isError === true} ${JSON.stringify(result.content)}`,
      (error: Error) => `error ${error.message}`);
    expect(called).toBe("error Tool converse not found");
    await client.close();
  }, 60_000);

  it("are no tool on the keyed /api/tools/*: 404, no such tool", async () => {
    const app = await signInApp({ owner: owner() });
    const minted = await appPost("keys", app.access_token, app.session, {
      name: "laptop", scopes: [{ universe: "cc", agent: "main", capability: "read_graph" }] });
    const { key } = await minted.json<{ key: string }>();
    for (const tool of AGENT_TOOLS) {
      const answer = await call(new Request(url(`/api/tools/${tool}`), {
        method: "POST", headers: { Authorization: `Bearer ${key}`,
                                   "Content-Type": "application/json" },
        body: JSON.stringify({}) }));
      expect(answer.status, tool).toBe(404);
      expect(await answer.json()).toEqual({ error: "no such tool" });
    }
  }, 60_000);

  it("are not found on the owner's /app/api/agent/*", async () => {
    const app = await signInApp({ owner: owner() });
    for (const route of ["agent/submit", "agent/run", "agent/status", "agent/cancel"]) {
      const answer = await appPost(route, app.access_token, null, {});
      expect(answer.status).toBe(404);
      expect(await answer.json()).toEqual({ error: "not found" });
    }
  }, 60_000);
});
