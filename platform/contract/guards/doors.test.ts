/**
 * The owner object's door table (`contract/doors.json`, read by `src/doors.ts`): every row
 * whole and of known parts, so the doors' tool table and the object's runner read it alone.
 * A row with no argument schema, an unknown class or capability, a runner's step (the
 * owner's, `owner/doors.json`, held by `owner/guards/doors.test.ts`), a tool missing a hint,
 * a tool named twice, a door named twice, one named as RPC reserves, or a stop (`stop`) not
 * true or on a door that takes no binding or no request is refused when the object's class
 * loads, and here.
 *
 * And the door classes the spec names (`owner-object/spec.md`, admission): a billing
 * suspension refuses only `work`, an abuse suspension every door.
 */
import { describe, expect, it } from "vitest";

import raw from "../doors.json" with { type: "json" };
import { argsSchema, doorProblems, doorRow, doorTable, requestFields, toolTable }
  from "../src/doors";

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
    ["a door a name RPC reserves", changed("fileStat", { door: "alarm" })],
    ["a door class past the five", changed("fileStat", { class: "write" })],
    ["a door of no class that binds", changed("keyList", { class: null })],
    ["a class on a door that takes no binding", changed("sandboxRung", { class: "work" })],
    ["a tool missing a hint", changed("fileStat", { tool: { name: "file_stat", title: "x",
      description: "x", readOnlyHint: true, destructiveHint: false, idempotentHint: true } })],
    ["a tool on a loose door", changed("egressReserve", { tool: { name: "egress",
      title: "x", description: "x", readOnlyHint: false, destructiveHint: true,
      idempotentHint: false, openWorldHint: false } })],
    ["a tool named twice", changed("fileUsage", { tool: { ...(row("fileStat")["tool"] as object) } })],
    ["a door named twice", [...raw.doors, row("fileStat")]],
    ["a key of no part", changed("fileStat", { owner: "alice" })],
    ["a runner's step, the owner's", changed("fileStat", { gate: "door" })],
    ["a stop that is not true", changed("codeRun", { stop: false })],
    ["a stop on a door that takes no binding", changed("sandboxRung", { stop: true })],
    ["a stop on a door that takes no request", changed("keyList", { stop: true })],
    ["a dropped door with its tool", changed("fileStat", { dropped: true })],
    ["a dropped door marked other than true", changed("keyList", { dropped: false })],
    ["a dropped field it still lists", changed("fileStat", { args: {
      refused: "x", required: { path: {} }, optional: {}, dropped: ["path"] } })],
    ["dropped fields not a list", changed("fileStat", { args: {
      refused: "x", required: { path: {} }, optional: {}, dropped: "etag" } })],
  ])("refuses %s", (_what, rows) => {
    expect(doorProblems(rows)).not.toEqual([]);
  });

  it("takes a dropped door, and a dropped field, which it takes and leaves out", () => {
    expect(doorProblems(changed("keyList", { dropped: true }))).toEqual([]);
    const args = { refused: "invalid file request", required: { path: {} }, optional: {},
                   dropped: ["etag"] };
    expect(doorProblems(changed("fileStat", { args }))).toEqual([]);
    expect(requestFields(args, { path: "/a", etag: "e1" })).toEqual({ path: "/a" });
    expect(requestFields(args, { path: "/a" })).toEqual({ path: "/a" });
    expect(() => requestFields(args, { path: "/a", other: 1 })).toThrow("invalid file request");
    // Its tool lists it no more.
    expect(argsSchema(args)).toEqual({ type: "object", properties: { path: {} },
                                       required: ["path"], additionalProperties: false });
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
