/**
 * The MCP door's file readers (moved from the starter's tests, which hold the seed alone):
 * search by title, description, tags, index lines and small text, fetch by id, windows cut
 * at a character, `file_list`'s heads, every reader held to the caller's grant, and the
 * search paging by matches, with nothing out of reach.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { type Entry, fileUrl } from "../src/files";
import { SEARCH_ANSWER_BYTES, SEARCH_SCAN, searchPage, type Scanned } from "../src/file-search";
import { MCP, MUSE, rig, signedIn } from "./ledger-support";
import { TIERS } from "./runs-support";
import { failure, filesOn, inGate, text } from "./support";

const decode = (bytes: Uint8Array | undefined) => new TextDecoder().decode(bytes);

describe("the MCP door's file readers", () => {
  async function seededWithKnowledge() {
    const r = await rig();
    const write = (path: string, value: string) => r.stub.fileWrite(r.session(null), {
      path, expected_etag: null, content: text(value) });
    await r.stub.fileMkdir(r.session(null), { path: "/workspace/knowledge/people",
                                           expected_etag: null });
    await write("/workspace/knowledge/index.md",
                "---\nokf_version: \"0.2\"\n---\n# People\n\n* [People](people/index.md) - who\n");
    await write("/workspace/knowledge/people/index.md",
                "# People\n\n* [Ana Lima](/people/ana.md) - the accountant who files our taxes\n");
    // A large page: only its index line and its path describe it without a bucket read.
    await write("/workspace/knowledge/people/ana.md",
                `---\ntype: Person\ntitle: Ana Lima\n---\n${"notes ".repeat(2000)}\nquarterly`);
    await write("/workspace/knowledge/people/bo.md",
                "---\ntype: Person\ntitle: Bo\ndescription: Our plumber, calls on Tuesdays\n" +
                "tags: [home, repairs]\n---\nBo fixed the boiler.\n");
    await write("/workspace/notes.txt", "a word: zanzibar");
    return r;
  }

  it("find by title, description, tags, index lines and small text, answering ChatGPT's " +
     "shape, and fetch by id", async () => {
    const r = await seededWithKnowledge();
    const ids = async (query: string) => (await r.stub.fileSearch(r.bind(null), { query }))
      .results.map((hit) => hit.id);
    expect(await ids("plumber")).toEqual(["/workspace/knowledge/people/bo.md"]);
    expect(await ids("repairs")).toEqual(["/workspace/knowledge/people/bo.md"]);
    // The large page by its index line alone, ahead of the index that names it.
    expect((await ids("accountant taxes"))[0]).toBe("/workspace/knowledge/people/ana.md");
    expect(await ids("zanzibar")).toEqual(["/workspace/notes.txt"]);
    const { results, next_cursor } = await r.stub.fileSearch(r.bind(null), { query: "Bo" });
    expect(next_cursor).toBeNull();
    expect(results[0]).toEqual({ id: "/workspace/knowledge/people/bo.md", title: "Bo",
                                 url: fileUrl("/workspace/knowledge/people/bo.md") });
    expect(results[0]!.url).toBe("https://tinyassets.io/app#data/" +
                                 encodeURIComponent("/workspace/knowledge/people"));
    const fetched = await r.stub.fileFetch(r.bind(null), { id: "/workspace/knowledge/people/bo.md" });
    expect(fetched).toMatchObject({ id: "/workspace/knowledge/people/bo.md", title: "Bo",
                                    url: results[0]!.url,
                                    metadata: { type: "Person", tags: ["home", "repairs"],
                                                description: "Our plumber, calls on Tuesdays",
                                                truncated: false } });
    expect(fetched.text).toContain("Bo fixed the boiler.");
    // The large page reads whole through its chunk, and in windows by file_read.
    const big = await r.stub.fileFetch(r.bind(null), { id: "/workspace/knowledge/people/ana.md" });
    expect(big.text.endsWith("quarterly")).toBe(true);
    const first = await r.stub.fileText(r.bind(null), { path: "/workspace/knowledge/people/ana.md",
                                                        max_bytes: 5000 });
    expect(first.text.length).toBe(5000);
    const rest = await r.stub.fileText(r.bind(null), { path: "/workspace/knowledge/people/ana.md",
                                                       offset: first.next_offset! });
    expect(first.text + rest.text).toBe(big.text);
    expect(rest.next_offset).toBeNull();
  });

  it("cut a window at a character, refuse a start inside one, and refuse what is not text",
     async () => {
    const r = await rig();
    await r.stub.fileWrite(r.session(null), { path: "/workspace/u.md", expected_etag: null,
                                           content: text("aé€😀z") });
    const one = await r.stub.fileText(r.bind(null), { path: "/workspace/u.md", max_bytes: 4 });
    expect(one).toMatchObject({ text: "aé", next_offset: 3 });
    expect((await r.stub.fileText(r.bind(null), { path: "/workspace/u.md", offset: 3,
                                                   max_bytes: 1 })).text).toBe("€");
    expect((await failure(() => r.stub.fileText(r.bind(null), { path: "/workspace/u.md",
                                                                 offset: 2 }))).message)
      .toMatch(/inside a character/);
    await r.stub.fileWrite(r.session(null), { path: "/workspace/b.bin", expected_etag: null,
                                           content: new Uint8Array([0xff, 0xfe, 0x00]) });
    expect((await failure(() => r.stub.fileText(r.bind(null), { path: "/workspace/b.bin" })))
      .message).toMatch(/not UTF-8 text/);
  });

  it("give file_list heads of small files only", async () => {
    const r = await seededWithKnowledge();
    const { entries } = await r.stub.fileList(r.bind(null), {
      dir: "/workspace/knowledge/people", limit: 10, head_chars: 30 });
    const heads = Object.fromEntries(entries.map((entry: Entry & { head?: string }) =>
      [entry.path.split("/").at(-1), entry.head ?? null]));
    expect(heads).toEqual({ "ana.md": null,
                            "bo.md": "---\ntype: Person\ntitle: Bo\ndescription: Our plumber"
                              .slice(0, 30),
                            "index.md": "# People\n\n* [Ana Lima](/people/ana.md)".slice(0, 30) });
  });

  it("are held to the caller's grant: read_files, as any file door", async () => {
    const r = await seededWithKnowledge();
    // A client whose grant holds no read_files reads nothing.
    const client = r.bind(MUSE, { authenticated_at: signedIn() });
    await r.stub.outsideAgents(client);
    const { revision } = (await r.stub.grantList(r.bind(null))).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "read_graph" }],
      expected_revision: revision, name: "Muse" });
    const { card_id, digest } = await r.stub.registerPropose(client, {
      command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
      recipe_id: null });
    await r.stub.registerAgent(r.session(null), { card_id, digest });
    for (const call of [() => r.stub.fileSearch(client, { query: "Bo" }),
                        () => r.stub.fileFetch(client, { id: "/workspace/notes.txt" }),
                        () => r.stub.fileText(client, { path: "/workspace/notes.txt" })]) {
      expect((await failure(call)).message).toBe("outside client capability not granted");
    }
    // Granted read_files, it reads.
    const granted = (await r.stub.grantList(r.bind(null))).clients
      .find((entry) => entry.client === MUSE.client)!;
    await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: ["read_graph", "read_files"].map((capability) =>
        ({ universe: "alpha", agent: "main", capability })),
      expected_revision: granted.revision, name: "Muse" });
    expect((await r.stub.fileSearch(client, { query: "zanzibar" })).results.map((h) => h.id))
      .toEqual(["/workspace/notes.txt"]);
  });

  it("search a page of the manifest at a time, from a cursor, in path order", async () => {
    expect(SEARCH_SCAN).toBe(5000);
    const r = await rig();
    for (const path of ["/workspace/a.md", "/workspace/d/b.md", "/workspace/d/e/c.md"]) {
      await r.stub.fileWrite(r.session(null), { path, expected_etag: null,
                                             content: text("needle") });
    }
    const all = await r.stub.fileSearch(r.bind(null), { query: "needle" });
    expect([all.results.length, all.next_cursor]).toEqual([3, null]);
    // From a cursor, only what comes after it; the manifest's own pages, in path order.
    const after = await r.stub.fileSearch(r.bind(null), { query: "needle",
                                                          cursor: Buffer.from(
                                                            "/workspace/d/b.md")
                                                            .toString("base64url") });
    expect(after.results.map((hit) => hit.id)).toEqual(["/workspace/d/e/c.md"]);
    const pages = await inGate(r.stub, (_instance, _state, gate) => {
      const files = filesOn(gate.storage, gate.write);
      const first = files.scan(null, 2);
      return [first, files.scan(first.next, 2)].map((page) => ({
        paths: page.entries.map((entry) => entry.path), next: page.next }));
    });
    expect(pages).toEqual([
      { paths: ["/workspace/a.md", "/workspace/d"], next: "/workspace/d" },
      { paths: ["/workspace/d/b.md", "/workspace/d/e"], next: "/workspace/d/e" }]);
    expect((await failure(() => r.stub.fileSearch(r.bind(null), { query: "  ,; " }))).message)
      .toMatch(/at least one word/);
  });
});

describe("the MCP door's search (review M3, L4)", () => {
  it("answers every match, with nothing out of reach", async () => {
    const r = await rig();
    for (let i = 0; i < 25; i++) {
      await r.stub.fileWrite(r.session(null), { path: `/workspace/n${String(i).padStart(2, "0")}.md`,
                                             expected_etag: null, content: text("needle") });
    }
    const found = await r.stub.fileSearch(r.bind(null), { query: "needle" });
    expect(found.results).toHaveLength(25);
    expect(found.next_cursor).toBeNull();
  });

  it("pages by matches past its answer's bytes, resuming right after the last one answered",
     () => {
    const entries: Scanned[] = Array.from({ length: 30 }, (_, i) => ({
      path: `/workspace/p${String(i).padStart(2, "0")}.md`, kind: "file",
      inline: text(i % 3 === 0 ? "plain" : "needle") }));
    const query = new Set(["needle"]);
    const all: string[] = [];
    let from = 0;
    for (let pages = 0; pages < 30; pages++) {
      const page = searchPage(entries.slice(from), false, query, () => "u", 300);
      all.push(...page.results.map((hit) => hit.id));
      if (page.resume === null) {
        break;
      }
      expect(page.results.map((hit) => hit.id)).toContain(page.resume);
      from = entries.findIndex((entry) => entry.path === page.resume) + 1;
    }
    expect(all.sort()).toEqual(entries.filter((_, i) => i % 3 !== 0).map((e) => e.path));
    // The scan's own end says more is left, past the last entry read.
    expect(searchPage(entries.slice(0, 2), true, query, () => "u").resume)
      .toBe("/workspace/p01.md");
    expect(SEARCH_ANSWER_BYTES).toBe(50_000 - 64 - (Math.ceil(4096 * 4 / 3) + 2 + 1024));
    expect(Buffer.from("\u0001".repeat(4096)).toString("base64url")).toHaveLength(5_462);
  });

  it("fetches every id a search answers, a file that is not text as its metadata", async () => {
    const r = await rig();
    await r.stub.fileWrite(r.session(null), { path: "/workspace/receipt-scan.pdf",
                                           expected_etag: null,
                                           content: new Uint8Array([0x25, 0x50, 0xff, 0xfe]) });
    const ids = (await r.stub.fileSearch(r.bind(null), { query: "receipt" })).results
      .map((hit) => hit.id);
    expect(ids).toEqual(["/workspace/receipt-scan.pdf"]);
    expect(await r.stub.fileFetch(r.bind(null), { id: ids[0]! })).toMatchObject({
      id: "/workspace/receipt-scan.pdf", title: "receipt-scan.pdf", text: "",
      metadata: { binary: true, truncated: false, size: 4 } });
    // file_read still refuses it: it reads text.
    expect((await failure(() => r.stub.fileText(r.bind(null), { path: ids[0]! }))).message)
      .toMatch(/not UTF-8 text/);
  });
});
