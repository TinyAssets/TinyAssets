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
import {
  type Args, argsSchema, doorProblems, doorRow, doorTable, requestFields, toolTable,
} from "../src/doors";

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
      refused: "x", required: { path: { type: "string" } },
      optional: { path: { type: "string" } } } })],
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
      refused: "x", required: { path: { type: "string" } }, optional: {}, dropped: ["path"] } })],
    ["dropped fields not a list", changed("fileStat", { args: {
      refused: "x", required: { path: { type: "string" } }, optional: {}, dropped: "etag" } })],
    ["a field's schema past its type", changed("fileStat", { args: {
      refused: "x", required: { path: { type: "string", enum: ["/a"] } }, optional: {} } })],
    ["a field's type no field type", changed("fileStat", { args: {
      refused: "x", required: { path: { type: "text" } }, optional: {} } })],
    ["a field whose schema names no type", changed("fileStat", { args: {
      refused: "x", required: { path: {} }, optional: {} } })],
    ["a tool's field of bytes", changed("fileStat", { args: {
      refused: "x", required: { path: { type: ["string", "bytes"] } }, optional: {} } })],
    ["a tool's outside field of bytes", changed("fileWrite", { args: {
      owner: (row("fileWrite")["args"] as Record<string, unknown>)["owner"],
      outside: { refused: "x", required: { content: { type: "bytes" } }, optional: {} } } })],
    ["a dropped type its schema names", changed("fileStat", { args: {
      refused: "x", required: { path: { type: "string" } }, optional: {},
      droppedTypes: { path: ["string"] } } })],
    ["a dropped type of a field it lists not", changed("fileStat", { args: {
      refused: "x", required: { path: { type: "string" } }, optional: {},
      droppedTypes: { etag: ["integer"] } } })],
  ])("refuses %s", (_what, rows) => {
    expect(doorProblems(rows)).not.toEqual([]);
  });

  it("takes a dropped door, and a dropped field, which it takes and leaves out", () => {
    expect(doorProblems(changed("keyList", { dropped: true }))).toEqual([]);
    const args = { refused: "invalid file request", required: { path: { type: "string" } },
                   optional: {}, dropped: ["etag"] } as const;
    expect(doorProblems(changed("fileStat", { args }))).toEqual([]);
    expect(requestFields(args, { path: "/a", etag: "e1" })).toEqual({ path: "/a" });
    expect(requestFields(args, { path: "/a" })).toEqual({ path: "/a" });
    expect(() => requestFields(args, { path: "/a", other: 1 })).toThrow("invalid file request");
    // Its tool lists it no more.
    expect(argsSchema(args)).toEqual({ type: "object", properties: { path: { type: "string" } },
                                       required: ["path"], additionalProperties: false });
    // A dropped type, which the owner takes and its tool serves no more.
    const typed = { refused: "invalid file request", required: { path: { type: "string" } },
                    optional: {}, droppedTypes: { path: ["integer", "null"] } } as const;
    expect(doorProblems(changed("fileStat", { args: typed }))).toEqual([]);
    expect(argsSchema(typed)["properties"]).toEqual({ path: { type: "string" } });
  });

  it("takes each field only as a type its schema names, or its dropped types", () => {
    const args = {
      refused: "invalid file request",
      required: { path: { type: "string" }, at: { type: "number" } },
      optional: { etag: { type: ["string", "null"] }, body: { type: "bytes" },
                  more: { type: "object" }, list: { type: "array" } },
      droppedTypes: { path: ["integer"] },
    } as const;
    const taken = { path: "/a", at: 2, etag: null, body: new Uint8Array([1]), more: {}, list: [] };
    expect(requestFields(args, taken)).toEqual(taken);
    // A whole number is a number; a type it dropped is still taken.
    expect(requestFields(args, { path: 7, at: 2.5 })).toEqual({ path: 7, at: 2.5 });
    for (const [field, value] of [["path", true], ["path", 1.5], ["at", "2"], ["at", Infinity],
                                  ["etag", 1], ["body", "AQ=="], ["more", []],
                                  ["more", new Date(0)], ["list", {}]] as const) {
      expect(() => requestFields(args, { path: "/a", at: 1, [field]: value }), field)
        .toThrow(field === "path" ? "invalid file request: path is a string"
          : field === "etag" ? "etag is a string or null" : `invalid file request: ${field} is`);
    }
    // An undefined field, where its row takes one, is its module's to read.
    expect(requestFields({ ...args, undefinedAllowed: true }, { path: "/a", at: 1,
                                                                 etag: undefined }))
      .toEqual({ path: "/a", at: 1, etag: undefined });
  });

  it("serves a tool by who asks the outside fields, the owner's its own", () => {
    const write = doorRow("fileWrite");
    const args = write.args as { owner: Args; outside: Args };
    expect(argsSchema(write.args)).toEqual(argsSchema(args.outside));
    expect(() => requestFields(args.outside, { path: "/a", expected_etag: null, content: "" }))
      .toThrow("invalid file request: expected_etag is a string");
    expect(requestFields(args.owner, { path: "/a", expected_etag: null,
                                       content: new Uint8Array([1]) }))
      .toHaveProperty("expected_etag", null);
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
