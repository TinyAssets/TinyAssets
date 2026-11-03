/**
 * The agent's definitions (the run doors themselves are `runs.test.ts`'s): its files
 * under `/workspace/agents` and its skills under `/workspace/.agents`, `/workspace/skills`
 * and `/workspace/prompts`, which every engine reads, the owner's alone to change by any file door (review H1).
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { type Origin } from "../src/ledger";
import { type Binding, OwnerDO } from "../src/owner-do";
import { AGENTS_DIR, AGENTS_OWNER_ONLY, OWNER_TREES, within } from "../src/paths";
import { MCP, MUSE, signedIn } from "./ledger-support";
import { failure } from "./support";

const bytes = (text: string) => new TextEncoder().encode(text);

/** An owner of its own, signed in; an outside client admitted by its first verified call. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.OWNER.getByName(owner);
  const at = signedIn();
  const unbound = (origin: Origin | null): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }) });
  const { session } = await stub.sessionOpen({ ...unbound(null), authenticated_at: at });
  // The owner's own changes to its agents' files are made in its session, as its app makes
  // them; `unbound(null)` is the owner's bare token.
  const bind = (origin: Origin | null): Binding =>
    origin === null ? { ...unbound(null), session } : unbound(origin);
  return { stub, bind, unbound, admit: (origin: Origin) => stub.outsideAgents(bind(origin)) };
}

describe("the owner's agent definitions (review H1)", () => {
  const AGENTS_MD = "/workspace/agents/main/AGENTS.md";

  it("are the owner's alone to change by any file door", async () => {
    const r = await rig();
    await r.admit(MUSE);
    const client = r.bind(MUSE);
    const refused = async (call: () => Promise<unknown>) => (await failure(call)).message;
    expect(await refused(() => r.stub.fileMkdir(client, { path: "/workspace/agents",
                                                          expected_etag: null })))
      .toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileWrite(client, { path: AGENTS_MD, expected_etag: null,
                                                          content: bytes("PLANTED") })))
      .toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileUploadCommit(client, {
      intent_id: "intent_x", path: AGENTS_MD, expected_etag: null }))).toBe(AGENTS_OWNER_ONLY);
    // The owner makes the tree; the client can move nothing into, out of or within it.
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/agents", expected_etag: null });
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/agents/main", expected_etag: null });
    const made = await r.stub.fileWrite(r.bind(null), { path: AGENTS_MD, expected_etag: null,
                                                        content: bytes("Be brief.") });
    const etag = "entry" in made ? made.entry.etag : null;
    await r.stub.fileWrite(client, { path: "/workspace/mine.md", expected_etag: null,
                                     content: bytes("PLANTED") });
    const mine = (await r.stub.fileStat(client, { path: "/workspace/mine.md" })).entry!.etag;
    expect(await refused(() => r.stub.fileRename(client, { from: "/workspace/mine.md",
      to: AGENTS_MD, expected_etag: mine, replace: true }))).toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileRename(client, { from: AGENTS_MD,
      to: "/workspace/out.md", expected_etag: etag, replace: false }))).toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileRename(client, { from: "/workspace/agents/main",
      to: "/workspace/main", expected_etag: null, replace: false }))).toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileRemove(client, { path: "/workspace/agents",
      expected_etag: null, recursive: true }))).toBe(AGENTS_OWNER_ONLY);
    expect(await refused(() => r.stub.fileRestore(client, { path: AGENTS_MD, version: 1,
                                                            expected_etag: etag })))
      .toBe(AGENTS_OWNER_ONLY);
    // A client still writes the rest of the workspace, and reads the tree.
    expect((await r.stub.fileStat(client, { path: AGENTS_MD })).entry?.writer).toBe("owner");
    // The owner changes and moves its own.
    await r.stub.fileRename(r.bind(null), { from: AGENTS_MD, to: "/workspace/agents/main/old.md",
                                            expected_etag: etag, replace: false });
    expect((await r.stub.fileStat(r.bind(null), { path: AGENTS_MD })).entry).toBeNull();
  }, 30_000);

  it("take the owner's own change only in its live session, refused and receipted without",
     async () => {
    const r = await rig();
    const bare = r.unbound(null);
    const AGENT_JSON = "/workspace/agents/main/agent.json";
    const refused = async (call: () => Promise<unknown>) => (await failure(call)).message;
    const SESSION = "interactive owner session required";
    // Without the session the owner's bare token defines, widens, moves or removes nothing.
    expect(await refused(() => r.stub.fileMkdir(bare, { path: "/workspace/agents",
                                                        expected_etag: null }))).toBe(SESSION);
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/agents/main", expected_etag: null });
    const made = await r.stub.fileWrite(r.bind(null), { path: AGENT_JSON, expected_etag: "",
      content: bytes(JSON.stringify({ engine: "auto", scopes: ["read_files"] })) });
    const etag = "entry" in made ? made.entry.etag : "";
    const widened = bytes(JSON.stringify({ engine: "auto", scopes: ["read_files", "run_graph"] }));
    expect(await refused(() => r.stub.fileWrite(bare, { path: AGENT_JSON, expected_etag: etag,
                                                        content: widened }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileRemove(bare, { path: AGENT_JSON, expected_etag: etag,
                                                         recursive: false }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileRemove(bare, { path: "/workspace/agents",
      expected_etag: null, recursive: true }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileRename(bare, { from: AGENT_JSON,
      to: "/workspace/x.json", expected_etag: etag, replace: false }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileRename(bare, { from: "/workspace/notes.md",
      to: "/workspace/prompts/p.md", expected_etag: null, replace: false }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileRestore(bare, { path: AGENT_JSON, version: 1,
                                                          expected_etag: etag }))).toBe(SESSION);
    expect(await refused(() => r.stub.fileUploadCommit(bare, { intent_id: "intent_x",
      path: "/workspace/skills/x/SKILL.md", expected_etag: null }))).toBe(SESSION);
    // Each refusal is receipted, and nothing changed.
    const { receipts } = await r.stub.policyReceipts(r.bind(null));
    expect(receipts.filter((receipt) => receipt.outcome === "refused").length).toBe(8);
    expect((await r.stub.fileRead(r.bind(null), { path: AGENT_JSON })).entry.etag).toBe(etag);
    // Outside the owner's trees, the bare token writes as before.
    expect(await r.stub.fileWrite(bare, { path: "/workspace/notes.md", expected_etag: "",
                                          content: bytes("mine") })).toHaveProperty("entry");
  }, 30_000);

  it("hold the skills trees too: a client writes no skill an engine would read", async () => {
    expect(OWNER_TREES).toEqual([AGENTS_DIR, "/workspace/.agents", "/workspace/skills",
                                 "/workspace/prompts"]);
    const r = await rig();
    await r.admit(MUSE);
    for (const path of ["/workspace/.agents/skills/x/SKILL.md", "/workspace/skills/x/SKILL.md",
                        "/workspace/prompts/x.md"]) {
      expect((await failure(() => r.stub.fileWrite(r.bind(MUSE), {
        path, expected_etag: null, content: bytes("PLANTED") }))).message)
        .toBe(AGENTS_OWNER_ONLY);
    }
  }, 30_000);

  it("hold only the tree and the roots themselves, never a name that merely starts alike " +
     "(review round 2)", async () => {
    expect([within("/workspaceX/a", "/workspace"), within("/workspace/agentsX/a", AGENTS_DIR),
            within("/workspace/agents", AGENTS_DIR), within("/workspace/agents/a", AGENTS_DIR)])
      .toEqual([false, false, true, true]);
    const r = await rig();
    await r.admit(MUSE);
    const made = await r.stub.fileWrite(r.bind(MUSE), { path: "/workspace/agentsX/AGENTS.md",
                                                        expected_etag: null,
                                                        content: bytes("mine") });
    expect("entry" in made && made.entry.writer).toBe(`client:${MUSE.client}`);
    expect((await failure(() => r.stub.fileWrite(r.bind(MUSE), {
      path: "/workspaceX/a.md", expected_etag: null, content: bytes("x") }))).name)
      .toBe("Invalid");
  }, 30_000);
});
