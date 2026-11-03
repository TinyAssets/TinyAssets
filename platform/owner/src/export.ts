/**
 * The owner export (ADR-018; slice P): everything the owner holds, in one format with the
 * nightly manifest (`backup.ts`): a manifest of every row and every chunk's hash, read a
 * page at a time from any page's cursor, and the chunks' bytes fetched by hash
 * (`exportChunks`), so a client that drops resumes at a page or a chunk, whatever the size.
 *
 * **Format `tinyassets-export`, version 1.** Newline-delimited JSON (one object a line,
 * UTF-8), in this order:
 * 1. `{"t":"header","format":"tinyassets-export","version":1,"schema":<n>,"owner":"<id>",
 *    "at":<ms>,"clock":<n>}`: once, first; `schema` is the owner schema's migration count,
 *    which says what each table's columns are (`schema.ts`); `clock` the owner's change
 *    clock the stream is pinned to (below). An export's header also names its `export_id`
 *    and `ends_at` (below).
 * 2. `{"t":"row","table":"<name>","row":{<column>:<value>,...}}`: every row of every table
 *    in {@link exported}, a table at a time in that order, each in its key's order. A value
 *    is JSON's number, string or null; bytes (a small file's inline content) are
 *    `{"$base64":"<standard base64>"}`. Each table's columns are those `exported` lists.
 * 3. `{"t":"chunk","sha256":"<hex>","size":<n>}`: every chunk some content still names
 *    (files, their kept versions, and placed bodies: ADR-035 bodies are files-plane
 *    contents), once each, by hash. Its bytes are fetched by that hash (`exportChunks`, the
 *    owner surface's `export/chunks`), checked against it.
 * 4. `{"t":"end"}`: once, last. A stream without it is incomplete.
 *
 * A file's bytes are its content's: `file_entries` and `file_history` name a `content_id`;
 * `file_contents` holds a small one `inline`, else `file_content_chunks` lists its chunks
 * (`idx` order) and section 3 their bytes. A row's or a message's placed body is a content
 * the same way.
 *
 * **Resumable, and one snapshot.** The stream is read a page at a time (`ownerExport`), each
 * page ending with its cursor (`{"t":"cursor","cursor":"<opaque>"}`, the doors' line) unless
 * it ended the stream; asking with that cursor goes on from the next row. A cursor is the
 * section and the last key read, so it never skips or repeats a row; each page is read in
 * one unit. The stream is pinned to the owner's change clock (`owner_backup_state.clock`,
 * which every unit that changes the owner moves: `backup.ts` `mark`), read when it opens and
 * named in its header: a later page whose clock moved is refused (`EXPORT_CHANGED`), so a
 * stream that ends is the owner as it stood at one moment, never a torn one (an entry naming
 * a content the stream no longer carries). The nightly manifest is pinned the same way.
 *
 * **Its chunks.** An export lives `EXPORT_LIFE_S` from its first page (`owner_exports` names
 * it; nothing holds chunks for it), under `GC_GRACE_S` (30 days, the object's point-in-time
 * recovery): every chunk its pages name was live when they were read, and a chunk no
 * content holds stays in the bucket for that grace, so every hash an export names can be
 * fetched until it ends. The one exception is the owner's own churn: garbage past the
 * tier's storage figure is collected early, oldest first (`files.ts` `collect`), and a
 * chunk gone that way is refused by name (`CHUNK_GONE`): the export is begun again.
 *
 * **What it never holds** (ADR-018: credentials are never exported), each table kept out
 * with its reason in {@link NOT_EXPORTED}: sealed credentials and connect flows, session
 * and ticket hashes, run and lease secrets, the bucket namespace and the wrapped data key,
 * and counters the schema's own triggers derive. A test holds every table of the schema to
 * exactly one of the two lists and every column of an exported table to its columns or its
 * omissions, so a table or column a later change adds is decided, never exported by default.
 */
import { Invalid } from "./errors";

export const EXPORT_FORMAT = "tinyassets-export";
export const EXPORT_VERSION = 1;
/** The rows' text one page gathers before it ends: well inside one RPC's 32 MiB. */
export const EXPORT_PAGE_BYTES = 1024 * 1024;
/** Rows read at a time while a page fills. */
const READ_BATCH = 256;
/**
 * A cursor's most characters: past the widest key it can hold (a file entry's parent path,
 * at most 4 KiB, and its name), as base64url JSON.
 */
export const CURSOR_CHARS = 16_384;

/** One exported table: its columns, and the key it is read in order of. */
export interface ExportTable {
  readonly columns: readonly string[];
  /** Its primary key's columns, or `rowid`. */
  readonly key: readonly string[];
  /** Columns kept out, each with why. */
  readonly omitted?: Readonly<Record<string, string>>;
}

function t(columns: string, key: string, omitted?: Record<string, string>): ExportTable {
  return Object.freeze({ columns: Object.freeze(columns.split(" ")),
                         key: Object.freeze(key.split(" ")),
                         ...(omitted === undefined ? {} : { omitted: Object.freeze(omitted) }) });
}

/**
 * The exported tables, in the stream's order: settings, files, collections, ledger, spend,
 * runs. A function, so no module holds the table: each call makes it afresh.
 */
export function exported(): Readonly<Record<string, ExportTable>> {
  return Object.freeze({
    // The owner, its tier and its settings.
    object_owner: t("only owner", "only"),
    owner_admission: t("only state reasons tier", "only"),
    owner_policy: t("only generation digest document egress_generation", "only"),
    policy_proposals: t("request_id digest document at applied_at proposer", "request_id"),
    policy_receipts: t("seq bucket kind at old_digest new_digest request_digest " +
                       "generation rule decision outcome", "seq",
                       { session_id: "the owner's session hash the change was made in, as " +
                         "owner_sessions keeps it: a credential's hash, never exported" }),
    outside_clients: t("client source mode scopes generation revoked_at name first_seen " +
                       "last_seen owner_set agents held profile revision", "client"),
    agent_cards: t("card_id client family source_ref generation authenticated_at request " +
                   "digest filed_at", "card_id"),
    connections: t("id source kind display_name provider models status version expires_at " +
                   "created_at pending_until revoked_at revoke_due revoke_tries revocable egress",
                   "id", { lease_id: "a refresh's claim token", lease_until: "its claim's end" }),
    engine_signins: t("engine signed_in models checked_at changed_at", "engine"),
    owner_notices: t("seq at type detail", "seq"),
    // Files: the tree, its kept versions, the contents and their chunks.
    file_entries: t("parent name kind version content_id size mode mtime_ms writer",
                    "parent name"),
    file_history: t("version path op kind content_id size mode writer at prune_after released",
                    "version"),
    file_temps: t("name expires_at writer", "name"),
    file_contents: t("content_id size inline refs", "content_id"),
    file_content_chunks: t("content_id idx sha256", "content_id idx"),
    file_chunks: t("sha256 size state stored refs holds gc_after holder", "sha256"),
    // Collections.
    collection_rows: t("collection row_id revision seq etag writer private body updated_at " +
                       "creator change body_bytes content_id filer", "collection row_id"),
    collection_history: t("collection key revision op body_json at writer change " +
                          "private_writer body_bytes content_id filer",
                          "collection key revision"),
    collection_seqs: t("collection seq", "collection"),
    collection_clock: t("only change", "only"),
    // The ledger: agents, messages, their bodies, recipients, receipts and readers.
    outside_agents: t("agent_id command_center_id outside_client outside_family source " +
                      "source_ref display_name delivery_method recipe_id revision " +
                      "disabled_reason created_at max_wakes_per_hour batch_window_s " +
                      "unacked_to_owner", "agent_id",
                      { wake_tat: "the doorbell's rate pacing, an internal mark" }),
    native_agents: t("agent_id agent created_at retired_at unacked_to_owner", "agent_id"),
    ledger_messages: t("seq message_id command_center_id room_id sender_kind sender_id " +
                       "client_message_id body_sha256 parent_id chain_root chain_depth " +
                       "created_at body_bytes unacked authority attended", "seq"),
    message_bodies: t("message_id room_id body created_at body_bytes content_id filer",
                      "message_id"),
    ledger_recipients: t("message_id agent_id seq state changed_at unacked", "message_id agent_id"),
    ledger_receipts: t("seq agent_id kind ref at detail_json", "seq"),
    ledger_readers: t("reader through_seq acked_at unacked", "reader"),
    // Spend.
    model_spend: t("period source cost", "period source"),
    model_reservations: t("seq id run connection_id source model state rule max_output_tokens " +
                          "est_tokens est_cost tokens cost policy_generation day month " +
                          "created_at settled_at", "seq"),
    egress_reservations: t("id lease_id epoch run source charge rule max_output_tokens " +
                           "est_tokens est_cost tokens cost day month served connection_id " +
                           "version method path generation bytes_hint opened_at beat_at state " +
                           "code ended outcome status bytes settled_at", "id",
                           { token: "the sandbox's egress token" }),
    // Runs, their inputs, schedules and conversations, and the sandbox time they took.
    runs: t("seq id agent conversation source credential model state initiator client stamp " +
            "request unattended mail cancel_requested requests tokens cost last_seq result " +
            "result_cut error code exit_code created_at started_at settled_at epoch beat_at " +
            "sandbox_ms sandbox_wait",
            "seq", { lease_id: "the sandbox lease's claim token" }),
    run_inputs: t("run prompt bytes origin", "run"),
    run_schedules: t("id agent input trigger cron next_at client stamp origin last_run " +
                     "last_error created_at", "id"),
    conversations: t("id agent source client session_id last_run created_at last_at", "id"),
  });
}

/** The tables kept out, each with why. */
export const NOT_EXPORTED: Readonly<Record<string, string>> = Object.freeze({
  connection_secrets: "sealed credentials (ADR-018: credentials are never exported)",
  connection_flows: "an OAuth connect in flight: its PKCE verifier and state",
  owner_sessions: "the owner's session hashes",
  socket_tickets: "single-use socket ticket hashes",
  run_grants: "a live run's grant and its secret's hash",
  sandbox_leases: "the sandbox leases' token hashes",
  file_space: "the bucket namespace, the wrapped data key, and counters the triggers keep",
  file_intents: "uploads in flight, not yet a file",
  file_shares: "counters the schema's triggers derive",
  file_scope_contents: "counters the schema's triggers derive",
  file_scope_chunks: "counters the schema's triggers derive",
  collection_usage: "counters the schema's triggers derive",
  collection_space: "counters the schema's triggers derive, and the change feed's floors",
  ledger_usage: "counters the schema's triggers derive",
  policy_usage: "counters the schema's triggers derive",
  proposal_shares: "counters the schema's triggers derive",
  ledger_wakes: "doorbell deliveries in flight",
  wake_callbacks: "sealed doorbell tokens (ADR-018: credentials are never exported)",
  grant_fences: "the grants' revoke fence, an internal mark",
  platform_tiers: "the platform's tier numbers as last read, not the owner's",
  owner_erasure: "an erasure in progress",
  schema_digests: "the applied migrations' digests, the start's own check",
  owner_backups: "the nightly manifest's own bookkeeping",
  owner_exports: "the exports open: each its id, its end and the clock it is pinned to",
  owner_backup_state: "the owner's change clock and the nightly manifest's own bookkeeping",
  sandbox_meter: "the sandbox meter's cursor in the sandbox Worker's log",
  sandbox_spans: "the sandbox meter's open and recent spans",
  sandbox_days: "the platform's cost meter: sandbox seconds a day",
  sandbox_billed: "the E2B executions already counted, by execution id",
});

/** The exported tables' names, in order. */
function tables(): string[] {
  return Object.keys(exported());
}

/** Where a page starts: a section and the last key read in it (null: its start). */
export interface Cursor {
  readonly section: number;
  readonly after: readonly (string | number)[] | null;
}

export const START: Cursor = Object.freeze({ section: -1, after: null });

/** A cursor as a page hands it out: opaque base64url JSON. */
export function encodeCursor(cursor: Cursor): string {
  return Buffer.from(JSON.stringify([EXPORT_VERSION, cursor.section, cursor.after]))
    .toString("base64url");
}

/** A cursor a caller handed back, checked whole: anything else is refused. */
export function decodeCursor(text: unknown): Cursor {
  if (text === null || text === undefined) {
    return START;
  }
  if (typeof text !== "string" || text.length > CURSOR_CHARS
      || !/^[A-Za-z0-9_-]+$/.test(text)) {
    throw new Invalid("an export cursor is one a page handed out");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(text, "base64url").toString("utf8"));
  } catch {
    throw new Invalid("an export cursor is one a page handed out");
  }
  if (!Array.isArray(parsed) || parsed.length !== 3 || parsed[0] !== EXPORT_VERSION
      || !Number.isSafeInteger(parsed[1]) || parsed[1] < 0 || parsed[1] > tables().length) {
    throw new Invalid("an export cursor is one a page handed out");
  }
  const section = parsed[1] as number;
  const after = parsed[2] as unknown;
  const names = tables();
  const width = section === names.length ? 1 : exported()[names[section]!]!.key.length;
  if (after !== null && (!Array.isArray(after) || after.length !== width
                         || after.some((v) => typeof v !== "string" && typeof v !== "number"))) {
    throw new Invalid("an export cursor is one a page handed out");
  }
  return { section, after: after as (string | number)[] | null };
}

/** A column's value as the stream carries it. */
function value(cell: unknown): unknown {
  if (cell instanceof ArrayBuffer) {
    return { $base64: Buffer.from(cell).toString("base64") };
  }
  return cell;
}

/** A page of the stream: its lines, the chunks it names, and where the next one starts. */
export interface Page {
  readonly lines: string[];
  /** The chunks section 3 names in this page, in order; their bytes are the caller's to add. */
  readonly chunks: { readonly sha256: string; readonly size: number }[];
  readonly next: Cursor | null;
}

/** What the header line says. */
export interface Header {
  readonly schema: number;
  readonly owner: string;
  readonly at: number;
  /** The owner's change clock the stream is pinned to. */
  readonly clock: number;
  /** An export's: its id, and when (s) it ends. */
  readonly export_id?: string;
  readonly ends_at?: number;
}

/**
 * An export's life from its first page: a week to fetch every chunk, under `GC_GRACE_S`
 * (the export test holds it there), so every chunk its pages name is still in the bucket.
 */
export const EXPORT_LIFE_S = 7 * 86_400;
/** Exports open at once; past them the oldest is ended. */
export const EXPORTS_OPEN = 16;
/** What a later page answers once the owner changed since the export's first. */
export const EXPORT_CHANGED = "changed since this export began: begin it again";

/**
 * One page from `cursor`, read in the caller's unit: at most `EXPORT_PAGE_BYTES` of rows'
 * text past the first row, or chunks up to `chunkBytes` in all (at least one), then the
 * next cursor, or null once the end line is written.
 */
export function readPage(sql: Pick<SqlStorage, "exec">, cursor: Cursor, header: Header,
                         chunkBytes: number): Page {
  const lines: string[] = [];
  const all = exported();
  const names = Object.keys(all);
  /** The section after every table: the chunks' bytes. */
  const chunksAt = names.length;
  let section = cursor.section;
  let after = cursor.after;
  let bytes = 0;
  if (section < 0) {
    lines.push(JSON.stringify({ t: "header", format: EXPORT_FORMAT, version: EXPORT_VERSION,
                                ...header }));
    section = 0;
    after = null;
  }
  while (section < chunksAt) {
    const table = names[section]!;
    const { columns, key } = all[table]!;
    const keyed = key.join(", ");
    const where = after === null ? "" : `WHERE (${keyed}) > (${key.map(() => "?").join(", ")}) `;
    const rows = sql.exec<Record<string, SqlStorageValue>>(
      `SELECT ${[...new Set([...columns, ...key])].join(", ")} FROM ${table} ${where}` +
        `ORDER BY ${keyed} LIMIT ${READ_BATCH}`, ...(after ?? [])).toArray();
    for (const row of rows) {
      const line = JSON.stringify({ t: "row", table, row: Object.fromEntries(
        columns.map((column) => [column, value(row[column])])) });
      lines.push(line);
      bytes += line.length;
      after = key.map((column) => row[column] as string | number);
      if (bytes >= EXPORT_PAGE_BYTES) {
        return { lines, chunks: [], next: { section, after } };
      }
    }
    if (rows.length === READ_BATCH) {
      continue;
    }
    section++;
    after = null;
  }
  // The chunks: each one content still names, once, by hash.
  const chunks = sql.exec<{ sha256: string; size: number }>(
    "SELECT sha256, size FROM file_chunks WHERE refs > 0 " +
      `${after === null ? "" : "AND sha256 > ? "}ORDER BY sha256 LIMIT ?`, ...(after ?? []),
    READ_BATCH).toArray();
  const taken: { sha256: string; size: number }[] = [];
  let held = 0;
  for (const chunk of chunks) {
    if (taken.length > 0 && held + chunk.size > chunkBytes) {
      return { lines, chunks: taken, next: { section: chunksAt, after: [taken.at(-1)!.sha256] } };
    }
    taken.push(chunk);
    held += chunk.size;
  }
  if (chunks.length === READ_BATCH) {
    return { lines, chunks: taken, next: { section: chunksAt, after: [taken.at(-1)!.sha256] } };
  }
  return { lines, chunks: taken, next: null };
}

/** A chunk's line: its bytes as base64, or none for the manifest. */
export function chunkLine(chunk: { sha256: string; size: number }, bytes?: Uint8Array): string {
  return JSON.stringify({ t: "chunk", sha256: chunk.sha256, size: chunk.size,
                          ...(bytes === undefined ? {}
                            : { data: Buffer.from(bytes).toString("base64") }) });
}

/** The stream's last line. */
export const END_LINE = '{"t":"end"}';
