/**
 * The owner object's schema, migrated on wake.
 *
 * `schema_version` holds one row: how many of `MIGRATIONS` this object has applied.
 * The constructor applies the rest in order inside `blockConcurrencyWhile`, each in
 * its own transaction with its version bump, so a crash mid-way leaves a whole number
 * of migrations applied. A stored version past this code's list stops the object
 * loudly: code older than its data never runs against it.
 *
 * A migration is append-only. Once deployed it never changes; a new shape is a new
 * entry at the end. Nothing is deployed yet, so every migration is still edited in place:
 * ADR-034's deletion slice rewrote the ones pi's loop had added (its threads, turns and
 * scheduled work) out of the list.
 *
 * Where a body lives is the placement rule (ADR-035): a collection row's, a history entry's
 * or a message's body of more than `PLACE_BYTES` UTF-8 bytes is never in its row. It is
 * content of the files plane, in R2, which the row names by its `content_id`; the `CHECK`s
 * hold every inline body within `PLACE_BYTES`, and the triggers of migration 14 count each
 * reference on the content, so the files plane's quota, garbage collection and erasure
 * cover the placed bytes.
 */
import { CRON_MAX_CHARS } from "./cron";
import { GC_GRACE_S } from "./files";
import type { OwnerStorage } from "./gate";
import {
  CHUNK_BYTES, CHUNK_ROW_BYTES, CLIENT_WRITER, CLIENTS, CONTENT_BYTES, ENTRY_BYTES,
  HISTORY_BYTES as FILE_HISTORY_BYTES, INDEX_KEPT, OWNER_SCOPE, PLACE_BYTES, record,
  SCOPE_REF_BYTES, SHARE_BYTES, TABLE_KEPT, TEMP_BYTES,
} from "./manifest";
import { DEFAULT_POLICY_DIGEST, DEFAULT_POLICY_JSON, FILE_DEFAULTS } from "./policy";
import {
  AUTHORITY_CHARS, CONVERSATION_ID_CHARS, CONVERSATIONS_KEPT, ENGINE_SIGNINS, ERROR_BYTES,
  INPUT_BYTES, MESSAGE_ID_CHARS, NATIVE_ID_CHARS, QUEUE_BYTES, QUEUE_ROWS, RESULT_BYTES,
  RUN_ID_CHARS, SCHEDULE_ID_CHARS, SCHEDULES_KEPT, SIGNIN_LIST_CHARS, SIGNIN_MODELS_CHARS,
} from "./run-rows";
import { SOCKET_TICKETS } from "./socket-shape";
import { RUNS_CEILING } from "./tier";

export type Migration = readonly string[];

/**
 * The owner's admission row (`admission.ts`): one row, `admitted`, `suspended` for one or
 * both of its reasons, or `erased`, the tombstone the erasure leaves as its last step. Its
 * reasons are a sorted JSON list, empty unless suspended. The erasure makes the table again
 * in the wiped database, so it is spelled once, here.
 */
export const ADMISSION_TABLE = "CREATE TABLE owner_admission (only INTEGER PRIMARY KEY " +
  "CHECK (only = 1), state TEXT NOT NULL CHECK (state IN ('admitted', 'suspended', " +
  "'erased')), reasons TEXT NOT NULL CHECK (reasons IN ('[]', '[\"abuse\"]', " +
  "'[\"billing\"]', '[\"abuse\",\"billing\"]')), " +
  // The owner's tier (ADR-034 decision 8), which only the platform sets (`tierSet`).
  "tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'paid')), " +
  "CHECK ((state = 'suspended') = (reasons != '[]')))";

/** A text column's bytes. */
const TEXT_BYTES = (column: string) => `length(CAST(${column} AS BLOB))`;
/** A column's bytes, nothing when null. */
const OR_NONE = (column: string) => `coalesce(${TEXT_BYTES(column)}, 0)`;
/** An integer's or a real's most bytes in a record. */
const NUMBER = 9;
/**
 * What one collection row takes in the object, laid out as SQLite lays it (`manifest.ts`'s
 * `record`): its own record, its names, inline body and content id in it, and its entry in
 * each of its four indexes (its key, its seq, its writer's and its creator's). The rows are
 * bounded by these bytes (`LIMITS.maxRowsCharge`), whatever their names' lengths.
 */
export const ROW_CHARGE = (row: string) =>
  `(${record(`${TEXT_BYTES(`${row}.collection`)} + ${TEXT_BYTES(`${row}.row_id`)} + ` +
             `${TEXT_BYTES(`${row}.etag`)} + ${TEXT_BYTES(`${row}.writer`)} + ` +
             `${TEXT_BYTES(`${row}.body`)} + ${TEXT_BYTES(`${row}.creator`)} + ` +
             `${OR_NONE(`${row}.content_id`)} + ${TEXT_BYTES(`${row}.filer`)} + ` +
             `${6 * NUMBER} + 1`, TABLE_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${row}.collection`)} + ${TEXT_BYTES(`${row}.row_id`)} + ${NUMBER}`,
            INDEX_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${row}.collection`)} + ${2 * NUMBER}`, INDEX_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${row}.collection`)} + ${TEXT_BYTES(`${row}.writer`)} + ${NUMBER}`,
            INDEX_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${row}.collection`)} + ${TEXT_BYTES(`${row}.creator`)} + ${NUMBER}`,
            INDEX_KEPT)})`;
/** A collection's high-water seq row: its record and its key's index entry. */
export const SEQ_CHARGE = (seq: string) =>
  `(${record(`${TEXT_BYTES(`${seq}.collection`)} + ${NUMBER}`, TABLE_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${seq}.collection`)} + ${NUMBER}`, INDEX_KEPT)})`;
/** A writer's share counter (`collection_usage`, kept as an index's record). */
export const USAGE_CHARGE = (usage: string) =>
  record(`${TEXT_BYTES(`${usage}.who`)} + ${NUMBER}`, INDEX_KEPT);
/**
 * What one collection history entry takes in the object, laid out as SQLite lays it: its
 * own record (names, op, inline body, content id, numbers), its primary key's index entry,
 * and its entry in the age index the prune reads. The history is bounded by these bytes
 * (`LIMITS.maxHistoryCharge`), not by rows times revisions.
 */
export const HISTORY_CHARGE = (entry: string) =>
  `(${record(`${TEXT_BYTES(`${entry}.collection`)} + ${TEXT_BYTES(`${entry}.key`)} + ` +
             `${TEXT_BYTES(`${entry}.op`)} + ${TEXT_BYTES(`${entry}.writer`)} + ` +
             `${TEXT_BYTES(`${entry}.body_json`)} + ${OR_NONE(`${entry}.content_id`)} + ` +
             `${TEXT_BYTES(`${entry}.filer`)} + ${4 * NUMBER}`, TABLE_KEPT)} + ` +
  `${record(`${TEXT_BYTES(`${entry}.collection`)} + ${TEXT_BYTES(`${entry}.key`)} + ` +
            `${2 * NUMBER}`, INDEX_KEPT)} + ${record(`${2 * NUMBER}`, INDEX_KEPT)})`;
/** Move the collections' own counters (`collection_space`) by `charge`, times `sign`. */
const SPACE = (column: "charge" | "rows_charge", sign: string, charge: string) =>
  `UPDATE collection_space SET ${column} = ${column} ${sign} ${charge};`;
/**
 * Charge a row to its outside writer's share (`collection_usage`: its own, `client:<id>`,
 * and all outside writers', `*`), or credit it; the owner's own rows count in the owner's
 * total alone.
 */
const ROW_SHARE = (row: string, sign: "+" | "-") => sign === "+"
  ? "INSERT INTO collection_usage (who, charge) SELECT who, " + ROW_CHARGE(row) +
    ` FROM (SELECT '${CLIENTS}' AS who UNION ALL SELECT ${row}.filer) WHERE ${row}.filer ` +
    `GLOB '${CLIENT_WRITER}' ON CONFLICT (who) DO UPDATE SET charge = charge + excluded.charge;`
  : `UPDATE collection_usage SET charge = charge - ${ROW_CHARGE(row)} WHERE ${row}.filer ` +
    `GLOB '${CLIENT_WRITER}' AND who IN ('${CLIENTS}', ${row}.filer);`;
/**
 * A body's columns (ADR-035): inline, its bytes counted, or placed past `PLACE_BYTES`, the
 * row's text empty and its content named.
 */
const PLACEMENT = (body: string) =>
  `CHECK (CASE WHEN content_id IS NULL THEN body_bytes = length(CAST(${body} AS BLOB)) ` +
  `ELSE ${body} = '' AND body_bytes > ${PLACE_BYTES} AND length(content_id) = 64 END)`;
/** A row's files scope (`filer`): the owner's, or an outside writer's `client:<id>`. */
const FILER = `filer TEXT NOT NULL CHECK (filer = '${OWNER_SCOPE}' OR (length(filer) = 71 ` +
  `AND filer GLOB '${CLIENT_WRITER}'))`;
/**
 * Count a row's reference to placed content (`+`), or take it out (`-`): in its writer's
 * files scope first, as a file entry's (`file_scope_contents`, so `client_share` and
 * `owner_reserve` hold), then on the content itself; taken out in the other order, so a
 * credit reads the content it was charged for.
 */
const PLACED = (row: string, sign: "+" | "-") => sign === "+"
  ? `UPDATE file_contents SET refs = refs + 1 WHERE content_id = ${row}.content_id; ${
    CONTENT_REF(row, "+", "filer")}`
  : `${CONTENT_REF(row, "-", "filer")} UPDATE file_contents SET refs = refs - 1 ` +
    `WHERE content_id = ${row}.content_id;`;

/**
 * Move a grant row's class, times `sign`: set by the owner (counted against the owner's
 * bound), or free to make room (nothing holds it).
 */
const CLIENT_USAGE = (row: string, sign: string) =>
  `UPDATE policy_usage SET owner_clients = owner_clients ${sign} (${row}.owner_set = 1), ` +
  `free_clients = free_clients ${sign} (${row}.owner_set = 0 AND ${row}.agents = 0 ` +
  `AND ${row}.held = 0);`;
/** Count a row that holds its client's grant row (`+`), or release it (`-`). */
const HOLD = (row: string, sign: string) =>
  `UPDATE outside_clients SET held = held ${sign} 1 WHERE client = ${row}.client;`;
/** Count a pending proposal in its proposer's share (`+`), or take it out (`-`). */
const SHARE = (row: string, sign: string) => sign === "+"
  ? `INSERT INTO proposal_shares (proposer, pending) SELECT ${row}.proposer, 1 ` +
    `WHERE ${row}.applied_at IS NULL ON CONFLICT (proposer) DO UPDATE SET ` +
    "pending = pending + 1;"
  : `UPDATE proposal_shares SET pending = pending - 1 WHERE proposer = ${row}.proposer ` +
    `AND ${row}.applied_at IS NULL;`;

/** The chunk states whose bytes count against the owner's quota. */
const COUNTED = "('uploading', 'live')";
/** Move `file_space.chunk_bytes` by a chunk row's size, when its state counts. */
const CHUNK_USAGE = (sign: string, row: string) =>
  `chunk_bytes = chunk_bytes ${sign} ${row}.size * (${row}.state IN ${COUNTED});`;
/** What a model reservation row holds against the ceilings, in µ$. */
const HELD = (row: string) =>
  `(CASE WHEN ${row}.state IN ('reserved', 'dispatched', 'unknown') THEN ${row}.est_cost ` +
  `WHEN ${row}.state IN ('succeeded', 'failed') THEN coalesce(${row}.cost, 0) ELSE 0 END)`;
/** Add `amount` to the row's source's day and month in `model_spend`. */
const SPEND = (row: string, amount: string) =>
  "INSERT INTO model_spend (period, source, cost) VALUES " +
  `('d:' || ${row}.day, ${row}.source, ${amount}), ` +
  `('m:' || ${row}.month, ${row}.source, ${amount}) ` +
  "ON CONFLICT (period, source) DO UPDATE SET cost = cost + excluded.cost;";
/**
 * What a sandbox call's charge holds (migration 11): its estimate while held or unknown,
 * what it used once settled, as a model reservation's (`HELD`).
 */
const CHARGED = (row: string) =>
  `(CASE WHEN ${row}.charge IN ('held', 'unknown') THEN ${row}.est_cost ` +
  `WHEN ${row}.charge IN ('succeeded', 'failed') THEN coalesce(${row}.cost, 0) ELSE 0 END)`;
const CHARGED_TOKENS = (row: string) =>
  `(CASE WHEN ${row}.charge IN ('held', 'unknown') THEN ${row}.est_tokens ` +
  `WHEN ${row}.charge IN ('succeeded', 'failed') THEN coalesce(${row}.tokens, 0) ELSE 0 END)`;
/** Move the row's run's counters (`runs`, migration 12) by these amounts. */
const RUN = (row: string, requests: string, tokens: string, cost: string) =>
  `UPDATE runs SET requests = requests + ${requests}, tokens = tokens + (${tokens}), ` +
  `cost = cost + (${cost}) WHERE id = ${row}.run;`;
type ShareCounter = "entries" | "history" | "chunk_bytes" | "inline_bytes" | "manifest_bytes";
const SHARE_COUNTERS: readonly ShareCounter[] =
  Object.freeze(["entries", "history", "chunk_bytes", "inline_bytes", "manifest_bytes"]);
/** Which share rows a charge goes to: the rows to charge, as a query, and as a list. */
interface Scopes {
  readonly rows: string;
  readonly list: string;
}
/**
 * The scopes the rows of `writer` (a writer, or a chunk's holder) count in: an outside
 * client's own and all clients' (`*`), else the owner's (`owner`).
 */
const SCOPES = (writer: string): Scopes => ({
  rows: `(SELECT '${CLIENTS}' AS who WHERE ${writer} GLOB '${CLIENT_WRITER}' UNION ALL ` +
    `SELECT ${writer} WHERE ${writer} GLOB '${CLIENT_WRITER}' UNION ALL ` +
    `SELECT '${OWNER_SCOPE}' WHERE ${writer} NOT GLOB '${CLIENT_WRITER}')`,
  list: `(CASE WHEN ${writer} GLOB '${CLIENT_WRITER}' THEN '${CLIENTS}' ELSE '${OWNER_SCOPE}' ` +
    `END, ${writer})`,
});
/** One scope, by name. */
const SCOPE = (scope: string): Scopes => ({ rows: `(SELECT ${scope} AS who)`, list: `(${scope})` });
/**
 * Move share rows by `amounts`, charged (`+`) or credited (`-`). A charge makes a client's
 * row on its first; a credit only ever finds the row its charge made (the checks hold the
 * counts at zero or more, and a test recounts them from the rows).
 */
const SHARE_USAGE = (scopes: Scopes, sign: "+" | "-",
                     amounts: Partial<Record<ShareCounter, string>>) => {
  const moved = SHARE_COUNTERS.filter((counter) => amounts[counter] !== undefined);
  return sign === "-"
    ? `UPDATE file_shares SET ${moved.map((counter) =>
        `${counter} = ${counter} - (${amounts[counter]})`).join(", ")} ` +
      `WHERE who IN ${scopes.list};`
    : `INSERT INTO file_shares (who, ${SHARE_COUNTERS.join(", ")}) SELECT who, ${
        SHARE_COUNTERS.map((counter) => amounts[counter] ?? "0").join(", ")} ` +
      `FROM ${scopes.rows} WHERE 1 ON CONFLICT (who) DO UPDATE SET ${moved.map((counter) =>
        `${counter} = ${counter} + excluded.${counter}`).join(", ")};`;
};
/** A row's writer's scopes, by the column naming its files scope writer. */
const WRITER = (row: string, column = "writer"): Scopes => SCOPES(`${row}.${column}`);
/**
 * Count a content reference of a row (an entry's or a history row's) in each scope its
 * writer's rows count in (`+`), or take it out (`-`).
 */
const CONTENT_REF = (row: string, sign: "+" | "-", column = "writer") => sign === "+"
  ? "INSERT INTO file_scope_contents (scope, content_id, refs) SELECT who, " +
    `${row}.content_id, 1 FROM ${WRITER(row, column).rows} WHERE ${row}.content_id IS NOT NULL ` +
    "ON CONFLICT (scope, content_id) DO UPDATE SET refs = refs + 1;"
  : `UPDATE file_scope_contents SET refs = refs - 1 WHERE content_id = ${row}.content_id ` +
    `AND scope IN ${WRITER(row, column).list};`;
/** A content's bytes and its inline bytes, read from its row. */
const CONTENT_OF = (id: string, what: string) =>
  `(SELECT ${what} FROM file_contents c WHERE c.content_id = ${id})`;
/** A chunk's row bytes and size, read from its row. */
const CHUNK_OF = (sha: string, what: string) =>
  `(SELECT ${what} FROM file_chunks k WHERE k.sha256 = ${sha})`;

/**
 * Model calls open at once (`reserved` or `dispatched`), past which a reservation is
 * refused (`model_calls_full`).
 */
export const CALLS_OPEN = 1000;
/**
 * Spend counters kept (`model_spend`): a day's or a month's per source, for the days and
 * months the usage module keeps (40 and 3), each at a hundred sources, past which a new
 * source's first call in a period is refused (`model_spend_full`).
 */
export const SPEND_KEPT = 4300;
export const MIGRATIONS: readonly Migration[] = Object.freeze([
  // 1: the owner this object serves, and the one revision primitive (S34, ADR-032).
  Object.freeze([
    "CREATE TABLE object_owner (only INTEGER PRIMARY KEY CHECK (only = 1), owner TEXT NOT NULL)",
    "CREATE TABLE collection_rows (" +
      "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, " +
      "private INTEGER NOT NULL CHECK (private IN (0, 1)), " +
      // The body inline, or '' with its content placed (ADR-035); its UTF-8 bytes either way.
      `body TEXT NOT NULL CHECK (length(CAST(body AS BLOB)) <= ${PLACE_BYTES}), ` +
      "updated_at REAL NOT NULL, " +
      // Who created the row; a takeover never changes it (a clear by writer matches it).
      "creator TEXT NOT NULL, " +
      `body_bytes INTEGER NOT NULL, content_id TEXT, ${FILER}, ${PLACEMENT("body")}, ` +
      "PRIMARY KEY (collection, row_id), UNIQUE (collection, seq))",
    // A clear by writer matches either.
    "CREATE INDEX collection_writers ON collection_rows (collection, writer)",
    "CREATE INDEX collection_creators ON collection_rows (collection, creator)",
    // The high-water seq survives a clear, so a reader's cursor never goes back; a
    // collection left with no row gives its seq to `seq_floor` and goes, so the table holds
    // a row for each collection with rows, and no more.
    "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    "CREATE TABLE collection_history (" +
      "collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL, " +
      `op TEXT NOT NULL, body_json TEXT NOT NULL CHECK (length(CAST(body_json AS BLOB)) <= ${
        PLACE_BYTES}), at REAL NOT NULL, writer TEXT NOT NULL, ` +
      `body_bytes INTEGER NOT NULL, content_id TEXT, ${FILER}, ${PLACEMENT("body_json")}, ` +
      "PRIMARY KEY (collection, key, revision))",
    // The collections are bounded in what they take, laid out as SQLite lays them, whatever
    // their names: `rows_charge` is every row with its four index entries (`ROW_CHARGE`), each
    // collection's seq row and each writer's share counter, held to `maxRowsCharge` (and an
    // outside writer to its share of it); `charge` is the history's entries
    // (`HISTORY_CHARGE`), pruned oldest first past `maxHistoryCharge`. `revision_floor` is the
    // newest revision a prune let go, so a key whose entries are all gone goes on past it and
    // never repeats a revision; `seq_floor` the newest seq of a collection that was emptied,
    // so a collection made again goes on past it and no reader's cursor goes back.
    "CREATE TABLE collection_space (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "charge INTEGER NOT NULL CHECK (charge >= 0), " +
      "rows_charge INTEGER NOT NULL CHECK (rows_charge >= 0), " +
      "revision_floor INTEGER NOT NULL, seq_floor INTEGER NOT NULL)",
    "INSERT INTO collection_space VALUES (1, 0, 0, 0, 0)",
    "CREATE INDEX collection_history_by_age ON collection_history (at)",
    `CREATE TRIGGER collection_history_added AFTER INSERT ON collection_history BEGIN ${
      SPACE("charge", "+", HISTORY_CHARGE("NEW"))} END`,
    `CREATE TRIGGER collection_history_removed AFTER DELETE ON collection_history BEGIN ${
      SPACE("charge", "-", HISTORY_CHARGE("OLD"))} END`,
    "CREATE TRIGGER collection_history_kept BEFORE UPDATE ON collection_history " +
      "BEGIN SELECT RAISE(ABORT, 'a history entry is never changed'); END",
    // Each outside writer's share of the rows' charge, and all outside writers' (`*`); only
    // the triggers write it, in the statement that changes a row, and a row at zero goes.
    "CREATE TABLE collection_usage (who TEXT PRIMARY KEY CHECK (who = '*' OR " +
      `(length(who) = 71 AND who GLOB '${CLIENT_WRITER}')), ` +
      "charge INTEGER NOT NULL CHECK (charge >= 0)) WITHOUT ROWID",
    `CREATE TRIGGER collection_rows_added AFTER INSERT ON collection_rows BEGIN ${
      SPACE("rows_charge", "+", ROW_CHARGE("NEW"))} ${ROW_SHARE("NEW", "+")} END`,
    `CREATE TRIGGER collection_rows_changed AFTER UPDATE ON collection_rows BEGIN ${
      SPACE("rows_charge", "-", ROW_CHARGE("OLD"))} ${ROW_SHARE("OLD", "-")} ${
      SPACE("rows_charge", "+", ROW_CHARGE("NEW"))} ${ROW_SHARE("NEW", "+")} END`,
    `CREATE TRIGGER collection_rows_removed AFTER DELETE ON collection_rows BEGIN ${
      SPACE("rows_charge", "-", ROW_CHARGE("OLD"))} ${ROW_SHARE("OLD", "-")} END`,
    `CREATE TRIGGER collection_seqs_added AFTER INSERT ON collection_seqs BEGIN ${
      SPACE("rows_charge", "+", SEQ_CHARGE("NEW"))} END`,
    `CREATE TRIGGER collection_seqs_removed AFTER DELETE ON collection_seqs BEGIN ${
      SPACE("rows_charge", "-", SEQ_CHARGE("OLD"))} END`,
    `CREATE TRIGGER collection_usage_added AFTER INSERT ON collection_usage BEGIN ${
      SPACE("rows_charge", "+", USAGE_CHARGE("NEW"))} END`,
    `CREATE TRIGGER collection_usage_removed AFTER DELETE ON collection_usage BEGIN ${
      SPACE("rows_charge", "-", USAGE_CHARGE("OLD"))} END`,
    "CREATE TRIGGER collection_usage_emptied AFTER UPDATE OF charge ON collection_usage " +
      "WHEN NEW.charge = 0 BEGIN DELETE FROM collection_usage WHERE who = NEW.who; END",
  ]),
  // 2: the message ledger (S30, S3a, S3b), its bodies, and the W3 wake limits (ledger.ts).
  // The object is its owner, so no row carries an owner column. Every bound the ledger
  // keeps is a counter that only the triggers below write, inside the statement that
  // changes the rows it counts, and every query a door or a lane runs has an index, so
  // nothing scans the whole store.
  Object.freeze([
    "CREATE TABLE outside_agents (" +
      "agent_id TEXT PRIMARY KEY, command_center_id TEXT NOT NULL, " +
      "outside_client TEXT NOT NULL, outside_family TEXT NOT NULL, " +
      "source TEXT NOT NULL CHECK (source IN ('api_key', 'mcp')), source_ref TEXT NOT NULL, " +
      "display_name TEXT NOT NULL, delivery_method TEXT NOT NULL " +
      "CHECK (delivery_method IN ('mcp_event', 'routine_fire', 'webhook', 'a2a', 'pull')), " +
      "recipe_id TEXT, revision INTEGER NOT NULL, disabled_reason TEXT, " +
      "created_at REAL NOT NULL, " +
      // Owner policy (W3): set only by the owner's wake-limits setter, never by a
      // registration; the codespec's defaults.
      "max_wakes_per_hour INTEGER NOT NULL DEFAULT 60 " +
      "CHECK (max_wakes_per_hour BETWEEN 1 AND 60), " +
      "batch_window_s INTEGER NOT NULL DEFAULT 60 CHECK (batch_window_s BETWEEN 0 AND 3600), " +
      // Messages this agent sent that the owner has not acked yet, and their bytes: its
      // share of the owner's mailbox.
      "unacked_to_owner INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner >= 0), " +
      // The doorbell callbacks' fence: each subscribe opens the next generation, and a
      // revoke erases the callbacks at or before the generation it carries.
      // `revoked_through` is the newest generation a revoke has been scheduled for.
      "subscription INTEGER NOT NULL DEFAULT 0 CHECK (subscription >= 0), " +
      "revoked_through INTEGER NOT NULL DEFAULT 0 " +
      "CHECK (revoked_through BETWEEN 0 AND subscription), " +
      // A deleted agent whose revoke is still to run: a tombstone, disabled, counted by
      // the registry bound, and gone when its revoke settles.
      "deleted_at REAL CHECK (deleted_at IS NULL OR disabled_reason IS NOT NULL), " +
      "UNIQUE (command_center_id, outside_client, outside_family))",
    "CREATE INDEX outside_identities ON outside_agents (outside_client, outside_family)",
    // A registration's id, universe and client never change: its client's grant row counts
    // it (`outside_clients.agents`, moved only on insert and delete), and its universe is
    // where a scoped stamp acts.
    "CREATE TRIGGER outside_agents_stamped BEFORE UPDATE OF agent_id, command_center_id, " +
      "outside_client ON outside_agents WHEN NEW.agent_id IS NOT OLD.agent_id " +
      "OR NEW.command_center_id IS NOT OLD.command_center_id " +
      "OR NEW.outside_client IS NOT OLD.outside_client BEGIN " +
      "SELECT RAISE(ABORT, 'a registration never changes its id, universe or client'); END",
    // AUTOINCREMENT: a seq is never reused once its message is evicted, so no cursor
    // ever points at a different message.
    "CREATE TABLE ledger_messages (" +
      "seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, " +
      "command_center_id TEXT NOT NULL, room_id TEXT NOT NULL, " +
      "sender_kind TEXT NOT NULL CHECK (sender_kind IN ('owner', 'native', 'outside')), " +
      "sender_id TEXT NOT NULL, client_message_id TEXT NOT NULL, body_sha256 TEXT NOT NULL, " +
      "parent_id TEXT, chain_root TEXT NOT NULL, chain_depth INTEGER NOT NULL, " +
      "created_at REAL NOT NULL, " +
      // The body's size as counted, and how many of its recipient rows still hold it.
      "body_bytes INTEGER NOT NULL, unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0), " +
      // The outside client's authority the mail was sent under (its client, family, grant
      // generation and sign-in, as JSON), or none for the owner's own: what bounds the run
      // a mail to one of the owner's own agents starts (`ledger.ts`, migration 13).
      `authority TEXT CHECK (authority IS NULL OR (length(authority) <= ${AUTHORITY_CHARS} ` +
      "AND authority NOT GLOB '*[^ -~]*')), " +
      // Whether a person sent it (the owner's own call): a run it starts is attended. A
      // run's mail and a client's are not.
      "attended INTEGER NOT NULL DEFAULT 0 CHECK (attended IN (0, 1)), " +
      "UNIQUE (sender_id, client_message_id))",
    // The messages no recipient holds, oldest first: what eviction takes.
    "CREATE INDEX messages_done ON ledger_messages (seq) WHERE unacked = 0",
    // A recipient row carries its message's seq, so a reader's mail is an index range.
    // `unacked` is 1 until the reader acks past it, or its agent is disabled (released).
    // A message has one recipient (a room is one agent's dm), and the key holds it to
    // one: the recipients are bounded by their own count, the messages'.
    "CREATE TABLE ledger_recipients (" +
      "message_id TEXT NOT NULL PRIMARY KEY, agent_id TEXT NOT NULL, seq INTEGER NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('queued', 'held_cap', 'woken', 'read', " +
      "'replied', 'failed', 'unknown')), changed_at REAL NOT NULL, " +
      "unacked INTEGER NOT NULL DEFAULT 1 CHECK (unacked IN (0, 1)))",
    "CREATE INDEX recipients_by_seq ON ledger_recipients (agent_id, seq)",
    "CREATE INDEX recipients_unacked ON ledger_recipients (agent_id, seq) WHERE unacked = 1",
    "CREATE INDEX recipients_by_state ON ledger_recipients (agent_id, state)",
    "CREATE TABLE ledger_receipts (" +
      "seq INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL, kind TEXT NOT NULL " +
      "CHECK (kind IN ('wake_sent', 'wake_accepted', 'read', 'ack', 'reply', " +
      "'rate_limited', 'failed', 'claim', 'complete', 'revoked')), " +
      "ref TEXT NOT NULL, at REAL NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}')",
    "CREATE INDEX receipts_by_ref ON ledger_receipts (kind, ref, agent_id)",
    "CREATE INDEX receipts_by_agent ON ledger_receipts (agent_id)",
    // Each reader's ack cursor, and the mail it holds unacked: its mailbox's counters.
    "CREATE TABLE ledger_readers (" +
      "reader TEXT PRIMARY KEY, through_seq INTEGER NOT NULL DEFAULT 0, acked_at REAL, " +
      "unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0))",
    // The object's one schedule: each outside agent's wake, and each revoke of a
    // suppressed agent's callbacks, which the doorbell consumes on the `wakes` lane.
    "CREATE TABLE ledger_wakes (" +
      "wake_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, " +
      "kind TEXT NOT NULL DEFAULT 'wake' CHECK (kind IN ('wake', 'revoke')), " +
      "state TEXT NOT NULL " +
      "CHECK (state IN ('pending', 'sending', 'sent', 'unknown', 'rate_limited', 'failed', " +
      "'suppressed')), due REAL NOT NULL, claim_id TEXT, " +
      "attempts INTEGER NOT NULL DEFAULT 0, " +
      // The durable may-have-sent mark (the Python op journal's): set before a byte leaves.
      "marked INTEGER NOT NULL DEFAULT 0 CHECK (marked IN (0, 1)), through_seq INTEGER, " +
      // A revoke's fence: it erases only callbacks at or before this subscription.
      "generation INTEGER CHECK ((kind = 'revoke') = (generation IS NOT NULL)), " +
      "receipt_json TEXT, created_at REAL NOT NULL, settled_at REAL)",
    "CREATE UNIQUE INDEX one_open_wake ON ledger_wakes (agent_id, kind) " +
      "WHERE state IN ('pending', 'rate_limited')",
    "CREATE INDEX wakes_by_agent ON ledger_wakes (agent_id)",
    // The open rows by due time (what the claim pass reads), and the claims being sent by
    // lease (what the recover pass reads): each pass its own index.
    "CREATE INDEX wakes_open ON ledger_wakes (due) WHERE state IN ('pending', 'rate_limited')",
    "CREATE INDEX wakes_sending ON ledger_wakes (due) WHERE state = 'sending'",
    "CREATE INDEX wakes_settled ON ledger_wakes (settled_at) WHERE settled_at IS NOT NULL",
    // Each claim's time, kept one hour: what the per-agent hourly cap counts.
    "CREATE TABLE ledger_rings (agent_id TEXT NOT NULL, at REAL NOT NULL)",
    "CREATE INDEX rings_by_agent ON ledger_rings (agent_id, at)",
    // A message's body, inline or placed past `PLACE_BYTES` (ADR-035); its bytes are its
    // message's `body_bytes`.
    "CREATE TABLE message_bodies (" +
      "message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, " +
      `body TEXT NOT NULL CHECK (length(CAST(body AS BLOB)) <= ${PLACE_BYTES}), ` +
      "created_at REAL NOT NULL, body_bytes INTEGER NOT NULL CHECK (body_bytes >= 1), " +
      `content_id TEXT, ${FILER}, ${PLACEMENT("body")})`,
    // The owner-wide counters the ledger's bounds read.
    "CREATE TABLE ledger_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "agents INTEGER NOT NULL, enabled_agents INTEGER NOT NULL, messages INTEGER NOT NULL, " +
      "settled_wakes INTEGER NOT NULL, " +
      // Deleted agents kept as tombstones while a revoke of their callbacks is pending.
      "tombstones INTEGER NOT NULL)",
    "INSERT INTO ledger_usage VALUES (1, 0, 0, 0, 0, 0)",
    "CREATE TRIGGER outside_agents_added AFTER INSERT ON outside_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents + 1, " +
      "enabled_agents = enabled_agents + (NEW.disabled_reason IS NULL), " +
      "tombstones = tombstones + (NEW.deleted_at IS NOT NULL); END",
    "CREATE TRIGGER outside_agents_tombstoned AFTER UPDATE OF deleted_at ON outside_agents " +
      "WHEN (OLD.deleted_at IS NULL) != (NEW.deleted_at IS NULL) BEGIN " +
      "UPDATE ledger_usage SET tombstones = tombstones + " +
      "CASE WHEN NEW.deleted_at IS NULL THEN -1 ELSE 1 END; END",
    "CREATE TRIGGER outside_agents_enabled AFTER UPDATE OF disabled_reason ON outside_agents " +
      "WHEN (OLD.disabled_reason IS NULL) != (NEW.disabled_reason IS NULL) BEGIN " +
      "UPDATE ledger_usage SET enabled_agents = enabled_agents + " +
      "CASE WHEN NEW.disabled_reason IS NULL THEN 1 ELSE -1 END; END",
    "CREATE TRIGGER outside_agents_removed AFTER DELETE ON outside_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents - 1, " +
      "enabled_agents = enabled_agents - (OLD.disabled_reason IS NULL), " +
      "tombstones = tombstones - (OLD.deleted_at IS NOT NULL); END",
    "CREATE TRIGGER ledger_messages_added AFTER INSERT ON ledger_messages BEGIN " +
      "UPDATE ledger_usage SET messages = messages + 1; END",
    "CREATE TRIGGER ledger_messages_removed AFTER DELETE ON ledger_messages BEGIN " +
      "UPDATE ledger_usage SET messages = messages - 1; END",
    // A recipient row holds its message until it is acked or released: the message's
    // `unacked` and the reader's mailbox counters move with it. Its bytes are read from
    // its message, so a recipient row goes before its message (a NULL fails loudly).
    "CREATE TRIGGER ledger_recipient_added AFTER INSERT ON ledger_recipients " +
      "WHEN NEW.unacked = 1 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked + 1 WHERE message_id = NEW.message_id; " +
      "INSERT INTO ledger_readers (reader, unacked) VALUES (NEW.agent_id, 1) " +
      "ON CONFLICT (reader) DO UPDATE SET unacked = unacked + 1; END",
    "CREATE TRIGGER ledger_recipient_held BEFORE UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 0 AND NEW.unacked = 1 BEGIN " +
      "SELECT RAISE(ABORT, 'an acked recipient never holds its message again'); END",
    "CREATE TRIGGER ledger_recipient_released AFTER UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 1 AND NEW.unacked = 0 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = NEW.message_id; " +
      "UPDATE ledger_readers SET unacked = unacked - 1 WHERE reader = NEW.agent_id; END",
    "CREATE TRIGGER ledger_recipient_removed AFTER DELETE ON ledger_recipients " +
      "WHEN OLD.unacked = 1 BEGIN " +
      "UPDATE ledger_readers SET unacked = unacked - 1 WHERE reader = OLD.agent_id; " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = OLD.message_id; END",
    // An outside agent's message in its own dm room goes to the owner; while any recipient
    // holds it, it counts against its sender's share of the owner's mailbox. Its mail in a
    // room with one of the owner's own agents goes to that agent, and counts in its mailbox.
    "CREATE TRIGGER ledger_outside_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN NEW.sender_kind = 'outside' AND NEW.room_id = 'dm:' || NEW.sender_id " +
      "AND (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN " +
      "UPDATE outside_agents SET " +
      "unacked_to_owner = unacked_to_owner + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END " +
      "WHERE agent_id = NEW.sender_id; END",
    "CREATE TRIGGER ledger_outside_removed AFTER DELETE ON ledger_messages " +
      "WHEN OLD.sender_kind = 'outside' AND OLD.room_id = 'dm:' || OLD.sender_id " +
      "AND OLD.unacked > 0 BEGIN " +
      "UPDATE outside_agents SET unacked_to_owner = unacked_to_owner - 1 " +
      "WHERE agent_id = OLD.sender_id; END",
    "CREATE TRIGGER ledger_wakes_added AFTER INSERT ON ledger_wakes " +
      "WHEN NEW.settled_at IS NOT NULL BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes + 1; END",
    "CREATE TRIGGER ledger_wakes_settled AFTER UPDATE OF settled_at ON ledger_wakes " +
      "WHEN (OLD.settled_at IS NULL) != (NEW.settled_at IS NULL) BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes + " +
      "CASE WHEN NEW.settled_at IS NULL THEN -1 ELSE 1 END; END",
    "CREATE TRIGGER ledger_wakes_removed AFTER DELETE ON ledger_wakes " +
      "WHEN OLD.settled_at IS NOT NULL BEGIN " +
      "UPDATE ledger_usage SET settled_wakes = settled_wakes - 1; END",
  ]),
  // 3: owner policy, outside-client grants and the owner's protected sessions (N10,
  // P0-3; tasks 3.5): policy.ts, grants.ts, sessions.ts. Each bound reads `policy_usage`,
  // which only the triggers below write, and every query has an index.
  Object.freeze([
    // A session row holds the hash of the cookie the object minted, never the cookie.
    "CREATE TABLE owner_sessions (" +
      "session_hash TEXT PRIMARY KEY CHECK (length(session_hash) = 64), " +
      "created_at REAL NOT NULL, expires_at REAL NOT NULL)",
    "CREATE INDEX sessions_by_expiry ON owner_sessions (expires_at)",
    // The owner's one policy document, its digest, and a generation every change bumps.
    "CREATE TABLE owner_policy (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "generation INTEGER NOT NULL CHECK (generation >= 1), digest TEXT NOT NULL, " +
      "document TEXT NOT NULL)",
    `INSERT INTO owner_policy VALUES (1, 1, '${DEFAULT_POLICY_DIGEST}', '${DEFAULT_POLICY_JSON}')`,
    // Append-only. Each bucket is kept and listed apart, so neither an agent's proposals
    // nor a stranger's refusals can push the owner's own changes out.
    "CREATE TABLE policy_receipts (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "bucket TEXT NOT NULL CHECK (bucket IN ('owner', 'agent', 'refusal')), " +
      "kind TEXT NOT NULL CHECK (kind IN ('write', 'wake_limits', 'outside_grant', " +
      "'proposal', 'refusal')), at REAL NOT NULL, session_id TEXT, old_digest TEXT, " +
      "new_digest TEXT, request_digest TEXT, generation INTEGER NOT NULL, rule TEXT, " +
      "decision TEXT, outcome TEXT, " +
      "CHECK ((kind = 'proposal') = (bucket = 'agent') " +
      "AND (kind = 'refusal') = (bucket = 'refusal')))",
    "CREATE INDEX policy_receipts_by_bucket ON policy_receipts (bucket, seq)",
    "CREATE TRIGGER policy_receipts_kept BEFORE UPDATE ON policy_receipts " +
      "BEGIN SELECT RAISE(ABORT, 'a policy receipt is never changed'); END",
    // `proposer` is who filed it: `owner`, or the outside client's id, whose pending
    // proposals are held to a share of the set (`proposal_shares`).
    "CREATE TABLE policy_proposals (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, " +
      "document TEXT NOT NULL, at REAL NOT NULL, applied_at REAL, proposer TEXT NOT NULL)",
    "CREATE INDEX proposals_by_age ON policy_proposals (at)",
    "CREATE INDEX proposals_applied ON policy_proposals (applied_at) " +
      "WHERE applied_at IS NOT NULL",
    "CREATE TABLE proposal_shares (proposer TEXT PRIMARY KEY, " +
      "pending INTEGER NOT NULL CHECK (pending >= 0))",
    // One row per verified outside client. A row an agent holds (`agents`, kept by the
    // triggers on `outside_agents` below) or stamped work holds (`held`: open scheduled
    // work and pending agent registrations, kept by their triggers) is never deleted: it
    // is what that work is rechecked against. A row the owner set (`owner_set`: a grant
    // change or a revoke) is kept while the grant stands; a revoked one may go only by an
    // act the module makes (an API key the directory deleted, or the owner's own forget in
    // its session), and an MCP revoke's time then becomes the owner-wide fence
    // (`grant_fences`), so no credential signed in before it is ever admitted again. Any
    // other row is exactly what the client's next verified call would record again
    // (`full`, generation 1, never revoked), so it is free: the oldest free rows make room
    // for new clients.
    "CREATE TABLE outside_clients (" +
      "client TEXT PRIMARY KEY CHECK (length(client) = 64), " +
      "source TEXT NOT NULL CHECK (source IN ('mcp', 'api_key')), " +
      "mode TEXT NOT NULL CHECK (mode IN ('full', 'scoped', 'revoked')), " +
      // A grant's scopes are counted in bytes (`GRANT_LIMITS.maxScopesBytes`, which the
      // owner-bound guard holds equal), not as so many scopes of so many characters each.
      "scopes TEXT NOT NULL CHECK (length(CAST(scopes AS BLOB)) <= 65536), " +
      "generation INTEGER NOT NULL CHECK (generation >= 1), " +
      "revoked_at REAL, name TEXT NOT NULL, first_seen REAL NOT NULL, " +
      "last_seen REAL NOT NULL, " +
      "owner_set INTEGER NOT NULL DEFAULT 0 CHECK (owner_set IN (0, 1)), " +
      "agents INTEGER NOT NULL DEFAULT 0 CHECK (agents >= 0), " +
      "held INTEGER NOT NULL DEFAULT 0 CHECK (held >= 0), " +
      "CHECK (owner_set = 1 OR (source = 'mcp' AND mode = 'full' AND generation = 1 " +
      "AND revoked_at IS NULL AND scopes = '[]' AND name = '')))",
    "CREATE INDEX outside_clients_listed ON outside_clients (first_seen, client)",
    "CREATE INDEX outside_clients_free ON outside_clients (last_seen) " +
      "WHERE owner_set = 0 AND agents = 0 AND held = 0",
    "CREATE TRIGGER outside_clients_kept BEFORE DELETE ON outside_clients " +
      "WHEN OLD.agents > 0 OR OLD.held > 0 OR (OLD.owner_set = 1 AND OLD.mode != 'revoked') " +
      "BEGIN SELECT RAISE(ABORT, 'an outside client grant the owner set or an agent holds is " +
      "never deleted while it stands or holds'); END",
    // The latest MCP revoke ever forgotten: an MCP client with no row is admitted only
    // from a sign-in after it. Every path that drops a revoked MCP row moves it, and it
    // only ever rises: lowering it, or dropping its row, is refused.
    "CREATE TABLE grant_fences (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "mcp_through REAL NOT NULL CHECK (mcp_through >= 0))",
    "INSERT INTO grant_fences VALUES (1, 0)",
    "CREATE TRIGGER grant_fences_rise BEFORE UPDATE ON grant_fences " +
      "WHEN NEW.mcp_through < OLD.mcp_through OR NEW.only != OLD.only " +
      "BEGIN SELECT RAISE(ABORT, 'the revoke fence only ever rises'); END",
    "CREATE TRIGGER grant_fences_kept BEFORE DELETE ON grant_fences " +
      "BEGIN SELECT RAISE(ABORT, 'the revoke fence only ever rises'); END",
    "CREATE TRIGGER outside_clients_forgotten AFTER DELETE ON outside_clients " +
      "WHEN OLD.source = 'mcp' AND OLD.revoked_at IS NOT NULL BEGIN " +
      "UPDATE grant_fences SET mcp_through = MAX(mcp_through, OLD.revoked_at); END",
    "CREATE TABLE policy_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "sessions INTEGER NOT NULL, owner_clients INTEGER NOT NULL, " +
      "free_clients INTEGER NOT NULL, " +
      "pending_proposals INTEGER NOT NULL, applied_proposals INTEGER NOT NULL, " +
      "owner_receipts INTEGER NOT NULL, agent_receipts INTEGER NOT NULL, " +
      "refusal_receipts INTEGER NOT NULL, pending_cards INTEGER NOT NULL DEFAULT 0)",
    "INSERT INTO policy_usage (only, sessions, owner_clients, free_clients, " +
      "pending_proposals, applied_proposals, owner_receipts, agent_receipts, " +
      "refusal_receipts) VALUES (1, 0, 0, 0, 0, 0, 0, 0, 0)",
    // An MCP client's held registration (slice 3's review L2): filed through the client's
    // own verified call, so the object captured its origin, and applied by the owner in
    // its session by card id. One pending card per outside identity; it holds its client's
    // grant row and carries the authority it was filed under.
    "CREATE TABLE agent_cards (card_id TEXT PRIMARY KEY CHECK (length(card_id) = 37), " +
      "client TEXT NOT NULL CHECK (length(client) = 64), " +
      "family TEXT NOT NULL CHECK (length(family) = 64), source_ref TEXT NOT NULL, " +
      "generation INTEGER NOT NULL, authenticated_at REAL, " +
      "request TEXT NOT NULL CHECK (length(request) <= 4096), digest TEXT NOT NULL, " +
      "filed_at REAL NOT NULL, UNIQUE (client, family))",
    "CREATE INDEX agent_cards_by_age ON agent_cards (filed_at)",
    "CREATE TRIGGER owner_sessions_added AFTER INSERT ON owner_sessions BEGIN " +
      "UPDATE policy_usage SET sessions = sessions + 1; END",
    "CREATE TRIGGER owner_sessions_removed AFTER DELETE ON owner_sessions BEGIN " +
      "UPDATE policy_usage SET sessions = sessions - 1; END",
    `CREATE TRIGGER outside_clients_added AFTER INSERT ON outside_clients BEGIN ${
      CLIENT_USAGE("NEW", "+")} END`,
    "CREATE TRIGGER outside_clients_changed AFTER UPDATE OF owner_set, agents, held " +
      `ON outside_clients BEGIN ${CLIENT_USAGE("OLD", "-")} ${CLIENT_USAGE("NEW", "+")} END`,
    `CREATE TRIGGER outside_clients_removed AFTER DELETE ON outside_clients BEGIN ${
      CLIENT_USAGE("OLD", "-")} END`,
    // A client recorded after its agents or held cards were (as a direct store change
    // could leave it) still counts them.
    "CREATE TRIGGER outside_clients_held AFTER INSERT ON outside_clients BEGIN " +
      "UPDATE outside_clients SET agents = (SELECT COUNT(*) FROM outside_agents " +
      "WHERE outside_client = NEW.client), held = (SELECT COUNT(*) FROM " +
      "agent_cards WHERE client = NEW.client) WHERE client = NEW.client; END",
    "CREATE TRIGGER outside_agents_hold AFTER INSERT ON outside_agents BEGIN " +
      "UPDATE outside_clients SET agents = agents + 1 WHERE client = NEW.outside_client; END",
    "CREATE TRIGGER outside_agents_release AFTER DELETE ON outside_agents BEGIN " +
      "UPDATE outside_clients SET agents = agents - 1 WHERE client = OLD.outside_client; END",
    "CREATE TRIGGER policy_proposals_added AFTER INSERT ON policy_proposals BEGIN " +
      "UPDATE policy_usage SET pending_proposals = pending_proposals + " +
      "(NEW.applied_at IS NULL), applied_proposals = applied_proposals + " +
      `(NEW.applied_at IS NOT NULL); ${SHARE("NEW", "+")} END`,
    "CREATE TRIGGER policy_proposals_applied AFTER UPDATE OF applied_at ON policy_proposals " +
      "WHEN (OLD.applied_at IS NULL) != (NEW.applied_at IS NULL) BEGIN " +
      "UPDATE policy_usage SET " +
      "pending_proposals = pending_proposals + CASE WHEN NEW.applied_at IS NULL " +
      "THEN 1 ELSE -1 END, applied_proposals = applied_proposals + " +
      `CASE WHEN NEW.applied_at IS NULL THEN -1 ELSE 1 END; ${SHARE("OLD", "-")} ${
        SHARE("NEW", "+")} END`,
    "CREATE TRIGGER policy_proposals_removed AFTER DELETE ON policy_proposals BEGIN " +
      "UPDATE policy_usage SET pending_proposals = pending_proposals - " +
      "(OLD.applied_at IS NULL), applied_proposals = applied_proposals - " +
      `(OLD.applied_at IS NOT NULL); ${SHARE("OLD", "-")} END`,
    "CREATE TRIGGER agent_cards_added AFTER INSERT ON agent_cards BEGIN " +
      `UPDATE policy_usage SET pending_cards = pending_cards + 1; ${HOLD("NEW", "+")} END`,
    "CREATE TRIGGER agent_cards_removed AFTER DELETE ON agent_cards BEGIN " +
      `UPDATE policy_usage SET pending_cards = pending_cards - 1; ${HOLD("OLD", "-")} END`,
    "CREATE TRIGGER agent_cards_kept BEFORE UPDATE ON agent_cards " +
      "BEGIN SELECT RAISE(ABORT, 'a held registration never changes'); END",
    "CREATE TRIGGER proposal_shares_emptied AFTER UPDATE ON proposal_shares " +
      "WHEN NEW.pending = 0 BEGIN DELETE FROM proposal_shares WHERE proposer = NEW.proposer; END",
    "CREATE TRIGGER policy_receipts_added AFTER INSERT ON policy_receipts BEGIN " +
      "UPDATE policy_usage SET owner_receipts = owner_receipts + (NEW.bucket = 'owner'), " +
      "agent_receipts = agent_receipts + (NEW.bucket = 'agent'), " +
      "refusal_receipts = refusal_receipts + (NEW.bucket = 'refusal'); END",
    "CREATE TRIGGER policy_receipts_removed AFTER DELETE ON policy_receipts BEGIN " +
      "UPDATE policy_usage SET owner_receipts = owner_receipts - (OLD.bucket = 'owner'), " +
      "agent_receipts = agent_receipts - (OLD.bucket = 'agent'), " +
      "refusal_receipts = refusal_receipts - (OLD.bucket = 'refusal'); END",
  ]),
  // 4: owner files (plan §6; files.ts): the manifest of paths, versions and history,
  // which chunks make which content, chunk reference counts, upload intents, quotas, the
  // change feed and the sync lease. The bytes are chunks in the files Worker's bucket.
  //
  // Every index and trigger write is a billed row written, so there are only the ones a
  // query or a bound needs. Tables keyed by text are WITHOUT ROWID (the key is the table,
  // no second index), and an entry is keyed by (parent, name), which serves a lookup, a
  // directory listing and a subtree's range alike. Measured (`rowsWritten`, triggers and
  // indexes included, under the default retention): a new small file writes 10 rows, and
  // its overwrite 10 while the path keeps fewer than `keep_versions`, 12 until it keeps
  // `keep_recent`, then 17 in the steady state (each change also moves a row between the
  // retention bands and drops the oldest); a new one-chunk file 22, its overwrite 22, 24,
  // then 35. A delete also releases the path's kept rows, two or three rows written for
  // each: 13 for a path of one version, 72 for one of 20 (78 chunked); a path made again
  // takes them back, so its first write is 44 rows after a gone path of 20 (62 chunked).
  // An entry's own content reference costs a row or two of each figure: the new content's
  // reference, and on an overwrite the old one's release.
  //
  // Counters only triggers write: `file_space`'s entries, history rows, chunk bytes
  // (chunks uploading or live), inline bytes, open intents and the chunks they hold,
  // which the bounds read; a content's `refs` (every entry and every kept history row
  // that names it, in any root: one model, so no path, history or none, can drop
  // content an entry still names) and a chunk's `refs` (contents made of it). A content
  // nothing refers to goes, with its chunk list; a chunk no content refers to and no
  // upload holds becomes garbage, deleted by housekeeping after `GC_GRACE_S` (24 h,
  // SQLite's clock: the schedule compares it with the object's, and a day's grace does
  // not mind which). So content any entry or kept row names is never garbage. A history
  // row never changes but for its prune time, its `released` mark and a redaction of
  // its content.
  Object.freeze([
    "CREATE TABLE file_space (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      // The owner's chunk namespace, made on first use (files.ts `ns`).
      "ns TEXT CHECK (ns IS NULL OR (length(ns) = 32 AND ns NOT GLOB '*[^0-9a-f]*')), " +
      "seq INTEGER NOT NULL, feed_floor INTEGER NOT NULL, " +
      "entries INTEGER NOT NULL CHECK (entries >= 0), " +
      "history INTEGER NOT NULL CHECK (history >= 0), " +
      "chunk_bytes INTEGER NOT NULL CHECK (chunk_bytes >= 0), " +
      "inline_bytes INTEGER NOT NULL CHECK (inline_bytes >= 0), " +
      "intents INTEGER NOT NULL CHECK (intents >= 0), " +
      "intent_chunks INTEGER NOT NULL CHECK (intent_chunks >= 0), " +
      // The sandbox leases' fencing epoch, only rising: each lease (`sandbox_leases`,
      // migration 12, one an authority) takes the next.
      "epoch INTEGER NOT NULL CHECK (epoch >= 0), " +
      // The retention every kept row is marked by, and how far housekeeping has re-marked
      // the rows for a newer one.
      "retained_keep INTEGER NOT NULL, retained_recent INTEGER NOT NULL, " +
      "retained_days INTEGER NOT NULL, retain_cursor TEXT)",
    `INSERT INTO file_space VALUES (1, NULL, 0, 0, 0, 0, 0, 0, 0, 0, 0, ${
      FILE_DEFAULTS.keep_versions}, ${FILE_DEFAULTS.keep_recent}, ${
      FILE_DEFAULTS.retention_days}, NULL)`,
    "CREATE TRIGGER file_epoch_rises BEFORE UPDATE OF epoch ON file_space " +
      "WHEN NEW.epoch < OLD.epoch BEGIN SELECT RAISE(ABORT, 'the lease epoch only rises'); END",
    "CREATE TRIGGER file_seq_rises BEFORE UPDATE OF seq ON file_space " +
      "WHEN NEW.seq <= OLD.seq BEGIN SELECT RAISE(ABORT, 'a file version never repeats'); END",
    "CREATE TABLE file_entries (parent TEXT NOT NULL, name TEXT NOT NULL, " +
      "kind TEXT NOT NULL CHECK (kind IN ('file', 'dir')), version INTEGER NOT NULL, " +
      "content_id TEXT, size INTEGER NOT NULL CHECK (size >= 0), " +
      "mode INTEGER NOT NULL CHECK (mode IN (0, 1)), mtime_ms INTEGER NOT NULL, " +
      "writer TEXT NOT NULL, CHECK ((kind = 'file') = (content_id IS NOT NULL)), " +
      "PRIMARY KEY (parent, name)) WITHOUT ROWID",
    "CREATE TABLE file_contents (content_id TEXT PRIMARY KEY, size INTEGER NOT NULL, " +
      "inline BLOB, refs INTEGER NOT NULL DEFAULT 0 CHECK (refs >= 0), " +
      // Inline content is 4,096 bytes at most (a file's `INLINE_BYTES`); chunked content
      // is any size (a placed body's past `PLACE_BYTES`, ADR-035).
      "CHECK (inline IS NULL OR size <= 4096), " +
      "CHECK (inline IS NULL OR length(inline) = size)) WITHOUT ROWID",
    "CREATE TABLE file_content_chunks (content_id TEXT NOT NULL, idx INTEGER NOT NULL, " +
      "sha256 TEXT NOT NULL, PRIMARY KEY (content_id, idx)) WITHOUT ROWID",
    // `stored`: the files Worker accepted a put R2 verified, recorded after it resolved,
    // and nothing else; a commit refuses a chunk not stored, and garbage never stored is
    // sent again, never revived.
    "CREATE TABLE file_chunks (sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64), " +
      "size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 4194304), " +
      "state TEXT NOT NULL CHECK (state IN ('uploading', 'live', 'garbage', 'deleting')), " +
      "stored INTEGER NOT NULL CHECK (stored IN (0, 1)), " +
      "refs INTEGER NOT NULL DEFAULT 0 CHECK (refs >= 0), " +
      "holds INTEGER NOT NULL DEFAULT 0 CHECK (holds >= 0), gc_after REAL, " +
      "CHECK ((gc_after IS NOT NULL) = (state IN ('garbage', 'deleting'))), " +
      "CHECK (state != 'live' OR stored = 1)) WITHOUT ROWID",
    "CREATE INDEX file_chunks_due ON file_chunks (gc_after) WHERE gc_after IS NOT NULL",
    // An upload of one file: its size and its chunks in order (64 hex each, repeats kept).
    "CREATE TABLE file_intents (intent_id TEXT PRIMARY KEY, size INTEGER NOT NULL, " +
      "chunks TEXT NOT NULL CHECK (length(chunks) % 64 = 0), expires_at REAL NOT NULL) " +
      "WITHOUT ROWID",
    "CREATE INDEX file_intents_due ON file_intents (expires_at)",
    "CREATE TRIGGER file_intents_added AFTER INSERT ON file_intents BEGIN " +
      "UPDATE file_space SET intents = intents + 1, " +
      "intent_chunks = intent_chunks + length(NEW.chunks) / 64; END",
    "CREATE TRIGGER file_intents_removed AFTER DELETE ON file_intents BEGIN " +
      "UPDATE file_space SET intents = intents - 1, " +
      "intent_chunks = intent_chunks - length(OLD.chunks) / 64; END",
    // The version is the rowid: the feed reads a key range.
    "CREATE TABLE file_history (version INTEGER PRIMARY KEY, path TEXT NOT NULL, " +
      "op TEXT NOT NULL CHECK (op IN ('write', 'mkdir', 'delete', 'rename_from', " +
      "'rename_to', 'restore', 'conflict')), " +
      "kind TEXT CHECK (kind IS NULL OR kind IN ('file', 'dir')), content_id TEXT, " +
      "size INTEGER NOT NULL, mode INTEGER NOT NULL, writer TEXT NOT NULL, at REAL NOT NULL, " +
      // `released`: the path is gone, so retention has let the row go (it is always due);
      // a change at the history quota prunes these first (files.ts `#fit`).
      "prune_after REAL, released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1)), " +
      "CHECK (released = 0 OR prune_after IS NOT NULL))",
    "CREATE INDEX file_history_paths ON file_history (path, version)",
    "CREATE INDEX file_history_due ON file_history (prune_after) WHERE prune_after IS NOT NULL",
    "CREATE INDEX file_history_released ON file_history (prune_after) WHERE released = 1",
    "CREATE TABLE file_temps (name TEXT PRIMARY KEY, expires_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE INDEX file_temps_due ON file_temps (expires_at)",
    // Every entry holds its own content, in every root, whatever history keeps.
    "CREATE TRIGGER file_entries_added AFTER INSERT ON file_entries BEGIN " +
      "UPDATE file_space SET entries = entries + 1; " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; END",
    "CREATE TRIGGER file_entries_removed AFTER DELETE ON file_entries BEGIN " +
      "UPDATE file_space SET entries = entries - 1; " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_entries_changed AFTER UPDATE OF content_id ON file_entries " +
      "WHEN NEW.content_id IS NOT OLD.content_id BEGIN " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_history_added AFTER INSERT ON file_history BEGIN " +
      "UPDATE file_space SET history = history + 1; " +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; END",
    "CREATE TRIGGER file_history_removed AFTER DELETE ON file_history BEGIN " +
      "UPDATE file_space SET history = history - 1; " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_history_kept BEFORE UPDATE OF version, path, op, kind, size, mode, " +
      "writer, at ON file_history BEGIN " +
      "SELECT RAISE(ABORT, 'a file history row is never changed'); END",
    "CREATE TRIGGER file_history_redacted BEFORE UPDATE OF content_id ON file_history " +
      "WHEN NEW.content_id IS NOT NULL BEGIN " +
      "SELECT RAISE(ABORT, 'a file history row is never changed'); END",
    "CREATE TRIGGER file_history_released AFTER UPDATE OF content_id ON file_history BEGIN " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_contents_added AFTER INSERT ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes + coalesce(length(NEW.inline), 0); END",
    "CREATE TRIGGER file_contents_unreferenced AFTER UPDATE OF refs ON file_contents " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_contents WHERE content_id = NEW.content_id; END",
    "CREATE TRIGGER file_contents_removed AFTER DELETE ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes - coalesce(length(OLD.inline), 0); " +
      "DELETE FROM file_content_chunks WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_content_chunks_added AFTER INSERT ON file_content_chunks BEGIN " +
      "UPDATE file_chunks SET refs = refs + 1 WHERE sha256 = NEW.sha256; END",
    "CREATE TRIGGER file_content_chunks_removed AFTER DELETE ON file_content_chunks BEGIN " +
      "UPDATE file_chunks SET refs = refs - 1 WHERE sha256 = OLD.sha256; END",
    `CREATE TRIGGER file_chunks_added AFTER INSERT ON file_chunks BEGIN UPDATE file_space SET ${
      CHUNK_USAGE("+", "NEW")} END`,
    `CREATE TRIGGER file_chunks_removed AFTER DELETE ON file_chunks BEGIN UPDATE file_space SET ${
      CHUNK_USAGE("-", "OLD")} END`,
    "CREATE TRIGGER file_chunks_moved AFTER UPDATE OF state ON file_chunks " +
      `WHEN (OLD.state IN ${COUNTED}) != (NEW.state IN ${COUNTED}) BEGIN ` +
      `UPDATE file_space SET ${CHUNK_USAGE("+", "NEW")} UPDATE file_space SET ${
        CHUNK_USAGE("-", "OLD")} END`,
    "CREATE TRIGGER file_chunks_released AFTER UPDATE OF refs, holds ON file_chunks " +
      "WHEN NEW.refs = 0 AND NEW.holds = 0 AND NEW.state IN ('uploading', 'live') BEGIN " +
      "UPDATE file_chunks SET state = 'garbage', " +
      `gc_after = unixepoch('subsec') + ${GC_GRACE_S} WHERE sha256 = NEW.sha256; END`,
  ]),
  // 5: owner notices (notices.ts): what the owner's work reports that no call fails with
  // (a connection's setup check or provider revoke that failed), the newest kept.
  Object.freeze([
    "CREATE TABLE owner_notices (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, " +
      "type TEXT NOT NULL CHECK (length(type) BETWEEN 1 AND 64), " +
      "detail TEXT NOT NULL CHECK (length(detail) <= 1024))",
  ]),
  // 6: model connections and their use (gateway research §4, §7; connections.ts,
  // usage.ts). A connection's credential is a blob sealed by the gateway, which alone
  // holds the key: the object never holds a plaintext secret. Times are the object's
  // clock in seconds, but an OAuth access expiry and the refresh lease, which are epoch
  // ms as the gateway gives them. A reservation's amount is held against the owner's
  // ceilings by a counter only the triggers below write, so no ceiling ever reads a
  // receipt: `model_spend`, each source's day and month. What is reserved or unknown holds
  // its estimate, what settled holds what it used, and what was never sent or was refused
  // holds nothing. Receipts are history only, pruned without touching a counter.
  Object.freeze([
    "CREATE TABLE connections (id TEXT PRIMARY KEY CHECK (length(id) = 25), " +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "kind TEXT NOT NULL CHECK (kind IN ('api_key', 'oauth2')), " +
      "display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 128), " +
      "provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64), " +
      "models TEXT NOT NULL DEFAULT '[]' CHECK (length(models) <= 655360), " +
      "status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked', 'error')), " +
      "version INTEGER NOT NULL CHECK (version >= 1), expires_at INTEGER, " +
      "lease_id TEXT, lease_until INTEGER, created_at REAL NOT NULL, pending_until REAL, " +
      "revoked_at REAL, revoke_due REAL, revoke_tries INTEGER NOT NULL DEFAULT 0, " +
      "revocable INTEGER NOT NULL CHECK (revocable IN (0, 1)), " +
      "CHECK ((status = 'pending') = (pending_until IS NOT NULL)), " +
      "CHECK (revoke_due IS NULL OR status IN ('revoked', 'error'))) WITHOUT ROWID",
    "CREATE INDEX connections_pending ON connections (pending_until) " +
      "WHERE pending_until IS NOT NULL",
    "CREATE INDEX connections_revokes ON connections (revoke_due) WHERE revoke_due IS NOT NULL",
    // One sealed blob per connection, at its current version.
    "CREATE TABLE connection_secrets (connection_id TEXT NOT NULL, " +
      "version INTEGER NOT NULL, kid TEXT NOT NULL CHECK (length(kid) BETWEEN 1 AND 32), " +
      "blob TEXT NOT NULL CHECK (length(blob) <= 65536), " +
      "PRIMARY KEY (connection_id, version)) WITHOUT ROWID",
    // A connection OAuth flow, keyed by the SHA-256 of its state; its PKCE verifier is
    // held only while it is open.
    "CREATE TABLE connection_flows (flow TEXT PRIMARY KEY CHECK (length(flow) = 64), " +
      "connection_id TEXT NOT NULL, session_id TEXT NOT NULL, verifier TEXT, " +
      "redirect_uri TEXT NOT NULL CHECK (length(redirect_uri) <= 2048), " +
      "created_at REAL NOT NULL, expires_at REAL NOT NULL, " +
      "status TEXT NOT NULL CHECK (status IN ('open', 'used', 'cancelled')), " +
      "CHECK ((status = 'open') = (verifier IS NOT NULL))) WITHOUT ROWID",
    "CREATE INDEX connection_flows_by_expiry ON connection_flows (expires_at)",
    "CREATE TABLE model_reservations (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      "id TEXT NOT NULL UNIQUE, run TEXT NOT NULL CHECK (length(run) <= 320), " +
      "connection_id TEXT NOT NULL, source TEXT NOT NULL CHECK (length(source) <= 64), " +
      "model TEXT NOT NULL CHECK (length(model) <= 256), " +
      "state TEXT NOT NULL CHECK (state IN ('reserved', 'dispatched', 'succeeded', " +
      "'failed', 'unknown', 'not_sent', 'refused')), rule TEXT, " +
      "max_output_tokens INTEGER NOT NULL, est_tokens INTEGER NOT NULL, " +
      "est_cost INTEGER NOT NULL, tokens INTEGER, cost INTEGER, " +
      "policy_generation INTEGER NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, " +
      "created_at REAL NOT NULL, settled_at REAL)",
    "CREATE INDEX model_reservations_open ON model_reservations (state) " +
      "WHERE state IN ('reserved', 'dispatched')",
    "CREATE TABLE model_spend (period TEXT NOT NULL, source TEXT NOT NULL, " +
      "cost INTEGER NOT NULL, PRIMARY KEY (period, source)) WITHOUT ROWID",
    "CREATE TRIGGER model_reservations_spent AFTER INSERT ON model_reservations " +
      `WHEN ${HELD("NEW")} != 0 BEGIN ${SPEND("NEW", HELD("NEW"))} END`,
    "CREATE TRIGGER model_reservations_respent AFTER UPDATE OF state, cost " +
      `ON model_reservations WHEN ${HELD("NEW")} != ${HELD("OLD")} BEGIN ${
        SPEND("NEW", `${HELD("NEW")} - ${HELD("OLD")}`)} END`,
    "CREATE TRIGGER model_reservations_kept BEFORE UPDATE OF seq, id, run, connection_id, " +
      "source, " +
      "model, est_tokens, est_cost, policy_generation, day, month, created_at " +
      "ON model_reservations BEGIN SELECT RAISE(ABORT, 'a reservation never changes " +
      "what it reserved'); END",
  ]),
  // 7: the files byte quota and each outside client's share of it (files.ts, manifest.ts),
  // and the bounds on the model reservations (usage.ts). Every manifest row is charged what
  // SQLite stores it in, path bytes and overflow pages included, by triggers only, as the
  // other counters are: once to `file_space`'s `manifest_bytes`, the object's real storage;
  // and to each scope its writer counts in (`file_shares`: an outside client's own,
  // `client:<id>`, all clients' together, `*`, and the owner's own writers', `owner`). A
  // scope is charged its own entries, history rows and temporary directories, and once for
  // each content and chunk its own rows (or its open uploads) reference, counted in
  // `file_scope_contents` and `file_scope_chunks` and charged while the count is above zero,
  // deduplicated within the scope and never across scopes; a chunk no scope references is
  // garbage, charged to its `holder`, the scope that let it go last, until it is deleted or
  // revived. A share row is charged its own bytes, and a client's goes once it holds
  // nothing else; all clients' and the owner's rows are never dropped. Every reference is
  // counted before a content or chunk can go, so a credit reads the row it was charged for.
  // A file row from before this migration (none can be: nothing is deployed) fails it
  // loudly, its scope unknown.
  //
  // The calls open at once and the spend counters are bounded by count (`CALLS_OPEN`,
  // `SPEND_KEPT`).
  Object.freeze([
    "ALTER TABLE file_space ADD COLUMN manifest_bytes INTEGER NOT NULL DEFAULT 0 " +
      "CHECK (manifest_bytes >= 0)",
    `ALTER TABLE file_chunks ADD COLUMN holder TEXT NOT NULL DEFAULT '${OWNER_SCOPE}'`,
    "ALTER TABLE file_temps ADD COLUMN writer TEXT NOT NULL DEFAULT 'system'",
    "ALTER TABLE file_intents ADD COLUMN writer TEXT NOT NULL DEFAULT 'system'",
    // The chunks the upload's writer has still to send: its proof of holding their bytes.
    "ALTER TABLE file_intents ADD COLUMN pending TEXT NOT NULL DEFAULT '' " +
      "CHECK (length(pending) % 64 = 0)",
    `CREATE TABLE file_shares (who TEXT PRIMARY KEY CHECK (who IN ('${CLIENTS}', '${
      OWNER_SCOPE}') OR (length(who) = 71 AND who GLOB '${CLIENT_WRITER}')), ` +
      "entries INTEGER NOT NULL CHECK (entries >= 0), " +
      "history INTEGER NOT NULL CHECK (history >= 0), " +
      "chunk_bytes INTEGER NOT NULL CHECK (chunk_bytes >= 0), " +
      "inline_bytes INTEGER NOT NULL CHECK (inline_bytes >= 0), " +
      "manifest_bytes INTEGER NOT NULL CHECK (manifest_bytes >= 0)) WITHOUT ROWID",
    "CREATE TABLE file_scope_contents (scope TEXT NOT NULL, content_id TEXT NOT NULL, " +
      "refs INTEGER NOT NULL CHECK (refs >= 0), PRIMARY KEY (scope, content_id)) WITHOUT ROWID",
    "CREATE TABLE file_scope_chunks (scope TEXT NOT NULL, sha256 TEXT NOT NULL, " +
      "refs INTEGER NOT NULL CHECK (refs >= 0), PRIMARY KEY (scope, sha256)) WITHOUT ROWID",
    // Fails the `who` check when any file row is already there.
    "INSERT INTO file_shares (who, entries, history, chunk_bytes, inline_bytes, " +
      "manifest_bytes) SELECT 'a file row from before migration 7', 0, 0, 0, 0, 0 " +
      "WHERE EXISTS (SELECT 1 FROM file_entries) OR EXISTS (SELECT 1 FROM file_history) " +
      "OR EXISTS (SELECT 1 FROM file_chunks) OR EXISTS (SELECT 1 FROM file_temps)",
    `INSERT INTO file_shares VALUES ('${CLIENTS}', 0, 0, 0, 0, ${SHARE_BYTES}), ('${
      OWNER_SCOPE}', 0, 0, 0, 0, ${SHARE_BYTES})`,
    `UPDATE file_space SET manifest_bytes = 2 * ${SHARE_BYTES}`,
    `CREATE TRIGGER file_shares_charged AFTER INSERT ON file_shares WHEN NEW.who NOT IN ('${
      CLIENTS}', '${OWNER_SCOPE}') BEGIN UPDATE file_shares SET manifest_bytes = ` +
      `manifest_bytes + ${SHARE_BYTES} WHERE who IN (NEW.who, '${CLIENTS}'); ` +
      `UPDATE file_space SET manifest_bytes = manifest_bytes + ${SHARE_BYTES}; END`,
    `CREATE TRIGGER file_shares_emptied AFTER UPDATE ON file_shares WHEN NEW.who NOT IN ('${
      CLIENTS}', '${OWNER_SCOPE}') AND NEW.entries = 0 AND NEW.history = 0 ` +
      "AND NEW.chunk_bytes = 0 " +
      `AND NEW.inline_bytes = 0 AND NEW.manifest_bytes = ${SHARE_BYTES} BEGIN ` +
      "DELETE FROM file_shares WHERE who = NEW.who; END",
    `CREATE TRIGGER file_shares_credited AFTER DELETE ON file_shares WHEN OLD.who NOT IN ('${
      CLIENTS}', '${OWNER_SCOPE}') BEGIN UPDATE file_shares SET manifest_bytes = ` +
      `manifest_bytes - ${SHARE_BYTES} WHERE who = '${CLIENTS}'; UPDATE file_space SET ` +
      `manifest_bytes = manifest_bytes - ${SHARE_BYTES}; END`,
    `CREATE TRIGGER file_shares_kept BEFORE DELETE ON file_shares WHEN OLD.who IN ('${
      CLIENTS}', '${OWNER_SCOPE}') BEGIN SELECT RAISE(ABORT, 'all clients'' and the ` +
      "owner''s share rows are never dropped'); END",
    // A scope's reference: charged its own row, and the content's bytes or the chunk's
    // while it holds any; a content's chunks are its scope's too.
    "CREATE TRIGGER file_scope_contents_added AFTER INSERT ON file_scope_contents BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes + ${SCOPE_REF_BYTES("NEW")}; ${
        SHARE_USAGE(SCOPE("NEW.scope"), "+", {
          inline_bytes: CONTENT_OF("NEW.content_id", "coalesce(length(c.inline), 0)"),
          manifest_bytes: `${SCOPE_REF_BYTES("NEW")} + ${
            CONTENT_OF("NEW.content_id", CONTENT_BYTES("c"))}` })} ` +
      "INSERT INTO file_scope_chunks (scope, sha256, refs) SELECT NEW.scope, x.sha256, " +
      "COUNT(*) FROM file_content_chunks x WHERE x.content_id = NEW.content_id " +
      "GROUP BY x.sha256 ON CONFLICT (scope, sha256) DO UPDATE SET refs = refs + excluded.refs; " +
      "END",
    "CREATE TRIGGER file_scope_contents_emptied AFTER UPDATE OF refs ON file_scope_contents " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_scope_contents WHERE scope = NEW.scope " +
      "AND content_id = NEW.content_id; END",
    "CREATE TRIGGER file_scope_contents_removed AFTER DELETE ON file_scope_contents BEGIN " +
      "UPDATE file_scope_chunks SET refs = refs - (SELECT COUNT(*) FROM file_content_chunks x " +
      "WHERE x.content_id = OLD.content_id AND x.sha256 = file_scope_chunks.sha256) " +
      "WHERE scope = OLD.scope AND sha256 IN (SELECT sha256 FROM file_content_chunks " +
      "WHERE content_id = OLD.content_id); " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes - ${SCOPE_REF_BYTES("OLD")}; ${
        SHARE_USAGE(SCOPE("OLD.scope"), "-", {
          inline_bytes: CONTENT_OF("OLD.content_id", "coalesce(length(c.inline), 0)"),
          manifest_bytes: `${SCOPE_REF_BYTES("OLD")} + ${
            CONTENT_OF("OLD.content_id", CONTENT_BYTES("c"))}` })} END`,
    "CREATE TRIGGER file_scope_chunks_added AFTER INSERT ON file_scope_chunks BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes + ${SCOPE_REF_BYTES("NEW")}; ${
        SHARE_USAGE(SCOPE("NEW.scope"), "+", {
          chunk_bytes: CHUNK_OF("NEW.sha256", "k.size"),
          manifest_bytes: `${SCOPE_REF_BYTES("NEW")} + ${CHUNK_ROW_BYTES()}` })} END`,
    "CREATE TRIGGER file_scope_chunks_emptied AFTER UPDATE OF refs ON file_scope_chunks " +
      "WHEN NEW.refs = 0 BEGIN DELETE FROM file_scope_chunks WHERE scope = NEW.scope " +
      "AND sha256 = NEW.sha256; END",
    // The scope that lets a chunk go last holds its garbage.
    "CREATE TRIGGER file_scope_chunks_removed AFTER DELETE ON file_scope_chunks BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes - ${SCOPE_REF_BYTES("OLD")}; ${
        SHARE_USAGE(SCOPE("OLD.scope"), "-", {
          chunk_bytes: CHUNK_OF("OLD.sha256", "k.size"),
          manifest_bytes: `${SCOPE_REF_BYTES("OLD")} + ${CHUNK_ROW_BYTES()}` })} ` +
      `UPDATE file_chunks SET holder = OLD.scope WHERE sha256 = OLD.sha256 AND OLD.scope != '${
        CLIENTS}'; END`,
    "DROP TRIGGER file_entries_added",
    "DROP TRIGGER file_entries_removed",
    "DROP TRIGGER file_entries_changed",
    "DROP TRIGGER file_history_added",
    "DROP TRIGGER file_history_removed",
    "DROP TRIGGER file_history_released",
    "DROP TRIGGER file_contents_added",
    "DROP TRIGGER file_contents_removed",
    "DROP TRIGGER file_chunks_added",
    "DROP TRIGGER file_chunks_removed",
    // Each scope's references are counted before the content's own, and taken out first.
    "CREATE TRIGGER file_entries_added AFTER INSERT ON file_entries BEGIN " +
      `UPDATE file_space SET entries = entries + 1, manifest_bytes = manifest_bytes + ${
        ENTRY_BYTES("NEW")}; ` +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      `${SHARE_USAGE(WRITER("NEW"), "+", { entries: "1", manifest_bytes: ENTRY_BYTES("NEW") })} ${
        CONTENT_REF("NEW", "+")} END`,
    "CREATE TRIGGER file_entries_removed AFTER DELETE ON file_entries BEGIN " +
      `${CONTENT_REF("OLD", "-")} ` +
      `UPDATE file_space SET entries = entries - 1, manifest_bytes = manifest_bytes - ${
        ENTRY_BYTES("OLD")}; ` +
      `${SHARE_USAGE(WRITER("OLD"), "-", { entries: "1", manifest_bytes: ENTRY_BYTES("OLD") })} ` +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // An overwrite moves the entry's reference to its new content and writer.
    "CREATE TRIGGER file_entries_changed AFTER UPDATE OF content_id, writer ON file_entries " +
      "WHEN NEW.content_id IS NOT OLD.content_id OR NEW.writer IS NOT OLD.writer BEGIN " +
      `${CONTENT_REF("NEW", "+")} ${CONTENT_REF("OLD", "-")} ` +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // An overwrite by another writer moves the entry to it.
    "CREATE TRIGGER file_entries_recharged AFTER UPDATE OF parent, name, kind, writer " +
      "ON file_entries WHEN NEW.writer IS NOT OLD.writer OR NEW.parent IS NOT OLD.parent " +
      "OR NEW.name IS NOT OLD.name OR NEW.kind IS NOT OLD.kind BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes + ${ENTRY_BYTES("NEW")} - ${
        ENTRY_BYTES("OLD")}; ` +
      `${SHARE_USAGE(WRITER("OLD"), "-", { entries: "1", manifest_bytes: ENTRY_BYTES("OLD") })} ${
        SHARE_USAGE(WRITER("NEW"), "+", { entries: "1", manifest_bytes: ENTRY_BYTES("NEW") })} END`,
    "CREATE TRIGGER file_history_added AFTER INSERT ON file_history BEGIN " +
      `UPDATE file_space SET history = history + 1, manifest_bytes = manifest_bytes + ${
        FILE_HISTORY_BYTES("NEW")}; ` +
      "UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; " +
      `${SHARE_USAGE(WRITER("NEW"), "+", { history: "1",
                                          manifest_bytes: FILE_HISTORY_BYTES("NEW") })} ${
        CONTENT_REF("NEW", "+")} END`,
    "CREATE TRIGGER file_history_removed AFTER DELETE ON file_history BEGIN " +
      `${CONTENT_REF("OLD", "-")} ` +
      `UPDATE file_space SET history = history - 1, manifest_bytes = manifest_bytes - ${
        FILE_HISTORY_BYTES("OLD")}; ` +
      `${SHARE_USAGE(WRITER("OLD"), "-", { history: "1",
                                          manifest_bytes: FILE_HISTORY_BYTES("OLD") })} ` +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    // A redaction lets the version's content go, in its writer's scopes first.
    "CREATE TRIGGER file_history_released AFTER UPDATE OF content_id ON file_history BEGIN " +
      `${CONTENT_REF("OLD", "-")} ` +
      "UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_contents_added AFTER INSERT ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes + coalesce(length(NEW.inline), 0), " +
      `manifest_bytes = manifest_bytes + ${CONTENT_BYTES("NEW")}; END`,
    "CREATE TRIGGER file_contents_removed AFTER DELETE ON file_contents BEGIN " +
      "UPDATE file_space SET inline_bytes = inline_bytes - coalesce(length(OLD.inline), 0), " +
      `manifest_bytes = manifest_bytes - ${CONTENT_BYTES("OLD")}; ` +
      "DELETE FROM file_content_chunks WHERE content_id = OLD.content_id; END",
    // A chunk list is exactly the chunks its content's size is cut into, as it is charged.
    "CREATE TRIGGER file_content_chunks_planned BEFORE INSERT ON file_content_chunks " +
      `WHEN NEW.idx < 0 OR NOT (NEW.idx < coalesce(${CONTENT_OF("NEW.content_id",
        `CASE WHEN c.inline IS NULL THEN (c.size + ${CHUNK_BYTES - 1}) / ${CHUNK_BYTES} ` +
        "ELSE 0 END")}, 0)) BEGIN SELECT RAISE(ABORT, 'a chunk list holds the chunks its ` +
      "content''s size is cut into'); END",
    "CREATE TRIGGER file_chunks_added AFTER INSERT ON file_chunks BEGIN " +
      `UPDATE file_space SET ${CHUNK_USAGE("+", "NEW")} UPDATE file_space SET manifest_bytes = ` +
      `manifest_bytes + ${CHUNK_ROW_BYTES()}; END`,
    "CREATE TRIGGER file_chunks_removed AFTER DELETE ON file_chunks BEGIN " +
      `UPDATE file_space SET ${CHUNK_USAGE("-", "OLD")} UPDATE file_space SET manifest_bytes = ` +
      `manifest_bytes - ${CHUNK_ROW_BYTES()}; ${
        SHARE_USAGE(SCOPES("OLD.holder"), "-", { manifest_bytes: `${CHUNK_ROW_BYTES()} * ` +
          "(OLD.state IN ('garbage', 'deleting'))" })} END`,
    // Garbage is its holder's, from the moment no scope holds it until it is deleted or an
    // upload revives it.
    "CREATE TRIGGER file_chunks_discarded AFTER UPDATE OF state ON file_chunks " +
      `WHEN OLD.state IN ${COUNTED} AND NEW.state = 'garbage' BEGIN ${
        SHARE_USAGE(SCOPES("NEW.holder"), "+", { manifest_bytes: CHUNK_ROW_BYTES() })} END`,
    "CREATE TRIGGER file_chunks_revived AFTER UPDATE OF state ON file_chunks " +
      `WHEN OLD.state = 'garbage' AND NEW.state IN ${COUNTED} BEGIN ${
        SHARE_USAGE(SCOPES("OLD.holder"), "-", { manifest_bytes: CHUNK_ROW_BYTES() })} END`,
    "CREATE TRIGGER file_chunks_held BEFORE UPDATE OF holder ON file_chunks " +
      "WHEN NEW.holder IS NOT OLD.holder AND OLD.state IN ('garbage', 'deleting') BEGIN " +
      "SELECT RAISE(ABORT, 'a garbage chunk''s holder never changes'); END",
    "CREATE TRIGGER file_temps_added AFTER INSERT ON file_temps BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes + ${TEMP_BYTES("NEW")}; ${
        SHARE_USAGE(WRITER("NEW"), "+", { manifest_bytes: TEMP_BYTES("NEW") })} END`,
    "CREATE TRIGGER file_temps_removed AFTER DELETE ON file_temps BEGIN " +
      `UPDATE file_space SET manifest_bytes = manifest_bytes - ${TEMP_BYTES("OLD")}; ${
        SHARE_USAGE(WRITER("OLD"), "-", { manifest_bytes: TEMP_BYTES("OLD") })} END`,
    // The writer each is charged to never changes.
    "CREATE TRIGGER file_temps_writer_kept BEFORE UPDATE OF writer ON file_temps BEGIN " +
      "SELECT RAISE(ABORT, 'a file row''s writer never changes'); END",
    "CREATE TRIGGER file_intents_writer_kept BEFORE UPDATE OF writer ON file_intents BEGIN " +
      "SELECT RAISE(ABORT, 'a file row''s writer never changes'); END",
    // The calls open at once and the spend counters, each bounded by count; an open call is
    // read by its partial index.
    "CREATE TRIGGER model_reservations_open BEFORE INSERT ON model_reservations " +
      "WHEN NEW.state IN ('reserved', 'dispatched') AND (SELECT COUNT(*) FROM " +
      `model_reservations WHERE state IN ('reserved', 'dispatched')) >= ${CALLS_OPEN} BEGIN ` +
      `SELECT RAISE(ABORT, 'model_calls_full: ${CALLS_OPEN} model calls are open'); END`,
    "CREATE TRIGGER model_spend_kept AFTER INSERT ON model_spend " +
      `WHEN (SELECT COUNT(*) FROM model_spend) > ${SPEND_KEPT} BEGIN ` +
      `SELECT RAISE(ABORT, 'model_spend_full: ${SPEND_KEPT} spend counters are kept'); END`,
  ]),
  // 8: the owner's erasure (erasure.ts; S4-S5): the owner being erased is one row
  // (`owner_erasure`, with the step it reached), the mark every door and lane refuses on.
  Object.freeze([
    "CREATE TABLE owner_erasure (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "started_at REAL NOT NULL, step INTEGER NOT NULL DEFAULT 1 CHECK (step IN (1, 2)))",
  ]),
  // 9: a grant's revision apart from its authority (grants.ts). `revision` is the owner's
  // concurrency token, moved by every set and revoke and named by the owner's set;
  // `generation` moves only when the grant's mode or scopes do, and is what every stamp is
  // checked against, so a rename keeps the client's stamps. A free row is never set, so it
  // stays at revision 1. Nothing is deployed, so no row is backfilled.
  Object.freeze([
    "ALTER TABLE outside_clients ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 " +
      "CHECK (revision >= 1)",
  ]),
  // 10: the owner's admission (tasks 5, F1): `owner_admission` is the owner's one
  // admission row (`admission.ts`): admitted when the object is first made, suspended for
  // billing or abuse by the platform, or, as the last step of the owner's erasure, the
  // erased tombstone, alone in the wiped database.
  Object.freeze([
    ADMISSION_TABLE,
    "INSERT INTO owner_admission (only, state, reasons) VALUES (1, 'admitted', '[]')",
  ]),
  // 11: the egress door (tasks 4 G4; egress.ts). The owner's policy row keeps the egress
  // generation, moved only by a write that changes the egress table (each sandbox lease,
  // migration 12, keeps the one its network was last applied under).
  // A connection keeps where its credential may be forwarded (`egress`: its sealed base
  // URL, endpoints and pi-ai's metered call, no secret), from its setup check. Each sandbox
  // call the object admits is an open row until the egress door settles it; a refused one
  // is kept as its receipt. A model call's row carries its own charge (`charge`, held to
  // settled once, as a model reservation's states), which the triggers alone count: into
  // `model_spend`, its source's day and month, the counters the model reservations move
  // too, and (migration 12) into its run's own requests, tokens and cost on its `runs` row.
  // Each row names the run its path named. The newest `EGRESS_LIMITS.receiptsKept`
  // admitted and `refusalsKept` refused rows are kept, and at most `EGRESS_LIMITS.open` are
  // open at once; a path is visible ASCII, as the door sends it.
  Object.freeze([
    "ALTER TABLE owner_policy ADD COLUMN egress_generation INTEGER NOT NULL DEFAULT 1 " +
      "CHECK (egress_generation >= 1)",
    "ALTER TABLE connections ADD COLUMN egress TEXT CHECK (egress IS NULL OR " +
      "length(egress) <= 16384)",
    "CREATE TABLE egress_reservations (id TEXT PRIMARY KEY CHECK (length(id) = 32), " +
      "lease_id TEXT CHECK (lease_id IS NULL OR length(lease_id) = 32), epoch INTEGER, " +
      // The run the call came from, as its path named it (`/r/<run>/c/...`).
      `run TEXT NOT NULL CHECK (length(run) = ${RUN_ID_CHARS}), ` +
      "token TEXT NOT NULL CHECK (length(token) = 64), " +
      "source TEXT CHECK (source IS NULL OR length(source) BETWEEN 1 AND 64), " +
      "charge TEXT CHECK (charge IS NULL OR charge IN ('held', 'succeeded', 'failed', " +
      "'unknown')), rule TEXT CHECK (rule IS NULL OR length(rule) <= 64), " +
      "max_output_tokens INTEGER, est_tokens INTEGER CHECK (est_tokens IS NULL OR " +
      "est_tokens >= 0), est_cost INTEGER CHECK (est_cost IS NULL OR est_cost >= 0), " +
      "tokens INTEGER CHECK (tokens IS NULL OR tokens >= 0), " +
      "cost INTEGER CHECK (cost IS NULL OR cost >= 0), day TEXT, month TEXT, " +
      "served TEXT CHECK (served IS NULL OR (length(served) BETWEEN 1 AND 256 " +
      "AND served NOT GLOB '*[^!-~]*')), " +
      "connection_id TEXT NOT NULL CHECK (length(connection_id) = 25), version INTEGER, " +
      "method TEXT NOT NULL CHECK (length(method) BETWEEN 1 AND 7), " +
      "path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 2048 AND " +
      "path NOT GLOB '*[^ -~]*'), " +
      "generation INTEGER NOT NULL, bytes_hint INTEGER CHECK (bytes_hint IS NULL OR " +
      "bytes_hint >= 0), opened_at REAL NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('open', 'settled', 'refused')), " +
      "code TEXT CHECK (code IS NULL OR length(code) <= 64), " +
      "ended TEXT CHECK (ended IS NULL OR ended IN ('policy', 'lease', 'connection', " +
      "'admission', 'erasure', 'run')), " +
      "outcome TEXT CHECK (outcome IS NULL OR outcome IN ('complete', 'aborted', 'failed', " +
      "'deadline', 'ended', 'lost')), " +
      "status INTEGER CHECK (status IS NULL OR status BETWEEN 100 AND 599), " +
      "bytes INTEGER CHECK (bytes IS NULL OR bytes >= 0), settled_at REAL, " +
      "CHECK ((state = 'refused') = (code IS NOT NULL)), " +
      "CHECK ((state = 'settled') = (outcome IS NOT NULL)), " +
      "CHECK ((state = 'open') = (settled_at IS NULL)), " +
      "CHECK ((charge IS NULL) = (est_cost IS NULL) AND (charge IS NULL) = (est_tokens IS NULL) " +
      "AND (charge IS NULL) = (source IS NULL) AND (charge IS NULL) = (day IS NULL) " +
      "AND (charge IS NULL) = (month IS NULL) " +
      "AND (charge IS NULL) = (max_output_tokens IS NULL)), " +
      "CHECK (charge IS NULL OR (state != 'refused' AND lease_id IS NOT NULL " +
      "AND epoch IS NOT NULL)), " +
      "CHECK ((charge IN ('succeeded', 'failed')) = (cost IS NOT NULL)), " +
      "CHECK ((charge IN ('succeeded', 'failed')) = (tokens IS NOT NULL)))",
    "CREATE INDEX egress_reservations_by_state ON egress_reservations (state, opened_at)",
    "CREATE INDEX egress_reservations_by_time ON egress_reservations (opened_at)",
    "CREATE TRIGGER egress_reservations_charged AFTER INSERT ON egress_reservations " +
      `WHEN NEW.charge IS NOT NULL BEGIN ${SPEND("NEW", CHARGED("NEW"))} END`,
    "CREATE TRIGGER egress_reservations_recharged AFTER UPDATE OF charge, tokens, cost " +
      "ON egress_reservations WHEN NEW.charge IS NOT NULL AND OLD.charge IS NOT NULL " +
      `AND ${CHARGED("NEW")} != ${CHARGED("OLD")} BEGIN ${
        SPEND("NEW", `${CHARGED("NEW")} - ${CHARGED("OLD")}`)} END`,
    "CREATE TRIGGER egress_reservations_kept BEFORE UPDATE OF id, lease_id, epoch, run, token, " +
      "source, max_output_tokens, est_tokens, est_cost, day, month, connection_id " +
      "ON egress_reservations BEGIN SELECT RAISE(ABORT, 'a reservation never changes what " +
      "it reserved'); END",
    "CREATE TRIGGER egress_reservations_settled_once BEFORE UPDATE OF charge " +
      "ON egress_reservations WHEN OLD.charge IS NOT NULL AND OLD.charge != 'held' " +
      "BEGIN SELECT RAISE(ABORT, 'a charge is settled once'); END",
  ]),
  // 12: runs (ADR-034 decision 4; `runs.ts`). A run is one engine invocation: its row
  // (`runs`) is its receipt, kept past its settle, the newest `RUNS_KEPT` settled ones, with
  // its usage counters, which only the egress triggers below move (a call's charge, held to
  // settled, counts to its run as it counts to its source's day and month). At most
  // `RUNS_CEILING` are live (`queued` or `running`) at once, the most any tier record may
  // set; the tier's own number is admission's (`tier.ts`). A live run names the sandbox
  // lease it runs under and its own last heartbeat (`beat_at`), and holds one run grant
  // (`run_grants`): its initiator's stamp and origin, its scopes, its own connection (none
  // on the vendor's own sign-in) and the SHA-256 of its own secret, deleted at its settle or
  // cancel (its authority ends at once; the runner's end still settles it). The sandbox
  // leases (`sandbox_leases`): one sandbox an authority, the owner's own work's or one
  // outside client's, so runs of different authority never share a VM; each lease its
  // fencing epoch, its expiry (its tokens' too), the SHA-256 of its egress, sync and
  // runner tokens, and the egress generation its network was last applied under; at most
  // `RUNS_CEILING`, since a lease is opened only for a live run and a lapsed one goes. A
  // conversation (`conversations`) keeps its engine and that engine's session id, at most
  // `CONVERSATIONS_KEPT` (the least recently run one with no live run goes first). The
  // `runs` lane's rows (`run_schedules`, at most `SCHEDULES_KEPT`): a launch at a time or a
  // cron recurrence, each with its input. The platform's tier
  // numbers as the binder last handed them (`platform_tiers`, one row), and the owner app's
  // socket tickets (`socket_tickets`, at most `SOCKET_TICKETS`, single-use, each kept as
  // its SHA-256 only). A run that waits for its conversation's live run to settle is a
  // `waiting` row (not live: it holds no slot, grant or lease), its input kept beside it
  // (`run_inputs`) until it starts, at most `QUEUE_ROWS` and `QUEUE_BYTES` of input in all.
  // Each engine's sign-in, as the runner last reported it (`engine_signins`, one row an
  // engine, at most `ENGINE_SIGNINS`): signed in or not, when, and the models it offers;
  // never a credential.
  Object.freeze([
    "CREATE TABLE runs (seq INTEGER PRIMARY KEY AUTOINCREMENT, " +
      `id TEXT NOT NULL UNIQUE CHECK (length(id) = ${RUN_ID_CHARS}), ` +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      `conversation TEXT NOT NULL CHECK (length(conversation) = ${CONVERSATION_ID_CHARS}), ` +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "credential TEXT NOT NULL CHECK (credential IN ('vendor_login_in_sandbox', 'api_key')), " +
      "model TEXT CHECK (model IS NULL OR (length(model) BETWEEN 1 AND 256 " +
      "AND model NOT GLOB '*[^!-~]*')), " +
      "state TEXT NOT NULL CHECK (state IN ('waiting', 'queued', 'running', 'succeeded', " +
      "'failed', 'unknown')), " +
      "initiator TEXT NOT NULL CHECK (initiator IN ('owner', 'client', 'schedule', 'mail')), " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "request TEXT CHECK (request IS NULL OR length(request) = 64), " +
      "unattended INTEGER NOT NULL CHECK (unattended IN (0, 1)), " +
      // The mail it answers, when mail to one of the owner's agents started it (slice ID):
      // the mail is held in the ledger until the run settles.
      `mail TEXT CHECK (mail IS NULL OR length(mail) = ${MESSAGE_ID_CHARS}), ` +
      "cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)), " +
      "requests INTEGER NOT NULL DEFAULT 0 CHECK (requests >= 0), " +
      "tokens INTEGER NOT NULL DEFAULT 0 CHECK (tokens >= 0), " +
      "cost INTEGER NOT NULL DEFAULT 0 CHECK (cost >= 0), " +
      "last_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_seq >= 0), " +
      `result TEXT CHECK (result IS NULL OR length(CAST(result AS BLOB)) <= ${RESULT_BYTES}), ` +
      "result_cut INTEGER NOT NULL DEFAULT 0 CHECK (result_cut IN (0, 1)), " +
      `error TEXT CHECK (error IS NULL OR length(CAST(error AS BLOB)) <= ${ERROR_BYTES}), ` +
      "code TEXT CHECK (code IS NULL OR length(code) BETWEEN 1 AND 64), " +
      "exit_code INTEGER, created_at REAL NOT NULL, started_at REAL, settled_at REAL, " +
      // The sandbox lease a live run runs under, and its last heartbeat.
      "lease_id TEXT CHECK (lease_id IS NULL OR length(lease_id) = 32), epoch INTEGER, " +
      "beat_at REAL, " +
      "CHECK ((state IN ('waiting', 'queued', 'running')) = (settled_at IS NULL)), " +
      "CHECK (state NOT IN ('queued', 'running') OR (lease_id IS NOT NULL " +
      "AND epoch IS NOT NULL AND beat_at IS NOT NULL)), " +
      // A client's own run names its client; the owner's names none; a schedule's or the
      // mail's names the client that set it, if one did.
      "CHECK (initiator != 'client' OR client IS NOT NULL), " +
      "CHECK (initiator != 'owner' OR client IS NULL))",
    "CREATE UNIQUE INDEX runs_by_request ON runs (request) WHERE request IS NOT NULL",
    "CREATE INDEX runs_live ON runs (state) WHERE state IN ('queued', 'running')",
    "CREATE INDEX runs_by_conversation ON runs (conversation, seq)",
    "CREATE INDEX runs_waiting ON runs (seq) WHERE state = 'waiting'",
    "CREATE INDEX runs_settled ON runs (settled_at, seq) WHERE settled_at IS NOT NULL",
    "CREATE TRIGGER runs_live_full BEFORE INSERT ON runs WHEN (SELECT COUNT(*) FROM runs " +
      `WHERE state IN ('queued', 'running')) >= ${RUNS_CEILING} BEGIN SELECT RAISE(ABORT, ` +
      `'runs_full: ${RUNS_CEILING} runs are live'); END`,
    "CREATE TRIGGER runs_settled_once BEFORE UPDATE OF state ON runs " +
      "WHEN OLD.state NOT IN ('waiting', 'queued', 'running') BEGIN SELECT RAISE(ABORT, " +
      "'a run is settled once'); END",
    "CREATE TRIGGER runs_kept BEFORE UPDATE OF seq, id, agent, conversation, initiator, " +
      "client, stamp, request, unattended, mail, created_at ON runs BEGIN " +
      "SELECT RAISE(ABORT, 'a run never changes what it was admitted as'); END",
    // A waiting run's source is chosen again as it starts; a started one's never moves.
    "CREATE TRIGGER runs_source_kept BEFORE UPDATE OF source, credential ON runs " +
      "WHEN OLD.state != 'waiting' BEGIN SELECT RAISE(ABORT, 'a run never changes what " +
      "it was admitted as'); END",
    "CREATE TABLE run_inputs (" +
      `run TEXT PRIMARY KEY CHECK (length(run) = ${RUN_ID_CHARS}), ` +
      "prompt TEXT NOT NULL, bytes INTEGER NOT NULL CHECK (bytes >= 1), " +
      // The client's origin, as its run grant will carry it once the run starts.
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      "CHECK (bytes = length(CAST(prompt AS BLOB)))) WITHOUT ROWID",
    "CREATE TRIGGER run_inputs_full BEFORE INSERT ON run_inputs WHEN (SELECT COUNT(*) " +
      `FROM run_inputs) >= ${QUEUE_ROWS} OR (SELECT coalesce(SUM(bytes), 0) FROM run_inputs) ` +
      `+ NEW.bytes > ${QUEUE_BYTES} BEGIN SELECT RAISE(ABORT, 'run_inputs_full: ` +
      `${QUEUE_ROWS} inputs or ${QUEUE_BYTES} bytes wait already; let a run settle first'); END`,
    "CREATE TABLE engine_signins (engine TEXT PRIMARY KEY CHECK (length(engine) BETWEEN 1 " +
      "AND 64), signed_in INTEGER NOT NULL CHECK (signed_in IN (0, 1)), " +
      `models TEXT NOT NULL CHECK (length(models) <= ${SIGNIN_MODELS_CHARS} ` +
      "AND models NOT GLOB '*[^ -~]*'), checked_at REAL NOT NULL, changed_at REAL NOT NULL) " +
      "WITHOUT ROWID",
    "CREATE TRIGGER engine_signins_full BEFORE INSERT ON engine_signins WHEN (SELECT " +
      `COUNT(*) FROM engine_signins) >= ${ENGINE_SIGNINS} BEGIN SELECT RAISE(ABORT, ` +
      `'engine_signins_full: ${ENGINE_SIGNINS} engines are known'); END`,
    "CREATE TABLE sandbox_leases (authority TEXT PRIMARY KEY CHECK (authority = 'owner' " +
      "OR length(authority) = 64), " +
      "lease_id TEXT NOT NULL CHECK (length(lease_id) = 32 AND lease_id NOT GLOB " +
      "'*[^0-9a-f]*'), epoch INTEGER NOT NULL CHECK (epoch >= 1), " +
      "holder TEXT NOT NULL CHECK (length(holder) BETWEEN 1 AND 128), " +
      "lease_expires REAL NOT NULL, " +
      "egress_token TEXT NOT NULL CHECK (length(egress_token) = 64 AND egress_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "sync_token TEXT NOT NULL CHECK (length(sync_token) = 64 AND sync_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "runner_token TEXT NOT NULL CHECK (length(runner_token) = 64 AND runner_token " +
      "NOT GLOB '*[^0-9a-f]*'), " +
      "network_generation INTEGER CHECK (network_generation IS NULL OR " +
      "network_generation >= 1), " +
      // The engines signed in inside this sandbox, as its own runner last reported them
      // (a client's sandbox's: a login is never copied between sandboxes; the owner's own
      // sandbox's are `engine_signins`'), comma-joined, presence only.
      `signins TEXT NOT NULL DEFAULT '' CHECK (length(signins) <= ${SIGNIN_LIST_CHARS} ` +
      "AND signins NOT GLOB '*[^!-~]*'), " +
      // A client's sandbox's readable set at its sync agent's last read, as a digest: a
      // change sends the reader back to the feed's start.
      "readable TEXT CHECK (readable IS NULL OR (length(readable) = 16 AND readable NOT GLOB " +
      "'*[^0-9a-f]*')), " +
      "CHECK (egress_token != sync_token AND egress_token != " +
      "runner_token AND sync_token != runner_token)) WITHOUT ROWID",
    "CREATE TRIGGER sandbox_leases_full BEFORE INSERT ON sandbox_leases WHEN (SELECT " +
      `COUNT(*) FROM sandbox_leases) >= ${RUNS_CEILING} BEGIN SELECT RAISE(ABORT, ` +
      `'sandbox_leases_full: ${RUNS_CEILING} sandboxes are leased'); END`,
    "CREATE TRIGGER sandbox_leases_fenced BEFORE UPDATE OF authority, lease_id, epoch, " +
      "egress_token, sync_token, runner_token ON sandbox_leases BEGIN " +
      "SELECT RAISE(ABORT, 'a lease never changes what it was taken as'); END",
    "CREATE TABLE run_grants (" +
      `run TEXT PRIMARY KEY CHECK (length(run) = ${RUN_ID_CHARS}), ` +
      // The SHA-256 of the run's own secret, which only its CLI's environment holds.
      "secret TEXT NOT NULL CHECK (length(secret) = 64 AND secret NOT GLOB '*[^0-9a-f]*'), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      "scopes TEXT NOT NULL CHECK (length(scopes) <= 512), " +
      "connection TEXT CHECK (connection IS NULL OR length(connection) = 25), " +
      "created_at REAL NOT NULL, CHECK ((stamp IS NULL) = (origin IS NULL))) WITHOUT ROWID",
    "CREATE TABLE conversations (" +
      `id TEXT PRIMARY KEY CHECK (length(id) = ${CONVERSATION_ID_CHARS}), ` +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      "source TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64), " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "session_id TEXT CHECK (session_id IS NULL OR (length(session_id) BETWEEN 1 AND 256 " +
      "AND session_id NOT GLOB '*[^!-~]*')), " +
      `last_run TEXT CHECK (last_run IS NULL OR length(last_run) = ${RUN_ID_CHARS}), ` +
      "created_at REAL NOT NULL, last_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE INDEX conversations_by_age ON conversations (last_at)",
    "CREATE TRIGGER conversations_full BEFORE INSERT ON conversations WHEN (SELECT COUNT(*) " +
      `FROM conversations) >= ${CONVERSATIONS_KEPT} BEGIN SELECT RAISE(ABORT, ` +
      `'conversations_full: ${CONVERSATIONS_KEPT} conversations are kept'); END`,
    "CREATE TABLE run_schedules (" +
      `id TEXT PRIMARY KEY CHECK (length(id) = ${SCHEDULE_ID_CHARS}), ` +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128), " +
      `input TEXT NOT NULL CHECK (length(CAST(input AS BLOB)) BETWEEN 1 AND ${INPUT_BYTES}), ` +
      "trigger TEXT NOT NULL CHECK (trigger IN ('at', 'cron')), " +
      `cron TEXT CHECK (cron IS NULL OR length(cron) BETWEEN 1 AND ${CRON_MAX_CHARS}), ` +
      "next_at REAL, " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), " +
      "stamp TEXT CHECK (stamp IS NULL OR length(stamp) <= 1024), " +
      "origin TEXT CHECK (origin IS NULL OR (length(origin) <= 256 " +
      "AND origin NOT GLOB '*[^ -~]*')), " +
      `last_run TEXT CHECK (last_run IS NULL OR length(last_run) = ${RUN_ID_CHARS}), ` +
      "last_error TEXT CHECK (last_error IS NULL OR length(last_error) BETWEEN 1 AND 256), " +
      "created_at REAL NOT NULL, CHECK ((trigger = 'cron') = (cron IS NOT NULL)), " +
      "CHECK ((client IS NULL) = (stamp IS NULL) AND (stamp IS NULL) = (origin IS NULL))) " +
      "WITHOUT ROWID",
    "CREATE INDEX run_schedules_due ON run_schedules (next_at) WHERE next_at IS NOT NULL",
    "CREATE TRIGGER run_schedules_full BEFORE INSERT ON run_schedules WHEN (SELECT COUNT(*) " +
      `FROM run_schedules) >= ${SCHEDULES_KEPT} BEGIN SELECT RAISE(ABORT, ` +
      `'run_schedules_full: ${SCHEDULES_KEPT} schedules are kept; cancel one first'); END`,
    "CREATE TABLE platform_tiers (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "tiers TEXT NOT NULL CHECK (length(tiers) <= 1024), read_at REAL NOT NULL)",
    "CREATE TABLE socket_tickets (hash TEXT PRIMARY KEY CHECK (length(hash) = 64 " +
      "AND hash NOT GLOB '*[^0-9a-f]*'), " +
      `conversation TEXT NOT NULL CHECK (length(conversation) = ${CONVERSATION_ID_CHARS}), ` +
      "grant_id TEXT NOT NULL CHECK (length(grant_id) BETWEEN 1 AND 128), " +
      "expires_at REAL NOT NULL, token_expires_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE TRIGGER socket_tickets_full BEFORE INSERT ON socket_tickets " +
      `WHEN (SELECT COUNT(*) FROM socket_tickets) >= ${SOCKET_TICKETS} ` +
      "BEGIN SELECT RAISE(ABORT, 'socket_tickets_full: as many socket tickets are " +
      "outstanding as may be; use one or wait for one to expire'); END",
    // A sandbox call's charge counts to its run, as it counts to its source's day and month.
    "CREATE TRIGGER egress_reservations_run_charged AFTER INSERT ON egress_reservations " +
      `WHEN NEW.charge IS NOT NULL BEGIN ${RUN("NEW", "1", CHARGED_TOKENS("NEW"),
                                               CHARGED("NEW"))} END`,
    "CREATE TRIGGER egress_reservations_run_recharged AFTER UPDATE OF charge, tokens, cost " +
      "ON egress_reservations WHEN NEW.charge IS NOT NULL AND OLD.charge IS NOT NULL " +
      `AND (${CHARGED("NEW")} != ${CHARGED("OLD")} OR ${CHARGED_TOKENS("NEW")} != ${
        CHARGED_TOKENS("OLD")}) BEGIN ${
        RUN("NEW", "0", `${CHARGED_TOKENS("NEW")} - ${CHARGED_TOKENS("OLD")}`,
            `${CHARGED("NEW")} - ${CHARGED("OLD")}`)} END`,
  ]),
  // 13: the owner's own agents as ledger identities (slice ID; ledger.ts). Each
  // `/workspace/agents/<agent>/agent.json` is one, made as the file appears and retired as
  // it goes, in the file change's own unit: a mail recipient and sender beside the outside
  // agents, counted with them in the registry's rows and, while live, its reserved
  // mailboxes (`ledger_usage`). A retired one keeps its row, and its mail stays readable.
  // Mail to one waits `queued` in its recipient row (read by its agent through
  // `recipients_by_state`) until the `runs` lane starts the run that takes it. The identity
  // is keyed by its ledger id, which is its name's digest, so a name finds its row by the
  // key and no second index is kept.
  Object.freeze([
    "CREATE TABLE native_agents (" +
      `agent_id TEXT PRIMARY KEY CHECK (length(agent_id) = ${NATIVE_ID_CHARS} ` +
      "AND agent_id GLOB 'ta_*' AND substr(agent_id, 4) NOT GLOB '*[^0-9a-f]*'), " +
      "agent TEXT NOT NULL CHECK (length(agent) BETWEEN 1 AND 128 " +
      "AND agent NOT GLOB '*[^A-Za-z0-9._-]*'), " +
      "created_at REAL NOT NULL, retired_at REAL, " +
      // Messages this agent sent the owner that the owner has not acked yet: its share of
      // the owner's mailbox, as an outside agent's.
      "unacked_to_owner INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner >= 0)) " +
      "WITHOUT ROWID",
    // An identity's name and ledger id never change: its mail names the id.
    "CREATE TRIGGER native_agents_kept BEFORE UPDATE OF agent, agent_id ON native_agents " +
      "BEGIN SELECT RAISE(ABORT, 'an agent identity never changes its name or id'); END",
    "CREATE TRIGGER native_agents_added AFTER INSERT ON native_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents + 1, " +
      "enabled_agents = enabled_agents + (NEW.retired_at IS NULL); END",
    "CREATE TRIGGER native_agents_retired AFTER UPDATE OF retired_at ON native_agents " +
      "WHEN (OLD.retired_at IS NULL) != (NEW.retired_at IS NULL) BEGIN " +
      "UPDATE ledger_usage SET enabled_agents = enabled_agents + " +
      "CASE WHEN NEW.retired_at IS NULL THEN 1 ELSE -1 END; END",
    "CREATE TRIGGER native_agents_removed AFTER DELETE ON native_agents BEGIN " +
      "UPDATE ledger_usage SET agents = agents - 1, " +
      "enabled_agents = enabled_agents - (OLD.retired_at IS NULL); END",
    // A native agent's message in its own dm room goes to the owner: its share, as above.
    "CREATE TRIGGER ledger_native_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN NEW.sender_kind = 'native' AND NEW.room_id = 'dm:' || NEW.sender_id " +
      "AND (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN " +
      "UPDATE native_agents SET " +
      "unacked_to_owner = unacked_to_owner + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END " +
      "WHERE agent_id = NEW.sender_id; END",
    "CREATE TRIGGER ledger_native_removed AFTER DELETE ON ledger_messages " +
      "WHEN OLD.sender_kind = 'native' AND OLD.room_id = 'dm:' || OLD.sender_id " +
      "AND OLD.unacked > 0 BEGIN " +
      "UPDATE native_agents SET unacked_to_owner = unacked_to_owner - 1 " +
      "WHERE agent_id = OLD.sender_id; END",
  ]),
  // 14: the placement rule (ADR-035). A body past `PLACE_BYTES` is content of the files
  // plane: a collection row, a history entry or a message body that names it holds a
  // reference on it (`file_contents.refs`) and one in its writer's files scope
  // (`file_scope_contents`, by its `filer`), counted here as each row comes and goes, so
  // content nothing names goes, its chunk becomes garbage and housekeeping deletes it from
  // R2, as a file's. Its bytes count in the files byte quota (`chunk_bytes`), the tier's
  // storage, and in its outside writer's share of it (`client_share`, `owner_reserve`).
  Object.freeze([
    "CREATE TRIGGER collection_rows_placed AFTER INSERT ON collection_rows " +
      `WHEN NEW.content_id IS NOT NULL BEGIN ${PLACED("NEW", "+")} END`,
    "CREATE TRIGGER collection_rows_unplaced AFTER DELETE ON collection_rows " +
      `WHEN OLD.content_id IS NOT NULL BEGIN ${PLACED("OLD", "-")} END`,
    "CREATE TRIGGER collection_history_placed AFTER INSERT ON collection_history " +
      `WHEN NEW.content_id IS NOT NULL BEGIN ${PLACED("NEW", "+")} END`,
    "CREATE TRIGGER collection_history_unplaced AFTER DELETE ON collection_history " +
      `WHEN OLD.content_id IS NOT NULL BEGIN ${PLACED("OLD", "-")} END`,
    "CREATE TRIGGER message_bodies_placed AFTER INSERT ON message_bodies " +
      `WHEN NEW.content_id IS NOT NULL BEGIN ${PLACED("NEW", "+")} END`,
    "CREATE TRIGGER message_bodies_unplaced AFTER DELETE ON message_bodies " +
      `WHEN OLD.content_id IS NOT NULL BEGIN ${PLACED("OLD", "-")} END`,
    "CREATE TRIGGER collection_rows_replaced AFTER UPDATE OF content_id, filer ON " +
      "collection_rows WHEN NEW.content_id IS NOT OLD.content_id OR NEW.filer IS NOT " +
      `OLD.filer BEGIN ${PLACED("NEW", "+")} ${PLACED("OLD", "-")} END`,
    "CREATE TRIGGER message_bodies_kept BEFORE UPDATE ON message_bodies " +
      "BEGIN SELECT RAISE(ABORT, 'a message body is never changed'); END",
  ]),
]);

/** Apply every migration past the stored version; return the version now stored. */
export function migrate(
  storage: OwnerStorage,
  migrations: readonly Migration[] = MIGRATIONS,
): number {
  const sql = storage.sql;
  sql.exec(
    "CREATE TABLE IF NOT EXISTS schema_version " +
      "(only INTEGER PRIMARY KEY CHECK (only = 1), version INTEGER NOT NULL)",
  );
  const stored = sql.exec<{ version: number }>("SELECT version FROM schema_version").toArray();
  const current = stored[0]?.version ?? 0;
  if (current > migrations.length) {
    throw new Error(
      `schema version ${current} is newer than this code's ${migrations.length}; refusing to run`,
    );
  }
  for (let version = current + 1; version <= migrations.length; version++) {
    const statements = migrations[version - 1] ?? [];
    storage.transactionSync(() => {
      for (const statement of statements) {
        sql.exec(statement);
      }
      sql.exec(
        "INSERT INTO schema_version (only, version) VALUES (1, ?) " +
          "ON CONFLICT (only) DO UPDATE SET version = excluded.version",
        version,
      );
    });
  }
  return migrations.length;
}
