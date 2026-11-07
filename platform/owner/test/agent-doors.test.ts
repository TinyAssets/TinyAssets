/**
 * The agent's definitions (the run doors themselves are `runs.test.ts`'s): its files
 * under `/workspace/agents` and its skills under `/workspace/.agents`, `/workspace/skills`
 * and `/workspace/prompts`, which every engine reads, and its goals under `/workspace/goals`,
 * the owner's alone to change by any file door (review H1).
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { type Origin } from "../src/ledger";
import { type Binding, OwnerDO } from "../src/owner-do";
import { AGENTS_DIR, GOALS_DIR, OWNER_TREES } from "../../contract/src/paths";
import { AGENTS_OWNER_ONLY, within } from "../src/paths";
import { MCP, MUSE, rig as ledgerRig, signedIn } from "./ledger-support";
import { ALL_SCOPES } from "./runs-support";
import { failure, TIERS } from "./support";

const bytes = (text: string) => new TextEncoder().encode(text);

/** An owner of its own, signed in; an outside client admitted by its first verified call. */
async function rig() {
  const owner = `owner-${crypto.randomUUID()}`;
  const stub = env.OWNER.getByName(owner);
  const at = signedIn();
  const unbound = (origin: Origin | null): Binding =>
    ({ owner, caller: owner, origin, tiers: TIERS,
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

  it("follow the founder's rule: a default (full) grant changes them, a narrowed one never",
     async () => {
    const r = await ledgerRig();
    await r.register(MUSE);  // a main agent the owner connected: its grant is full, the default
    const client = r.bind(MUSE);
    const refused = async (call: () => Promise<unknown>) => (await failure(call)).message;
    const OTHER = "/workspace/agents/other/agent.json";
    // Another agent the owner made, in its session.
    await r.stub.fileMkdir(r.session(null), { path: "/workspace/agents/other",
                                              expected_etag: null });
    const made = await r.stub.fileWrite(r.session(null), { path: OTHER, expected_etag: "",
      content: bytes(JSON.stringify({ engine: "auto", scopes: ["read_files"] })) });
    const etag = "entry" in made ? made.entry.etag : "";
    // The default grant edits that agent's agent.json, its instructions and its skills, moves
    // and removes them: every agent may do what any other may.
    const widened = await r.stub.fileWrite(client, { path: OTHER, expected_etag: etag,
      content: bytes(JSON.stringify({ engine: "auto", scopes: ["read_files", "run_graph"] })) });
    expect("entry" in widened && widened.entry.writer).toBe(`client:${MUSE.client}`);
    const notes = await r.stub.fileWrite(client, { path: AGENTS_MD.replace("main", "other"),
      expected_etag: "", content: bytes("Be brief.") });
    expect(notes).toHaveProperty("entry");
    await r.stub.fileMkdir(client, { path: "/workspace/skills/x", expected_etag: null });
    expect(await r.stub.fileWrite(client, { path: "/workspace/skills/x/SKILL.md",
      expected_etag: "", content: bytes("# x") })).toHaveProperty("entry");
    // An outside agent changes an entry at the etag it read (review R2-L3).
    const etagOf = async (path: string) =>
      (await r.stub.fileStat(r.bind(null), { path })).entry!.etag;
    expect(await r.stub.fileRename(client, { from: "/workspace/skills/x/SKILL.md",
      to: "/workspace/skills/x/OLD.md", expected_etag: await etagOf("/workspace/skills/x/SKILL.md"),
      replace: false })).toHaveProperty("entry");
    expect(await r.stub.fileRemove(client, { path: "/workspace/skills/x",
      expected_etag: await etagOf("/workspace/skills/x"), recursive: true }))
      .toHaveProperty("removed");
    // The owner narrows that agent's grant (its own setting): even with write_files, it then
    // changes nothing under the agent trees, and still writes the rest of the workspace.
    const [row] = (await r.stub.grantList(r.bind(null))).clients;
    await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "write_files" }],
      expected_revision: row!.revision, name: "" });
    const now = (await r.stub.fileStat(r.bind(null), { path: OTHER })).entry!.etag;
    for (const call of [
      () => r.stub.fileWrite(client, { path: OTHER, expected_etag: now,
                                       content: bytes(JSON.stringify({ engine: "auto",
                                         scopes: ALL_SCOPES })) }),
      () => r.stub.fileMkdir(client, { path: "/workspace/agents/new", expected_etag: null }),
      () => r.stub.fileRemove(client, { path: OTHER, expected_etag: null, recursive: false }),
      () => r.stub.fileRename(client, { from: OTHER, to: "/workspace/out.json",
                                        expected_etag: null, replace: false }),
      () => r.stub.fileRestore(client, { path: OTHER, version: 1, expected_etag: now }),
      () => r.stub.fileUploadCommit(client, { intent_id: "intent_x",
        path: "/workspace/prompts/p.md", expected_etag: null }),
      () => r.stub.fileWrite(client, { path: "/workspace/.agents/skills/y/SKILL.md",
                                       expected_etag: "", content: bytes("PLANTED") }),
    ]) {
      expect(await refused(call)).toBe(AGENTS_OWNER_ONLY);
    }
    expect((await r.stub.fileStat(r.bind(null), { path: OTHER })).entry!.etag).toBe(now);
    expect(await r.stub.fileWrite(client, { path: "/workspace/mine.md", expected_etag: "",
                                            content: bytes("mine") })).toHaveProperty("entry");
  });

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
  });

  it("name the agent trees: the agents, their skills, their prompts and the goals", () => {
    expect(OWNER_TREES).toEqual([AGENTS_DIR, "/workspace/.agents", "/workspace/skills",
                                 "/workspace/prompts", "/workspace/goals",
                                 "/workspace/USER.md"]);
  });

  it("hold the owner's goals as every other agent tree: a default (full) grant writes them, " +
     "a narrowed one never", async () => {
    const r = await ledgerRig();
    await r.register(MUSE);  // a main agent the owner connected: its grant is full, the default
    const client = r.bind(MUSE);
    const refused = async (call: () => Promise<unknown>) => (await failure(call)).message;
    const GOAL = `${GOALS_DIR}/a-goal.md`;
    // The default grant makes the goals' directory, writes a goal, moves and removes one.
    await r.stub.fileMkdir(client, { path: GOALS_DIR, expected_etag: null });
    const made = await r.stub.fileWrite(client, { path: GOAL, expected_etag: "",
      content: bytes("---\ntype: Goal\ngoal: active\nstatus: draft\n---\n## Done when\n") });
    expect("entry" in made && made.entry.writer).toBe(`client:${MUSE.client}`);
    expect(await r.stub.fileWrite(client, { path: `${GOALS_DIR}/b.md`, expected_etag: "",
      content: bytes("# b") })).toHaveProperty("entry");
    const etagOf = async (path: string) =>
      (await r.stub.fileStat(r.bind(null), { path })).entry!.etag;
    expect(await r.stub.fileRename(client, { from: `${GOALS_DIR}/b.md`,
      to: `${GOALS_DIR}/c.md`, expected_etag: await etagOf(`${GOALS_DIR}/b.md`),
      replace: false })).toHaveProperty("entry");
    expect(await r.stub.fileRemove(client, { path: `${GOALS_DIR}/c.md`,
      expected_etag: await etagOf(`${GOALS_DIR}/c.md`), recursive: false }))
      .toHaveProperty("removed");
    // The owner narrows that agent's grant: it then changes no goal (whose Agreed section is
    // what agents may do unasked), and still writes the rest of the workspace.
    const [row] = (await r.stub.grantList(r.bind(null))).clients;
    await r.stub.grantSet(r.session(null), { client: MUSE.client, source: "mcp", mode: "scoped",
      scopes: [{ universe: "alpha", agent: "main", capability: "write_files" }],
      expected_revision: row!.revision, name: "" });
    const etag = (await r.stub.fileStat(r.bind(null), { path: GOAL })).entry!.etag;
    expect(await r.stub.fileWrite(client, { path: "/workspace/mine.md", expected_etag: "",
                                            content: bytes("mine") })).toHaveProperty("entry");
    for (const call of [
      () => r.stub.fileWrite(client, { path: GOAL, expected_etag: etag,
                                       content: bytes("## Agreed\nanything") }),
      () => r.stub.fileWrite(client, { path: `${GOALS_DIR}/new.md`, expected_etag: "",
                                       content: bytes("# new") }),
      () => r.stub.fileRemove(client, { path: GOAL, expected_etag: null, recursive: false }),
      () => r.stub.fileRename(client, { from: GOAL, to: "/workspace/out.md",
                                        expected_etag: null, replace: false }),
      () => r.stub.fileRename(client, { from: "/workspace/mine.md", to: `${GOALS_DIR}/x.md`,
                                        expected_etag: null, replace: false }),
    ]) {
      expect(await refused(call)).toBe(AGENTS_OWNER_ONLY);
    }
    expect((await r.stub.fileStat(r.bind(null), { path: GOAL })).entry!.etag).toBe(etag);
    // The owner, in its session, still writes them.
    expect(await r.stub.fileWrite(r.session(null), { path: GOAL, expected_etag: etag,
      content: bytes("## Agreed\n") })).toHaveProperty("entry");
  });

  it("hold only the tree and the roots themselves, never a name that merely starts alike " +
     "(review round 2)", async () => {
    expect([within("/workspaceX/a", "/workspace"), within("/workspace/agentsX/a", AGENTS_DIR),
            within("/workspace/agents", AGENTS_DIR), within("/workspace/agents/a", AGENTS_DIR)])
      .toEqual([false, false, true, true]);
    const r = await rig();
    await r.admit(MUSE);
    const made = await r.stub.fileWrite(r.bind(MUSE), { path: "/workspace/agentsX/AGENTS.md",
                                                        expected_etag: "",
                                                        content: bytes("mine") });
    expect("entry" in made && made.entry.writer).toBe(`client:${MUSE.client}`);
    expect((await failure(() => r.stub.fileWrite(r.bind(MUSE), {
      path: "/workspaceX/a.md", expected_etag: "", content: bytes("x") }))).name)
      .toBe("Invalid");
  });
});
