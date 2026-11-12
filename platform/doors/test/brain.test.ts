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
import type { DoorsEnv } from "../src/env";
import {
  call, connect, doorsEnv, OPEN, registerClient, setControl, sqlIn, type Tokens, url,
  answerOf,
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
    : { error: null, value: answerOf(answer) };
}

const register = (environment: DoorsEnv, name: string, redirect = "https://client.test/cb") =>
  call(new Request(url("/oauth/register"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_name: name, redirect_uris: [redirect],
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
    for (const [name, vendor] of [["Muse", "muse"], ["Gemini", "gemini"],
                                  ["Some Agent", null]] as const) {
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
    const metadata = await (await call(new Request(
      url("/.well-known/oauth-authorization-server")), on)).json<Record<string, unknown>>();
    expect(metadata["registration_endpoint"]).toBe("https://tinyassets.io/oauth/register");
  });

  it("takes no registration for a vendor whose redirects are not recorded yet, a CIMD " +
     "vendor's name or none, and none of the review's phishing ones (M3)", async () => {
    const refused = async (name: string, redirect = "https://client.test/cb") => {
      const answer = await register(on, name, redirect);
      expect(answer.status, name).toBe(400);
      return (await answer.json<{ error: string; error_description: string }>());
    };
    // Every dcr vendor's redirects wait for the live test: dynamic registration is off.
    for (const name of ["Gemini", "Grok Bot", "Cursor", "Muse"]) {
      expect(await refused(name)).toMatchObject({ error: "invalid_client_metadata",
        error_description: expect.stringMatching(/is off until its redirect URIs are recorded/) });
    }
    for (const name of ["ChatGPT", "Claude", "Some Agent", ""]) {
      expect((await refused(name)).error_description).toMatch(/^Dynamic registration is only/);
    }
    for (const name of ["Grok official TinyAssets support", "Cursor helper by evil",
                        "Gemini Import Tool", "Grok by phish.example"]) {
      await refused(name, "https://phish.example/cb");
    }
  });

  it("logs one line for a refused registration naming a dcr vendor, for the founder to " +
     "record its redirects (R2-M1), and none for any other", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
      await register(on, "Grok", "https://real-grok.example/oauth/callback");
      await register(on, "Some Agent", "https://other.test/cb");
      const lines = warn.mock.calls.map((args) => String(args[0]))
        .filter((line) => line.startsWith("dcr_refused "));
      expect(lines.map((line) => JSON.parse(line.slice("dcr_refused ".length)))).toEqual([
        { app: "grok", client_name: "Grok",
          redirect_uris: ["https://real-grok.example/oauth/callback"] }]);
    } finally {
      warn.mockRestore();
    }
  });
});

describe("a CIMD document on a vendor's host that sends access elsewhere (review M2)", () => {
  it("is that vendor unverified, and its consent says where access goes", async () => {
    const document = "https://chatgpt.com/some/user/path/cimd.json";
    const redirect = "https://attacker.example/cb";
    const real = globalThis.fetch;
    const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = new Request(input, init);
      return request.url === document
        ? Response.json({ client_id: document, client_name: "ChatGPT",
                          redirect_uris: [redirect], token_endpoint_auth_method: "none",
                          grant_types: ["authorization_code", "refresh_token"],
                          response_types: ["code"] })
        : real(input, init);
    });
    try {
      const alice = owner();
      const client = await mcpClient(await connect({ clientId: document, redirectUri: redirect },
                                                   { owner: alice }));
      await tool(client, "agents_list", {});
      expect(await sqlIn(alice, "SELECT vendor, vendor_verified FROM outside_clients"))
        .toEqual([{ vendor: "chatgpt", vendor_verified: 0 }]);
      await client.close();
    } finally {
      spy.mockRestore();
    }
  });
});

describe("file_write over MCP (review H1)", () => {
  it("never replaces a file with no etag: null is refused, and \"\" on a file there is a " +
     "conflict", async () => {
    const alice = owner();
    const client = await mcpClient(await connect(await registerClient("Claude Code",
                                                                      "http://127.0.0.1/cb"),
                                                 { owner: alice }));
    const write = (await client.listTools()).tools.find((t) => t.name === "file_write")!;
    expect(JSON.stringify(write.inputSchema)).not.toContain("null");
    // The schema refuses null before the object; the object refuses it too (owner test).
    const nulled = await tool(client, "file_write", { path: "/workspace/USER.md",
                                                      expected_etag: null,
                                                      content: "# replaced\n" });
    expect(nulled.error).not.toBeNull();
    const created = await tool(client, "file_write", { path: "/workspace/USER.md",
                                                       expected_etag: "",
                                                       content: "# replaced\n" });
    expect(created.error).toBeNull();
    expect(created.value).toHaveProperty("conflict");
    const after = await tool(client, "fetch", { id: "/workspace/USER.md" });
    expect(after.value["text"]).toContain("## Profile");
    // At the etag it read, the replacement lands.
    const etag = (after.value["metadata"] as { etag: string }).etag;
    const replaced = await tool(client, "file_write", { path: "/workspace/USER.md",
                                                        expected_etag: etag,
                                                        content: "# mine\n" });
    expect(replaced.value).toHaveProperty("entry");
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

