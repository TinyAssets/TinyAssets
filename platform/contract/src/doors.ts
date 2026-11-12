/**
 * The owner object's doors, as data (`doors.json`; ADR-032,
 * `docs/decisions/untangle-2026-11-wave-1.md` A2): one row for each public RPC method of
 * `OwnerDO`, and nothing else lists them. A row says how the object takes a call, and the
 * object's one runner (`owner/src/owner-do.ts`, `#run`) does each step from it, in this order:
 *
 * 1. **bind**: the object's own owner, its admission for the door's `class` (`work`, `read`
 *    or `reduce`, which decides what a suspension leaves it, `admission.ts`; `admission`,
 *    the platform's own doors, read none; `import`, the cutover's import doors, are taken
 *    only while the owner is `pending`), and the sandbox, inbound or import binding its
 *    `caller` kind takes;
 * 2. **account**: who calls (`caller`), then the row's `guard`, if any;
 * 3. **scope**: `capability`, held where the caller acts (`scope`: the caller's own place,
 *    the agent its request names, or a place the door's handler reads from the request);
 * 4. **fields**: `args`, exactly the required fields and any of the optional ones, none
 *    `undefined`, else refused with the row's words; `fieldsFirst` checks them before the
 *    scope (where the scope's place is read from them).
 *
 * Then the door's handler, in its module (`handler`), runs in the row's `gate`: one unit of
 * the write gate (`door`), a quiet one (`quiet`), a unit and then the schedule synced
 * (`synced`; `filing`, whatever the unit did), or units of its own (`units`).
 *
 * The outside-callable doors' rows carry their MCP tool (`tool`, research §4.3), which the
 * doors serve on `/mcp` and as `POST /api/tools/<name>` (`doors/src/tools.ts`): its capability
 * and its arguments are its door's own. Each tool's four hints are explicit (OpenAI's
 * directory requires three, and `idempotentHint`); none is open world, each confined to the
 * owner's own account; `room_post` is destructive, since a send cannot be taken back; no tool
 * selects an operation. The run tools start an engine run in the owner's sandbox, so their
 * effects cannot be taken back; `code` (code mode, slice M) is a generic executor, never in
 * the directory profile. Descriptions say what a tool does and when to use it, never "always",
 * and name no other vendor's tool: both directories' rules.
 *
 * **Profiles** (`profiles.json`, data): which of the tools a caller is served: `custom`, the
 * owner's own choice (its policy's `tools.custom`, or every tool), which every grant on the
 * MCP door and every API key names; `directory`, what a public connector directory allows;
 * `run`, a run's own MCP door: the tools but `converse` (work between agents is mail, ADR-034
 * decision 11) and the mailbox tools, since a run's mail is its input.
 */
import raw from "../doors.json" with { type: "json" };
import profiles from "../profiles.json" with { type: "json" };

import { RUN_CAPABILITIES, type RunCapability } from "./capabilities";
import { Refusal } from "./refusal";

type JsonSchema = Readonly<Record<string, unknown>>;

/**
 * What a suspension leaves a door (`admission.ts`); the platform's own doors read none; the
 * cutover's import doors are taken only while the owner is `pending` (`owner/src/import.ts`).
 */
export const DOOR_CLASSES = Object.freeze(["work", "read", "reduce", "admission", "import"] as const);
export type DoorClass = (typeof DOOR_CLASSES)[number];

/**
 * Who a door takes, as its account step binds them:
 * - `account`: the owner's own account, acting itself or through a verified outside client
 *   admitted against its grant, or a run on the egress host as its initiator;
 * - `visitors`: `account`, and another owner visiting (origin null): the collections;
 * - `owner`: the owner itself, on its own account, never an outside client nor a run
 *   (`action`: what only the owner may do; `live`: in its live session, receipting nothing);
 * - `session`: the owner in its live session, a widening, whose refusals leave a receipt
 *   under `rule` of the request (or of `receipt`, in its place); `action`, null when an
 *   outside client may ask in it, and `registering`, an API key's registration;
 * - `reserved`: the platform's reserved caller, with no outside client and no session
 *   (`action`: its act, `admission` or `tier`);
 * - `erasure`: the platform's reserved caller, at an erasure under way;
 * - `import`: the platform's reserved caller with an import binding (the doors' import
 *   consumer, `doors/src/import.ts`), while the owner is `pending`: the `import` class's doors
 *   alone, deleted at the cutover;
 * - `discard`: the same binding, while the owner is `pending` or its name is a rehearsal's
 *   (`rehearsalRun`, `import-message.ts`), in any state: the import's discard alone;
 * - `egress`; `runner`, with its runner's secret (`closed`: its refusal once the lease is
 *   gone); `run`, with the run its path names;
 * - `sync`: the sync agent's token, at its live lease (the request's, when it names one);
 * - `reader`, `changes`: a file reader's account, or the sync agent's;
 * - `lease`: the owner itself (`action`), or the sync agent at its lease;
 * - `inbound`: an inbound event's route, at its binding's revision;
 * - `none`: no binding at all (the sandbox line's ring, and billed events): those two doors
 *   alone (`UNBOUND_DOORS`), so every other door binds.
 */
export const CALLER_KINDS = Object.freeze(["account", "visitors", "owner", "session",
  "reserved", "erasure", "import", "discard", "egress", "runner", "run", "sync", "reader",
  "changes", "lease", "inbound", "none"] as const);
export type CallerKind = (typeof CALLER_KINDS)[number];

/** The doors that take no binding: the sandbox line's ring and E2B's billed events. */
export const UNBOUND_DOORS: readonly string[] = Object.freeze(["sandboxBilled", "sandboxRung"]);

/**
 * A check of the caller after its account: a run reads no mailbox (`no_mailbox`); a visitor
 * changes no row it may not (`owner_rows`, `action`: what); only an MCP client files its own
 * card (`own_card`); a door that reaches the sandbox needs one bound (`sandbox`).
 */
export const GUARDS = Object.freeze(["no_mailbox", "owner_rows", "own_card", "sandbox"] as const);
export type Guard = (typeof GUARDS)[number];

/** How a door's handler runs in the object's write gate (`gate.ts`). */
export const GATES = Object.freeze(["door", "quiet", "synced", "filing", "units"] as const);
export type Gate = (typeof GATES)[number];

/** The owner modules whose state the doors' handlers read and write. */
export const HANDLERS = Object.freeze(["admission", "codemode", "collections", "connections",
  "egress", "erasure", "export", "files", "grants", "import", "inbound", "keys", "ledger",
  "notices", "policy", "registrations", "relay", "runs", "sandbox-time", "sessions", "signins",
  "starter", "tier", "usage"] as const);
export type Handler = (typeof HANDLERS)[number];

/** A door's request: its fields, each a JSON Schema, and the words its refusal is given. */
export interface Args {
  readonly refused: string;
  readonly required: Readonly<Record<string, JsonSchema>>;
  readonly optional: Readonly<Record<string, JsonSchema>>;
  /** A field may be sent as `undefined`, its value its module's to check (the export doors). */
  readonly undefinedAllowed?: true;
  /** No request at all is an empty one (`agents_list`). */
  readonly omitted?: true;
}

/**
 * A door's arguments: its fields (`Args`); its fields by who asks, the owner itself or an
 * outside client (`registerAgent`); `none`, a door that takes no request; or `loose`, a
 * request its module reads whole, held to no field list.
 */
export type DoorArgs = Args | { readonly owner: Args; readonly outside: Args } | "none" | "loose";

/** An outside-callable door's MCP tool. */
export interface ToolInfo {
  readonly name: string;
  readonly title: string;
  readonly readOnlyHint: boolean;
  readonly destructiveHint: boolean;
  readonly idempotentHint: boolean;
  readonly openWorldHint: boolean;
  readonly description: string;
}

export interface DoorRow {
  readonly door: string;
  readonly class: DoorClass | null;
  readonly caller: CallerKind;
  readonly action?: string | null;
  readonly rule?: string;
  readonly receipt?: unknown;
  readonly registering?: true;
  readonly closed?: "egress_lease_closed";
  readonly live?: true;
  readonly guard?: Guard;
  readonly capability: RunCapability | null;
  readonly scope?: "caller" | "agent" | "handler";
  /** The request's field naming the agent a run door acts as, and its default. */
  readonly agent?: { readonly field: string; readonly default?: string };
  readonly fieldsFirst?: true;
  readonly handler: Handler;
  readonly gate: Gate;
  readonly args: DoorArgs;
  readonly tool?: ToolInfo;
}

const ROW_KEYS = Object.freeze(["door", "class", "caller", "action", "rule", "receipt",
  "registering", "closed", "live", "guard", "capability", "scope", "agent", "fieldsFirst",
  "handler", "gate", "args", "tool"]);
const TOOL_KEYS = "description,destructiveHint,idempotentHint,name,openWorldHint," +
  "readOnlyHint,title";

const one = <T>(list: readonly T[], value: unknown): value is T =>
  (list as readonly unknown[]).includes(value);
const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Each way a door's arguments break the table's rules. */
function argsProblems(value: unknown): string[] {
  if (value === "none" || value === "loose") {
    return [];
  }
  if (!plain(value)) {
    return ["no argument schema"];
  }
  if ("owner" in value || "outside" in value) {
    return Object.keys(value).sort().join() === "outside,owner"
      ? [...argsProblems(value["owner"]), ...argsProblems(value["outside"])]
      : ["arguments by who asks name the owner's and an outside client's"];
  }
  const problems: string[] = [];
  if (typeof value["refused"] !== "string" || value["refused"] === "") {
    problems.push("arguments with no refusal");
  }
  for (const part of ["required", "optional"]) {
    const fields = value[part];
    if (!plain(fields) || !Object.values(fields).every(plain)) {
      problems.push(`${part} fields that are not schemas`);
    }
  }
  if (plain(value["required"]) && plain(value["optional"])
      && Object.keys(value["required"]).some((key) => key in (value["optional"] as object))) {
    problems.push("a field both required and optional");
  }
  for (const key of Object.keys(value)) {
    if (!["refused", "required", "optional", "undefinedAllowed", "omitted"].includes(key)
        || (["undefinedAllowed", "omitted"].includes(key) && value[key] !== true)) {
      problems.push(`an argument key ${key}`);
    }
  }
  return problems;
}

/** Each way `rows` breaks the table's rules, as `door: what`. */
export function doorProblems(rows: unknown): string[] {
  if (!Array.isArray(rows) || rows.length === 0) {
    return ["the table is a list of rows"];
  }
  const problems: string[] = [];
  const seen = new Set<unknown>();
  const tools = new Set<unknown>();
  for (const row of rows as unknown[]) {
    if (!plain(row)) {
      problems.push("a row that is not an object");
      continue;
    }
    const say = (what: string) => problems.push(`${String(row["door"])}: ${what}`);
    if (typeof row["door"] !== "string" || !/^[a-z][A-Za-z]{1,63}$/.test(row["door"])) {
      say("not a method's name");
    }
    if (seen.has(row["door"])) {
      say("named twice");
    }
    seen.add(row["door"]);
    for (const key of Object.keys(row)) {
      if (!ROW_KEYS.includes(key)) {
        say(`a key ${key}`);
      }
    }
    if (!one(CALLER_KINDS, row["caller"])) {
      say("no caller kind");
    }
    if (row["caller"] === "none" ? row["class"] !== null : !one(DOOR_CLASSES, row["class"])) {
      say("no door class");
    }
    if ((row["caller"] === "none") !== UNBOUND_DOORS.includes(row["door"] as string)) {
      say("only the sandbox line's ring and billed events take no binding");
    }
    if ((row["class"] === "admission") !== one(["reserved", "erasure"], row["caller"])) {
      say("only the platform's doors read no admission");
    }
    if ((row["class"] === "import") !== one(["import", "discard"], row["caller"])) {
      say("only the import's doors take the import class, and they take no other");
    }
    if (!("capability" in row) || (row["capability"] !== null
                                   && !one(RUN_CAPABILITIES, row["capability"]))) {
      say("no capability, nor null");
    }
    if (row["scope"] !== undefined && !one(["caller", "agent", "handler"], row["scope"])) {
      say("an unknown scope");
    }
    if (row["guard"] !== undefined && !one(GUARDS, row["guard"])) {
      say("an unknown guard");
    }
    if (!one(HANDLERS, row["handler"])) {
      say("no handler module");
    }
    if (!one(GATES, row["gate"])) {
      say("no gate");
    }
    if (!("args" in row)) {
      say("no argument schema");
    } else {
      for (const problem of argsProblems(row["args"])) {
        say(problem);
      }
    }
    if (one(["owner", "session"], row["caller"]) && !("action" in row)) {
      say("an owner's door names what only the owner may do");
    }
    if (row["caller"] === "session" && typeof row["rule"] !== "string") {
      say("a session's door names its receipt's rule");
    }
    const tool = row["tool"];
    if (tool !== undefined) {
      if (!plain(tool) || Object.keys(tool).sort().join() !== TOOL_KEYS) {
        say("a tool is its name, title, description and four hints");
      } else if (tools.has(tool["name"])) {
        say("a tool named twice");
      } else {
        tools.add(tool["name"]);
      }
      const args = row["args"];
      if (args !== "none" && (!plain(args) || !("refused" in args))) {
        say("a tool's door takes its fields, or none");
      }
    }
  }
  return problems;
}

/** `value` and everything in it frozen, so no module can change a row for the isolate. */
function frozen<T>(value: T): T {
  if (typeof value === "object" && value !== null && !Object.isFrozen(value)) {
    for (const each of Object.values(value)) {
      frozen(each);
    }
    Object.freeze(value);
  }
  return value;
}

/**
 * The table: the tools first, in the order `tools/list` serves them, then every other door.
 * The data file as it is (the module keeps no state), frozen whole at its first read, so no
 * module can change a row for the isolate; `doorProblems` holds it whole, and so does each
 * row read by name.
 */
export function doorTable(): readonly DoorRow[] {
  return frozen(raw.doors as unknown as readonly DoorRow[]);
}

/** The row of the door named `name`, held whole; an unknown or broken one is a bug. */
export function doorRow(name: string): DoorRow {
  const row = doorTable().find((each) => each.door === name);
  if (row === undefined) {
    throw new Error(`no door named ${name}`);
  }
  const problems = doorProblems([row]);
  if (problems.length > 0) {
    throw new Error(`the door table is broken: ${problems.join("; ")}`);
  }
  return row;
}

/**
 * A tool's arguments as JSON Schema: an object of exactly its required fields and any optional
 * ones; an empty one for a door that takes no request.
 */
export function argsSchema(args: DoorArgs): JsonSchema {
  const fields = args === "none" ? { required: {}, optional: {} } : args as Args;
  return { type: "object", properties: { ...fields.required, ...fields.optional },
           required: Object.keys(fields.required), additionalProperties: false };
}

/** A tool as the doors serve it and code mode calls it: its door's tool, capability and arguments. */
export interface Tool extends ToolInfo {
  readonly door: string;
  readonly capability: RunCapability | null;
  readonly input: JsonSchema;
}

/** The tools, in the order `tools/list` serves them, each a fresh copy. */
export function toolTable(): readonly Tool[] {
  return doorTable().flatMap((row) => row.tool === undefined ? [] : [{
    ...row.tool, door: row.door, capability: row.capability,
    input: structuredClone(argsSchema(row.args)) }]);
}

/** The tools `names` lists, in the table's order; an unknown name is a bug. */
export function toolsNamed(names: readonly string[]): readonly Tool[] {
  const table = toolTable();
  const unknown = names.filter((name) => !table.some((tool) => tool.name === name));
  if (unknown.length > 0) {
    throw new Error(`no tool named ${unknown.join(", ")}`);
  }
  return table.filter((tool) => names.includes(tool.name));
}

/** Whether a door takes a request after its binding. */
export function takesRequest(row: DoorRow): boolean {
  return row.args !== "none";
}

/** A profile's name: what a caller is served (`profiles.json`). */
export type ProfileName = "directory" | "custom" | "run";
/** The profiles an outside client's grant may name; `run` is a run's own door's. */
export const CLIENT_PROFILES: readonly ProfileName[] = Object.freeze(["directory", "custom"]);
/** What a grant names until the owner names another: every client is a custom connector. */
export const DEFAULT_PROFILE: ProfileName = "custom";

/** A profile's tool names, as its data lists them (`custom`: the default of the owner's choice). */
export function profileNames(name: ProfileName): readonly string[] {
  return Object.freeze([...profiles[name].tools]);
}

/** Every tool's name, in the table's order. */
export function toolNames(): readonly string[] {
  return doorTable().flatMap((row) => row.tool === undefined ? [] : [row.tool.name]);
}

/**
 * The request's fields are exactly `required`, plus any of `optional`, and none is
 * `undefined` unless `undefinedAllowed`: a field sent is a value. Else refused `refusal`.
 */
export function fields(request: unknown, required: readonly string[], optional: readonly string[],
                refusal: string, undefinedAllowed = false): Record<string, unknown> {
  if (typeof request !== "object" || request === null || Array.isArray(request)) {
    throw new Refusal("invalid", refusal);
  }
  const entries = Object.entries(request);
  const keys = entries.map(([key]) => key);
  if (!required.every((key) => keys.includes(key))
      || !keys.every((key) => required.includes(key) || optional.includes(key))
      || (!undefinedAllowed && entries.some(([, value]) => value === undefined))) {
    throw new Refusal("invalid", refusal);
  }
  return request as Record<string, unknown>;
}
