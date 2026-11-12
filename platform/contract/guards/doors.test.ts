/**
 * The owner object's door table (`contract/doors.json`, read by `src/doors.ts`): every row
 * whole and of known parts, so the object's runner and the doors' tool table read it alone.
 * A row with no argument schema, an unknown caller or gate, a tool missing a hint, a tool
 * named twice or a door named twice is refused when the table loads, and here.
 *
 * And the door classes the spec names (`owner-object/spec.md`, admission): a billing
 * suspension refuses only `work`, an abuse suspension every door, and the platform's own
 * doors alone read no admission.
 */
import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import raw from "../doors.json" with { type: "json" };
import { argsSchema, doorProblems, doorRow, doorTable, toolTable } from "../src/doors";

const row = (name: string): Record<string, unknown> =>
  ({ ...(raw.doors.find((each) => each.door === name) as Record<string, unknown>) });
const changed = (name: string, change: Record<string, unknown>) =>
  raw.doors.map((each) => each.door === name ? { ...row(name), ...change } : each);
const without = (name: string, key: string) =>
  raw.doors.map((each) => {
    if (each.door !== name) {
      return each;
    }
    const { [key]: _gone, ...rest } = row(name);
    return rest;
  });

describe("the door table", () => {
  it("holds every row whole", () => {
    expect(doorProblems(raw.doors)).toEqual([]);
    expect(doorTable().length).toBe(raw.doors.length);
    expect(doorTable().length).toBeGreaterThan(100);
    // Frozen whole: no module can change a row for the isolate.
    const fileStat = doorRow("fileStat");
    expect([Object.isFrozen(doorTable()), Object.isFrozen(fileStat),
            Object.isFrozen(fileStat.tool), Object.isFrozen(fileStat.args)])
      .toEqual([true, true, true, true]);
  });

  it.each([
    ["a row without an argument schema", without("fileStat", "args")],
    ["a field that is not a schema", changed("fileStat", { args: {
      refused: "invalid file request", required: { path: "a path" }, optional: {} } })],
    ["arguments with no refusal", changed("fileStat", { args: { required: {}, optional: {} } })],
    ["a field both required and optional", changed("fileStat", { args: {
      refused: "x", required: { path: {} }, optional: { path: {} } } })],
    ["no capability", without("fileStat", "capability")],
    ["an unknown capability", changed("fileStat", { capability: "read_everything" })],
    ["an unknown caller", changed("fileStat", { caller: "anyone" })],
    ["an unknown gate", changed("fileStat", { gate: "later" })],
    ["an unknown guard", changed("fileStat", { guard: "maybe" })],
    ["no handler module", without("fileStat", "handler")],
    ["a door class past the five", changed("fileStat", { class: "write" })],
    ["admission read by an account door", changed("fileStat", { class: "admission" })],
    ["a platform door that reads admission", changed("tierSet", { class: "work" })],
    ["an owner's door that names no act", without("policyRead", "action")],
    ["a session's door with no receipt rule", without("policyWrite", "rule")],
    ["a tool missing a hint", changed("fileStat", { tool: { name: "file_stat", title: "x",
      description: "x", readOnlyHint: true, destructiveHint: false, idempotentHint: true } })],
    ["a tool on a loose door", changed("egressReserve", { tool: { name: "egress",
      title: "x", description: "x", readOnlyHint: false, destructiveHint: true,
      idempotentHint: false, openWorldHint: false } })],
    ["a tool named twice", changed("fileUsage", { tool: { ...(row("fileStat")["tool"] as object) } })],
    ["a door named twice", [...raw.doors, row("fileStat")]],
    ["a key of no step", changed("fileStat", { owner: "alice" })],
    ["a door that binds nothing", changed("keyList", { caller: "none", class: null })],
    ["an account door of the import class", changed("fileStat", { class: "import" })],
    ["an import door of another class", changed("importRows", { class: "work" })],
    ["an import door on the reserved line", changed("importRows", { caller: "reserved" })],
    ["an import door that binds nothing", changed("importFiles", { caller: "none", class: null })],
    ["the discard's caller at an account door", changed("fileStat", { caller: "discard" })],
    ["the discard of another class", changed("importDiscard", { class: "work" })],
    ["an unbound door that binds", changed("sandboxRung", { caller: "account", class: "work" })],
  ])("refuses %s", (_what, rows) => {
    expect(doorProblems(rows)).not.toEqual([]);
  });

  it("names each door's handler module, one of the owner's", () => {
    for (const each of doorTable()) {
      expect(existsSync(join(PLATFORM, "owner", "src", `${each.handler}.ts`)), each.door)
        .toBe(true);
    }
  });

  it("serves each tool its door's capability and arguments", () => {
    for (const tool of toolTable()) {
      const door = doorRow(tool.door);
      expect(tool.capability).toBe(door.capability);
      expect(tool.input).toEqual(argsSchema(door.args));
      expect(tool.name).toBe(door.tool?.name);
    }
  });

  it("classes each door as admission reads it", () => {
    const classOf = (name: string) => doorRow(name).class;
    for (const door of ["admissionSet", "tierSet", "dataKeyRewrap", "gatewayReseal",
                        "erasureOutside", "erasureOutsideDone"]) {
      expect(classOf(door), door).toBe("admission");
    }
    expect(doorTable().filter((each) => each.class === "admission").map((each) => each.door).sort())
      .toEqual(["admissionSet", "dataKeyRewrap", "erasureOutside", "erasureOutsideDone",
                "gatewayReseal", "tierSet"]);
    // The work a billing suspension refuses, and some of what it leaves.
    for (const door of ["fileWrite", "fileUploadBegin", "collectionWrite", "policyWrite",
                        "roomPost", "agentSubmit", "agentRun"]) {
      expect(classOf(door), door).toBe("work");
    }
    for (const door of ["fileRead", "policyRead", "sessionOpen", "mailboxRead", "agentStatus",
                        "socketTicket"]) {
      expect(classOf(door), door).toBe("read");
    }
    // A cancel, a revoke and the runner's report reduce: a settle lowers what runs.
    for (const door of ["grantRevoke", "fileRemove", "deleteAgent", "agentCancel", "runEvents",
                        "socketsEnd"]) {
      expect(classOf(door), door).toBe("reduce");
    }
  });
});
