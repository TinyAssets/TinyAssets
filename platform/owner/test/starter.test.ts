/**
 * The starter package (`starter.ts`; the starter slice): every new owner seeded with the
 * default agent's files as the owner's own writes, one receipt a file and `agent.json` last;
 * updates by the conffile rule (an unedited file replaced, an edited one given a `.ta-new`
 * and one notice, a deleted one never restored, a widening a proposal); a failed seed that
 * leaves no agent and is tried again; a run that makes its owner's schedules while a client's
 * run is held to its grant; a scheduled run's result posted to the owner's room with its
 * agent; and the MCP door's file readers held to the caller's grant.
 */
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { agentFile } from "../src/agent-file";
import { type Entry } from "../src/files";
import { nativeId } from "../src/ledger";
import { Notices } from "../src/notices";
import { fileUrl, type OwnerDO, RUN_STARTS_NONE } from "../src/owner-do";
import { SEARCH_SCAN } from "../src/file-search";
import {
  carried, contentIdOf, NEWER, Starter, type StarterFile, starterPackage, type StarterPackage,
} from "../src/starter";
import { gated, type Gated } from "./gateway-support";
import { MCP, MUSE, rig, signedIn } from "./ledger-support";
import {
  ALL_SCOPES, defineAgent, ownerRun, recorder, runBinding, runnerBinding, startOf, TIERS,
} from "./runs-support";
import { drainDue, failure, filesOn, inGate, laneAt, text } from "./support";

const decode = (bytes: Uint8Array | undefined) => new TextDecoder().decode(bytes);
const SIGNED_IN = Object.freeze({ engine: "claude-code", credential: "vendor_login_in_sandbox",
                                  scopes: ALL_SCOPES });

type Rig = Awaited<ReturnType<typeof rig>>;

/** A file's whole text through `file_read`'s door, or null when there is none. */
async function readText(r: Rig, path: string): Promise<string | null> {
  const { entry } = await r.stub.fileStat(r.bind(null), { path });
  if (entry === null) {
    return null;
  }
  return (await r.stub.fileText(r.bind(null), { path, max_bytes: 4_194_304 })).text;
}

/**
 * One pass of `pkg` over `r`'s object (built with no package of its own), as its lane runs
 * it: the files plane over the object's storage, at `now`.
 */
function pass(r: Rig, pkg: StarterPackage, now = Date.now() / 1000): Promise<void> {
  return inGate(r.stub, async (_instance, state, gate) => {
    const notices = new Notices(gate.storage);
    const starter = new Starter({
      storage: gate.storage, files: filesOn(gate.storage, gate.write), write: gate.write,
      admitted: () => true, clock: () => now,
      notice: (type, detail) => notices.add(type, detail, now),
    }, pkg);
    await starter.run(now);
  });
}

/** When the lane would next run `pkg` on `r`'s object, at `now`. */
function nextOf(r: Rig, pkg: StarterPackage, now: number): Promise<number | null> {
  return inGate(r.stub, (_instance, _state, gate) => new Starter({
    storage: gate.storage, files: filesOn(gate.storage, gate.write), write: gate.write,
    admitted: () => true, clock: () => now, notice: () => {},
  }, pkg).next(now));
}

const AGENTS = "/workspace/agents/main";
const SKILLS = "/workspace/.agents/skills";
const file = (path: string, value: string, extra: Partial<StarterFile> = {}): StarterFile =>
  ({ path, class: "managed", text: value, ...extra });
const agentJson = (scopes: string[]) => JSON.stringify({ engine: "auto", scopes });

const V1 = starterPackage({ version: 1, files: [
  file(`${SKILLS}/a/SKILL.md`, "a1"), file(`${SKILLS}/b/SKILL.md`, "b1"),
  file(`${SKILLS}/c/SKILL.md`, "c1"), file(`${AGENTS}/SOUL.md`, "soul1", { class: "personal" }),
  file(`${AGENTS}/AGENTS.md`, "agents1"), file(`${AGENTS}/agent.json`, agentJson(["read_files"])),
] });
const V2 = starterPackage({ version: 2, files: [
  file(`${SKILLS}/a/SKILL.md`, "a2"), file(`${SKILLS}/b/SKILL.md`, "b2"),
  file(`${SKILLS}/c/SKILL.md`, "c2"), file(`${AGENTS}/SOUL.md`, "soul2", { class: "personal" }),
  file(`${SKILLS}/d/SKILL.md`, "d2"), file(`${SKILLS}/e/run.sh`, "echo hi"),
  file(`${SKILLS}/f/SKILL.md`, "f2", { widens: { version: 2, why: "it fetches the web" } }),
  file(`${AGENTS}/AGENTS.md`, "agents1"),
  file(`${AGENTS}/agent.json`, agentJson(["read_files", "run_graph"])),
] });
const V3 = starterPackage({ ...V2, version: 3, files: V2.files.map((each) =>
  each.path === `${SKILLS}/a/SKILL.md` ? { ...each, text: "a3" } : each) });

async function receipts(r: Rig) {
  return Object.fromEntries((await r.stub.starterReceipts(r.bind(null))).receipts
    .map((row) => [row.path, row]));
}

async function notices(r: Rig, type: string): Promise<string[]> {
  return (await r.stub.noticeList(r.bind(null))).notices
    .filter((notice) => notice.type === type).map((notice) => notice.detail);
}

describe("a new owner", () => {
  it("is seeded with every starter file, as its own writes, a receipt each, agent.json last",
     async () => {
    const r = await rig(env.SEEDED as unknown as DurableObjectNamespace<OwnerDO>);
    await drainDue(r.stub);
    const pkg = carried();
    const versions: Record<string, number> = {};
    for (const each of pkg.files) {
      const { entry } = await r.stub.fileStat(r.bind(null), { path: each.path });
      expect(entry).toMatchObject({ kind: "file", writer: "owner" });
      versions[each.path] = entry!.version;
      expect(await readText(r, each.path)).toBe(each.text);
      const { changes } = await r.stub.fileHistory(r.bind(null), { path: each.path, limit: 5 });
      expect(changes.map((change) => change.op)).toEqual(["starter"]);
    }
    // agent.json is the last write: until it lands there is no agent.
    const json = `${AGENTS}/agent.json`;
    expect(Math.max(...Object.values(versions))).toBe(versions[json]);
    expect(agentFile(pkg.files.at(-1)!.text).scopes).toContain("run_graph");
    // A receipt for each, its content id the files plane's own.
    const { version, carried: carrying, receipts: rows } =
      await r.stub.starterReceipts(r.bind(null));
    expect([version, carrying]).toEqual([pkg.version, pkg.version]);
    expect(rows.map((row) => [row.path, row.state, row.installed, row.offered]).sort())
      .toEqual(pkg.files.map((each) => [each.path, "installed",
                                        contentIdOf(text(each.text)), null]).sort());
    // `main` is an agent of the owner's, a ledger identity, and nothing is left to do.
    expect((await r.stub.outsideAgents(r.bind(null))).owner_agents.map((a) => a.agent))
      .toEqual(["main"]);
    expect(await laneAt(r.stub, "starter")).toBeNull();
    // The vendored OKF spec is placed in R2 like any large file, and reads back whole.
    const okf = pkg.files.find((each) => each.path.endsWith("okf-spec.md"))!;
    expect(okf.text.length).toBeGreaterThan(4096);
  });

  it("whose seed fails part way gets no agent, one notice, and a retry until it lands",
     async () => {
    const r = await rig();
    // Something of the owner's is in the way: a file where the agent's directory goes.
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/agents", expected_etag: null });
    await r.stub.fileWrite(r.bind(null), { path: AGENTS, expected_etag: null,
                                           content: text("not a directory") });
    const now = Date.now() / 1000;
    await pass(r, V1, now);
    expect((await r.stub.fileStat(r.bind(null), { path: `${SKILLS}/a/SKILL.md` })).entry)
      .not.toBeNull();
    expect((await r.stub.fileStat(r.bind(null), { path: `${SKILLS}/c/SKILL.md` })).entry)
      .not.toBeNull();
    expect((await r.stub.outsideAgents(r.bind(null))).owner_agents).toEqual([]);
    expect(await notices(r, "starter_failed")).toHaveLength(1);
    expect((await r.stub.starterReceipts(r.bind(null))).version).toBe(0);
    // It waits its retry, then fails again without telling the owner twice.
    expect(await nextOf(r, V1, now)).toBeGreaterThan(now);
    await pass(r, V1, now + 301);
    expect(await notices(r, "starter_failed")).toHaveLength(1);
    // Out of the way, it lands whole, agent.json last.
    const { entry } = await r.stub.fileStat(r.bind(null), { path: AGENTS });
    await r.stub.fileRemove(r.bind(null), { path: AGENTS, expected_etag: entry!.etag,
                                            recursive: false });
    await pass(r, V1, now + 700);
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(agentJson(["read_files"]));
    const versions = await Promise.all(V1.files.map(async (each) =>
      (await r.stub.fileStat(r.bind(null), { path: each.path })).entry!.version));
    expect(Math.max(...versions)).toBe(versions.at(-1));
    expect((await r.stub.starterReceipts(r.bind(null))).version).toBe(1);
    expect(await nextOf(r, V1, now + 700)).toBeNull();
  });

  it("keeps a file the owner made before the seed, offering the starter's beside it",
     async () => {
    const r = await rig();
    await r.stub.fileMkdir(r.bind(null), { path: AGENTS, expected_etag: null });
    await r.stub.fileWrite(r.bind(null), { path: `${AGENTS}/AGENTS.md`, expected_etag: null,
                                           content: text("mine") });
    await pass(r, V1);
    expect(await readText(r, `${AGENTS}/AGENTS.md`)).toBe("mine");
    expect(await readText(r, `${AGENTS}/AGENTS.md${NEWER}`)).toBe("agents1");
    expect((await receipts(r))[`${AGENTS}/AGENTS.md`]).toMatchObject({ state: "edited" });
  });
});

describe("a starter update", () => {
  it("replaces unedited files, offers edited ones beside them with one notice, never " +
     "restores a deleted one, never touches personal ones, and proposes a widening",
     async () => {
    const r = await rig();
    await pass(r, V1);
    // The owner edits b and SOUL.md, and deletes c.
    const edit = async (path: string, value: string) => {
      const { entry } = await r.stub.fileStat(r.bind(null), { path });
      await r.stub.fileWrite(r.bind(null), { path, expected_etag: entry!.etag,
                                             content: text(value) });
    };
    await edit(`${SKILLS}/b/SKILL.md`, "b-mine");
    await edit(`${AGENTS}/SOUL.md`, "soul-mine");
    const { entry: c } = await r.stub.fileStat(r.bind(null), { path: `${SKILLS}/c/SKILL.md` });
    await r.stub.fileRemove(r.bind(null), { path: `${SKILLS}/c/SKILL.md`, expected_etag: c!.etag,
                                            recursive: false });
    await pass(r, V2);
    expect(await readText(r, `${SKILLS}/a/SKILL.md`)).toBe("a2");
    expect(await readText(r, `${SKILLS}/b/SKILL.md`)).toBe("b-mine");
    expect(await readText(r, `${SKILLS}/b/SKILL.md${NEWER}`)).toBe("b2");
    expect(await readText(r, `${SKILLS}/c/SKILL.md`)).toBeNull();
    expect(await readText(r, `${AGENTS}/SOUL.md`)).toBe("soul-mine");
    expect(await readText(r, `${AGENTS}/SOUL.md${NEWER}`)).toBeNull();
    expect(await readText(r, `${SKILLS}/d/SKILL.md`)).toBe("d2");
    // A widening waits beside its file, applied or not, unedited or new.
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(agentJson(["read_files"]));
    expect(await readText(r, `${AGENTS}/agent.json${NEWER}`))
      .toBe(agentJson(["read_files", "run_graph"]));
    expect(await readText(r, `${SKILLS}/e/run.sh`)).toBeNull();
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBe("echo hi");
    expect(await readText(r, `${SKILLS}/f/SKILL.md`)).toBeNull();
    expect(await readText(r, `${SKILLS}/f/SKILL.md${NEWER}`)).toBe("f2");
    const kept = await receipts(r);
    expect(Object.fromEntries(Object.entries(kept).map(([path, row]) => [path, row.state])))
      .toEqual({ [`${SKILLS}/a/SKILL.md`]: "installed", [`${SKILLS}/b/SKILL.md`]: "edited",
                 [`${SKILLS}/c/SKILL.md`]: "deleted", [`${SKILLS}/d/SKILL.md`]: "installed",
                 [`${SKILLS}/e/run.sh`]: "proposed", [`${SKILLS}/f/SKILL.md`]: "proposed",
                 [`${AGENTS}/SOUL.md`]: "installed", [`${AGENTS}/AGENTS.md`]: "installed",
                 [`${AGENTS}/agent.json`]: "proposed" });
    // One notice for the edited files, one for the proposals.
    const updated = await notices(r, "starter_update");
    expect(updated).toHaveLength(1);
    expect(updated[0]).toContain(`${SKILLS}/b/SKILL.md`);
    const proposed = await notices(r, "starter_proposal");
    expect(proposed).toHaveLength(1);
    expect(proposed[0]).toContain("agent.json adds the scopes run_graph");
    expect(proposed[0]).toContain("e/run.sh is a new script");
    expect(proposed[0]).toContain("it fetches the web");
    // The replacement is a version of its own: the history says the starter wrote it.
    const { changes } = await r.stub.fileHistory(r.bind(null), { path: `${SKILLS}/a/SKILL.md`,
                                                                  limit: 5 });
    expect(changes.map((change) => change.op)).toEqual(["starter", "starter"]);
    // A pass of the same version again changes nothing and tells nothing new.
    expect(await nextOf(r, V2, Date.now() / 1000)).toBeNull();
  });

  it("is undone by the file's own history, and an adopted proposal counts as installed",
     async () => {
    const r = await rig();
    await pass(r, V1);
    await pass(r, V2);
    // Undo: the owner restores the version before the update.
    const path = `${SKILLS}/a/SKILL.md`;
    const { changes } = await r.stub.fileHistory(r.bind(null), { path, limit: 5 });
    const { entry } = await r.stub.fileStat(r.bind(null), { path });
    await r.stub.fileRestore(r.bind(null), { path, version: changes[1]!.version,
                                             expected_etag: entry!.etag });
    expect(await readText(r, path)).toBe("a1");
    // Adopt: the owner moves the proposal over its file.
    const json = `${AGENTS}/agent.json`;
    const { entry: offered } = await r.stub.fileStat(r.bind(null), { path: `${json}${NEWER}` });
    await r.stub.fileRename(r.bind(null), { from: `${json}${NEWER}`, to: json,
                                            expected_etag: offered!.etag, replace: true });
    await pass(r, V3);
    // The restored file is the owner's choice now: never replaced, the update offered.
    expect(await readText(r, path)).toBe("a1");
    expect(await readText(r, `${path}${NEWER}`)).toBe("a3");
    const kept = await receipts(r);
    expect(kept[path]).toMatchObject({ state: "edited" });
    expect(kept[json]).toMatchObject({ state: "installed", version: 3,
                                       installed: contentIdOf(text(agentJson(["read_files",
                                                                              "run_graph"]))) });
  });
});

describe("a run's own schedules (the founder, 2026-11-03)", () => {
  /** The owner's run of `main`, holding `scopes`, and its own MCP binding. */
  async function asRun(r: Gated, scopes: readonly string[] = ALL_SCOPES) {
    const { start } = await ownerRun(r.stub, r.bind(), { ...SIGNED_IN, scopes });
    return { start, binding: runBinding(r, start) };
  }

  it("are made, read and cancelled by an owner's run holding run_graph", async () => {
    const r = await gated();
    const { binding } = await asRun(r);
    const made = await r.stub.agentRun(binding, { agent: "main", input: "the morning brief",
                                                  cron: "0 7 * * *" }) as { schedule_id: string };
    expect(made).toMatchObject({ agent: "main", trigger: "cron", cron: "0 7 * * *" });
    // The owner's own: the owner reads it, and so does the run.
    expect(await r.stub.agentStatus(r.bind(), { schedule_id: made.schedule_id }))
      .toMatchObject({ schedule_id: made.schedule_id });
    expect(await r.stub.agentStatus(binding, { schedule_id: made.schedule_id }))
      .toMatchObject({ schedule_id: made.schedule_id });
    expect(await r.stub.agentCancel(binding, { schedule_id: made.schedule_id }))
      .toEqual({ schedule_id: made.schedule_id, cancelled: true });
    // What a run launches now has no one at it: unattended.
    const now = await r.stub.agentRun(binding, { agent: "main", input: "now" }) as
      { run_id: string };
    expect((await startOf(r.stub, now.run_id)).unattended).toBe(true);
    // Its file readers are held to its grant, as any caller's (no sync token's check).
    expect((await r.stub.fileList(binding, { dir: "/workspace", limit: 10 })).entries
      .map((entry) => entry.path)).toContain("/workspace/agents");
    expect((await r.stub.fileSearch(binding, { query: "claude" })).results.map((hit) => hit.id))
      .toEqual(["/workspace/agents/main/agent.json"]);
    // It still never converses: work between agents is mail.
    expect((await failure(() => r.stub.agentSubmit(binding, {
      message: "x", client_send_id: "c-1" }))).message).toBe(RUN_STARTS_NONE);
  });

  it("are refused to a run whose agent holds no run_graph", async () => {
    const r = await gated();
    const { binding } = await asRun(r, ALL_SCOPES.filter((scope) => scope !== "run_graph"));
    expect((await failure(() => r.stub.agentRun(binding, {
      agent: "main", input: "x", cron: "0 7 * * *" }))).message)
      .toBe("outside client capability not granted");
  });

  it("are refused to a client's run whose grant holds no run_graph, and made by one whose " +
     "grant does, as that client's", async () => {
    for (const held of [["converse"], ["converse", "run_graph"]]) {
      const r = await gated();
      await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
      const client = r.bind({ origin: MUSE, source: MCP, authenticated_at: signedIn() });
      await r.stub.outsideAgents(client);
      const { revision } = (await r.stub.grantList(r.bind())).clients
        .find((entry) => entry.client === MUSE.client)!;
      await r.stub.grantSet(r.session(), { client: MUSE.client, source: "mcp", mode: "scoped",
        scopes: held.map((capability) => ({ universe: "alpha", agent: "main", capability })),
        expected_revision: revision, name: "Muse" });
      const { card_id, digest } = await r.stub.registerPropose(client, {
        command_center_id: "alpha", display_name: "Muse", delivery_method: "pull",
        recipe_id: null });
      await r.stub.registerAgent(r.session(), { card_id, digest });
      const run = await r.stub.agentSubmit({ ...client, tiers: TIERS },
                                           { message: "hi", client_send_id: "s-1" });
      const binding = runBinding(r, await startOf(r.stub, run.run_id));
      const asked = () => r.stub.agentRun(binding, { agent: "main", input: "x",
                                                     cron: "0 7 * * *" });
      if (!held.includes("run_graph")) {
        expect((await failure(asked)).message).toBe("outside client capability not granted");
        continue;
      }
      const made = await asked() as { schedule_id: string };
      // The client's, not the owner's: its client reads it; another caller does not.
      expect(await r.stub.agentStatus(client, { schedule_id: made.schedule_id }))
        .toMatchObject({ schedule_id: made.schedule_id });
      expect(await r.sql("SELECT client FROM run_schedules")).toEqual([{ client: MUSE.client }]);
    }
  });
});

describe("a scheduled run's result", () => {
  it("is posted to the owner's room with its agent, once, and an empty one posts nothing",
     async () => {
    const r = await gated();
    await defineAgent(r.stub, r.bind(), "main", SIGNED_IN);
    for (const result of ["Your brief: nothing due today.", ""]) {
      await r.stub.agentRun(r.bind({ tiers: TIERS }), { agent: "main", input: "brief",
                                                        at: Date.now() / 1000 + 3600 });
      await r.sql("UPDATE run_schedules SET next_at = ? WHERE next_at IS NOT NULL",
                  Date.now() / 1000 - 1);
      await r.stub.fileMkdir(r.bind(), { path: `/workspace/d-${crypto.randomUUID()}`,
                                         expected_etag: null });
      await drainDue(r.stub);
      const start = (await recorder(r.stub)).starts.at(-1)!;
      await startOf(r.stub, start.run);
      const done = { frames: [{ seq: 1, at: 1, event: { type: "system", subtype: "init",
                                                        session_id: `s-${start.run}` } },
                              { seq: 2, at: 2, event: { type: "result", result } }],
                     end: { exit_code: 0 } };
      await r.stub.runEvents(runnerBinding(r, start), done);
    }
    // The owner's mailbox holds it, from main, in their room: the one post, the empty
    // result none.
    const { messages } = await r.stub.mailboxRead(r.bind(), { limit: 50 });
    expect(messages.map((message) => [message.room_id, message.body]))
      .toEqual([[`dm:${nativeId("main")}`, "Your brief: nothing due today."]]);
    expect((await r.stub.noticeList(r.bind())).notices).toEqual([]);
  });
});

describe("the MCP door's file readers", () => {
  async function seededWithKnowledge() {
    const r = await rig();
    const write = (path: string, value: string) => r.stub.fileWrite(r.bind(null), {
      path, expected_etag: null, content: text(value) });
    await r.stub.fileMkdir(r.bind(null), { path: "/workspace/knowledge/people",
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
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/u.md", expected_etag: null,
                                           content: text("aé€😀z") });
    const one = await r.stub.fileText(r.bind(null), { path: "/workspace/u.md", max_bytes: 4 });
    expect(one).toMatchObject({ text: "aé", next_offset: 3 });
    expect((await r.stub.fileText(r.bind(null), { path: "/workspace/u.md", offset: 3,
                                                   max_bytes: 1 })).text).toBe("€");
    expect((await failure(() => r.stub.fileText(r.bind(null), { path: "/workspace/u.md",
                                                                 offset: 2 }))).message)
      .toMatch(/inside a character/);
    await r.stub.fileWrite(r.bind(null), { path: "/workspace/b.bin", expected_etag: null,
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
      await r.stub.fileWrite(r.bind(null), { path, expected_etag: null,
                                             content: text("needle") });
    }
    const all = await r.stub.fileSearch(r.bind(null), { query: "needle" });
    expect([all.results.length, all.next_cursor]).toEqual([3, null]);
    // From a cursor, only what comes after it; the manifest's own pages, in path order.
    const after = await r.stub.fileSearch(r.bind(null), { query: "needle",
                                                          cursor: "/workspace/d/b.md" });
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
