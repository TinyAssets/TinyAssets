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
  const bind = (origin: Origin | null): Binding =>
    ({ owner, caller: owner, origin,
       ...(origin === null ? {} : { source: MCP, authenticated_at: at }) });
  await stub.sessionOpen({ ...bind(null), authenticated_at: at });
  return { stub, bind, admit: (origin: Origin) => stub.outsideAgents(bind(origin)) };
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
