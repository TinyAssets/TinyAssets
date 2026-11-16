/**
 * The owner object's runner's steps (`owner/doors.json`, read by `src/door-steps.ts`): every
 * door of the contract's table (`contract/doors.json`) stepped once and nothing else, each
 * step of known parts and held to its door's row, so the runner reads the two alone. A door
 * with no steps, the steps of no door, a door stepped twice, an unknown caller, guard, scope,
 * handler or gate, or a caller its door's class or binding does not allow is refused when the
 * object's class loads (`ownerRows`), when `npm run generate` writes the typed handler map,
 * and here.
 *
 * And the outside-callable doors, which a tool must be (research §4.3; each tool's hints are
 * the contract's guard's, `contract/guards/tools.test.ts`): the doors an outside client's
 * call reaches (an `account`, `visitors`, `reader` or `changes` caller); each must have a
 * tool, or be named below with the slice that owes it.
 */
import { describe, expect, it } from "vitest";

import { doorTable, type Tool, toolTable } from "../../contract/src/doors";
import raw from "../doors.json" with { type: "json" };
import { type OwnerRow, ownerRow, ownerRows, stepProblems } from "../src/door-steps";

const step = (name: string): Record<string, unknown> =>
  ({ ...(raw.steps.find((each) => each.door === name) as Record<string, unknown>) });
const changed = (name: string, change: Record<string, unknown>) =>
  raw.steps.map((each) => each.door === name ? { ...step(name), ...change } : each);
const without = (name: string, key: string) =>
  raw.steps.map((each) => {
    if (each.door !== name) {
      return each;
    }
    const { [key]: _gone, ...rest } = step(name);
    return rest;
  });

describe("the door table's steps", () => {
  it("steps every door of the contract's table once, each whole", () => {
    expect(stepProblems(raw.steps, doorTable())).toEqual([]);
    expect(ownerRows().map((row) => row.door)).toEqual(doorTable().map((row) => row.door));
    // A door as the runner takes it: its contract row and its steps, frozen.
    const fileStat = ownerRow("fileStat");
    expect([fileStat.class, fileStat.capability, fileStat.caller, fileStat.handler, fileStat.gate])
      .toEqual(["read", "read_files", "account", "files", "door"]);
    expect([Object.isFrozen(ownerRows()), Object.isFrozen(fileStat)]).toEqual([true, true]);
    expect(() => ownerRow("fileProbe")).toThrow("no door named fileProbe");
  });

  it.each([
    ["an unknown caller", changed("fileStat", { caller: "anyone" })],
    ["an unknown gate", changed("fileStat", { gate: "later" })],
    ["an unknown guard", changed("fileStat", { guard: "maybe" })],
    ["an unknown scope", changed("fileStat", { scope: "anywhere" })],
    ["a seed awaited at a door run in one unit", changed("fileStat", { seeded: true })],
    ["no handler module", without("fileStat", "handler")],
    ["a handler that is no module's name", changed("fileStat", { handler: "../files" })],
    ["a key of no step", changed("fileStat", { owner: "alice" })],
    ["what every caller sees, the contract's", changed("fileStat", { capability: "read_files" })],
    ["an owner's door that names no act", without("policyRead", "action")],
    ["a session's door with no receipt rule", without("policyWrite", "rule")],
    ["admission read by an account door", changed("tierSet", { caller: "account" })],
    ["a platform door that reads admission", changed("fileStat", { caller: "reserved" })],
    ["a door that binds nothing", changed("keyList", { caller: "none" })],
    ["an unbound door that binds", changed("sandboxRung", { caller: "account" })],
    ["the import's caller at an account door", changed("fileStat", { caller: "import" })],
    ["an import door on the reserved line", changed("importRows", { caller: "reserved" })],
    ["an import door that binds nothing", changed("importFiles", { caller: "none" })],
    ["the discard's caller at an account door", changed("fileStat", { caller: "discard" })],
  ])("refuses %s", (_what, steps) => {
    expect(stepProblems(steps, doorTable())).not.toEqual([]);
  });

  it.each([
    ["the steps of no door", [...raw.steps, { ...step("fileStat"), door: "fileProbe" }],
     "fileProbe: the steps of no door"],
    ["a door with no steps", raw.steps.filter((each) => each.door !== "fileStat"),
     "fileStat: a door with no steps"],
    ["a door stepped twice", [...raw.steps, step("fileStat")], "fileStat: stepped twice"],
  ])("refuses %s: a row of one half with no match in the other", (_what, steps, problem) => {
    expect(stepProblems(steps, doorTable())).toEqual([problem]);
  });

  it("holds a door's steps to its contract row's class", () => {
    const classed = (name: string, change: OwnerRow["class"]) =>
      doorTable().map((row) => row.door === name ? { ...row, class: change } : row);
    expect(stepProblems(raw.steps, classed("fileStat", "admission")))
      .toEqual(["fileStat: only the platform's doors read no admission"]);
    expect(stepProblems(raw.steps, classed("tierSet", "work")))
      .toEqual(["tierSet: only the platform's doors read no admission"]);
    expect(stepProblems(raw.steps, classed("fileStat", "import")))
      .toEqual(["fileStat: only the import's doors take the import class, and they take no other"]);
  });
});

/** Outside-callable doors without a tool yet, and what owes each. */
const DEFERRED: Readonly<Record<string, string>> = Object.freeze({
  fileRead: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileReadChunks: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileUploadBegin: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileUploadChunks: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileUploadCommit: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileUploadAbort: "an outside client's file bytes: the binder streams the owner's (slice L)",
  fileChanges: "tasks 6: the sandbox's sync feed, reached by its sync token",
  runList: "slice SIGNIN, owed: an MCP tool for the past runs (the owner app's runs route " +
    "serves them)",
});
/** Outside-callable doors the doors call themselves, never a tool, and why. */
const DOORS_ONLY: Readonly<Record<string, string>> = Object.freeze({
  toolProfile: "the MCP and API doors' read of which tools the caller is served",
  fileUploadTarget: "the file door's bytes: the binder's read of an upload's plan (slice L)",
  fileUploadStored: "the file door's bytes: the binder's report of the chunks it stored",
  wakeEvents: "the MCP door's events/list (MCP Events, slice W), never a tool",
  wakeSubscribe: "the MCP door's events/subscribe, after its signed callback challenge",
  wakeUnsubscribe: "the MCP door's events/unsubscribe",
});

/** The callers an outside client's call reaches a door as. */
const OUTSIDE_CALLERS: readonly string[] = Object.freeze(["account", "visitors", "reader",
                                                          "changes"]);

/** Every outside-callable door of `rows`, with the capability its row holds it to. */
function outsideDoors(rows: readonly Pick<OwnerRow, "door" | "caller" | "capability">[]):
    Map<string, string | null> {
  return new Map(rows.filter((row) => OUTSIDE_CALLERS.includes(row.caller))
    .map((row) => [row.door, row.capability]));
}

/**
 * Each way `tools` and the outside-callable `doors` break the rules: a tool on a door no
 * outside client reaches, or an outside-callable door with no tool and none owed.
 */
function outsideProblems(tools: readonly Tool[], doors: ReadonlyMap<string, string | null>):
    string[] {
  const used = new Set(tools.map((tool) => tool.door));
  const problems = tools.filter((tool) => !doors.has(tool.door))
    .map((tool) => `${tool.name}: ${tool.door} is not an outside-callable OwnerDO door`);
  for (const door of doors.keys()) {
    if (!used.has(door) && !Object.hasOwn(DEFERRED, door) && !Object.hasOwn(DOORS_ONLY, door)) {
      problems.push(`${door}: an outside-callable door with no tool, and none owed`);
    }
  }
  for (const door of Object.keys(DEFERRED)) {
    if (!doors.has(door)) {
      problems.push(`${door}: deferred, but not an outside-callable door`);
    } else if (used.has(door)) {
      problems.push(`${door}: deferred, but it has a tool`);
    }
  }
  return problems;
}

const OUTSIDE = outsideDoors(ownerRows());

describe("the outside-callable doors", () => {
  it("reads the outside-callable doors, and no owner-only one", () => {
    expect(OUTSIDE.get("mailboxRead")).toBe("read_graph");
    expect(OUTSIDE.get("collectionWrite")).toBe("write_graph");
    expect(OUTSIDE.get("fileStat")).toBe("read_files");
    expect(OUTSIDE.get("outsideAgents")).toBeNull();
    // The run doors: a start held to its capability, a read or a cancel to the caller's own.
    expect(OUTSIDE.get("agentSubmit")).toBe("converse");
    expect(OUTSIDE.get("agentRun")).toBe("run_graph");
    expect(OUTSIDE.get("agentStatus")).toBeNull();
    expect(OUTSIDE.get("agentCancel")).toBeNull();
    for (const runner of ["runEvents", "socketTicket", "socketsEnd", "tierSet"]) {
      expect(OUTSIDE.has(runner), runner).toBe(false);
    }
    for (const ownerOnly of ["policyWrite", "grantSet", "grantRevoke", "grantForget",
                             "sessionOpen", "registerAgent", "fileRedact", "fileHistoryPurge",
                             "sandboxLeaseOpen", "fileSyncCommit", "policyRead",
                             "sandboxConfig", "sandboxNetworkApplied", "egressOpen",
                             "egressReserve", "egressSettle", "egressReceipts",
                             "connectionPrepare", "connectionCommit", "connectionRevoke",
                             "connectionList", "flowFinish"]) {
      expect(OUTSIDE.has(ownerOnly)).toBe(false);
    }
    // The doors' own read of which tools a caller is served: outside-callable, never a tool.
    expect(OUTSIDE.has("toolProfile")).toBe(true);
  });

  it("holds every tool to an outside-callable door, and every such door to a tool or a debt",
     () => {
    expect(outsideProblems(toolTable(), OUTSIDE)).toEqual([]);
  });

  it("sees a tool on an owner-only door", () => {
    const [base, ...rest] = toolTable();
    expect(outsideProblems([{ ...base!, door: "grantSet" }, ...rest], OUTSIDE))
      .toContain("inbox_digest: grantSet is not an outside-callable OwnerDO door");
  });

  it("sees an outside-callable door with no tool and none owed", () => {
    expect(outsideProblems(toolTable().filter((tool) => tool.name !== "file_usage"), OUTSIDE))
      .toEqual(["fileUsage: an outside-callable door with no tool, and none owed"]);
  });

  it("reads a new outside door from the table, and no owner-only one", () => {
    const row = (door: string, caller: OwnerRow["caller"]) =>
      ({ door, caller, capability: "read_graph" as const });
    expect([...outsideDoors([row("peek", "account"), row("mine", "owner")])])
      .toEqual([["peek", "read_graph"]]);
  });
});
