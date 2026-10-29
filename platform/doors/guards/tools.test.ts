/**
 * The external tool table (research §4.3, checklist 18, 21): one tool per
 * outside-callable owner door, every hint explicit and right, and never the model's own.
 *
 * The outside-callable doors are read from `owner-do.ts` itself: a public method that
 * binds through `#account`, `#collector` or `#filer`, and never through `#owner` or
 * `#widening`, is reachable by an outside client. Its capability is the one string it
 * hands `#scope`, `#collector` or `#filer`. Each tool must name such a door, with that
 * capability, and arguments exactly the door's request fields; each such door must have
 * a tool, or be named below with the slice that owes it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM } from "../../owner/guards/sources";
import { type Tool, toolTable } from "../src/tools";

/** Outside-callable doors without a tool yet, and what owes each. */
const DEFERRED: Readonly<Record<string, string>> = Object.freeze({
  fileRead: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileReadChunks: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileWrite: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileUploadBegin: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileUploadChunks: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileUploadCommit: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileUploadAbort: "tasks 2, owed by 6.1: file bytes streamed by the binder",
  fileChanges: "tasks 6: the sandbox's sync feed, reached by its lease token",
  mailboxSubscriber: "tasks 7: the MCP Events subscribe door",
});
/** The model's own tools: pi's, inside the object, never served outside. */
const MODEL_TOOLS: readonly string[] = Object.freeze(["read", "write", "edit", "bash", "ta"]);
/** Fields that would make a tool an operation-selecting executor. */
const DISPATCH: readonly string[] = Object.freeze(["method", "operation", "tool", "target",
                                                   "action", "command"]);
/** Writes that destroy or send nothing: an ack, a filed card, a proposal, a new directory. */
const ADDITIVE: readonly string[] = Object.freeze(["mailboxAck", "registerPropose",
                                                   "policyPropose", "fileMkdir"]);
/** Writes that repeat to the same end: an ack through a sequence, a directory made once. */
const IDEMPOTENT_WRITES: readonly string[] = Object.freeze(["mailboxAck", "fileMkdir"]);
/** The one outside-callable read with no scope: an identity's own agents. */
const UNSCOPED_READS: readonly string[] = Object.freeze(["outsideAgents"]);

interface DoorShape {
  readonly capability: string | null;
  /** The request's fields, by name, with whether each is optional; null when it takes none. */
  readonly fields: ReadonlyMap<string, boolean> | null;
}

const OWNER_DO = join(PLATFORM, "owner", "src", "owner-do.ts");

function interfaces(file: ts.SourceFile): Map<string, ts.InterfaceDeclaration> {
  const found = new Map<string, ts.InterfaceDeclaration>();
  file.forEachChild((node) => {
    if (ts.isInterfaceDeclaration(node)) {
      found.set(node.name.text, node);
    }
  });
  return found;
}

function fieldsOf(name: string, all: Map<string, ts.InterfaceDeclaration>):
    Map<string, boolean> {
  const declaration = all.get(name);
  if (declaration === undefined) {
    throw new Error(`no interface ${name} in owner-do.ts`);
  }
  const fields = new Map<string, boolean>();
  for (const clause of declaration.heritageClauses ?? []) {
    for (const base of clause.types) {
      for (const [field, optional] of fieldsOf(base.expression.getText(), all)) {
        fields.set(field, optional);
      }
    }
  }
  for (const member of declaration.members) {
    if (ts.isPropertySignature(member) && ts.isIdentifier(member.name)) {
      fields.set(member.name.text, member.questionToken !== undefined);
    }
  }
  return fields;
}

/** Every outside-callable door of `OwnerDO`, read from its source. */
export function outsideDoors(source: string): Map<string, DoorShape> {
  const file = parse("owner-do.ts", source);
  const all = interfaces(file);
  const doors = new Map<string, DoorShape>();
  const owner = file.statements.find((node): node is ts.ClassDeclaration =>
    ts.isClassDeclaration(node) && node.name?.text === "OwnerDO");
  for (const member of owner?.members ?? []) {
    if (!ts.isMethodDeclaration(member) || !ts.isIdentifier(member.name)
        || member.body === undefined) {
      continue;
    }
    const helpers = new Set<string>();
    let capability: string | null = null;
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
          && ts.isPrivateIdentifier(node.expression.name)) {
        const helper = node.expression.name.text;
        helpers.add(helper);
        if (["#scope", "#collector", "#filer"].includes(helper)) {
          const literal = node.arguments.find(ts.isStringLiteralLike);
          capability = literal?.text ?? capability;
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(member.body);
    const outside = ["#account", "#collector", "#filer"].some((h) => helpers.has(h))
      && !helpers.has("#owner") && !helpers.has("#widening");
    if (!outside) {
      continue;
    }
    const request = member.parameters[1]?.type;
    const named = request !== undefined && ts.isTypeReferenceNode(request)
      ? request.typeName.getText(file) : null;
    doors.set(member.name.text, { capability, fields: named === null ? null
                                                                      : fieldsOf(named, all) });
  }
  return doors;
}

/** Each way the table breaks the rules, as `tool: what`. */
export function toolProblems(tools: readonly Tool[], doors: ReadonlyMap<string, DoorShape>):
    string[] {
  const problems: string[] = [];
  const names = new Set<string>();
  const used = new Set<string>();
  for (const tool of tools) {
    const say = (what: string) => problems.push(`${tool.name}: ${what}`);
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(tool.name)) {
      say("a name of at most 64 lowercase characters");
    }
    if (names.has(tool.name)) {
      say("named twice");
    }
    names.add(tool.name);
    if (MODEL_TOOLS.includes(tool.name)) {
      say("names one of the model's own tools");
    }
    if (typeof tool.title !== "string" || tool.title.trim() === "") {
      say("no title");
    }
    if (typeof tool.description !== "string" || tool.description.trim() === "") {
      say("no description");
    }
    for (const hint of ["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"] as const) {
      if (typeof tool[hint] !== "boolean") {
        say(`${hint} is not an explicit boolean`);
      }
    }
    if (tool.openWorldHint !== false) {
      say("open world, but every tool is confined to the owner's account");
    }
    const door = doors.get(tool.door);
    if (door === undefined) {
      say(`${tool.door} is not an outside-callable OwnerDO door`);
      continue;
    }
    if (used.has(tool.door)) {
      say(`${tool.door} has two tools`);
    }
    used.add(tool.door);
    if (tool.capability !== door.capability) {
      say(`capability ${tool.capability}, but ${tool.door} checks ${door.capability}`);
    }
    const reads = door.capability === null ? UNSCOPED_READS.includes(tool.door)
                                           : door.capability.startsWith("read_");
    if (tool.readOnlyHint !== reads) {
      say(`readOnlyHint must be ${reads} for ${tool.door}`);
    }
    if (reads && (tool.destructiveHint || !tool.idempotentHint)) {
      say("a read is neither destructive nor other than idempotent");
    }
    if (!reads && tool.destructiveHint !== !ADDITIVE.includes(tool.door)) {
      say(`destructiveHint must be ${!ADDITIVE.includes(tool.door)} for ${tool.door}`);
    }
    if (!reads && tool.idempotentHint !== IDEMPOTENT_WRITES.includes(tool.door)) {
      say(`idempotentHint must be ${IDEMPOTENT_WRITES.includes(tool.door)} for ${tool.door}`);
    }
    const input = tool.input;
    const properties = (input["properties"] ?? {}) as Record<string, unknown>;
    const required = (input["required"] ?? []) as string[];
    if (input["type"] !== "object" || input["additionalProperties"] !== false) {
      say("arguments are not a closed object");
    }
    for (const field of DISPATCH) {
      if (field in properties) {
        say(`a ${field} field selects an operation`);
      }
    }
    const expected = door.fields ?? new Map<string, boolean>();
    const given = new Map(Object.keys(properties).map((key) => [key, !required.includes(key)]));
    if (JSON.stringify([...given].sort()) !== JSON.stringify([...expected].sort())) {
      say(`arguments ${JSON.stringify([...given])} are not ${tool.door}'s ` +
          `${JSON.stringify([...expected])}`);
    }
  }
  for (const door of doors.keys()) {
    if (!used.has(door) && !Object.hasOwn(DEFERRED, door)) {
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

const DOORS = outsideDoors(readFileSync(OWNER_DO, "utf8"));
const base = toolTable()[0]!;

describe("tool table guard", () => {
  it("reads the outside-callable doors, and no owner-only one", () => {
    expect(DOORS.get("mailboxRead")?.capability).toBe("read_graph");
    expect(DOORS.get("collectionWrite")?.capability).toBe("write_graph");
    expect(DOORS.get("fileStat")?.capability).toBe("read_files");
    expect(DOORS.get("outsideAgents")?.capability).toBeNull();
    for (const ownerOnly of ["policyWrite", "grantSet", "grantRevoke", "grantForget",
                             "sessionOpen", "registerAgent", "fileRedact", "fileHistoryPurge",
                             "fileLeaseAcquire", "fileSyncCommit", "policyRead",
                             "connectionPrepare", "connectionCommit", "connectionRevoke",
                             "connectionList", "flowBegin", "flowFinish"]) {
      expect(DOORS.has(ownerOnly)).toBe(false);
    }
  });

  it("holds every tool to its door, its hints and its arguments", () => {
    expect(toolProblems(toolTable(), DOORS)).toEqual([]);
  });

  it.each([
    ["the model's bash", { name: "bash" }],
    ["the model's ta", { name: "ta" }],
    ["a long name", { name: "x".repeat(65) }],
    ["no title", { title: "" }],
    ["an omitted hint", { destructiveHint: undefined as unknown as boolean }],
    ["an open world", { openWorldHint: true }],
    ["a read that writes", { readOnlyHint: false }],
    ["a read that destroys", { destructiveHint: true }],
    ["an owner-only door", { door: "grantSet" as Tool["door"] }],
    ["a door that is not one", { door: "fetch" as Tool["door"] }],
    ["a wrong capability", { capability: "write_graph" as const }],
    ["a dispatch field", { input: { type: "object", additionalProperties: false,
      properties: { limit: {}, operation: {} }, required: ["limit"] } }],
    ["an open object", { input: { type: "object", properties: { limit: {} },
                                  required: ["limit"] } }],
    ["a field the door does not take", { input: { type: "object",
      additionalProperties: false, properties: { limit: {}, owner: {} }, required: ["limit"] } }],
    ["a required field made optional", { input: { type: "object",
      additionalProperties: false, properties: { limit: {} }, required: [] } }],
  ])("sees %s", (_what, change) => {
    expect(toolProblems([{ ...base, ...change }, ...toolTable().slice(1)], DOORS))
      .not.toEqual([]);
  });

  it("sees an ack marked other than idempotent, and a post marked idempotent", () => {
    const table = toolTable();
    for (const [name, idempotentHint] of [["mailbox_ack", false], ["room_post", true]] as const) {
      expect(toolProblems(table.map((tool) => tool.name === name
        ? { ...tool, idempotentHint } : tool), DOORS)).not.toEqual([]);
    }
  });

  it("sees a write marked non-destructive, and a door given two tools", () => {
    const table = toolTable();
    const post = table.find((tool) => tool.name === "room_post")!;
    expect(toolProblems(table.map((tool) => tool === post
      ? { ...tool, destructiveHint: false } : tool), DOORS)).not.toEqual([]);
    expect(toolProblems([...table, { ...post, name: "room_post_again" }], DOORS))
      .not.toEqual([]);
  });

  it("sees an outside-callable door with no tool and none owed", () => {
    expect(toolProblems(toolTable().filter((tool) => tool.name !== "file_usage"), DOORS))
      .toEqual(["fileUsage: an outside-callable door with no tool, and none owed"]);
  });

  it("reads a new outside door from the object's source", () => {
    const source = "class OwnerDO { peek(binding: Binding) { const a = this.#account(binding); " +
                   "this.#scope(a, 'read_graph'); } " +
                   "mine(binding: Binding) { this.#owner(binding, 'x'); } }";
    expect([...outsideDoors(source)]).toEqual([["peek", { capability: "read_graph",
                                                          fields: null }]]);
  });
});
