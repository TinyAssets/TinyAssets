/**
 * The MCP door (research §4; checklist 9, 10, 24): both revisions from one factory, the
 * tool table with every hint, the verified origin, source and sign-in reaching the
 * object, GET 405 and a foreign Origin 403; an owner's key (ADR-039 decision 2.18) served
 * as a token is, by the `/api` door's own check, from the `Authorization` header alone. Beside
 * the tools, the agent's definition: its two files as resources and in the `agent` prompt, its
 * skills by the Skills extension, each file's digest the bytes' served.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { SkillsClient } from "@modelcontextprotocol/client/ext/skills";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { apiKeyOrigin } from "../../contract/src/binding";
import { MCP_RESOURCE } from "../../contract/src/identity";
import { DEFINITION_BYTES_MAX } from "../../contract/src/limits";
import { slotBusy } from "../../contract/src/slots";
import { mcpOrigin } from "../src/binder";
import { now } from "../src/config";
import { profileNames, toolTable } from "../../contract/src/doors";
import { mintKeyFor } from "./keys";
import {
  appPost, call, connect, crossed, doorsEnv, grantsOf, legacyPost, legacyTool, OPEN, ownDoor,
  reached, refuseOnce, unseen, registerClient, rpcBody, serveFirstPartyDocument, setControl,
  type Tokens, url, answerOf,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** The real MCP client SDK, over the doors, as an outside agent connects. */
async function mcpClient(tokens: Pick<Tokens, "access_token">,
                         mode: "legacy" | { pin: string }): Promise<Client> {
  const client = new Client({ name: "test-agent", version: "1.0.0" },
                            { versionNegotiation: { mode } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

let restore: () => void;

beforeEach(async () => {
  await setControl(OPEN);
  restore = serveFirstPartyDocument();
});

afterEach(() => {
  restore();
});

/** The owner's file at `path` as `client` reads it with the file tools. */
async function stored(client: Client, path: string): Promise<string> {
  return answerOf(await client.callTool({ name: "fetch", arguments: { id: path } }))["text"] as
    string;
}

/** The skills of a new owner's `main`: the starter's shared ones, as it has none of its own. */
async function seededSkills(client: Client): Promise<string[]> {
  const listed = answerOf(await client.callTool({ name: "file_list", arguments: {
    dir: "/workspace/.agents/skills", limit: 100 } })) as { entries: { path: string }[] };
  return listed.entries.map((entry) => entry.path.split("/").at(-1)!).sort();
}

describe("the agent's definition past DEFINITION_BYTES_MAX", () => {
  it("is refused result_too_large, never cut, while its tools still serve", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }),
                                   { pin: "2026-07-28" });
    const content = `---\nname: big\ndescription: A skill past the ceiling.\n---\n${
      "x".repeat(DEFINITION_BYTES_MAX)}`;
    expect((await client.callTool({ name: "file_write", arguments: {
      path: "/workspace/skills/big/SKILL.md", expected_etag: "", content } })).isError)
      .toBeFalsy();
    for (const asked of [() => client.listResources(), () => client.getPrompt({ name: "agent" })]) {
      await expect(asked()).rejects.toThrow("result_too_large");
    }
    expect((await client.listTools()).tools.length).toBeGreaterThan(0);
    await client.close();
  });
});

describe.each([
  ["2026-07-28", { pin: "2026-07-28" }],
  ["2025-11-25", "legacy"],
] as const)("MCP %s", (revision, mode) => {
  it("serves a new client the custom profile, in order, with every hint explicit",
     async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const client = await mcpClient(tokens, mode);
    expect(client.getNegotiatedProtocolVersion()).toBe(revision);
    const { tools } = await client.listTools();
    // Code withheld: code mode is off by default (its hints: the tools recorder's custom-code).
    expect(tools.map((tool) => tool.name))
      .toEqual(profileNames("custom").filter((name) => name !== "code"));
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

  it("serves the agent's definition: its two files as resources and in the agent prompt, " +
     "its skills by the extension, each digest the bytes'", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }),
                                   mode);
    expect((await client.listPrompts()).prompts.map((prompt) => prompt.name)).toEqual(["agent"]);
    const files = await Promise.all(["AGENTS.md", "SOUL.md"].map(async (name) =>
      [`tinyassets://agent/main/${name}`, await stored(client, `/workspace/agents/main/${name}`)]));
    const skills = (await seededSkills(client)).map((name) => `skill://${name}/SKILL.md`);
    expect(skills.length).toBeGreaterThan(0);
    const { messages } = await client.getPrompt({ name: "agent" });
    expect(messages.map(({ content }) => content.type === "resource"
      ? [content.resource.uri, "text" in content.resource ? content.resource.text : null]
      : content.type === "resource_link" ? content.uri : null))
      .toEqual([...files, ...skills]);
    expect((await client.listResources()).resources.map((resource) => resource.uri).sort())
      .toEqual([...files.map(([uri]) => uri), ...skills].sort());
    expect((await client.readResource({ uri: files[1]![0]! })).contents)
      .toEqual([{ uri: files[1]![0], mimeType: "text/markdown", text: files[1]![1] }]);
    const extension = new SkillsClient(client);
    expect(extension.supported).toBe(true);
    const { skills: entries } = await extension.list();
    expect(entries.map((entry) => entry.uri).sort()).toEqual(skills.sort());
    for (const entry of entries) {
      for (const { uri } of entry.resources === "dynamic" ? [] : entry.resources) {
        // Read and checked against the entry's size and digest by the SDK's own client.
        const file = await extension.read(entry, uri);
        expect(new TextDecoder().decode(file.bytes))
          .toBe(await stored(client, uri.replace("skill://", "/workspace/.agents/skills/")));
      }
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
    // No outside agent; of the owner's own, at most main, the starter's, as its seed lands.
    expect(answerOf(listed)).toMatchObject({ agents: [] });
    expect((answerOf(listed) as unknown as { owner_agents: { agent: string }[] }).owner_agents
      .map((agent) => agent.agent).filter((agent) => agent !== "main")).toEqual([]);
    const filed = await client.callTool({ name: "agent_register", arguments: {
      command_center_id: "cc", display_name: "Muse", delivery_method: "pull", recipe_id: null } });
    expect(filed.isError).toBeFalsy();
    const origin = await mcpOrigin(registered.clientId, registered.redirectUri);
    expect((await grantsOf(alice)).map(({ client, source, mode }) => ({ client, source, mode })))
      .toEqual([{ client: origin.client, source: "mcp", mode: "full" }]);
    // The doors handed the object the verified origin, source and sign-in; its card holds them.
    const [proposed] = await crossed(alice, "registerPropose");
    expect(proposed!.binding).toMatchObject({
      origin, source: { kind: "mcp", ref: registered.clientId }, authenticated_at: signedIn });
    const { card_id } = answerOf(filed) as { card_id: string };
    expect(await ownDoor(alice, "registerProposal", { card_id })).toMatchObject({
      client: origin.client, family: origin.family, source_ref: registered.clientId });
    await client.close();
  });

  it("answers an owner refusal as the tool's error, with its reason and that it is final",
     async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const client = await mcpClient(tokens, mode);
    // A connection with no registered agent has no mailbox, and is told how it gets one.
    for (const [name, args] of [["mailbox_read", { limit: 5 }],
                                ["inbox_digest", { limit: 5, after: 0 }]] as const) {
      const refused = await client.callTool({ name, arguments: args });
      expect(refused.isError).toBe(true);
      expect(refused.content).toEqual([{ type: "text",
        text: "this connection has no registered agent, which a mailbox, its rooms and a " +
              "narrowed grant's work need: file one with agent_register, for the owner to " +
              "approve\nThis refusal is final: the same call is refused again until what it " +
              "names changes." }]);
    }
    const invalid = await client.callTool({ name: "mailbox_read",
                                            arguments: { limit: 5, owner: "bob" } });
    expect(invalid.isError).toBe(true);
    await client.close();
  });

  it("answers a file fault as the tool's error with its code, a removed file's read among " +
     "them, never as an internal error", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }),
                                   mode);
    const written = answerOf(await client.callTool({ name: "file_write", arguments: {
      path: "/workspace/gone.md", expected_etag: "", content: "soon gone" } })) as
      { entry: { etag: string } };
    expect((await client.callTool({ name: "file_remove", arguments: {
      path: "/workspace/gone.md", expected_etag: written.entry.etag, recursive: false } }))
      .isError).toBeFalsy();
    const made = answerOf(await client.callTool({ name: "file_mkdir", arguments: {
      path: "/workspace/empty", expected_etag: null } })) as { entry: { etag: string } };
    const final = "\nThis refusal is final: the same call is refused again until what it " +
      "names changes.";
    const skills = answerOf(await client.callTool({ name: "file_stat", arguments: {
      path: "/workspace/.agents" } })) as { entry: { etag: string } };
    // A stat answers a missing path, and a remove at its old etag a conflict; a read refuses it.
    expect(answerOf(await client.callTool({ name: "file_stat", arguments: {
      path: "/workspace/gone.md" } }))).toEqual({ entry: null });
    expect(answerOf(await client.callTool({ name: "file_remove", arguments: {
      path: "/workspace/gone.md", expected_etag: written.entry.etag, recursive: false } })))
      .toEqual({ conflict: null });
    for (const [name, args, said] of [
      ["file_read", { path: "/workspace/gone.md" }, "not_found: no such file"],
      ["fetch", { id: "/workspace/gone.md" }, "not_found: no such file"],
      ["file_read", { path: "/workspace/.agents" }, "is_directory: is a directory"],
      ["file_remove", { path: "/workspace/empty", expected_etag: made.entry.etag,
                        recursive: false }, "is_directory: a directory is removed with recursive"],
      ["file_remove", { path: "/workspace/.agents", expected_etag: skills.entry.etag,
                        recursive: false },
       "not_empty: a directory is removed with recursive"],
    ] as const) {
      const refused = await client.callTool({ name, arguments: args });
      expect([name, refused.isError, refused.content]).toEqual(
        [name, true, [{ type: "text", text: `${said}${final}` }]]);
    }
    await client.close();
  });

  it("answers a refusal for the moment as the tool's error, telling the model when to call " +
     "again, and the call made again succeeds", async () => {
    const alice = owner();
    const { key } = await mintKeyFor(alice);
    const client = await mcpClient({ access_token: key }, mode);
    await refuseOnce("outsideAgents", alice, slotBusy());
    const busy = await client.callTool({ name: "agents_list", arguments: {} });
    expect(busy.isError).toBe(true);
    expect(busy.content).toEqual([{ type: "text", text: `${slotBusy().message}\nThis refusal ` +
                                    "is temporary: try the same call again in 1 second." }]);
    const again = await client.callTool({ name: "agents_list", arguments: {} });
    expect(again.isError).toBeFalsy();
    expect(answerOf(again)).toMatchObject({ agents: [] });
    await client.close();
  });

  it("serves an owner's key its grant's profile, bound as the key", async () => {
    const alice = owner();
    const { key, key_id } = await mintKeyFor(alice);
    await unseen(alice);
    const client = await mcpClient({ access_token: key }, mode);
    expect(client.getNegotiatedProtocolVersion()).toBe(revision);
    // Custom, code withheld: code mode is off by default.
    expect((await client.listTools()).tools.map((tool) => tool.name))
      .toEqual(profileNames("custom").filter((name) => name !== "code"));
    const listed = await client.callTool({ name: "agents_list", arguments: {} });
    expect(listed.isError).toBeFalsy();
    expect(answerOf(listed)).toMatchObject({ agents: [] });
    const [profiled] = await crossed(alice, "toolProfile");
    expect(profiled!.binding).toMatchObject({
      owner: alice, caller: alice, origin: apiKeyOrigin(key_id),
      source: { kind: "api_key", ref: key_id } });
    // Its definition is read as its grant reads a file, refused as its file_read is: a key's
    // scoped grant reads where its agent is registered, and none is.
    const read = await client.callTool({ name: "file_read",
                                         arguments: { path: "/workspace/agents/main/AGENTS.md" } });
    expect(read.isError).toBe(true);
    await expect(client.listResources()).rejects.toThrow(
      (read.content as { text: string }[])[0]!.text.split("\n")[0]!);
    const [defined] = await crossed(alice, "definitionRead");
    expect(defined!.binding).toMatchObject({ origin: apiKeyOrigin(key_id) });
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
    await unseen(alice);
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

describe("a key on the MCP endpoint", () => {
  const LIST = { jsonrpc: "2.0", id: 1, method: "tools/list" };
  const CHALLENGE = `Bearer realm="OAuth", resource_metadata="${new URL(MCP_RESOURCE).origin
  }/.well-known/oauth-protected-resource/mcp", error="invalid_token"`;

  it("is refused on the call after its revoke, as a token that does not verify", async () => {
    const minted = await mintKeyFor(owner());
    expect((await legacyPost(minted.key, LIST)).status).toBe(200);
    expect((await appPost("keys/revoke", minted.token, null, { key_id: minted.key_id })).status)
      .toBe(200);
    const refused = await legacyPost(minted.key, LIST);
    expect([refused.status, refused.headers.get("WWW-Authenticate")]).toEqual([401, CHALLENGE]);
  });

  it("is answered the object's refusal of its revoked grant, as at /api", async () => {
    const alice = owner();
    const { key, key_id } = await mintKeyFor(alice);
    await doorsEnv.OWNER.getByName(alice).grantRevoke(
      { owner: alice, caller: alice, origin: null }, { client: apiKeyOrigin(key_id).client });
    const mcp = await legacyPost(key, LIST);
    const api = await call(new Request(url("/api/tools/agents_list"), {
      method: "POST", body: "{}",
      headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" } }));
    const outside = { error: "outside client revoked" };
    expect([mcp.status, await mcp.json()]).toEqual([403, outside]);
    expect([api.status, await api.json()]).toEqual([403, outside]);
  });

  it("refuses a key that does not verify 401 naming the metadata, before any object",
     async () => {
    const alice = owner();
    const { key } = await mintKeyFor(alice);
    await unseen(alice);
    for (const wrong of [`${key.slice(0, -1)}${key.endsWith("A") ? "B" : "A"}`,
                         `ta_key_${"0".repeat(16)}_${"a".repeat(43)}`, "ta_key_short"]) {
      const response = await legacyPost(wrong, LIST);
      expect([response.status, response.headers.get("WWW-Authenticate")])
        .toEqual([401, CHALLENGE]);
    }
    expect(await reached(alice)).toBe(false);
  });

  it("takes a key from the Authorization header alone, never a query or a cookie",
     async () => {
    const alice = owner();
    const { key } = await mintKeyFor(alice);
    await unseen(alice);
    const sent = (path: string, headers: Record<string, string> = {}) => call(new Request(
      url(path), { method: "POST", body: JSON.stringify(LIST), headers: {
        "Content-Type": "application/json", Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-11-25", ...headers } }));
    for (const name of ["key", "api_key", "access_token", "token", "authorization"]) {
      expect((await sent(`/mcp?${new URLSearchParams({ [name]: key })}`)).status).toBe(401);
    }
    for (const cookie of [key, `key=${key}`, `__Host-ta-owner=${key}`]) {
      expect((await sent("/mcp", { Cookie: cookie })).status).toBe(401);
    }
    expect(await reached(alice)).toBe(false);
  });

  it("takes the Bearer scheme in any case, at /mcp and /api alike", async () => {
    const { key } = await mintKeyFor(owner());
    for (const scheme of ["bearer", "BEARER"]) {
      const authorization = { Authorization: `${scheme} ${key}` };
      expect((await legacyPost(key, LIST, authorization)).status).toBe(200);
      const api = await call(new Request(url("/api/tools/agents_list"), {
        method: "POST", body: "{}",
        headers: { ...authorization, "Content-Type": "application/json" } }));
      expect(api.status).toBe(200);
    }
  });

  it("refuses a key with a present Origin 403, before the object", async () => {
    const alice = owner();
    const { key } = await mintKeyFor(alice);
    await unseen(alice);
    expect((await legacyPost(key, LIST, { Origin: "https://evil.example" })).status).toBe(403);
    expect(await reached(alice)).toBe(false);
  });
});
