/**
 * The external tool table (research §4.3, checklist 18, 21): one tool per
 * outside-callable owner door, every hint explicit and right, and never the model's own.
 *
 * A tool is its door's row of the owner's door table (`contract/doors.json`), so its
 * capability and its arguments are its door's by construction. The outside-callable doors are
 * the rows an outside client's call reaches (an `account`, `visitors`, `reader` or `changes`
 * caller); each must have a tool, or be named below with the slice that owes it. Every tool
 * is confined to the owner's own account, so none is open world.
 *
 * And the profiles (`profiles.json`, slice M): each lists exactly its tools, table tools in
 * the table's order; `code`, a generic executor, never in the directory's. What the doors
 * tell an agent of the tools is the doors' own guard (`doors/guards/definition.test.ts`).
 */
import { describe, expect, it } from "vitest";

import profiles from "../profiles.json" with { type: "json" };
import {
  CLIENT_PROFILES, DEFAULT_PROFILE, doorTable, type DoorRow, profileNames, type Tool, toolNames,
  toolTable,
} from "../src/doors";

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

/** The callers an outside client's call reaches a door as. */
const OUTSIDE_CALLERS: readonly string[] = Object.freeze(["account", "visitors", "reader",
                                                          "changes"]);

/** Every outside-callable door of the table, with the capability its row holds it to. */
export function outsideDoors(rows: readonly DoorRow[]): Map<string, string | null> {
  return new Map(rows.filter((row) => OUTSIDE_CALLERS.includes(row.caller))
    .map((row) => [row.door, row.capability]));
}

/** Each way the table breaks the rules, as `tool: what`. */
export function toolProblems(tools: readonly Tool[], doors: ReadonlyMap<string, string | null>):
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
    if (!doors.has(tool.door)) {
      say(`${tool.door} is not an outside-callable OwnerDO door`);
      continue;
    }
    const capability = doors.get(tool.door)!;
    if (tool.openWorldHint) {
      say("open world, but confined to the owner's account");
    }
    if (used.has(tool.door)) {
      say(`${tool.door} has two tools`);
    }
    used.add(tool.door);
    const reads = capability === null ? UNSCOPED_READS.includes(tool.door)
                                      : capability.startsWith("read_");
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
    if (!required.every((field) => field in properties)) {
      say("a required field the arguments do not hold");
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

const OUTSIDE = outsideDoors(doorTable());
const base = toolTable()[0]!;

describe("tool table guard", () => {
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
  });

  it("holds every tool to its door, its hints and its arguments", () => {
    expect(toolProblems(toolTable(), OUTSIDE)).toEqual([]);
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
    ["a dispatch field", { input: { type: "object", additionalProperties: false,
      properties: { limit: {}, operation: {} }, required: ["limit"] } }],
    ["an open object", { input: { type: "object", properties: { limit: {} },
                                  required: ["limit"] } }],
    ["a required field it does not hold", { input: { type: "object",
      additionalProperties: false, properties: { limit: {} }, required: ["limit", "after"] } }],
  ])("sees %s", (_what, change) => {
    expect(toolProblems([{ ...base, ...change }, ...toolTable().slice(1)], OUTSIDE))
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
        ? { ...tool, destructiveHint } : tool), OUTSIDE)).not.toEqual([]);
    }
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
    expect(OUTSIDE.has("toolProfile")).toBe(true);
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
        ? { ...tool, idempotentHint } : tool), OUTSIDE)).not.toEqual([]);
    }
  });

  it("sees a write marked non-destructive, and a door given two tools", () => {
    const table = toolTable();
    const post = table.find((tool) => tool.name === "room_post")!;
    expect(toolProblems(table.map((tool) => tool === post
      ? { ...tool, destructiveHint: false } : tool), OUTSIDE)).not.toEqual([]);
    expect(toolProblems([...table, { ...post, name: "room_post_again" }], OUTSIDE))
      .not.toEqual([]);
  });

  it("sees an outside-callable door with no tool and none owed", () => {
    expect(toolProblems(toolTable().filter((tool) => tool.name !== "file_usage"), OUTSIDE))
      .toEqual(["fileUsage: an outside-callable door with no tool, and none owed"]);
  });

  it("reads a new outside door from the table, and no owner-only one", () => {
    const row = (door: string, caller: DoorRow["caller"]): DoorRow =>
      ({ door, class: "read", caller, capability: "read_graph", handler: "ledger",
         gate: "door", args: "none" });
    expect([...outsideDoors([row("peek", "account"), row("mine", "owner")])])
      .toEqual([["peek", "read_graph"]]);
  });
});
