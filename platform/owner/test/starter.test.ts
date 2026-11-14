/**
 * The starter package (`starter.ts`; the starter slice): every new owner seeded with the
 * default agent's files as the owner's own writes, one receipt a file and `agent.json` last;
 * a seed that fails part way leaves no agent and is tried again; a file the owner made before
 * its seed is kept; and updates by the conffile rule (an unedited file replaced, an edited one
 * given a `.ta-new` and one notice, a deleted one never restored, a widening a proposal).
 *
 * The seed is the object's own, of the package this code carries (`starter-support.ts`); an
 * update is a pass of a package of the test's own, at a version past the seed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { agentFile } from "../src/agent-file";
import { contentIdOf } from "../../contract/src/content";
import { STARTER_RETRY_S } from "../src/starter";
import { carried, NEWER, starterPackage } from "../src/starter-package";
import { later } from "./runs-support";
import { drainDue, laneAt, text } from "./support";
import {
  AGENTS, agentJson, failedOwner, file, nextOf, notices, pass, readText, receipts, type Rig,
  SEED, SEED_REFUSED, settled, settledOwner, SKILLS, versionOf,
} from "./starter-support";

afterEach(() => {
  vi.useRealTimers();
});

const V1 = starterPackage({ version: SEED + 1, files: [
  file(`${SKILLS}/a/SKILL.md`, "a1"), file(`${SKILLS}/b/SKILL.md`, "b1"),
  file(`${SKILLS}/c/SKILL.md`, "c1"), file(`${AGENTS}/SOUL.md`, "soul1", { class: "personal" }),
  file(`${AGENTS}/AGENTS.md`, "agents1"), file(`${AGENTS}/agent.json`, agentJson(["read_files"])),
] });
const V2 = starterPackage({ version: SEED + 2, files: [
  file(`${SKILLS}/a/SKILL.md`, "a2"), file(`${SKILLS}/b/SKILL.md`, "b2"),
  file(`${SKILLS}/c/SKILL.md`, "c2"), file(`${AGENTS}/SOUL.md`, "soul2", { class: "personal" }),
  file(`${SKILLS}/d/SKILL.md`, "d2"), file(`${SKILLS}/e/run.sh`, "echo hi"),
  file(`${SKILLS}/f/SKILL.md`, "f2", { widens: { version: SEED + 2, why: "it fetches the web" } }),
  file(`${AGENTS}/AGENTS.md`, "agents1"),
  file(`${AGENTS}/agent.json`, agentJson(["read_files", "run_graph"])),
] });
const V3 = starterPackage({ ...V2, version: SEED + 3, files: V2.files.map((each) =>
  each.path === `${SKILLS}/a/SKILL.md` ? { ...each, text: "a3" } : each) });

/** The receipts of `pkg`'s own paths, by path, each its state. */
async function statesOf(r: Rig, pkg: typeof V1): Promise<Record<string, string>> {
  const paths = new Set(pkg.files.map((each) => each.path));
  return Object.fromEntries(Object.entries(await receipts(r))
    .filter(([path]) => paths.has(path)).map(([path, row]) => [path, row.state]));
}

describe("a new owner", () => {
  it("is seeded with every starter file, as its own writes, a receipt each, agent.json last",
     async () => {
    const r = await settledOwner();
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
    // The files plane refuses the seed's second upload, and its first retry's.
    const r = await failedOwner(2);
    const told = await notices(r, "starter_failed");
    expect(told).toHaveLength(1);
    expect(told[0]).toContain(SEED_REFUSED);
    // The file before it landed; none of the agent's did.
    const [first] = carried().files;
    expect(await readText(r, first!.path)).toBe(first!.text);
    expect((await r.stub.fileStat(r.bind(null), { path: `${AGENTS}/agent.json` })).entry)
      .toBeNull();
    expect((await r.stub.outsideAgents(r.bind(null))).owner_agents).toEqual([]);
    expect(await versionOf(r)).toBe(0);
    // It waits its retry, then fails again without telling the owner twice.
    expect(await laneAt(r.stub, "starter")).toBeGreaterThan(Date.now() / 1000);
    later(STARTER_RETRY_S + 1);
    await drainDue(r.stub);
    expect(await notices(r, "starter_failed")).toHaveLength(1);
    expect(await versionOf(r)).toBe(0);
    // The files plane taking its uploads again, the next retry lands it whole, agent.json last.
    later(STARTER_RETRY_S + 1);
    await settled(r);
    const json = carried().files.at(-1)!;
    expect(await readText(r, json.path)).toBe(json.text);
    const versions = await Promise.all(carried().files.map(async (each) =>
      (await r.stub.fileStat(r.bind(null), { path: each.path })).entry!.version));
    expect(Math.max(...versions)).toBe(versions.at(-1));
    expect(await laneAt(r.stub, "starter")).toBeNull();
  });

  it("keeps a file the owner made before the seed landed, offering the starter's beside it",
     async () => {
    // The seed failed, and the owner writes the agent's instructions before its retry.
    const r = await failedOwner();
    await r.stub.fileMkdir(r.session(null), { path: AGENTS, expected_etag: null });
    await r.stub.fileWrite(r.session(null), { path: `${AGENTS}/AGENTS.md`, expected_etag: null,
                                              content: text("mine") });
    later(STARTER_RETRY_S + 1);
    await settled(r);
    const starters = carried().files.find((each) => each.path === `${AGENTS}/AGENTS.md`)!;
    expect(await readText(r, `${AGENTS}/AGENTS.md`)).toBe("mine");
    expect(await readText(r, `${AGENTS}/AGENTS.md${NEWER}`)).toBe(starters.text);
    expect((await receipts(r))[`${AGENTS}/AGENTS.md`]).toMatchObject({ state: "edited" });
  });
});

describe("a starter update", () => {
  it("replaces unedited files, offers edited ones beside them with one notice, never " +
     "restores a deleted one, never touches personal ones, and proposes a widening",
     async () => {
    const r = await settledOwner();
    await pass(r, V1);
    // The owner edits b and SOUL.md, and deletes c.
    const edit = async (path: string, value: string) => {
      const { entry } = await r.stub.fileStat(r.bind(null), { path });
      await r.stub.fileWrite(r.session(null), { path, expected_etag: entry!.etag,
                                                content: text(value) });
    };
    await edit(`${SKILLS}/b/SKILL.md`, "b-mine");
    await edit(`${AGENTS}/SOUL.md`, "soul-mine");
    const { entry: c } = await r.stub.fileStat(r.bind(null), { path: `${SKILLS}/c/SKILL.md` });
    await r.stub.fileRemove(r.session(null), { path: `${SKILLS}/c/SKILL.md`,
                                               expected_etag: c!.etag, recursive: false });
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
    expect(await statesOf(r, V2))
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
    const r = await settledOwner();
    await pass(r, V1);
    await pass(r, V2);
    // Undo: the owner restores the version before the update.
    const path = `${SKILLS}/a/SKILL.md`;
    const { changes } = await r.stub.fileHistory(r.bind(null), { path, limit: 5 });
    const { entry } = await r.stub.fileStat(r.bind(null), { path });
    await r.stub.fileRestore(r.session(null), { path, version: changes[1]!.version,
                                                expected_etag: entry!.etag });
    expect(await readText(r, path)).toBe("a1");
    // Adopt: the owner moves the proposal over its file.
    const json = `${AGENTS}/agent.json`;
    const { entry: offered } = await r.stub.fileStat(r.bind(null), { path: `${json}${NEWER}` });
    await r.stub.fileRename(r.session(null), { from: `${json}${NEWER}`, to: json,
                                               expected_etag: offered!.etag, replace: true });
    await pass(r, V3);
    // The restored file is the owner's choice now: never replaced, the update offered.
    expect(await readText(r, path)).toBe("a1");
    expect(await readText(r, `${path}${NEWER}`)).toBe("a3");
    const kept = await receipts(r);
    expect(kept[path]).toMatchObject({ state: "edited" });
    expect(kept[json]).toMatchObject({ state: "installed", version: SEED + 3,
                                       installed: contentIdOf(text(agentJson(["read_files",
                                                                              "run_graph"]))) });
  });
});
