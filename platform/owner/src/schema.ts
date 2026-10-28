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
 * entry at the end. Nothing is deployed yet, so migrations 1 to 4 are still edited in
 * place.
 *
 * The `CHECK` on a row body is the store's fixed ceiling of 65,536 characters. The
 * primitive's own bound is `LIMITS.maxRow`, which may be lower but never higher (a
 * test ties the two); raising the ceiling is a new migration.
 */
import { GC_GRACE_S } from "./files";
import { DEFAULT_POLICY_DIGEST, DEFAULT_POLICY_JSON, FILE_DEFAULTS } from "./policy";

export type Migration = readonly string[];

/** Add a row's or history entry's counts, times `sign`, to each scope it falls in. */
const ADD_USAGE =
  "INSERT INTO collection_usage (scope, collection, who, rows, row_bytes, history_bytes) " +
  "VALUES ";
const ON_USAGE =
  " ON CONFLICT (scope, collection, who) DO UPDATE SET rows = rows + excluded.rows, " +
  "row_bytes = row_bytes + excluded.row_bytes, " +
  "history_bytes = history_bytes + excluded.history_bytes;";
const ROW_BYTES = (row: string) => `length(CAST(${row}.body AS BLOB))`;
const ROW_USAGE = (row: string, sign: string) =>
  ADD_USAGE +
  `('owner', '', '', ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('collection', ${row}.collection, '', ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('writer', ${row}.collection, ${row}.writer, ${sign}1, ${sign}${ROW_BYTES(row)}, 0), ` +
  `('creator', ${row}.collection, ${row}.creator, ${sign}1, ${sign}${ROW_BYTES(row)}, 0)` +
  ON_USAGE;
const HISTORY_BYTES = (entry: string) => `length(CAST(${entry}.body_json AS BLOB))`;
const HISTORY_USAGE = (entry: string, sign: string) =>
  ADD_USAGE +
  `('owner', '', '', 0, 0, ${sign}${HISTORY_BYTES(entry)}), ` +
  `('collection', ${entry}.collection, '', 0, 0, ${sign}${HISTORY_BYTES(entry)}), ` +
  `('writer', ${entry}.collection, ${entry}.writer, 0, 0, ${sign}${HISTORY_BYTES(entry)})` +
  ON_USAGE;

/**
 * Move a grant row's class, times `sign`: set by the owner (counted against the owner's
 * bound), or free to make room (nothing holds it).
 */
const CLIENT_USAGE = (row: string, sign: string) =>
  `UPDATE policy_usage SET owner_clients = owner_clients ${sign} (${row}.owner_set = 1), ` +
  `free_clients = free_clients ${sign} (${row}.owner_set = 0 AND ${row}.agents = 0 ` +
  `AND ${row}.held = 0);`;
/** Count an open stamped work row against its client's row (`+`), or release it (`-`). */
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
/** A file entry in the temporary area, which keeps no history. */
const TEMP_ENTRY = (row: string) =>
  `${row}.content_id IS NOT NULL AND (${row}.parent = '/.ta/tmp' OR ${row}.parent GLOB '/.ta/tmp/*')`;

export const MIGRATIONS: readonly Migration[] = Object.freeze([
  // 1: the owner this object serves, and the one revision primitive (S34, ADR-032).
  Object.freeze([
    "CREATE TABLE object_owner (only INTEGER PRIMARY KEY CHECK (only = 1), owner TEXT NOT NULL)",
    "CREATE TABLE collection_rows (" +
      "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, " +
      "private INTEGER NOT NULL CHECK (private IN (0, 1)), " +
      "body TEXT NOT NULL CHECK (length(body) <= 65536), updated_at REAL NOT NULL, " +
      // Who created the row; a takeover never changes it (the row share counts it).
      "creator TEXT NOT NULL, " +
      "PRIMARY KEY (collection, row_id), UNIQUE (collection, seq))",
    // A clear by writer matches either.
    "CREATE INDEX collection_writers ON collection_rows (collection, writer)",
    "CREATE INDEX collection_creators ON collection_rows (collection, creator)",
    // The high-water seq survives a clear, so a reader's cursor never goes back.
    "CREATE TABLE collection_seqs (collection TEXT PRIMARY KEY, seq INTEGER NOT NULL)",
    "CREATE TABLE collection_history (" +
      "collection TEXT NOT NULL, key TEXT NOT NULL, revision INTEGER NOT NULL, " +
      "op TEXT NOT NULL, body_json TEXT NOT NULL, at REAL NOT NULL, writer TEXT NOT NULL, " +
      "PRIMARY KEY (collection, key, revision))",
    // The bounds' counters: rows and payload bytes in the whole object (the owner's),
    // per collection, and per writer and per creator in a collection. Only the
    // triggers below write them, inside the statement that changes a row or a history
    // entry, so every write, delete, clear and history prune moves them in its own
    // transaction, and a bound check reads one row by key. A history entry counts
    // against its writer; `creator` counts rows alone. A row that falls to zero goes.
    "CREATE TABLE collection_usage (" +
      "scope TEXT NOT NULL CHECK (scope IN ('owner', 'collection', 'writer', 'creator')), " +
      "collection TEXT NOT NULL, who TEXT NOT NULL, rows INTEGER NOT NULL, " +
      "row_bytes INTEGER NOT NULL, history_bytes INTEGER NOT NULL, " +
      "PRIMARY KEY (scope, collection, who))",
    `CREATE TRIGGER collection_rows_added AFTER INSERT ON collection_rows BEGIN ${
      ROW_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_rows_changed AFTER UPDATE ON collection_rows BEGIN ${
      ROW_USAGE("OLD", "-")} ${ROW_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_rows_removed AFTER DELETE ON collection_rows BEGIN ${
      ROW_USAGE("OLD", "-")} END`,
    // An erase entry has no body and counts nothing.
    `CREATE TRIGGER collection_history_added AFTER INSERT ON collection_history ` +
      `WHEN ${HISTORY_BYTES("NEW")} > 0 BEGIN ${HISTORY_USAGE("NEW", "")} END`,
    `CREATE TRIGGER collection_history_removed AFTER DELETE ON collection_history ` +
      `WHEN ${HISTORY_BYTES("OLD")} > 0 BEGIN ${HISTORY_USAGE("OLD", "-")} END`,
    "CREATE TRIGGER collection_history_kept BEFORE UPDATE ON collection_history " +
      "BEGIN SELECT RAISE(ABORT, 'a history entry is never changed'); END",
    "CREATE TRIGGER collection_usage_emptied AFTER UPDATE ON collection_usage " +
      "WHEN NEW.rows = 0 AND NEW.row_bytes = 0 AND NEW.history_bytes = 0 BEGIN " +
      "DELETE FROM collection_usage WHERE scope = NEW.scope AND collection = NEW.collection " +
      "AND who = NEW.who; END",
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
      "max_wakes_per_hour INTEGER NOT NULL DEFAULT 12 " +
      "CHECK (max_wakes_per_hour BETWEEN 1 AND 60), " +
      "batch_window_s INTEGER NOT NULL DEFAULT 60 CHECK (batch_window_s BETWEEN 0 AND 3600), " +
      // Messages this agent sent that the owner has not acked yet, and their bytes: its
      // share of the owner's mailbox.
      "unacked_to_owner INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner >= 0), " +
      "unacked_to_owner_bytes INTEGER NOT NULL DEFAULT 0 CHECK (unacked_to_owner_bytes >= 0), " +
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
      "UNIQUE (sender_id, client_message_id))",
    // The messages no recipient holds, oldest first: what eviction takes.
    "CREATE INDEX messages_done ON ledger_messages (seq) WHERE unacked = 0",
    // A recipient row carries its message's seq, so a reader's mail is an index range.
    // `unacked` is 1 until the reader acks past it, or its agent is disabled (released).
    "CREATE TABLE ledger_recipients (" +
      "message_id TEXT NOT NULL, agent_id TEXT NOT NULL, seq INTEGER NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('queued', 'held_cap', 'woken', 'read', " +
      "'replied', 'failed', 'unknown')), changed_at REAL NOT NULL, " +
      "unacked INTEGER NOT NULL DEFAULT 1 CHECK (unacked IN (0, 1)), " +
      "PRIMARY KEY (message_id, agent_id))",
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
      "unacked INTEGER NOT NULL DEFAULT 0 CHECK (unacked >= 0), " +
      "unacked_bytes INTEGER NOT NULL DEFAULT 0 CHECK (unacked_bytes >= 0))",
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
    "CREATE TABLE message_bodies (" +
      "message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, " +
      "body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 16384), created_at REAL NOT NULL)",
    // The owner-wide counters the ledger's bounds read.
    "CREATE TABLE ledger_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "agents INTEGER NOT NULL, enabled_agents INTEGER NOT NULL, messages INTEGER NOT NULL, " +
      "body_bytes INTEGER NOT NULL, settled_wakes INTEGER NOT NULL, " +
      // Deleted agents kept as tombstones while a revoke of their callbacks is pending.
      "tombstones INTEGER NOT NULL)",
    "INSERT INTO ledger_usage VALUES (1, 0, 0, 0, 0, 0, 0)",
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
      "UPDATE ledger_usage SET messages = messages + 1, " +
      "body_bytes = body_bytes + NEW.body_bytes; END",
    "CREATE TRIGGER ledger_messages_removed AFTER DELETE ON ledger_messages BEGIN " +
      "UPDATE ledger_usage SET messages = messages - 1, " +
      "body_bytes = body_bytes - OLD.body_bytes; END",
    // A recipient row holds its message until it is acked or released: the message's
    // `unacked` and the reader's mailbox counters move with it. Its bytes are read from
    // its message, so a recipient row goes before its message (a NULL fails loudly).
    "CREATE TRIGGER ledger_recipient_added AFTER INSERT ON ledger_recipients " +
      "WHEN NEW.unacked = 1 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked + 1 WHERE message_id = NEW.message_id; " +
      "INSERT INTO ledger_readers (reader, unacked, unacked_bytes) VALUES (NEW.agent_id, 1, " +
      "(SELECT body_bytes FROM ledger_messages WHERE message_id = NEW.message_id)) " +
      "ON CONFLICT (reader) DO UPDATE SET unacked = unacked + 1, " +
      "unacked_bytes = unacked_bytes + excluded.unacked_bytes; END",
    "CREATE TRIGGER ledger_recipient_held BEFORE UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 0 AND NEW.unacked = 1 BEGIN " +
      "SELECT RAISE(ABORT, 'an acked recipient never holds its message again'); END",
    "CREATE TRIGGER ledger_recipient_released AFTER UPDATE OF unacked ON ledger_recipients " +
      "WHEN OLD.unacked = 1 AND NEW.unacked = 0 BEGIN " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = NEW.message_id; " +
      "UPDATE ledger_readers SET unacked = unacked - 1, unacked_bytes = unacked_bytes - " +
      "(SELECT body_bytes FROM ledger_messages WHERE message_id = NEW.message_id) " +
      "WHERE reader = NEW.agent_id; END",
    "CREATE TRIGGER ledger_recipient_removed AFTER DELETE ON ledger_recipients " +
      "WHEN OLD.unacked = 1 BEGIN " +
      "UPDATE ledger_readers SET unacked = unacked - 1, unacked_bytes = unacked_bytes - " +
      "(SELECT body_bytes FROM ledger_messages WHERE message_id = OLD.message_id) " +
      "WHERE reader = OLD.agent_id; " +
      "UPDATE ledger_messages SET unacked = unacked - 1 WHERE message_id = OLD.message_id; END",
    // An outside agent's message goes to the owner alone; while any recipient holds it,
    // it counts against its sender's share of the owner's mailbox.
    "CREATE TRIGGER ledger_outside_held AFTER UPDATE OF unacked ON ledger_messages " +
      "WHEN NEW.sender_kind = 'outside' AND (OLD.unacked = 0) != (NEW.unacked = 0) BEGIN " +
      "UPDATE outside_agents SET " +
      "unacked_to_owner = unacked_to_owner + CASE WHEN NEW.unacked = 0 THEN -1 ELSE 1 END, " +
      "unacked_to_owner_bytes = unacked_to_owner_bytes + " +
      "CASE WHEN NEW.unacked = 0 THEN -NEW.body_bytes ELSE NEW.body_bytes END " +
      "WHERE agent_id = NEW.sender_id; END",
    "CREATE TRIGGER ledger_outside_removed AFTER DELETE ON ledger_messages " +
      "WHEN OLD.sender_kind = 'outside' AND OLD.unacked > 0 BEGIN " +
      "UPDATE outside_agents SET unacked_to_owner = unacked_to_owner - 1, " +
      "unacked_to_owner_bytes = unacked_to_owner_bytes - OLD.body_bytes " +
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
      "scopes TEXT NOT NULL, generation INTEGER NOT NULL CHECK (generation >= 1), " +
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
    // from a sign-in after it. Every path that drops a revoked MCP row moves it.
    "CREATE TABLE grant_fences (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "mcp_through REAL NOT NULL CHECK (mcp_through >= 0))",
    "INSERT INTO grant_fences VALUES (1, 0)",
    "CREATE TRIGGER outside_clients_forgotten AFTER DELETE ON outside_clients " +
      "WHEN OLD.source = 'mcp' AND OLD.revoked_at IS NOT NULL BEGIN " +
      "UPDATE grant_fences SET mcp_through = MAX(mcp_through, OLD.revoked_at); END",
    "CREATE TABLE policy_usage (only INTEGER PRIMARY KEY CHECK (only = 1), " +
      "sessions INTEGER NOT NULL, owner_clients INTEGER NOT NULL, " +
      "free_clients INTEGER NOT NULL, " +
      "pending_proposals INTEGER NOT NULL, applied_proposals INTEGER NOT NULL, " +
      "owner_receipts INTEGER NOT NULL, agent_receipts INTEGER NOT NULL, " +
      "refusal_receipts INTEGER NOT NULL, open_work INTEGER NOT NULL DEFAULT 0, " +
      "settled_work INTEGER NOT NULL DEFAULT 0, pending_cards INTEGER NOT NULL DEFAULT 0)",
    "INSERT INTO policy_usage (only, sessions, owner_clients, free_clients, " +
      "pending_proposals, applied_proposals, owner_receipts, agent_receipts, " +
      "refusal_receipts) VALUES (1, 0, 0, 0, 0, 0, 0, 0, 0)",
    // Stamped scheduled work (tasks 3.6, `scheduled.ts`): a scheduled turn or automation,
    // the outside authority it was made under (`client`, `generation`, `authenticated_at`;
    // none for the owner's own), rechecked with `Grants.standing` before it is handed to
    // pi. Open work holds its client's grant row. A hand-off is fenced by its lease and
    // may-have-been-handed mark, as a wake is, so unknown work is never handed again.
    "CREATE TABLE scheduled_work (work_id TEXT PRIMARY KEY CHECK (length(work_id) = 37), " +
      "kind TEXT NOT NULL CHECK (kind IN ('turn')), due REAL NOT NULL, " +
      "state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'handed', 'failed', " +
      "'refused', 'unknown')), payload TEXT NOT NULL CHECK (length(payload) <= 16384), " +
      "client TEXT CHECK (client IS NULL OR length(client) = 64), generation INTEGER, " +
      "authenticated_at REAL, claim_id TEXT, marked INTEGER NOT NULL DEFAULT 0, " +
      "attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, settled_at REAL, " +
      "outcome TEXT, " +
      "CHECK ((client IS NULL) = (generation IS NULL) " +
      "AND (client IS NOT NULL OR authenticated_at IS NULL)), " +
      "CHECK ((settled_at IS NULL) = (state IN ('pending', 'running'))))",
    "CREATE INDEX work_open ON scheduled_work (due) WHERE state = 'pending'",
    "CREATE INDEX work_running ON scheduled_work (due) WHERE state = 'running'",
    "CREATE INDEX work_settled ON scheduled_work (settled_at) WHERE settled_at IS NOT NULL",
    "CREATE INDEX work_by_client ON scheduled_work (client, state) WHERE settled_at IS NULL",
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
    // A client recorded after its agents or stamped work were (as a direct store change
    // could leave it) still counts them.
    "CREATE TRIGGER outside_clients_held AFTER INSERT ON outside_clients BEGIN " +
      "UPDATE outside_clients SET agents = (SELECT COUNT(*) FROM outside_agents " +
      "WHERE outside_client = NEW.client), held = (SELECT COUNT(*) FROM scheduled_work " +
      "WHERE client = NEW.client AND settled_at IS NULL) + (SELECT COUNT(*) FROM " +
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
    "CREATE TRIGGER scheduled_work_added AFTER INSERT ON scheduled_work BEGIN " +
      "UPDATE policy_usage SET open_work = open_work + (NEW.settled_at IS NULL), " +
      `settled_work = settled_work + (NEW.settled_at IS NOT NULL); ` +
      "UPDATE outside_clients SET held = held + 1 WHERE client = NEW.client " +
      "AND NEW.settled_at IS NULL; END",
    "CREATE TRIGGER scheduled_work_settled AFTER UPDATE OF settled_at ON scheduled_work " +
      "WHEN (OLD.settled_at IS NULL) != (NEW.settled_at IS NULL) BEGIN " +
      "UPDATE policy_usage SET open_work = open_work + CASE WHEN NEW.settled_at IS NULL " +
      "THEN 1 ELSE -1 END, settled_work = settled_work + CASE WHEN NEW.settled_at IS NULL " +
      "THEN -1 ELSE 1 END; " +
      "UPDATE outside_clients SET held = held + CASE WHEN NEW.settled_at IS NULL THEN 1 " +
      "ELSE -1 END WHERE client = NEW.client; END",
    "CREATE TRIGGER scheduled_work_removed AFTER DELETE ON scheduled_work BEGIN " +
      "UPDATE policy_usage SET open_work = open_work - (OLD.settled_at IS NULL), " +
      "settled_work = settled_work - (OLD.settled_at IS NOT NULL); " +
      "UPDATE outside_clients SET held = held - 1 WHERE client = OLD.client " +
      "AND OLD.settled_at IS NULL; END",
    "CREATE TRIGGER scheduled_work_restamped BEFORE UPDATE OF client, generation, " +
      "authenticated_at ON scheduled_work " +
      "BEGIN SELECT RAISE(ABORT, 'scheduled work keeps the authority it was made under'); END",
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
  // directory listing and a subtree's range alike. A small file's overwrite writes about
  // ten rows: the seq, the content and its inline-bytes counter, the entry, the history
  // row with its path index, the history counter and the content's reference, and the
  // row retention lets go of with its due index.
  //
  // Counters only triggers write: `file_space`'s entries, history rows, chunk bytes
  // (chunks uploading or live), inline bytes, open intents and the chunks they hold,
  // which the bounds read; a content's `refs` (its history rows, and the temporary
  // area's entries, which keep no history: a live workspace entry's content is always its
  // newest row's) and a chunk's `refs` (contents made of it). A content nothing refers
  // to goes, with its chunk list; a chunk no content refers to and no upload holds
  // becomes garbage, deleted by housekeeping after `GC_GRACE_S` (24 h, SQLite's clock:
  // the schedule compares it with the object's, and a day's grace does not mind which).
  // A history row never changes but for its prune time and a redaction of its content.
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
      // The sync lease: its claim token, fencing epoch (only rising), holder and expiry.
      "lease_id TEXT, epoch INTEGER NOT NULL CHECK (epoch >= 0), holder TEXT, " +
      "lease_expires REAL NOT NULL, " +
      // The retention every kept row is marked by, and how far housekeeping has re-marked
      // the rows for a newer one.
      "retained_keep INTEGER NOT NULL, retained_days INTEGER NOT NULL, retain_cursor TEXT)",
    `INSERT INTO file_space VALUES (1, NULL, 0, 0, 0, 0, 0, 0, 0, 0, NULL, 0, NULL, 0, ${
      FILE_DEFAULTS.keep_versions}, ${FILE_DEFAULTS.retention_days}, NULL)`,
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
      "CHECK ((inline IS NOT NULL) = (size <= 4096)), " +
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
      "prune_after REAL)",
    "CREATE INDEX file_history_paths ON file_history (path, version)",
    "CREATE INDEX file_history_due ON file_history (prune_after) WHERE prune_after IS NOT NULL",
    "CREATE TABLE file_temps (name TEXT PRIMARY KEY, expires_at REAL NOT NULL) WITHOUT ROWID",
    "CREATE INDEX file_temps_due ON file_temps (expires_at)",
    "CREATE TRIGGER file_entries_added AFTER INSERT ON file_entries BEGIN " +
      "UPDATE file_space SET entries = entries + 1; END",
    "CREATE TRIGGER file_entries_removed AFTER DELETE ON file_entries BEGIN " +
      "UPDATE file_space SET entries = entries - 1; END",
    // The temporary area keeps no history, so its entries hold their own content.
    `CREATE TRIGGER file_temp_held AFTER INSERT ON file_entries WHEN ${TEMP_ENTRY("NEW")} ` +
      "BEGIN UPDATE file_contents SET refs = refs + 1 WHERE content_id = NEW.content_id; END",
    `CREATE TRIGGER file_temp_released AFTER DELETE ON file_entries WHEN ${TEMP_ENTRY("OLD")} ` +
      "BEGIN UPDATE file_contents SET refs = refs - 1 WHERE content_id = OLD.content_id; END",
    "CREATE TRIGGER file_temp_changed AFTER UPDATE OF content_id ON file_entries " +
      `WHEN ${TEMP_ENTRY("NEW")} BEGIN ` +
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
]);

/** Apply every migration past the stored version; return the version now stored. */
export function migrate(
  storage: DurableObjectStorage,
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
