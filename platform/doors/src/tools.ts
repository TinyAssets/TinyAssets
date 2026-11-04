/**
 * The external tools (research §4.3): one tool per outside-callable owner door, served on
 * `/mcp` and as `POST /api/tools/<name>`.
 *
 * Each row names the `OwnerDO` door it calls, the scope capability that door checks, a
 * title, and all four hints as explicit booleans: the three OpenAI's directory requires
 * (`readOnlyHint`, `destructiveHint`, `openWorldHint`) and `idempotentHint`, as the
 * Python set it. `openWorldHint` is false for every tool here, each confined to the owner's
 * own account, as the Python's annotations declared (`mcp_tool_annotations.py`).
 * `room_post` is destructive because a send cannot be taken back. No tool selects an
 * operation (no `method`, `operation`, `tool` or `target` field): each operation is its
 * own tool, which both directories require.
 *
 * The run tools (`converse`, `run_graph`, `run_status`, `run_cancel`; slice R) are run
 * doors (ADR-034): `converse` and `run_graph` start an engine run in the owner's sandbox,
 * so the agent's effects cannot be taken back (destructive, not idempotent; a retry of
 * `converse` with the same `client_send_id` is the same run); `run_status` reads a run's
 * receipt; `run_cancel` ends one (destructive, idempotent). An engine's own tools are its
 * vendor's, and are never served here.
 *
 * The file readers (the starter slice, ai_storage_research.md §8.2), for a main agent that
 * cannot walk the files itself: `file_read` (a file's text, a window at a time), heads on
 * `file_list`, and `search` and `fetch` in the shape ChatGPT's deep research and company
 * knowledge require, lexical and bounded (`owner/src/file-search.ts`).
 *
 * The file writers (slice BRAIN, shared_brain_research.md §6.2), so an outside agent writes
 * back what it learns: `file_append`, the everyday write-back, additive (it never changes a
 * byte already there, so `destructiveHint: false`, which OpenAI allows only for additive
 * writes and which spares the owner an approval per remembered fact); `file_write` (a whole
 * replacement) and `file_edit` (one exact occurrence replaced), both destructive. Their
 * descriptions, and the readers', say what each does and when to use it, never "always",
 * and name no other vendor's tool: both directories' rules.
 *
 * Slice M's tools, so a main agent spends few calls: `inbox_digest` and `board_digest`
 * (what changed for the caller since its cursor, in one read) and `code` (code mode: one
 * script calls the caller's other tools, each held to its grant, its VM in the hooks
 * Worker). `code` is a generic executor, so it is never in the directory profile, by
 * OpenAI's plugin guidelines (no generic executor enabling operations not exposed for review).
 *
 * **Profiles** (`profiles.json`, data): which of the table's tools a caller is served.
 * `custom`, the owner's own choice (its policy's `tools.custom`, or every tool), which every
 * grant on the MCP door and every API key names: each is a custom connector; `directory`,
 * what a public connector directory allows, for the listing's own MCP resource once the
 * listing is submitted (the founder's call: a new door); `run`, a run's own MCP door on
 * the egress host, the table but `converse` (work between agents is mail, ADR-034
 * decision 11; a run may launch, schedule, read and cancel runs, the founder 2026-11-03) and
 * the mailbox tools, `inbox_digest` among them, since a run's mail is
 * its input and its agent's mailbox holds every authority's mail (slice ID). The object
 * answers a caller's profile (`toolProfile`) and builds `code`'s
 * tools from it.
 * `guards/tools.test.ts` holds every row to these rules and to a real outside-callable
 * door, and names the outside-callable doors without a tool yet, each with the slice that
 * owes it.
 */
import type { Binding, OwnerDO } from "../../owner/src/owner-do";

import profiles from "../profiles.json" with { type: "json" };

type JsonSchema = Record<string, unknown>;

/** The doors a binding reaches: `OwnerDO` methods whose first argument is a `Binding`. */
export type Door = {
  [K in keyof OwnerDO]-?: OwnerDO[K] extends (...args: infer A) => unknown
    ? A extends [Binding, ...unknown[]] ? K : never : never;
}[keyof OwnerDO];

export interface Tool {
  readonly name: string;
  readonly door: Door;
  /** The scope capability the door checks a scoped grant for, or null when it checks none. */
  readonly capability: "read_graph" | "write_graph" | "propose_policy" | "read_files"
    | "write_files" | "converse" | "run_graph" | null;
  readonly title: string;
  readonly description: string;
  readonly readOnlyHint: boolean;
  readonly destructiveHint: boolean;
  readonly idempotentHint: boolean;
  readonly openWorldHint: boolean;
  /** The arguments, as JSON Schema; a door that takes no request has no properties. */
  readonly input: JsonSchema;
}

const str = (description: string): JsonSchema => ({ type: "string", description });
const int = (description: string): JsonSchema => ({ type: "integer", description });
const num = (description: string): JsonSchema => ({ type: "number", description });
const bool = (description: string): JsonSchema => ({ type: "boolean", description });
const orNull = (description: string): JsonSchema =>
  ({ type: ["string", "null"], description });

/** An object of exactly `required` and any of `optional`, nothing else. */
function args(required: Record<string, JsonSchema>,
              optional: Record<string, JsonSchema> = {}): JsonSchema {
  return { type: "object", properties: { ...required, ...optional },
           required: Object.keys(required), additionalProperties: false };
}

const READ = Object.freeze({ readOnlyHint: true, destructiveHint: false, idempotentHint: true,
                             openWorldHint: false } as const);
const CHANGE = Object.freeze({ readOnlyHint: false, destructiveHint: true,
                               idempotentHint: false, openWorldHint: false } as const);

/** The table, in the order `tools/list` serves it. */
export function toolTable(): readonly Tool[] {
  const collection = str("The collection's name");
  const row = str("The row's id");
  const path = str("An absolute path in the owner's files, such as /workspace/notes.md");
  const etag = orNull("The entry's current etag, or null to create it");
  const after = int("Changes after this cursor: 0 at first, then the last answer's cursor");
  const head = int("At most this many characters of each row's body (512 when omitted)");
  const bodyFrom = int("The first item's body from this byte: a body_next_offset an answer " +
                       "gave (0 when omitted)");
  return [
    { name: "inbox_digest", door: "inboxDigest", capability: "read_graph",
      title: "Inbox digest", ...READ,
      description: "Read what is new for this agent in one call: its unacknowledged mail, " +
        "oldest first, and the rows of the owner's rooms changed after a cursor, each " +
        "with the head of its body. Acknowledge mail with mailbox_ack; a mail body cut to " +
        "fit (truncated true) is read on with mailbox_read's body_offset and message_id; " +
        "read a whole row with collection_get. An answer with reset true means a deletion past the cursor " +
        "is no longer kept: read the rooms again whole, then go on from the cursor given.",
      input: args({ limit: int("At most this many messages, and this many room rows"),
                    after },
                  { max_bytes: int("At most this many mail body bytes in all"),
                    head_chars: head }) },
    { name: "board_digest", door: "boardDigest", capability: "read_graph",
      title: "Board digest", ...READ,
      description: "Read the owner's board rows changed after a cursor in one call, each " +
        "with its etag and the head of its body. Claim a row with collection_write on " +
        "its etag; read a whole row with collection_get. An answer with reset true means " +
        "a deletion past the cursor is no longer kept: read the board again whole " +
        "(collection_pull), then go on from the cursor given.",
      input: args({ limit: int("At most this many rows"), after }, { head_chars: head }) },
    { name: "mailbox_read", door: "mailboxRead", capability: "read_graph",
      title: "Read mailbox", ...READ,
      description: "Read this agent's unacknowledged mailbox messages, oldest first. One " +
        "answer holds at most 50000 characters: a message whose body does not fit is cut, " +
        "with truncated true and body_next_offset; read the rest by calling again with " +
        "body_offset set to it and message_id set to that message's, before acknowledging it.",
      input: args({ limit: int("At most this many messages") },
                  { max_bytes: int("At most this many body bytes in all"),
                    pad_bytes: int("Pad the page to this many bytes"),
                    body_offset: bodyFrom,
                    message_id: str("With body_offset, the message whose body is read on") }) },
    { name: "mailbox_ack", door: "mailboxAck", capability: "write_graph",
      title: "Acknowledge mailbox", readOnlyHint: false, destructiveHint: false,
      idempotentHint: true, openWorldHint: false,
      description: "Acknowledge this agent's mailbox messages through a sequence number.",
      input: args({ through: int("The last sequence number read") }) },
    { name: "room_post", door: "roomPost", capability: "write_graph",
      title: "Post to room", ...CHANGE,
      description: "Post a message to a room of the owner's account: dm:<agent> with the " +
        "owner, or dm:<agent>+<agent> between two agents. Mail to one of the owner's own " +
        "agents starts its run, whose result is its reply.",
      input: args({ room_id: str("The room's id"),
                    client_message_id: str("Your id for this message, for retries"),
                    body: str("The message text"),
                    parent_id: orNull("The message replied to, or null") }) },
    { name: "agents_list", door: "outsideAgents", capability: null,
      title: "List agents", ...READ,
      description: "List the agents registered for this connection, the owner's own " +
        "agents, each with the id its rooms name, and which of the owner's AI apps are " +
        "connected to TinyAssets, each one not connected with its connect_url, the page " +
        "with its connect step. One answer holds as many as fit; when it carries next, read " +
        "again with after set to it for the rest.",
      input: args({}, { after: str("Read on after this: a next an answer gave") }) },
    { name: "agent_register", door: "registerPropose", capability: null,
      title: "Request agent registration", readOnlyHint: false, destructiveHint: false,
      idempotentHint: false, openWorldHint: false,
      description: "File this connection's agent registration for the owner to approve.",
      input: args({ command_center_id: str("The command center to register in"),
                    display_name: str("The agent's display name"),
                    delivery_method: str("How the agent is reached: pull or mcp_event"),
                    recipe_id: orNull("The recipe the agent runs, or null") }) },
    { name: "policy_propose", door: "policyPropose", capability: "propose_policy",
      title: "Propose policy change", readOnlyHint: false, destructiveHint: false,
      idempotentHint: false, openWorldHint: false,
      description: "Propose a change to the owner's policy, for the owner to approve.",
      input: args({ change: { type: "object", description: "The proposed change" } },
                  { request_id: str("Your id for this proposal, for retries") }) },
    { name: "collection_get", door: "collectionGet", capability: "read_graph",
      title: "Get collection row", ...READ,
      description: "Read one row of a collection. One answer holds at most 50000 " +
        "characters: a body that does not fit is cut, with truncated true and " +
        "body_next_offset; read the rest by calling again with body_offset set to it and " +
        "etag set to the row's etag, which refuses the read if the row changed meanwhile.",
      input: args({ collection, row_id: row },
                  { body_offset: int("The body from this byte: a body_next_offset an " +
                                     "answer gave"),
                    etag: str("The etag the earlier part came with") }) },
    { name: "collection_list", door: "collectionPull", capability: "read_graph",
      title: "List collection rows", ...READ,
      description: "List a collection's rows changed after a revision, as many as one " +
        "answer holds (at most 50000 characters); go on from the cursor it gives. A first " +
        "row whose body does not fit is cut (truncated true): read the rest with " +
        "collection_get.",
      input: args({ collection, after: int("Rows changed after this revision"),
                    limit: int("At most this many rows") }) },
    { name: "collection_history", door: "collectionHistory", capability: "read_graph",
      title: "Collection row history", ...READ,
      description: "Read the kept history of one collection row, newest first by page; " +
        "pass before_revision for the older entries when the answer says more. A newest " +
        "entry whose body does not fit one answer is cut (truncated true): call again with " +
        "the same before_revision and body_offset set to its body_next_offset.",
      input: args({ collection, row_id: row },
                  { before_revision: int("Entries before this revision"),
                    body_offset: bodyFrom }) },
    { name: "collection_write", door: "collectionWrite", capability: "write_graph",
      title: "Write collection row", ...CHANGE,
      description: "Write one collection row against its current etag. A new row " +
        "(expected_etag \"\") is refused when the row exists, so creating one claims an " +
        "act: on the board, the first agent to create the row named for an act holds it, " +
        "and a conflict means another agent has it.",
      input: args({ collection, row_id: row, body: str("The row's new body"),
                    expected_etag: str("The row's current etag, or \"\" to create it"),
                    private: bool("Whether only the owner may read the row") },
                  { history_op: str("The history entry's operation name") }) },
    { name: "collection_delete", door: "collectionDelete", capability: "write_graph",
      title: "Delete collection row", ...CHANGE,
      description: "Delete one collection row against its current etag.",
      input: args({ collection, row_id: row, expected_etag: str("The row's current etag") }) },
    { name: "collection_clear", door: "collectionClear", capability: "write_graph",
      title: "Clear collection", ...CHANGE,
      description: "Delete a collection's rows, or one writer's rows.",
      input: args({ collection, writer: orNull("Only this writer's rows, or null for all") }) },
    { name: "file_stat", door: "fileStat", capability: "read_files",
      title: "File info", ...READ, description: "Read one file's or directory's entry.",
      input: args({ path }) },
    { name: "file_list", door: "fileList", capability: "read_files",
      title: "List directory", ...READ,
      description: "List a directory's entries by name. With head_chars, each small file " +
        "also carries the start of its text, enough to read its title and description.",
      input: args({ dir: path, limit: int("At most this many entries") },
                  { after: str("Entries after this name"),
                    head_chars: int("At most this many characters of each small file's text") }) },
    { name: "file_read", door: "fileText", capability: "read_files",
      title: "Read file", ...READ,
      description: "Use this to read a file of the user's TinyAssets workspace by its " +
        "path, such as their profile /workspace/USER.md: its UTF-8 text from offset (0 when " +
        "omitted), at most max_bytes (65536 when omitted, up to 4194304) and at most what " +
        "one answer holds (50000 characters), cut at a character. next_offset is where the " +
        "next part starts, or null at the end. Its entry carries the etag file_edit and " +
        "file_write take.",
      input: args({ path }, { offset: int("The byte to start at: 0, or a next_offset"),
                              max_bytes: int("At most this many bytes of text") }) },
    { name: "search", door: "fileSearch", capability: "read_files",
      title: "Search files", ...READ,
      description: "Use this when the user's request may depend on what they or their " +
        "other agents already saved: their details, preferences, goals, decisions, notes " +
        "and projects. Searches the user's TinyAssets workspace by words: file paths, " +
        "titles, descriptions and tags, small files' text, and index lines " +
        "(/workspace/index.md maps the workspace). Answers the best results, each an id for " +
        "fetch, a title and a url. When next_cursor is set, only part of the files were " +
        "searched: search again with it for the rest.",
      input: args({ query: str("The words to look for") },
                  { cursor: str("A next_cursor a search answered") }) },
    { name: "fetch", door: "fileFetch", capability: "read_files",
      title: "Fetch file", ...READ,
      description: "Use this to read one of the user's TinyAssets files a search found, " +
        "by its id, the file's path: its title, its text (as much as one answer holds; " +
        "when metadata.truncated, file_read reads on from metadata.next_offset), its url " +
        "and its metadata, the etag file_edit takes among them.",
      input: args({ id: str("The id a search answered: the file's path") }) },
    { name: "file_append", door: "fileAppend", capability: "write_files",
      title: "Append to file", readOnlyHint: false, destructiveHint: false,
      idempotentHint: false, openWorldHint: false,
      description: "Use this to save what the user tells you that is worth keeping, so " +
        "their other agents know it too: adds text at the end of a file in their TinyAssets " +
        "workspace, such as a directive at the end of /workspace/USER.md or a note in their " +
        "knowledge, and makes the file when it is absent. It never changes what is already " +
        "there. The text goes in exactly as given: begin it with a newline where the file " +
        "needs one. A JSON, TOML or YAML file is changed with file_edit instead.",
      input: args({ path, content: str("The text to add at the end, as UTF-8: at most " +
                                           "4 MiB") }) },
    { name: "file_write", door: "fileWrite", capability: "write_files",
      title: "Write file", ...CHANGE,
      description: "Write a whole file of the user's TinyAssets workspace: its text " +
        "replaces what is there, at the file's current etag, or \"\" to create a file that " +
        "is not there yet. A conflict answers the file as it now stands. At most 4 MiB of " +
        "text.",
      input: args({ path,
                    expected_etag: str("The file's current etag, from fetch, file_read or " +
                                       "file_stat, or \"\" to create it only when absent"),
                    content: str("The file's whole text, as UTF-8: at most 4 MiB") },
                  { mode: int("1 to make the file executable, 0 not; kept when omitted") }) },
    { name: "file_edit", door: "fileEdit", capability: "write_files",
      title: "Edit file", ...CHANGE,
      description: "Use this to correct a file of the user's TinyAssets workspace in " +
        "place, such as a directive in /workspace/USER.md that stopped being true: replaces " +
        "the one exact occurrence of old_text with new_text, at the file's current etag " +
        "(from fetch, file_read or file_stat). Text found nowhere, or more than once, is " +
        "refused: give enough of it to be unique. A conflict answers the file as it now " +
        "stands.",
      input: args({ path, old_text: str("The exact text to replace: it occurs once"),
                    new_text: str("What replaces it"),
                    expected_etag: str("The file's current etag") }) },
    { name: "file_history", door: "fileHistory", capability: "read_files",
      title: "File history", ...READ, description: "List a path's kept versions, newest first.",
      input: args({ path, limit: int("At most this many versions") },
                  { before: int("Versions before this one") }) },
    { name: "file_usage", door: "fileUsage", capability: "read_files",
      title: "File usage", ...READ, description: "Read the owner's file usage and quotas.",
      input: args({}) },
    { name: "file_mkdir", door: "fileMkdir", capability: "write_files",
      title: "Make directory", readOnlyHint: false, destructiveHint: false,
      idempotentHint: true, openWorldHint: false,
      description: "Make a directory against the path's current etag.",
      input: args({ path, expected_etag: etag }) },
    { name: "file_remove", door: "fileRemove", capability: "write_files",
      title: "Remove file", ...CHANGE,
      description: "Remove a file or directory against its current etag.",
      input: args({ path, expected_etag: str("The entry's current etag, from file_stat, " +
                                             "fetch or file_read"),
                    recursive: bool("Whether a directory's contents go too") }) },
    { name: "file_rename", door: "fileRename", capability: "write_files",
      title: "Rename file", ...CHANGE,
      description: "Rename a file or directory against its current etag.",
      input: args({ from: path, to: path,
                    expected_etag: str("The entry's current etag, from file_stat, fetch or " +
                                       "file_read"),
                    replace: bool("Whether an entry at the new path is replaced") }) },
    { name: "file_restore", door: "fileRestore", capability: "write_files",
      title: "Restore file version", ...CHANGE,
      description: "Make a kept version of a file its current content.",
      input: args({ path, version: int("The kept version to restore"),
                    expected_etag: str("The path's current etag, or \"\" when no file is " +
                                       "there now") }) },
    { name: "converse", door: "agentSubmit", capability: "converse",
      title: "Talk with an agent", ...CHANGE,
      description: "Send a message to one of the owner's agents: a run of its engine in a new " +
        "conversation, or in one of yours by conversation_id. A retry with the same " +
        "client_send_id is the same run. Waits up to wait_ms for the run to end, then " +
        "answers the run as it stands; run_status reads it later.",
      input: args({ message: str("The message"),
                    client_send_id: str("Your id for this message, for retries: letters, " +
                                        "digits, - and _") },
                  { agent_id: str("Which of the owner's agents: main when omitted"),
                    conversation_id: str("A conversation of yours to continue"),
                    wait_ms: int("How long to wait for the run to end, in ms: at most 60000") }) },
    { name: "run_graph", door: "agentRun", capability: "run_graph",
      title: "Run an agent", ...CHANGE,
      description: "Launch one of the owner's agents on an input, in a fresh conversation of " +
        "its own: now, at a time, or at the times a cron names. Answers the run, or the " +
        "schedule, at once.",
      input: args({ agent: str("Which of the owner's agents, such as main"),
                    input: str("What the agent is asked to do") },
                  { at: num("When to run it, in seconds since the epoch"),
                    cron: str("Run it at the times this names: minute, hour, day of the " +
                              "month, month and day of the week, in UTC") }) },
    { name: "run_status", door: "agentStatus", capability: null,
      title: "Run status", ...READ,
      description: "Read one of your runs, its state and its result once done, or one of " +
        "your schedules.",
      input: args({}, { run_id: str("The run's id"), schedule_id: str("The schedule's id") }) },
    { name: "run_cancel", door: "agentCancel", capability: null,
      title: "Cancel run", readOnlyHint: false, destructiveHint: true, idempotentHint: true,
      openWorldHint: false,
      description: "Cancel one of your runs (its engine is stopped), or one of your schedules.",
      input: args({}, { run_id: str("The run's id"), schedule_id: str("The schedule's id") }) },
    { name: "code", door: "codeRun", capability: null,
      title: "Run a script of tool calls", ...CHANGE,
      description: "Run one JavaScript script that calls your other tools here as " +
        "`await tools.<name>(args)`, each held to your grant, and answer its return value " +
        "and output: many tool calls in one. Its effects are the effects of the tools it " +
        "calls. Only while the owner's policy turns code mode on; a script past its time " +
        "or compute bound is stopped.",
      input: args({ script: str("The script: the body of an async function") }) },
  ];
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

/** Every tool name of the table, in its order. */
export function toolNames(): readonly string[] {
  return toolTable().map((tool) => tool.name);
}

/** The table's tools `names` lists, in the table's order; an unknown name is a bug. */
export function toolsNamed(names: readonly string[]): readonly Tool[] {
  const table = toolTable();
  const unknown = names.filter((name) => !table.some((tool) => tool.name === name));
  if (unknown.length > 0) {
    throw new Error(`no tool named ${unknown.join(", ")}`);
  }
  return table.filter((tool) => names.includes(tool.name));
}

/** Whether a door takes a request argument after its binding. */
export function takesRequest(tool: Tool): boolean {
  return Object.keys((tool.input["properties"] ?? {}) as object).length > 0;
}

/**
 * Call the tool's door on the bound object. The arguments go as the door's request,
 * which the object checks field by field; a door that takes none gets none.
 */
export function callDoor(stub: DurableObjectStub<OwnerDO>, binding: Binding, tool: Tool,
                         request: unknown): Promise<unknown> {
  // An RPC stub's method is called as itself: `call` and `apply` would be RPC names.
  const door = stub[tool.door] as unknown as (...args: unknown[]) => Promise<unknown>;
  return Reflect.apply(door, stub, takesRequest(tool) ? [binding, request] : [binding]);
}

/**
 * An owner object's refusal or invalid request, as opposed to a bug. A refusal at the
 * owner's storage figure (`storage_full`, `owner/src/storage-full.ts`) is 507 Insufficient
 * Storage, so a client tells the one wall from a permission; one only open uploads cause
 * (`uploads_open`), or an append that lost to other writes every try (`file_busy`), is 409,
 * since it clears as they finish. The isolate's chunk slots in use (`busy`) is 503, retried
 * after a second.
 */
export function refusal(error: unknown):
    { status: 400 | 403 | 409 | 503 | 507; message: string } | null {
  if (error instanceof Error && (error.name === "Refused" || error.name === "Invalid")) {
    return { status: error.name === "Invalid" ? 400
               : error.message.startsWith("busy:") ? 503
               : error.message.startsWith("storage_full:") ? 507
               : error.message.startsWith("uploads_open:")
                 || error.message.startsWith("file_busy:") ? 409 : 403,
             message: error.message };
  }
  return null;
}
