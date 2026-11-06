/**
 * The external tool table (research §4.3, checklist 18, 21): one tool per
 * outside-callable owner door, every hint explicit and right, and never the model's own.
 *
 * The outside-callable doors are read from `owner-do.ts` itself: a public method that
 * binds through `#account`, `#collector` or `#filer`, and never through `#owner` or
 * `#widening`, is reachable by an outside client. Its capability is the last string it
 * hands `#scope`, `#collector` or `#filer` (a door class comes before it). Each tool must
 * name such a door, with that capability, and arguments exactly the door's request
 * fields; each such door must have a tool, or be named below with the slice that owes it.
 * Every tool is confined to the owner's own account, so none is open world. The run doors
 * (slice R) hand their capability to `#runDoor`, read as `#scope`'s is.
 *
 * And the profiles (`profiles.json`, slice M): each lists exactly its tools, table tools in
 * the table's order; `code`, a generic executor, never in the directory's.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { parse } from "../../ci/kit/sources";
import profiles from "../profiles.json" with { type: "json" };
import { CLIENT_INSTRUCTIONS, CLIENT_KEY, RUN_INSTRUCTIONS } from "../src/instructions";
import { CLIENT_PROFILES, DEFAULT_PROFILE, profileNames, type Tool, toolNames, toolTable }
  from "../src/tools";

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
/**
 * The mailbox tools, which a run never holds (slice ID): its mail is its input, and its
 * agent's mailbox holds every authority's mail; `inbox_digest`'s mail part is a mailbox read.
 */
const MAILBOX_TOOLS: readonly string[] = Object.freeze(["mailbox_read", "mailbox_ack",
                                                        "inbox_digest"]);
/** The run doors' tools. */
const RUN_TOOLS: readonly string[] = Object.freeze(["converse", "run_graph", "run_status",
                                                    "run_cancel"]);
/**
 * What a run's own MCP door never serves: `converse`, since work between agents is mail
 * (ADR-034 decision 11). The other run tools it serves: an owner's agents may each do what
 * any other may (the founder, 2026-11-03), so a run launches, schedules, reads and cancels.
 */
const NEVER_RUN: readonly string[] = Object.freeze(["converse"]);
/** The file readers' tools (the starter slice): ChatGPT's search and fetch among them. */
const READERS: readonly string[] = Object.freeze(["file_read", "search", "fetch"]);
/** The file writers' tools (slice BRAIN): the additive write-back first. */
const WRITERS: readonly string[] = Object.freeze(["file_append", "file_write", "file_edit"]);
/** An engine's own tool names, its vendor's: never served as one of ours. */
const MODEL_TOOLS: readonly string[] = Object.freeze(["read", "write", "edit", "bash", "ta"]);
/** Fields that would make a tool an operation-selecting executor. */
const DISPATCH: readonly string[] = Object.freeze(["method", "operation", "tool", "target",
                                                   "action", "command"]);
/**
 * Writes that destroy or send nothing: an ack, a filed card, a proposal, a new directory,
 * text added after a file's bytes (slice BRAIN: never a byte already there changed).
 */
const ADDITIVE: readonly string[] = Object.freeze(["mailboxAck", "registerPropose",
                                                   "policyPropose", "fileMkdir", "fileAppend"]);
/**
 * Writes that repeat to the same end: an ack through a sequence, a directory made once, a
 * run's cancel (a second ends nothing).
 */
const IDEMPOTENT_WRITES: readonly string[] = Object.freeze(["mailboxAck", "fileMkdir",
                                                           "agentCancel"]);
/**
 * The outside-callable reads with no scope of their own: an identity's own agents, and its
 * own runs' receipts.
 */
const UNSCOPED_READS: readonly string[] = Object.freeze(["outsideAgents", "agentStatus"]);

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
        if (["#scope", "#collector", "#filer", "#reader", "#runDoor"].includes(helper)) {
          // The capability is the call's last string; a door class comes before it.
          const literal = node.arguments.filter(ts.isStringLiteralLike).at(-1);
          capability = literal?.text ?? capability;
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(member.body);
    const outside = ["#account", "#collector", "#filer", "#reader"].some((h) => helpers.has(h))
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
    const door = doors.get(tool.door);
    if (door === undefined) {
      say(`${tool.door} is not an outside-callable OwnerDO door`);
      continue;
    }
    if (tool.openWorldHint) {
      say("open world, but confined to the owner's account");
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

const DOORS = outsideDoors(readFileSync(OWNER_DO, "utf8"));
const base = toolTable()[0]!;

describe("tool table guard", () => {
  it("reads the outside-callable doors, and no owner-only one", () => {
    expect(DOORS.get("mailboxRead")?.capability).toBe("read_graph");
    expect(DOORS.get("collectionWrite")?.capability).toBe("write_graph");
    expect(DOORS.get("fileStat")?.capability).toBe("read_files");
    expect(DOORS.get("outsideAgents")?.capability).toBeNull();
    // The run doors: a start held to its capability, a read or a cancel to the caller's own.
    expect(DOORS.get("agentSubmit")?.capability).toBe("converse");
    expect(DOORS.get("agentRun")?.capability).toBe("run_graph");
    expect(DOORS.get("agentStatus")?.capability).toBeNull();
    expect(DOORS.get("agentCancel")?.capability).toBeNull();
    for (const runner of ["runEvents", "socketTicket", "socketsEnd", "tierSet"]) {
      expect(DOORS.has(runner), runner).toBe(false);
    }
    for (const ownerOnly of ["policyWrite", "grantSet", "grantRevoke", "grantForget",
                             "sessionOpen", "registerAgent", "fileRedact", "fileHistoryPurge",
                             "sandboxLeaseOpen", "fileSyncCommit", "policyRead",
                             "sandboxConfig", "sandboxNetworkApplied", "egressOpen",
                             "egressReserve", "egressSettle", "egressReceipts",
                             "connectionPrepare", "connectionCommit", "connectionRevoke",
                             "connectionList", "flowFinish"]) {
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

  it("serves the run tools, and a run's own profile all but converse", () => {
    expect(toolTable().filter((tool) => RUN_TOOLS.includes(tool.name)).map((tool) => tool.door))
      .toEqual(["agentSubmit", "agentRun", "agentStatus", "agentCancel"]);
    expect(profileNames("run").filter((name) => RUN_TOOLS.includes(name)))
      .toEqual(["run_graph", "run_status", "run_cancel"]);
  });

  it("serves the file readers read-only, held to read_files, in every profile", () => {
    const named = (name: string) => toolTable().find((tool) => tool.name === name)!;
    expect(READERS.map((name) => named(name).door)).toEqual(["fileText", "fileSearch",
                                                             "fileFetch"]);
    for (const name of READERS) {
      expect(named(name)).toMatchObject({ capability: "read_files", readOnlyHint: true,
                                          destructiveHint: false, idempotentHint: true,
                                          openWorldHint: false });
      for (const profile of ["directory", "custom", "run"] as const) {
        expect(profileNames(profile)).toContain(name);
      }
    }
    // ChatGPT's two names, each taking its one argument (platform.openai.com/docs/mcp).
    expect((named("search").input["required"] as string[])).toEqual(["query"]);
    expect((named("fetch").input["required"] as string[])).toEqual(["id"]);
  });

  it("serves the file writers held to write_files in every profile, the append additive " +
     "and the replacements destructive", () => {
    const named = (name: string) => toolTable().find((tool) => tool.name === name)!;
    expect(WRITERS.map((name) => named(name).door)).toEqual(["fileAppend", "fileWrite",
                                                             "fileEdit"]);
    expect(named("file_append")).toMatchObject({ capability: "write_files", readOnlyHint: false,
                                                 destructiveHint: false, idempotentHint: false });
    for (const name of ["file_write", "file_edit"]) {
      expect(named(name)).toMatchObject({ capability: "write_files", readOnlyHint: false,
                                          destructiveHint: true, idempotentHint: false });
    }
    for (const profile of ["directory", "custom", "run"] as const) {
      for (const name of WRITERS) {
        expect(profileNames(profile)).toContain(name);
      }
    }
    expect(Object.keys(DEFERRED)).not.toContain("fileWrite");
    // An append made destructive, or an edit made additive, is seen.
    const table = toolTable();
    for (const [name, destructiveHint] of [["file_append", true], ["file_edit", false]] as const) {
      expect(toolProblems(table.map((tool) => tool.name === name
        ? { ...tool, destructiveHint } : tool), DOORS)).not.toEqual([]);
    }
  });

  it("tells an outside client's agent the brain's rules in its first 512 characters, a " +
     "run's engine its working files", () => {
    expect(CLIENT_KEY.length).toBeLessThanOrEqual(512);
    expect(CLIENT_INSTRUCTIONS.startsWith(CLIENT_KEY)).toBe(true);
    // Each rule in the key part: goals, look first, save, claim.
    for (const named of ["/workspace/goals/index.md", "search", "fetch", "/workspace/index.md",
                         "file_append", "file_edit", "collection_write"]) {
      expect(CLIENT_KEY).toContain(named);
    }
    // The tools it names are the table's, so each is one the client is served (connect_url
    // is a field of agents_list's apps).
    for (const tool of (CLIENT_INSTRUCTIONS.match(/\b[a-z]+_[a-z_]+\b/g) ?? []).filter((word) => word !== "connect_url")) {
      expect(toolNames(), tool).toContain(tool);
    }
    for (const profile of CLIENT_PROFILES) {
      expect(profileNames(profile)).toEqual(expect.arrayContaining(
        ["search", "fetch", "file_append", "file_edit", "collection_write", "agents_list"]));
    }
    expect(RUN_INSTRUCTIONS).not.toBe(CLIENT_INSTRUCTIONS);
  });

  it("lists exactly each profile's tools: the directory's all but code, a run's all but " +
     "converse and the mailbox tools, custom's by default all", () => {
    const all = toolNames();
    expect(Object.keys(profiles).sort()).toEqual(["custom", "directory", "run"]);
    expect(profileNames("directory")).toEqual(all.filter((name) => name !== "code"));
    expect(profileNames("run")).toEqual(all.filter((name) => !NEVER_RUN.includes(name)
                                                    && !MAILBOX_TOOLS.includes(name)));
    expect(profileNames("custom")).toEqual(all);
    expect(profileNames("directory")).not.toContain("code");
    expect([...CLIENT_PROFILES]).toEqual(["directory", "custom"]);
    expect(DEFAULT_PROFILE).toBe("custom");
    // The directory profile cites the directories' rules it is held to.
    expect(profiles.directory.sources.join(" ")).toMatch(/openai\.com.*claude\.com/);
  });

  it("serves the digests and code mode, each with its door", () => {
    const named = (name: string) => toolTable().find((tool) => tool.name === name)!;
    expect(["inbox_digest", "board_digest", "code"].map((name) => named(name).door))
      .toEqual(["inboxDigest", "boardDigest", "codeRun"]);
    expect(named("code")).toMatchObject({ capability: null, readOnlyHint: false,
                                          destructiveHint: true, idempotentHint: false,
                                          openWorldHint: false });
    expect(DOORS.has("toolProfile")).toBe(true);
    expect(toolTable().map((tool) => tool.door)).not.toContain("toolProfile");
  });

  it("serves a run no mailbox tool: its mail is its input (slice ID)", () => {
    expect(profileNames("run").filter((name) => MAILBOX_TOOLS.includes(name))).toEqual([]);
    expect(profileNames("run").length).toBe(toolTable().length - NEVER_RUN.length
                                            - MAILBOX_TOOLS.length);
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
