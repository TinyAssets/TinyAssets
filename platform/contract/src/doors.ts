/**
 * The owner object's doors, as data (`doors.json`; ADR-032,
 * `docs/decisions/untangle-2026-11-wave-1.md` A2): one row for each public RPC method of
 * `OwnerDO`, and nothing else lists them. A row says what every caller of the door sees:
 *
 * - its `class`, which decides what a suspension leaves it (`work`, `read` or `reduce`,
 *   `owner/src/admission.ts`; `admission`, the platform's own doors, read none; `import`, the
 *   cutover's import doors, are taken only while the owner is `pending`); a door that takes
 *   no binding (`UNBOUND_DOORS`) has none;
 * - its `capability`, held where the caller acts;
 * - its request (`args`): exactly the required fields and any of the optional ones, none
 *   `undefined`, else refused with the row's words; and any it lists as dropped, which the
 *   doors' version before may still hand, taken and left out (`requestFields`). Each field's
 *   schema is its type (`fieldTypes`), which the doors' `/mcp` holds outside calls to and the
 *   owner's fields step every call (`requestFields`); a type the doors' version before may
 *   still hand, which the schema names no more, is listed beside it (`droppedTypes`).
 *
 * - its answer (`answer`), where the doors' code or the app they serve reads one field by
 *   field, or the sandbox's runner reads it as the doors relay it: the fields the owner
 *   answers always and those it answers when it has them, each its JSON type (`fieldTypes`),
 *   whether it may answer null instead, and the fields it answers that the doors read not
 *   (`unread`): for the doors' version before, or for the runner protocols a sandbox Worker
 *   speaks, each one's record of what it reads (`sandbox/runner/protocol.json` `relayed`;
 *   `deploy/handed.ts` holds both). `tsc` holds the doors' code, the runner's and the owner's
 *   handlers to it (`contract/requests.ts` writes its types); a row with none is an answer
 *   the doors pass on whole, which only readers outside those pairings read (the kit, tests,
 *   and outside callers by the tool contract).
 *
 * A door the doors call no more is marked `dropped`: the object still takes it, for the
 * doors' version before, and `OwnerDoors` lacks it (`deploy/handed.ts`).
 *
 * How the object takes a call, its runner's steps (who it takes, the checks after, where its
 * capability is held, the order of its scope and fields, its handler's module and its gate),
 * is the owner's, by the door's name (`owner/doors.json`, `owner/src/door-steps.ts`).
 *
 * A door that does one of the acts an owner's `actions` rule names says so (`act`, an act of
 * `acts.json`, and `when`, the condition under which a call is the act, every call when it
 * names none), so a rule on that act holds at that door.
 *
 * A door whose work runs while its caller waits (`code`'s script) says so (`stop`): the doors
 * hand it, after its request, its caller's stop, a stream they close once that caller stops
 * waiting (`doors/src/tools.ts` `callDoor`), and the door ends its work then.
 *
 * The outside-callable doors' rows carry their MCP tool (`tool`), which the
 * doors serve on `/mcp` and as `POST /api/tools/<name>` (`doors/src/tools.ts`): its capability
 * and its arguments are its door's own. Each tool's four hints are explicit (OpenAI's
 * directory requires three, and `idempotentHint`); none is open world, each confined to the
 * owner's own account; `room_post` is destructive, since a send cannot be taken back; no tool
 * selects an operation. The run tools start an engine run in the owner's sandbox, so their
 * effects cannot be taken back; `code` (code mode) is a generic executor, never in
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

import { actNamed, type Condition, isCondition } from "./acts.ts";
import { RUN_CAPABILITIES, type RunCapability } from "./capabilities.ts";
import { among, frozen, keyed, object } from "./checks.ts";
import { Refusal } from "./refusal.ts";

type JsonSchema = Readonly<Record<string, unknown>>;

/**
 * What a suspension leaves a door (`admission.ts`); the platform's own doors read none; the
 * cutover's import doors are taken only while the owner is `pending` (`owner/src/import.ts`).
 */
export const DOOR_CLASSES = Object.freeze(["work", "read", "reduce", "admission", "import"] as const);
export type DoorClass = (typeof DOOR_CLASSES)[number];

/**
 * The names no RPC method may take (Cloudflare's `rpc/reserved-methods`, `doors.json`
 * `reserved`): `fetch` and `connect`, which a stub keeps as its own; `dup` and `constructor`,
 * on any RPC type; and an object's `alarm` and hibernating socket handlers. No door is named
 * one: the owner object's loop refuses it when the class loads, as the table's rules do here.
 */
export function rpcReserved(): readonly string[] {
  return frozen(raw.reserved);
}

/** The doors that take no binding: the sandbox line's ring and E2B's billed events. */
export const UNBOUND_DOORS: readonly string[] = Object.freeze(["sandboxBilled", "sandboxRung"]);

/** A door's request: its fields, each a JSON Schema, and the words its refusal is given. */
export interface Args {
  readonly refused: string;
  readonly required: Readonly<Record<string, JsonSchema>>;
  readonly optional: Readonly<Record<string, JsonSchema>>;
  /** A field may be sent as `undefined`, its value its module's to check (the export doors). */
  readonly undefinedAllowed?: true;
  /** No request at all is an empty one (`agents_list`). */
  readonly omitted?: true;
  /**
   * Fields a release before listed, which the doors hand no more and the owner still takes
   * and drops, until the doors the stage serves list them no more
   * (`deploy/handed.ts`).
   */
  readonly dropped?: readonly string[];
  /**
   * Types a release before handed a field, beside its schema's, which the doors hand no more
   * and the owner still takes, until the doors the stage serves hand them no more
   * (`deploy/handed.ts`).
   */
  readonly droppedTypes?: Readonly<Record<string, readonly FieldType[]>>;
}

/**
 * The types a field's schema may name: JSON's, and those only RPC carries (the doors' own
 * calls), `bytes`, a `Uint8Array`, and `stream`, a `ReadableStream` of bytes, which no tool's
 * field names: `/mcp` serves JSON Schema.
 */
const FIELD_TYPES = Object.freeze(["string", "integer", "number", "boolean", "object",
                                   "array", "null", "bytes", "stream"] as const);
export type FieldType = (typeof FIELD_TYPES)[number];
/** The field types only RPC carries. */
const RPC_TYPES: readonly FieldType[] = Object.freeze(["bytes", "stream"]);

/**
 * A door's answer as its readers read it: the fields the owner answers every time and those
 * it answers when it has them, each a schema of its type (`fieldTypes`); whether it may answer
 * null in place of them; and the fields it answers, as listed, which the doors read not, for
 * the doors the stage serves and the runner protocols a sandbox Worker speaks
 * (`deploy/handed.ts`).
 */
export interface Answer {
  readonly required: Readonly<Record<string, JsonSchema>>;
  readonly optional: Readonly<Record<string, JsonSchema>>;
  readonly nullable?: true;
  readonly unread?: readonly string[];
}

/**
 * The types the field `name`'s schema names, or null where it names none (a table before
 * every field named one). A schema is its type and description: what the deploy kit holds
 * across a deploy (`deploy/handed.ts`), so any other key throws rather than narrow a field
 * unseen.
 */
export function fieldTypes(name: string, schema: unknown): readonly FieldType[] | null {
  if (!keyed(schema, [], ["type", "description"])) {
    throw new Error(`${name}'s schema is not its type and description alone`);
  }
  if (schema["type"] === undefined) {
    return null;
  }
  const types: unknown[] = Array.isArray(schema["type"]) ? schema["type"] : [schema["type"]];
  if (types.length === 0 || new Set(types).size !== types.length
      || !types.every((type) => among(FIELD_TYPES, type))) {
    throw new Error(`${name}'s type is not a field type, nor a list of distinct ones`);
  }
  return types as FieldType[];
}

/** Whether a field that takes `taken` takes a value of `type`: `integer` under `number` too. */
export function takesType(taken: readonly FieldType[], type: FieldType): boolean {
  return taken.includes(type) || (type === "integer" && taken.includes("number"));
}

/** Each type in a refusal's words. */
const TYPE_WORDS: Readonly<Record<FieldType, string>> = Object.freeze({
  string: "a string", integer: "an integer", number: "a number", boolean: "true or false",
  object: "an object", array: "a list", null: "null", bytes: "bytes", stream: "a stream",
});

/** The type `value` is, as a field's schema names it (`integer` for a whole number); else null. */
function typeOf(value: unknown): FieldType | null {
  if (value === null) {
    return "null";
  }
  if (Array.isArray(value)) {
    return "array";
  }
  if (value instanceof Uint8Array) {
    return "bytes";
  }
  if (value instanceof ReadableStream) {
    return "stream";
  }
  if (object(value)) {
    return "object";
  }
  if (typeof value === "string" || typeof value === "boolean") {
    return typeof value === "string" ? "string" : "boolean";
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? "integer" : "number";
  }
  return null;
}

/**
 * A door's arguments: its fields (`Args`); its fields by who asks, the owner itself (no
 * outside client, no run) or anyone else (`registerAgent`, and the file doors whose owner
 * names no etag, or writes bytes); `none`, a door that takes no request; or `loose`, a
 * request its module reads whole, held to no field list. A tool's schema is its fields, or
 * else the outside ones (`argsSchema`).
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

/** A door as every caller sees it; its runner's steps are the owner's (`owner/doors.json`). */
export interface DoorRow {
  readonly door: string;
  readonly class: DoorClass | null;
  readonly capability: RunCapability | null;
  /** The act a call of this door is, and when (`acts.json`). */
  readonly act?: { readonly name: string; readonly when?: Condition };
  readonly args: DoorArgs;
  /** Its answer as the doors read it, where they read one field by field. */
  readonly answer?: Answer;
  readonly tool?: ToolInfo;
  /** Its work runs while its caller waits: it takes the caller's stop after its request. */
  readonly stop?: true;
  /**
   * The doors call it no more (no tool, and no method of `OwnerDoors`); the owner still takes
   * it, until the doors the stage serves call it no more (`deploy/handed.ts`).
   */
  readonly dropped?: true;
}

const ROW_KEYS = Object.freeze(["door", "class", "capability", "act", "args", "answer", "tool",
                                "stop", "dropped"]);
const TOOL_KEYS = Object.freeze(["name", "title", "description", "destructiveHint",
  "idempotentHint", "openWorldHint", "readOnlyHint"]);

/** Each way a door's arguments break the table's rules. */
function argsProblems(value: unknown): string[] {
  if (value === "none" || value === "loose") {
    return [];
  }
  if (!object(value)) {
    return ["no argument schema"];
  }
  if ("owner" in value || "outside" in value) {
    return keyed(value, ["owner", "outside"])
      ? [...argsProblems(value["owner"]), ...argsProblems(value["outside"])]
      : ["arguments by who asks name the owner's and an outside client's"];
  }
  const problems: string[] = [];
  if (typeof value["refused"] !== "string" || value["refused"] === "") {
    problems.push("arguments with no refusal");
  }
  const typed = new Map<string, readonly FieldType[]>();
  for (const part of ["required", "optional"]) {
    const fields = value[part];
    if (!object(fields) || !Object.values(fields).every(object)) {
      problems.push(`${part} fields that are not schemas`);
      continue;
    }
    for (const [name, schema] of Object.entries(fields)) {
      try {
        const types = fieldTypes(name, schema);
        if (types === null) {
          problems.push(`${name}'s schema names no type`);
        } else {
          typed.set(name, types);
        }
      } catch (error) {
        problems.push((error as Error).message);
      }
    }
  }
  if (object(value["required"]) && object(value["optional"])
      && Object.keys(value["required"]).some((key) => key in (value["optional"] as object))) {
    problems.push("a field both required and optional");
  }
  const dropped = value["dropped"];
  if (dropped !== undefined && (!Array.isArray(dropped) || dropped.some((key) =>
    typeof key !== "string" || [value["required"], value["optional"]].some((fields) =>
      object(fields) && key in fields)))) {
    problems.push("dropped fields that are not a list of the fields it lists no more");
  }
  const droppedTypes = value["droppedTypes"];
  if (droppedTypes !== undefined && (!object(droppedTypes)
      || Object.entries(droppedTypes).some(([name, types]) => {
        const held = typed.get(name);
        return !Array.isArray(types) || types.length === 0 || held === undefined
          || new Set(types).size !== types.length
          || types.some((type) => !among(FIELD_TYPES, type) || held.includes(type));
      }))) {
    problems.push("dropped types that are not, by a typed field it lists, types its schema " +
                  "names not");
  }
  for (const key of Object.keys(value)) {
    if (!["refused", "required", "optional", "undefinedAllowed", "omitted", "dropped",
          "droppedTypes"].includes(key)
        || (["undefinedAllowed", "omitted"].includes(key) && value[key] !== true)) {
      problems.push(`an argument key ${key}`);
    }
  }
  return problems;
}

/** Each way a door's answer breaks the table's rules. */
function answerProblems(value: unknown): string[] {
  if (!keyed(value, ["required", "optional"], ["nullable", "unread"])) {
    return ["an answer is its required and optional fields, whether null, and its unread ones"];
  }
  const problems: string[] = [];
  const listed: string[] = [];
  for (const part of ["required", "optional"] as const) {
    const fields = value[part];
    if (!object(fields)) {
      problems.push(`answer ${part} fields that are not schemas`);
      continue;
    }
    for (const [name, schema] of Object.entries(fields)) {
      listed.push(name);
      try {
        if (fieldTypes(name, schema) === null) {
          problems.push(`answer ${name}'s schema names no type`);
        }
      } catch (error) {
        problems.push(`answer ${(error as Error).message}`);
      }
    }
  }
  if (listed.length === 0 || new Set(listed).size !== listed.length) {
    problems.push("an answer lists each field once, and at least one");
  }
  if ("nullable" in value && value["nullable"] !== true) {
    problems.push("an answer's nullable is true");
  }
  const unread = value["unread"];
  if (unread !== undefined && (!Array.isArray(unread) || unread.length === 0
      || new Set(unread).size !== unread.length
      || unread.some((name) => typeof name !== "string" || !listed.includes(name)))) {
    problems.push("unread answer fields that are not a list of the fields it answers");
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
    if (!object(row)) {
      problems.push("a row that is not an object");
      continue;
    }
    const say = (what: string) => problems.push(`${String(row["door"])}: ${what}`);
    if (typeof row["door"] !== "string" || !/^[a-z][A-Za-z]{1,63}$/.test(row["door"])) {
      say("not a method's name");
    }
    if (rpcReserved().includes(row["door"] as string)) {
      say("a name RPC reserves");
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
    if (UNBOUND_DOORS.includes(row["door"] as string) ? row["class"] !== null
        : !among(DOOR_CLASSES, row["class"])) {
      say("no door class, or one on a door that takes no binding");
    }
    if (!("capability" in row) || (row["capability"] !== null
                                   && !among(RUN_CAPABILITIES, row["capability"]))) {
      say("no capability, nor null");
    }
    const act = row["act"];
    if (act !== undefined && (!object(act) || actNamed(act["name"]) === undefined
        || !Object.keys(act).every((key) => key === "name" || key === "when")
        || ("when" in act && !isCondition(act["when"])))) {
      say("an act is one of acts.json's, and when, one of its conditions");
    }
    if ("stop" in row && (row["stop"] !== true || UNBOUND_DOORS.includes(row["door"] as string)
                          || row["args"] === "none")) {
      say("a stop is true, on a door that takes a binding and a request");
    }
    if ("dropped" in row && (row["dropped"] !== true || "tool" in row)) {
      say("a dropped door is marked true, and has no tool");
    }
    if (!("args" in row)) {
      say("no argument schema");
    } else {
      for (const problem of argsProblems(row["args"])) {
        say(problem);
      }
    }
    if ("answer" in row) {
      for (const problem of answerProblems(row["answer"])) {
        say(problem);
      }
    }
    const tool = row["tool"];
    if (tool !== undefined) {
      if (!keyed(tool, TOOL_KEYS)) {
        say("a tool is its name, title, description and four hints");
      } else if (tools.has(tool["name"])) {
        say("a tool named twice");
      } else {
        tools.add(tool["name"]);
      }
      const args = row["args"];
      const served = object(args) && object(args["outside"]) ? args["outside"] : args;
      if (args !== "none" && !(object(served) && "refused" in served)) {
        say("a tool's door takes its fields, or none");
      } else if (object(served) && [served["required"], served["optional"]].some((fields) =>
        object(fields) && Object.values(fields).some((schema) => object(schema)
          && [schema["type"]].flat().some((type) => RPC_TYPES.includes(type as FieldType))))) {
        say("a tool's field names a type only RPC carries, which JSON Schema has not");
      }
    }
  }
  return problems;
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
 * ones (by who asks, the outside ones); an empty one for a door that takes no request.
 */
export function argsSchema(args: DoorArgs): JsonSchema {
  const fields = args === "none" ? { required: {}, optional: {} }
    : "outside" in (args as object) ? (args as { outside: Args }).outside : args as Args;
  return { type: "object", properties: { ...fields.required, ...fields.optional },
           required: Object.keys(fields.required), additionalProperties: false };
}

/**
 * A tool as the doors serve it and code mode calls it: its door's tool, capability and
 * arguments.
 */
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

/** Whether a door takes its caller's stop after its request (`stop`). */
export function takesStop(row: DoorRow): boolean {
  return row.stop === true;
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

/** The doors that do the act named `name`. */
export function actDoors(name: string): readonly string[] {
  return doorTable().flatMap((row) => row.act?.name === name ? [row.door] : []);
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

/**
 * A door's request held to its arguments (`fields`): none at all an empty one where it may be
 * `omitted`; each field's value of a type its schema names, or one of its `droppedTypes`
 * (an `undefined` one, where `undefinedAllowed`, its module's to read), else refused with the
 * row's words and the types it names; and its dropped fields taken and left out.
 */
export function requestFields(args: Args, request: unknown): Record<string, unknown> {
  const { dropped = [] } = args;
  const asked = args.omitted === true && (request === undefined || request === null) ? {}
    : request;
  const r = fields(asked, Object.keys(args.required), [...Object.keys(args.optional), ...dropped],
                   args.refused, args.undefinedAllowed === true);
  for (const [name, value] of Object.entries(r)) {
    if (dropped.includes(name) || value === undefined) {
      continue;
    }
    const schema = Object.hasOwn(args.required, name) ? args.required[name] : args.optional[name];
    const types = fieldTypes(name, schema) ?? [];
    const type = typeOf(value);
    if (type === null || !takesType([...types, ...args.droppedTypes?.[name] ?? []], type)) {
      throw new Refusal("invalid", `${args.refused}: ${name} is ${
        types.map((each) => TYPE_WORDS[each]).join(" or ")}`);
    }
  }
  return dropped.some((key) => key in r)
    ? Object.fromEntries(Object.entries(r).filter(([key]) => !dropped.includes(key))) : r;
}
