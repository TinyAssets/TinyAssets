/**
 * The external tools (research §4.3): one tool per outside-callable owner door, served on
 * `/mcp` and as `POST /api/tools/<name>`.
 *
 * Each row names the `OwnerDO` door it calls, the scope capability that door checks, a
 * title, and all four hints as explicit booleans: the three OpenAI's directory requires
 * (`readOnlyHint`, `destructiveHint`, `openWorldHint`) and `idempotentHint`, as the
 * Python set it. `openWorldHint` is false for every tool confined to the owner's own
 * account, and true for the two that hand work to the owner's agent, `converse` and
 * `run_graph`: the agent acts on outside services through its connections and egress, as
 * the Python's annotations declared (`mcp_tool_annotations.py`). `room_post` is destructive
 * because a send cannot be taken back; `converse` and `run_graph` because the agent's
 * effects cannot be either, and neither is idempotent (a retry of `converse` with the same
 * `client_send_id` is deduplicated, but a new id repeats the effects). `run_status` reads;
 * `run_cancel` is destructive (it ends work) and idempotent (a second cancel ends nothing).
 * No tool selects an operation (no `method`, `operation`, `tool` or `target` field):
 * each operation is its own tool, which both directories require.
 *
 * The model's own tools (`read`, `write`, `edit`, `bash`, `ta`) are pi's, inside the
 * owner's object (ADR-032 Decision 4), and are never served here. `guards/tools.test.ts`
 * holds every row to these rules and to a real outside-callable door, and names the
 * outside-callable doors without a tool yet, each with the slice that owes it.
 *
 * Long agent work is not an MCP task: the MCP SDK's `createMcpHandler` 2.3.1 serves no
 * part of the Tasks extension (on 2026-07-28 it strips `capabilities.tasks` and a tool's
 * `execution.taskSupport` from what it sends, and has no `tasks/get` or `tasks/cancel`
 * handler of its own), so `converse` answers `running` with an `operation_id` past its
 * bounded wait, and a client polls `run_status` and cancels with `run_cancel`. When the SDK
 * serves the extension, its task id is the operation id, `tasks/get` is `agentStatus` and
 * `tasks/cancel` is `agentCancel`.
 */
import type { Binding, OwnerDO } from "../../owner/src/owner-do";

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
/** Work handed to the owner's agent, which acts on outside services too. */
const AGENT_WORK = Object.freeze({ readOnlyHint: false, destructiveHint: true,
                                   idempotentHint: false, openWorldHint: true } as const);

/** The table, in the order `tools/list` serves it. */
export function toolTable(): readonly Tool[] {
  const collection = str("The collection's name");
  const row = str("The row's id");
  const path = str("An absolute path in the owner's files, such as /workspace/notes.md");
  const etag = orNull("The entry's current etag, or null to create it");
  const operation = str("The operation_id converse or run_graph answered");
  return [
    { name: "mailbox_read", door: "mailboxRead", capability: "read_graph",
      title: "Read mailbox", ...READ,
      description: "Read this agent's unacknowledged mailbox messages, oldest first.",
      input: args({ limit: int("At most this many messages") },
                  { max_bytes: int("At most this many body bytes in all"),
                    pad_bytes: int("Pad the page to this many bytes") }) },
    { name: "mailbox_ack", door: "mailboxAck", capability: "write_graph",
      title: "Acknowledge mailbox", readOnlyHint: false, destructiveHint: false,
      idempotentHint: true, openWorldHint: false,
      description: "Acknowledge this agent's mailbox messages through a sequence number.",
      input: args({ through: int("The last sequence number read") }) },
    { name: "room_post", door: "roomPost", capability: "write_graph",
      title: "Post to room", ...CHANGE,
      description: "Post a message to a room of the owner's account.",
      input: args({ room_id: str("The room's id"),
                    client_message_id: str("Your id for this message, for retries"),
                    body: str("The message text"),
                    parent_id: orNull("The message replied to, or null") }) },
    { name: "agents_list", door: "outsideAgents", capability: null,
      title: "List agents", ...READ,
      description: "List the agents registered for this connection.",
      input: args({}) },
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
      description: "Read one row of a collection.",
      input: args({ collection, row_id: row }) },
    { name: "collection_list", door: "collectionPull", capability: "read_graph",
      title: "List collection rows", ...READ,
      description: "List a collection's rows changed after a revision.",
      input: args({ collection, after: int("Rows changed after this revision"),
                    limit: int("At most this many rows") }) },
    { name: "collection_history", door: "collectionHistory", capability: "read_graph",
      title: "Collection row history", ...READ,
      description: "Read the kept history of one collection row.",
      input: args({ collection, row_id: row }) },
    { name: "collection_write", door: "collectionWrite", capability: "write_graph",
      title: "Write collection row", ...CHANGE,
      description: "Write one collection row against its current etag.",
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
      title: "List directory", ...READ, description: "List a directory's entries by name.",
      input: args({ dir: path, limit: int("At most this many entries") },
                  { after: str("Entries after this name") }) },
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
      input: args({ path, expected_etag: etag,
                    recursive: bool("Whether a directory's contents go too") }) },
    { name: "file_rename", door: "fileRename", capability: "write_files",
      title: "Rename file", ...CHANGE,
      description: "Rename a file or directory against its current etag.",
      input: args({ from: path, to: path, expected_etag: etag,
                    replace: bool("Whether an entry at the new path is replaced") }) },
    { name: "file_restore", door: "fileRestore", capability: "write_files",
      title: "Restore file version", ...CHANGE,
      description: "Make a kept version of a file its current content.",
      input: args({ path, version: int("The kept version to restore"), expected_etag: etag }) },
    { name: "converse", door: "agentSubmit", capability: "converse",
      title: "Talk with your agent", ...AGENT_WORK,
      description: "Send a message to one of the owner's agents in your conversation with it, " +
        "and wait a bounded time for its reply. The agent can use the owner's files and " +
        "connected services, so a message can have effects; a retry with the same " +
        "client_send_id is the same message while your conversation lasts (one idle for a " +
        "week is dropped, and a retry after that runs again). Past the wait it answers " +
        "running with an operation_id, which run_status reads.",
      input: args({ message: str("The message"),
                    client_send_id: str("Your id for this message, for retries: letters, " +
                                        "digits, - and _") },
                  { agent_id: str("Which of the owner's agents: main when omitted"),
                    steer: bool("Join the agent's running turn after its current step"),
                    wait_ms: int("How long to wait for the reply, in ms: at most 60000") }) },
    { name: "run_graph", door: "agentRun", capability: "run_graph",
      title: "Run an agent", ...AGENT_WORK,
      description: "Launch one of the owner's agents on an input, in a fresh conversation of " +
        "its own, now or at a time. Answers at once with an operation_id, which run_status " +
        "reads and run_cancel ends.",
      input: args({ agent: str("Which of the owner's agents, such as main"),
                    input: str("What the agent is asked to do") },
                  { at: num("When to run it, in seconds since the epoch: now when omitted") }) },
    { name: "run_status", door: "agentStatus", capability: null,
      title: "Agent run status", ...READ,
      description: "Read how one of your agent operations stands, and its answer once done.",
      input: args({ operation_id: operation }) },
    { name: "run_cancel", door: "agentCancel", capability: null,
      title: "Cancel agent run", readOnlyHint: false, destructiveHint: true,
      idempotentHint: true, openWorldHint: false,
      description: "Cancel one of your agent operations: withdraw it, or stop the run it joined.",
      input: args({ operation_id: operation }) },
  ];
}

/** The tool named `name`, or undefined. */
export function toolNamed(name: string): Tool | undefined {
  return toolTable().find((tool) => tool.name === name);
}

/** Whether a door takes a request argument after its binding. */
function takesRequest(tool: Tool): boolean {
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

/** An owner object's refusal or invalid request, as opposed to a bug. */
export function refusal(error: unknown): { status: 400 | 403; message: string } | null {
  if (error instanceof Error && (error.name === "Refused" || error.name === "Invalid")) {
    return { status: error.name === "Refused" ? 403 : 400, message: error.message };
  }
  return null;
}
