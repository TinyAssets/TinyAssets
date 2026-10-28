/**
 * The MCP door (research §4; checklist 9, 10, 24): both revisions from one factory, the
 * tool table with every hint, the verified origin, source and sign-in reaching the
 * object, GET 405 and a foreign Origin 403.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it } from "vitest";

import { mcpOrigin } from "../src/binder";
import { now } from "../src/config";
import { toolTable } from "../src/tools";
import {
  call, connect, legacyPost, legacyTool, OPEN, reached, registerClient, rpcBody, setControl,
  sqlIn, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** The real MCP client SDK, over the doors, as an outside agent connects. */
async function mcpClient(tokens: Tokens, mode: "legacy" | { pin: string }): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

beforeEach(async () => {
  await setControl(OPEN);
});

describe.each([
  ["2026-07-28", { pin: "2026-07-28" }],
  ["2025-11-25", "legacy"],
] as const)("MCP %s", (revision, mode) => {
  it("serves the tool table, in order, with every hint explicit", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const client = await mcpClient(tokens, mode);
    expect(client.getNegotiatedProtocolVersion()).toBe(revision);
    const { tools } = await client.listTools();
    expect(tools.map((tool) => tool.name)).toEqual(toolTable().map((tool) => tool.name));
    for (const tool of tools) {
      const row = toolTable().find((r) => r.name === tool.name)!;
      expect(tool.title).toBe(row.title);
      expect(tool.annotations).toEqual({
        title: row.title, readOnlyHint: row.readOnlyHint, destructiveHint: row.destructiveHint,
        idempotentHint: row.idempotentHint, openWorldHint: row.openWorldHint,
      });
    }
    for (const model of ["read", "write", "edit", "bash", "ta"]) {
      expect(tools.map((tool) => tool.name)).not.toContain(model);
    }
    await client.close();
  });

  it("reaches the owner's object with the verified origin, source and sign-in", async () => {
    const registered = await registerClient();
    const alice = owner();
    const signedIn = now() - 300;
    const tokens = await connect(registered, { owner: alice, authenticatedAt: signedIn });
    const client = await mcpClient(tokens, mode);
    const listed = await client.callTool({ name: "agents_list", arguments: {} });
    expect(listed.isError).toBeFalsy();
    expect(listed.structuredContent).toEqual({ agents: [] });
    const filed = await client.callTool({ name: "agent_register", arguments: {
      command_center_id: "cc", display_name: "Muse", delivery_method: "pull", recipe_id: null } });
    expect(filed.isError).toBeFalsy();
    const origin = await mcpOrigin(registered.clientId, registered.redirectUri);
    expect(await sqlIn(alice, "SELECT client, source, mode FROM outside_clients")).toEqual([
      { client: origin.client, source: "mcp", mode: "full" }]);
    expect(await sqlIn(alice, "SELECT client, family, source_ref, authenticated_at " +
                              "FROM agent_cards")).toEqual([
      { client: origin.client, family: origin.family, source_ref: registered.clientId,
        authenticated_at: signedIn }]);
    await client.close();
  });

  it("answers an owner refusal as the tool's error, with its reason", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const client = await mcpClient(tokens, mode);
    const refused = await client.callTool({ name: "mailbox_read", arguments: { limit: 5 } });
    expect(refused.isError).toBe(true);
    expect(refused.content).toEqual([{ type: "text",
                                       text: "this outside identity is not one enabled agent" }]);
    const invalid = await client.callTool({ name: "mailbox_read",
                                            arguments: { limit: 5, owner: "bob" } });
    expect(invalid.isError).toBe(true);
    await client.close();
  });
});

describe("the MCP endpoint", () => {
  it("answers GET 405", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const response = await call(new Request(url("/mcp"), {
      headers: { Authorization: `Bearer ${tokens.access_token}`, Accept: "text/event-stream" } }));
    expect(response.status).toBe(405);
  });

  it("refuses a present foreign Origin 403, before the token or the object", async () => {
    const alice = owner();
    const tokens = await connect(await registerClient(), { owner: alice });
    const response = await legacyPost(tokens.access_token, {
      jsonrpc: "2.0", id: 0, method: "initialize", params: {
        protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "x", version: "1" } } },
      { Origin: "https://evil.example" });
    expect(response.status).toBe(403);
    const unauthenticated = await call(new Request(url("/mcp"), {
      method: "POST", headers: { Origin: "https://evil.example" } }));
    expect(unauthenticated.status).toBe(403);
    expect(await reached(alice)).toBe(false);
  });

  it("serves a request with no Origin", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const response = await legacyPost(tokens.access_token, {
      jsonrpc: "2.0", id: 0, method: "initialize", params: {
        protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "x", version: "1" } } });
    expect(response.status).toBe(200);
    expect(await rpcBody(response)).toMatchObject({ result: { protocolVersion: "2025-11-25" } });
  });

  it("refuses a token for another resource", async () => {
    const response = await legacyPost("alice:grant:secret", { jsonrpc: "2.0", id: 1,
                                                             method: "tools/list" });
    expect(response.status).toBe(401);
  });

  it("never names the model's own tools", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    for (const model of ["read", "write", "edit", "bash", "ta"]) {
      await expect(legacyTool(tokens.access_token, model, {})).rejects.toThrow("not found");
    }
  });
});
