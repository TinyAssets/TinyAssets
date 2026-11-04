/**
 * The shared brain at the doors (slice BRAIN), in workerd: an outside client's agent is told
 * the brain's rules; each client's vendor is recorded at sign-in (a CIMD host verified, a
 * registered name unverified); dynamic registration only for a vendor with no CIMD, behind
 * `DCR_ENABLED`; and the local proof of one brain: two MCP clients of one owner, a fact one
 * writes found by the other's `search`, and a race on one claim key leaving one claim.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CLIENT_INSTRUCTIONS, RUN_INSTRUCTIONS } from "../src/instructions";
import type { DoorsEnv } from "../src/config";
import {
  call, connect, doorsEnv, OPEN, registerClient, setControl, sqlIn, type Tokens, url,
} from "./support";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** The real MCP client SDK, over the doors, as an outside agent connects. */
async function mcpClient(tokens: Tokens, name = "test-agent"): Promise<Client> {
  const client = new Client({ name, version: "1.0.0" },
                            { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  const transport = new StreamableHTTPClientTransport(new URL(url("/mcp")), {
    fetch: (input, init) => call(new Request(input, init)),
    requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } },
  });
  await client.connect(transport);
  return client;
}

/** A tool's answer, or its error's text. */
async function tool(client: Client, name: string, args: Record<string, unknown>):
    Promise<{ error: string | null; value: Record<string, unknown> }> {
  const answer = await client.callTool({ name, arguments: args });
  const first = (answer.content as { type: string; text: string }[])[0];
  return answer.isError === true ? { error: first?.text ?? "", value: {} }
    : { error: null, value: answer.structuredContent as Record<string, unknown> };
}

const register = (environment: DoorsEnv, name: string) => call(new Request(
  url("/oauth/register"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_name: name, redirect_uris: ["https://client.test/cb"],
                           token_endpoint_auth_method: "none" }),
  }), environment);

beforeEach(async () => {
  await setControl(OPEN);
});

describe("an outside client's agent", () => {
  it("is told the brain's rules when it connects, and a run its working files", async () => {
    const client = await mcpClient(await connect(await registerClient(), { owner: owner() }));
    expect(client.getInstructions()).toBe(CLIENT_INSTRUCTIONS);
    expect(CLIENT_INSTRUCTIONS).not.toBe(RUN_INSTRUCTIONS);
    await client.close();
  });
});

describe("a client's vendor, at sign-in", () => {
  it("is named by a registered client's own name, unverified, or none", async () => {
    const alice = owner();
    for (const [name, vendor] of [["Muse", "muse"], ["Some Agent", null]] as const) {
      const client = await mcpClient(await connect(await registerClient(name), { owner: alice }));
      await tool(client, "agents_list", {});
      await client.close();
      const rows = await sqlIn(alice, "SELECT vendor, vendor_verified FROM outside_clients " +
                                      "ORDER BY first_seen, client");
      expect(rows.at(-1)).toEqual({ vendor, vendor_verified: 0 });
    }
  });

  it("is verified by a Client ID Metadata Document's host", async () => {
    const document = "https://claude.ai/oauth/test-client-metadata";
    const redirect = "https://claude.ai/api/mcp/auth_callback";
    const real = globalThis.fetch;
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = new Request(input, init);
      return request.url === document
        ? Response.json({ client_id: document, client_name: "Claude",
                          redirect_uris: [redirect], token_endpoint_auth_method: "none",
                          grant_types: ["authorization_code", "refresh_token"],
                          response_types: ["code"] })
        : real(input, init);
    });
    try {
      const alice = owner();
      const client = await mcpClient(await connect({ clientId: document, redirectUri: redirect },
                                                   { owner: alice }));
      const { value } = await tool(client, "agents_list", {});
      expect((value["apps"] as { id: string; connected: boolean; verified: boolean }[])
        .find((app) => app.id === "claude")).toMatchObject({ connected: true, verified: true });
      expect(await sqlIn(alice, "SELECT vendor, vendor_verified FROM outside_clients"))
        .toEqual([{ vendor: "claude", vendor_verified: 1 }]);
      await client.close();
    } finally {
      spy.mockRestore();
    }
  });
});

describe("dynamic registration (the founder's ruling: for vendors with no CIMD)", () => {
  const on: DoorsEnv = { ...doorsEnv, DCR_ENABLED: "true" };

  it("is served only with DCR_ENABLED", async () => {
    expect((await register(doorsEnv, "Grok")).status).toBe(404);
    expect((await register(on, "Grok")).status).toBe(201);
  });

  it("takes only a client whose name names a vendor that registers dynamically", async () => {
    for (const name of ["Gemini", "Grok Bot", "Cursor", "Muse"]) {
      expect((await register(on, name)).status, name).toBe(201);
    }
    // A CIMD vendor's name, or no vendor's, is refused before anything is stored.
    for (const name of ["ChatGPT", "Claude", "Some Agent", ""]) {
      const refused = await register(on, name);
      expect(refused.status, name).toBe(400);
      expect(await refused.json()).toMatchObject({ error: "invalid_client_metadata" });
    }
  });

  it("names the vendor at sign-in, unverified", async () => {
    const made = await (await register(on, "Gemini")).json<{ client_id: string }>();
    const alice = owner();
    const client = await mcpClient(await connect(
      { clientId: made.client_id, redirectUri: "https://client.test/cb" }, { owner: alice }));
    await tool(client, "agents_list", {});
    expect(await sqlIn(alice, "SELECT vendor, vendor_verified FROM outside_clients"))
      .toEqual([{ vendor: "gemini", vendor_verified: 0 }]);
    await client.close();
  });
});

describe("one brain across two vendors' agents (the local proof)", () => {
  it("finds what one agent saved through the other's search, and leaves one claim of a race",
     async () => {
    const alice = owner();
    const a = await mcpClient(await connect(await registerClient("Muse"), { owner: alice }), "a");
    const b = await mcpClient(await connect(await registerClient("Claude Code",
                                                                  "http://127.0.0.1/callback"),
                                            { owner: alice }), "b");
    // A tells TinyAssets something in passing; B, in a fresh conversation, looks it up.
    const nonce = `teal${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
    const saved = await tool(a, "file_append", { path: "/workspace/USER.md",
      content: `- Favourite colour: ${nonce} <!-- observed: 2026-11-04 | status: active -->\n` });
    expect(saved.error).toBeNull();
    const found = await tool(b, "search", { query: nonce });
    expect(found.error).toBeNull();
    const hits = found.value["results"] as { id: string }[];
    expect(hits.map((hit) => hit.id)).toEqual(["/workspace/USER.md"]);
    const fetched = await tool(b, "fetch", { id: hits[0]!.id });
    expect(fetched.value["text"]).toContain(nonce);
    // The profile the seed made is still whole: the append changed none of its bytes.
    expect(fetched.value["text"]).toContain("## Profile");
    const metadata = fetched.value["metadata"] as { writer: string; etag: string };
    expect(metadata.writer).toMatch(/^client:[0-9a-f]{64}$/);
    // B corrects it in place, at the etag it read.
    const edited = await tool(b, "file_edit", { path: "/workspace/USER.md", old_text: nonce,
      new_text: `${nonce}-dark`, expected_etag: metadata.etag });
    expect(edited.error).toBeNull();
    // Both are asked, close together, to take the same act: one claim row, created once.
    const claim = (client: Client, who: string) => tool(client, "collection_write", {
      collection: "board", row_id: "offer:connect:grok", expected_etag: "", private: false,
      body: `status: doing\nowner: ${who}\nuntil: 2026-11-04T12:00:00Z` });
    const both = await Promise.all([claim(a, "a"), claim(b, "b")]);
    // One created the row; the other was answered the conflict, the row as it stands.
    expect(both.filter((answer) => answer.error === null && "row" in answer.value).length)
      .toBe(1);
    expect(both.filter((answer) => answer.error === null && "conflict" in answer.value).length)
      .toBe(1);
    const row = await tool(a, "collection_get", { collection: "board",
                                                  row_id: "offer:connect:grok" });
    expect((row.value["row"] as { body: string }).body)
      .toMatch(/^status: doing\nowner: [ab]\n/);
    const rows = await sqlIn(alice, "SELECT count(*) AS n FROM collection_rows " +
                                    "WHERE collection = 'board' AND row_id = 'offer:connect:grok'");
    expect(rows).toEqual([{ n: 1 }]);
    await a.close();
    await b.close();
  });
});

