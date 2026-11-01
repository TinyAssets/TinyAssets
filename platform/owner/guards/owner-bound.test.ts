/**
 * The owner's own database stays well inside its 10 GB (ADR-032 §5; tasks 5 and 6).
 *
 * Past 10 GB every write fails `SQLITE_FULL`, grants, revokes and policy writes with the
 * rest, and the owner's object stops; so no owner table may grow but by a bound the code
 * enforces. This guard computes the worst case of every table the owner's schema makes,
 * from those bounds as their sources spell them, at the policy's *high* settings where the
 * owner may raise one, each row at its widest, and holds the sum under half the platform's
 * limit. A table no bound covers fails it: there are no exceptions.
 *
 * The file manifest is bounded in bytes, not rows: every row of its tables is charged what
 * SQLite lays it out in, path bytes and overflow pages included (`manifest.ts`), by triggers
 * this guard finds in the schema, against `max_manifest_bytes`, every index on them charged
 * too (`MANIFEST_INDEXES`). The charge counts a page at three quarters full; a thinned page
 * may stay a third full, so the guard counts the manifest at its high bound times
 * `WORST_FILL`, and a workerd test fills, thins and refills a database against it
 * (`files-share.test.ts`). Every other table's rows are laid out as SQLite lays them,
 * their share of a b-tree page at that worst fill too, their overflow pages whole
 * (`termBytes`). The model turns' counters live as long as the pi records that own them,
 * whose rows the schema refuses past `THREAD_INPUTS` open root inputs, `THREAD_CHILD_INPUTS`
 * subagent inputs and `THREAD_COMPACTIONS` live compactions a thread
 * (`CONVERSATION_COMPACTIONS` for each of its `THREAD_CONVERSATIONS` live conversations: its
 * root and the subagents working in it, A5; and `THREAD_MANUAL_COMPACTIONS` the compact door
 * starts, A6), and past `WORKING_THREADS` threads holding any,
 * each turn of at most `TURN_BYTES`; the calls open at once and
 * the spend counters are bounded by count (migration 10). pi's
 * records live in the thread stores (`thread-store.ts`, S1-S3), never in the owner's
 * database, so what the owner's object holds is its own bounded rows only, whatever its
 * agents do; this guard replaced A4's runtime storage gauge.
 *
 * The worst case is 46.0% of the limit (A6: 4,600,178,612 bytes), about 400 MB under half
 * (A5: 4,597,482,932; A6 adds 2,695,680). A6 adds one term's worth: a manual compaction a working thread
 * (`THREAD_MANUAL_COMPACTIONS`), a turn and its owner row each, 1,024 of each; its forks are
 * conversations within the conversations' bounds (`THREAD_LIMITS`), and its app sockets hold
 * no rows at all (their state is each socket's attachment), only memory, bounded by the
 * constants this guard names (`SOCKET_MEMORY`).
 * F1 (49.0%, 4,897,208,852 bytes) traded the `work` lane's table (its 2,000 rows of up to
 * 16 KiB) for the admission row; a scheduled launch is a run, counted in the thread terms
 * already. A5 bounds the turn rows by the threads at work (`WORKING_THREADS`, 1,024) rather
 * than by every live thread (13,024): sixteen owners a working thread (eight root inputs,
 * two subagents' and six compactions, two for each of three live conversations) where
 * every live thread held ten, freeing about 300 MB. A grant's revision (migration 12) fit
 * in pages S4-S5 already counted. A new table, or a raised bound, must come out of an existing term: lower another
 * bound in the same change, or the guard fails. Every later slice trades terms so; none has
 * room of its own left.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { LEDGER_LIMITS } from "../src/ledger";
import {
  CHUNK_BYTES, INDEX_KEPT, MANIFEST_INDEXES, MANIFEST_TABLES, recordLayout, TABLE_KEPT,
  WORST_FILL,
} from "../src/manifest";
import { NOTICE_DETAIL, NOTICES_KEPT } from "../src/notices";
import { SESSION_LIMITS } from "../src/sessions";
import {
  RECEIPT_REASON_MAX, RECEIPT_TEXT_MAX, THREAD_LIMITS, USAGE_ROW_MAX, USAGE_SPACE_MAX,
} from "../src/threads";
import { USAGE_LIMITS } from "../src/usage";
import { PLATFORM } from "./sources";

const SRC = join(PLATFORM, "owner", "src");

function source(module: string): string {
  return readFileSync(join(SRC, `${module}.ts`), "utf8");
}

/** A numeric constant's expression: `const NAME = <expr>;`, exported or the module's own. */
const NUMERIC = (name: string) =>
  new RegExp(`(?:^|\\n)(?:export )?const ${name}(?:: number)? = ([0-9A-Z_ *+()-]+);`);

/**
 * A name a bound is spelled with, read from its source: the module's own constant, else
 * the one constant of that name another owner module exports.
 */
function named(word: string, module: string): number {
  const own = NUMERIC(word).exec(source(module));
  if (own !== null) {
    return evaluate(own[1]!, word, module);
  }
  const found = readdirSync(SRC).filter((file) => file.endsWith(".ts"))
    .map((file) => [file.slice(0, -3), new RegExp(`(?:^|\\n)export const ${word}(?:: number)? = ` +
      "([0-9A-Z_ *+()-]+);").exec(readFileSync(join(SRC, file), "utf8"))] as const)
    .filter(([, match]) => match !== null);
  if (found.length !== 1) {
    throw new Error(`${word} is no one numeric constant of the owner's sources`);
  }
  return evaluate(found[0]![1]![1]!, word, found[0]![0]);
}

/** A numeric expression of constants, as `module` spells it. */
function evaluate(expression: string, what: string, module: string): number {
  const value = expression.replace(/(?<=[0-9])_(?=[0-9])/g, "")
    .replace(/[A-Z][A-Z0-9_]*/g, (word) => String(named(word, module)));
  if (!/^[0-9 *+()-]+$/.test(value)) {
    throw new Error(`${what} is no numeric expression`);
  }
  return Function(`"use strict"; return (${value});`)() as number;
}

/**
 * A frozen object of numbers, as its module declares it (`export const name = ...({ ... })`),
 * read from the source, since the module loads the Agents SDK's runtime with it: each
 * field's value, a numeric expression of constants, or a `[low, high]` range's high.
 */
function declared<K extends string>(module: string, name: string, ...keys: K[]):
    Readonly<Record<K, number>> {
  const text = source(module);
  const start = text.indexOf(`export const ${name}`);
  if (start < 0) {
    throw new Error(`${module}.ts declares no ${name}`);
  }
  const body = text.slice(start, text.indexOf("});", start));
  const fields: Record<string, number> = {};
  const field = /([a-z_A-Z]+): (Object\.freeze\(\[[^\]]*\] as const\)|[^,\n]+)/g;
  for (const [, key, raw] of body.slice(body.indexOf("{")).matchAll(field)) {
    const range = /\[\s*[^,]+,\s*(.+?)\]\s*as const/.exec(raw!);
    const expression = (range?.[1] ?? raw!).replace(/(?<=[0-9])_(?=[0-9])/g, "");
    if (!/^[0-9A-Z_ *+()-]+$/.test(expression)) {
      continue;  // not a number of constants: no bound this guard reads
    }
    fields[key!] = evaluate(expression, `${name}.${key}`, module);
  }
  for (const key of keys) {
    const value = fields[key];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`${name}.${key} is no number the guard read`);
    }
  }
  return fields as Readonly<Record<K, number>>;
}

/** A module's `export const NAME = <number>;`, read from its source. */
function constant(module: string, name: string): number {
  return named(name, module);
}

/** A bound a source spells inline, by the pattern that finds its number. */
function inline(module: string, pattern: RegExp): number {
  const found = pattern.exec(source(module));
  if (found === null) {
    throw new Error(`${module}.ts holds no ${pattern}`);
  }
  return Number(found[1]);
}

const CONNECTION_LIMITS = declared("connections", "CONNECTION_LIMITS", "maxConnections",
                                   "maxRevokedKept", "maxModels", "maxFlowRows");
const GRANT_LIMITS = declared("grants", "GRANT_LIMITS", "maxFreeClients", "maxOwnerClients",
                              "maxScopes");
const POLICY_LIMITS = declared("policy", "POLICY_LIMITS", "maxDocument", "maxChange",
                               "pendingProposals", "appliedProposals", "receiptsPerBucket");
const FILE_SETTINGS = declared("policy", "FILE_SETTINGS", "max_manifest_bytes");
const CARD_LIMITS = declared("registrations", "CARD_LIMITS", "maxPending", "maxRequest");
const MAX_OPEN_INTENTS = constant("files", "MAX_OPEN_INTENTS");
const MAX_INTENT_CHUNKS = constant("files", "MAX_INTENT_CHUNKS");
const MAX_PATH_BYTES = constant("paths", "MAX_PATH_BYTES");
const THREAD_INPUTS = constant("schema", "THREAD_INPUTS");
const THREAD_COMPACTIONS = constant("schema", "THREAD_COMPACTIONS");
const THREAD_CONVERSATIONS = constant("schema", "THREAD_CONVERSATIONS");
const THREAD_CHILD_INPUTS = constant("schema", "THREAD_CHILD_INPUTS");
const WORKING_THREADS = constant("schema", "WORKING_THREADS");
const CONVERSATION_COMPACTIONS = constant("schema", "CONVERSATION_COMPACTIONS");
const THREAD_MANUAL_COMPACTIONS = constant("schema", "THREAD_MANUAL_COMPACTIONS");
const TURN_BYTES = constant("schema", "TURN_BYTES");
const CALLS_OPEN = constant("schema", "CALLS_OPEN");
const SPEND_KEPT = constant("schema", "SPEND_KEPT");
/** A connection's models, and its sealed blob, as the schema's checks hold them, in characters. */
const MODELS_CHARS = inline("schema", /length\(models\) <= ([0-9]+)/);
const BLOB_CHARS = inline("schema", /length\(blob\) <= ([0-9]+)/);
/** A scope's every field, and an owner-set client's name, in code points (`grants.ts`). */
const SCOPE_FIELD = inline("grants", /codePoints\(v\) > ([0-9]+)/);
const CLIENT_NAME = inline("grants", /codePoints\(name\) > ([0-9]+)/);
/** The most wakes an hour policy lets an agent take, the rings one hour keeps (`ledger.ts`). */
const WAKES_AN_HOUR = inline("ledger", /maxWakesPerHour: Object\.freeze\(\{ low: [0-9]+, high: ([0-9]+) \}\)/);

/**
 * The owner app's sockets (A6, `sockets.ts`): no rows, only memory, each bounded here: the
 * sockets an owner and a thread hold open (each pinning its thread's Session, counted
 * against `OPEN_MAX`), the frames a socket holds unacknowledged before its watch waits (pi
 * then keeps at most 100 and catches up with one whole view), the longest frame a client
 * sends, and the live tee's text a stream keeps (`live-tee.ts`).
 */
export const SOCKET_MEMORY = Object.freeze({
  ownerSockets: constant("sockets", "OWNER_SOCKETS"),
  threadSockets: constant("sockets", "THREAD_SOCKETS"),
  window: constant("sockets", "SOCKET_WINDOW"),
  clientFrame: constant("sockets", "CLIENT_FRAME_MAX"),
  teeText: constant("live-tee", "TEE_TEXT_MAX"),
});

/** The platform's limit on one object's database. */
export const DATABASE_BYTES = 10_000_000_000;
/** The share of it the owner's worst case may reach. */
export const SHARE = 0.5;
/** A row's fixed overhead past its payload: keys, indexes and SQLite's own record. */
const ROW = 256;
const KIB = 1024;
/** A JSON string's most bytes for each code point: a control character's `\u00XX`. */
const JSON_POINT = 6;

/**
 * One table's worst case: its most rows, and the widest each may be, in bytes of payload;
 * or, for the manifest, the bytes its triggers charge (`charged`), laid out already.
 */
export interface Term {
  readonly rows: number;
  readonly bytes: number;
  readonly charged?: true;
  /** What bounds it. */
  readonly by: string;
}

/**
 * The tables the schema makes `WITHOUT ROWID`, by their final names: their records are
 * kept as an index's are.
 */
export function withoutRowid(schema: string = source("schema")): Set<string> {
  const tables = new Set<string>();
  const statements = /CREATE TABLE ([a-zA-Z_0-9]+) |ALTER TABLE ([a-zA-Z_0-9]+) RENAME TO ([a-zA-Z_0-9]+)|DROP TABLE ([a-zA-Z_0-9]+)/g;
  for (const match of schema.matchAll(statements)) {
    const [, made, from, to, dropped] = match;
    if (made !== undefined) {
      const rest = schema.slice(match.index + match[0].length);
      const next = rest.search(/CREATE |ALTER |DROP |INSERT /);
      if (rest.slice(0, next < 0 ? rest.length : next).includes("WITHOUT ROWID")) {
        tables.add(made);
      } else {
        tables.delete(made);
      }
    } else if (from !== undefined) {
      if (tables.delete(from)) {
        tables.add(to!);
      }
    } else {
      tables.delete(dropped!);
    }
  }
  return tables;
}
const KEPT_AS_INDEX = withoutRowid();

/**
 * The most a term's table may take of the database: each row laid out as SQLite lays it
 * (`recordLayout`), its share of its b-tree page at worst fill (`WORST_FILL`: a page
 * thinned by deletes may stay a third full), its overflow pages whole; the manifest's
 * charge, laid out by its triggers, at worst fill too.
 */
export function termBytes(name: string, term: Term): number {
  if (term.charged === true) {
    return term.rows * term.bytes * WORST_FILL;
  }
  const { inPlace, overflow } = recordLayout(term.bytes,
                                             KEPT_AS_INDEX.has(name) ? INDEX_KEPT : TABLE_KEPT);
  return term.rows * (inPlace * WORST_FILL + overflow);
}

/** A thread's id, as its row holds it. */
const THREAD_ID = 39;
/**
 * Every thread row at once, dropping ones included (each counts until it is forgotten):
 * the owner's conversations, every client's conversations, and the runs whose stores are
 * kept (`runs_kept`, every caller's).
 */
const threadRows = THREAD_LIMITS.owner + THREAD_LIMITS.clients + THREAD_LIMITS.runs;
/** The live runs' bound at the policy's high setting (`runs.runs_live`). */
const RUNS_LIVE = declared("policy", "RUN_SETTINGS", "runs_live").runs_live;
/**
 * The threads that may hold live work at once: every conversation, and the runs not yet
 * settled (`runs_live`, high). A settled run takes no input (`run_settled`, refused at the
 * host's admission, a check below), so it holds no turn, no live row, no wake job and no
 * usage row: only its thread row and its receipt.
 */
const liveRows = THREAD_LIMITS.owner + THREAD_LIMITS.clients + RUNS_LIVE;
/**
 * A thread row at its widest: its ids and store, an agent id of 128 bytes (its directory's
 * name, ASCII, a CHECK), its client hash, the registration its stamp was made from (an
 * outside agent's id, at most 32: the universe is that row's, counted in `outside_agents`),
 * a run's request id, and its numbers and words. It fits in place in its b-tree page.
 */
const THREAD_ROW = THREAD_ID + 32 + 128 + 64 + 32 + 128 + 10 * 8 + 2 * 8;
/**
 * Every thread's turn owners at once: each working thread's (`WORKING_THREADS`, the schema's
 * bound on the threads holding owners, however many are live) open root and subagent
 * inputs and live compactions, a dropping one's too until its row is forgotten.
 */
const TURN_OWNERS = WORKING_THREADS * (THREAD_INPUTS + THREAD_CHILD_INPUTS
                                       + THREAD_COMPACTIONS);

/** The file manifest's tables, together: `max_manifest_bytes` at its high bound, at worst fill. */
const MANIFEST: Term = Object.freeze({
  rows: 1, bytes: FILE_SETTINGS.max_manifest_bytes, charged: true,
  by: "max_manifest_bytes (high) at WORST_FILL, charged by triggers to " +
    Object.keys(MANIFEST_TABLES).join(", "),
});
/** An owner-set grant: its scopes, each three fields of JSON, and its name. */
const GRANT_BYTES = GRANT_LIMITS.maxScopes * (3 * SCOPE_FIELD * JSON_POINT + 64)
  + CLIENT_NAME * 4;

/** Every owner table's worst case; the manifest's tables share one term. */
export const TERMS: Readonly<Record<string, Term>> = Object.freeze<Record<string, Term>>({
  object_owner: { rows: 1, bytes: ROW + 4 * KIB, by: "one row" },
  file_manifest: MANIFEST,
  file_space: { rows: 1, bytes: ROW + MAX_PATH_BYTES + 4 * KIB,
                by: "one row; its retention cursor a path" },
  file_intents: { rows: MAX_OPEN_INTENTS,
                  bytes: ROW + 2 * MAX_INTENT_CHUNKS * 64,
                  by: "MAX_OPEN_INTENTS open uploads, each at most MAX_INTENT_CHUNKS chunk " +
                    "ids, and as many still to send" },
  owner_policy: { rows: 1, bytes: ROW + POLICY_LIMITS.maxDocument * 4,
                  by: "one document of maxDocument code points" },
  policy_receipts: { rows: 3 * POLICY_LIMITS.receiptsPerBucket, bytes: ROW + 1 * KIB,
                     by: "receiptsPerBucket in each of three buckets" },
  policy_proposals: { rows: POLICY_LIMITS.pendingProposals + POLICY_LIMITS.appliedProposals,
                      bytes: ROW + POLICY_LIMITS.maxChange * 4,
                      by: "pending and applied proposals of maxChange code points" },
  proposal_shares: { rows: GRANT_LIMITS.maxFreeClients + GRANT_LIMITS.maxOwnerClients,
                     bytes: ROW, by: "one per client" },
  policy_usage: { rows: 1, bytes: ROW, by: "one row" },
  outside_clients: { rows: GRANT_LIMITS.maxFreeClients + GRANT_LIMITS.maxOwnerClients,
                     bytes: ROW + 8 + GRANT_BYTES * GRANT_LIMITS.maxOwnerClients
                       / (GRANT_LIMITS.maxFreeClients + GRANT_LIMITS.maxOwnerClients),
                     by: "free clients (no scopes, no name) and owner-set ones (maxScopes " +
                       "scopes of three fields, and a name), each with its revision " +
                       "(migration 12)" },
  grant_fences: { rows: 1, bytes: ROW, by: "one row" },
  owner_sessions: { rows: SESSION_LIMITS.maxSessions, bytes: ROW, by: "maxSessions" },
  outside_agents: { rows: LEDGER_LIMITS.maxAgentRows, bytes: ROW + 1 * KIB,
                    by: "maxAgentRows, every row: a tombstone a revoke or a thread holds too" },
  ledger_messages: { rows: LEDGER_LIMITS.maxMessages, bytes: ROW + 1 * KIB,
                     by: "maxMessages" },
  message_bodies: { rows: LEDGER_LIMITS.maxMessages,
                    bytes: ROW + LEDGER_LIMITS.maxBodyBytes / LEDGER_LIMITS.maxMessages,
                    by: "maxBodyBytes over maxMessages" },
  ledger_recipients: { rows: LEDGER_LIMITS.maxMessages * LEDGER_LIMITS.maxAgents,
                       bytes: ROW, by: "each message to each agent" },
  ledger_readers: { rows: LEDGER_LIMITS.maxAgentRows, bytes: ROW, by: "one per agent" },
  ledger_receipts: { rows: LEDGER_LIMITS.maxReceipts, bytes: ROW + 1 * KIB,
                     by: "maxReceipts" },
  ledger_rings: { rows: LEDGER_LIMITS.maxAgentRows * WAKES_AN_HOUR, bytes: ROW,
                  by: "an hour's wakes per agent, at the policy's most an hour" },
  ledger_wakes: { rows: LEDGER_LIMITS.maxAgentRows + LEDGER_LIMITS.maxSettledWakes,
                  bytes: ROW + 1 * KIB, by: "an open wake per agent, maxSettledWakes settled" },
  ledger_usage: { rows: LEDGER_LIMITS.maxAgentRows + 1, bytes: ROW, by: "one per agent" },
  collection_rows: { rows: COLLECTIONS.maxOwnerRows, bytes: ROW + COLLECTIONS.maxOwnerBytes /
                                                              COLLECTIONS.maxOwnerRows,
                     by: "maxOwnerRows; rows and history bytes within maxOwnerBytes" },
  collection_history: { rows: COLLECTIONS.maxOwnerRows * COLLECTIONS.maxRevisions,
                        bytes: ROW + COLLECTIONS.maxOwnerBytes /
                                     (COLLECTIONS.maxOwnerRows * COLLECTIONS.maxRevisions),
                        by: "maxRevisions a row; bytes within maxOwnerBytes" },
  collection_seqs: { rows: COLLECTIONS.maxOwnerRows, bytes: ROW, by: "one per collection" },
  collection_usage: { rows: 4 * COLLECTIONS.maxOwnerRows, bytes: ROW,
                      by: "owner, collection, writer and creator counters" },
  agent_cards: { rows: CARD_LIMITS.maxPending, bytes: ROW + CARD_LIMITS.maxRequest,
                 by: "maxPending cards of maxRequest" },
  owner_notices: { rows: NOTICES_KEPT, bytes: ROW + NOTICE_DETAIL * 4, by: "NOTICES_KEPT" },
  connections: { rows: CONNECTION_LIMITS.maxConnections + CONNECTION_LIMITS.maxRevokedKept,
                 bytes: ROW + 4 * MODELS_CHARS,
                 by: "connections and revoked kept, the schema's models ceiling each" },
  connection_secrets: { rows: CONNECTION_LIMITS.maxConnections
                              + CONNECTION_LIMITS.maxRevokedKept,
                        bytes: ROW + 4 * BLOB_CHARS,
                        by: "one sealed blob a connection, at the schema's ceiling" },
  connection_flows: { rows: CONNECTION_LIMITS.maxFlowRows, bytes: ROW + 4 * KIB,
                      by: "maxFlowRows" },
  model_reservations: { rows: USAGE_LIMITS.receiptsKept + CALLS_OPEN, bytes: ROW + 1 * KIB,
                        by: "receiptsKept settled, and CALLS_OPEN open at once" },
  model_spend: { rows: SPEND_KEPT, bytes: ROW, by: "SPEND_KEPT day and month counters" },
  model_turns: { rows: TURN_OWNERS, bytes: ROW + TURN_BYTES + THREAD_ID,
                 by: "a turn per owner at most: WORKING_THREADS threads at work, each with " +
                   "THREAD_INPUTS open root inputs, THREAD_CHILD_INPUTS subagent inputs and " +
                   "THREAD_COMPACTIONS live compactions (CONVERSATION_COMPACTIONS for each " +
                   "of THREAD_CONVERSATIONS live conversations, and THREAD_MANUAL_COMPACTIONS " +
                   "the compact door starts), TURN_BYTES each" },
  model_turn_owners: { rows: TURN_OWNERS, bytes: ROW + TURN_BYTES + 2 * THREAD_ID,
                       by: "one per open input or live compaction, a thread each, indexed" },
  // The agent's threads: rows only, every transcript in its own store.
  agent_threads: { rows: threadRows, bytes: ROW + THREAD_ROW,
                   by: "THREAD_LIMITS (the owner's conversations, every client's, the runs)" },
  agent_live: { rows: liveRows, bytes: ROW, by: "one per live thread" },
  agent_usage: { rows: liveRows, bytes: ROW + Math.min(USAGE_ROW_MAX,
                                                       USAGE_SPACE_MAX / liveRows),
                 by: "one per live thread, USAGE_SPACE_MAX of usage in all" },
  agent_usage_space: { rows: 1, bytes: ROW, by: "one row" },
  agent_usage_total: { rows: 1, bytes: ROW + USAGE_ROW_MAX,
                       by: "one row, its keys folded to USAGE_ROW_MAX" },
  // A run's receipt, a history its row bound keeps.
  run_receipts: { rows: THREAD_LIMITS.receipts,
                  bytes: ROW + THREAD_ID + 64 + 128 + RECEIPT_REASON_MAX * 4 + RECEIPT_TEXT_MAX
                    + 8,
                  by: "THREAD_LIMITS.receipts, RECEIPT_TEXT_MAX of text each" },
  owner_erasure: { rows: 1, bytes: ROW, by: "one row" },
  // The owner's admission: one row, its reasons a short fixed list; after the erasure, the
  // erased tombstone, alone in the database.
  owner_admission: { rows: 1, bytes: ROW + 64, by: "one row (the schema's CHECK)" },
});

/**
 * What the runtime and Lifecycle keep in the owner's database beside its own tables, which
 * the schema does not make: Lifecycle's job table, one wake job per thread, the lanes' and
 * the erasure's; and the key-value store, a wake-failure count per thread, the memory-limit
 * strike counter and Lifecycle's own few keys.
 */
export const RUNTIME: Readonly<Record<string, Term>> = Object.freeze<Record<string, Term>>({
  cf_agents_jobs: { rows: liveRows + 16, bytes: ROW + 256,
                    by: "a wake job per live thread, a job per lane, the erasure's" },
  _cf_KV: { rows: liveRows + 16, bytes: ROW + 128,
            by: "a wake-failure count per live thread, and Lifecycle's own keys" },
});

/**
 * Every table the owner's schema makes, by its final name: its statements in order, a
 * rename moving a table and a drop taking one away (migration 13 drops the `work` lane's).
 */
export function ownerTables(schema: string = source("schema")): string[] {
  const tables = new Set<string>();
  const statements = /CREATE (?:VIRTUAL )?TABLE (?:IF NOT EXISTS )?([a-zA-Z_0-9]+)|ALTER TABLE ([a-zA-Z_0-9]+) RENAME TO ([a-zA-Z_0-9]+)|DROP TABLE (?:IF EXISTS )?([a-zA-Z_0-9]+)/g;
  for (const [, made, from, to, dropped] of schema.matchAll(statements)) {
    if (made !== undefined) {
      tables.add(made);
    } else if (from !== undefined) {
      tables.delete(from);
      tables.add(to!);
    } else {
      tables.delete(dropped!);
    }
  }
  tables.delete("schema_version");
  return [...tables].sort();
}

/** The indexes the schema makes on the manifest's tables. */
export function manifestIndexes(schema: string = source("schema")): string[] {
  return [...schema.matchAll(/CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?([a-zA-Z_0-9]+) ON ([a-zA-Z_0-9]+)/g)]
    .filter((match) => Object.hasOwn(MANIFEST_TABLES, match[2]!)).map((match) => match[1]!)
    .sort();
}

/** The tables the terms cover: each term's own, and the manifest's. */
export function covered(terms: Readonly<Record<string, Term>> = TERMS): string[] {
  return [...Object.keys(terms).filter((name) => name !== "file_manifest"),
          ...(terms["file_manifest"] === undefined ? [] : Object.keys(MANIFEST_TABLES))];
}

/**
 * The manifest tables the schema does not charge: each needs a trigger by its name after
 * an insert into (and a delete from) the table it is charged through, moving
 * `manifest_bytes`. A content's chunk list is charged through its content.
 */
export function uncharged(schema: string = source("schema")): string[] {
  const missing: string[] = [];
  for (const [table, [added, removed]] of Object.entries(MANIFEST_TABLES)) {
    const on = table === "file_content_chunks" ? "file_contents" : table;
    for (const [trigger, event] of [[added, "INSERT"], [removed, "DELETE"]] as const) {
      const at = schema.lastIndexOf(`CREATE TRIGGER ${trigger} AFTER ${event} ON ${on} `);
      const body = at < 0 ? "" : schema.slice(at, schema.indexOf(" END", at));
      if (!body.includes("manifest_bytes")) {
        missing.push(`${table}: ${trigger}`);
      }
    }
  }
  return missing;
}

/** The worst case of the bounded tables and the runtime's rows, in bytes. */
export function worstCase(terms: Readonly<Record<string, Term>> = { ...TERMS, ...RUNTIME }):
    number {
  return Object.entries(terms).reduce((sum, [name, term]) => sum + termBytes(name, term), 0);
}

describe("the owner's database bound", () => {
  it("covers every table the owner's schema makes, with no exceptions", () => {
    const tables = ownerTables();
    expect(tables.length).toBeGreaterThan(30);
    const named = covered();
    expect(tables.filter((table) => !named.includes(table))).toEqual([]);
    expect(named.filter((table) => !tables.includes(table))).toEqual([]);
    expect(new Set(named).size).toBe(named.length);
    expect(tables.filter((table) => table.startsWith("pi_"))).toEqual([]);
    // A virtual table, or a name with a digit, is a table too; a dropped one is not.
    expect(ownerTables('"CREATE VIRTUAL TABLE fts5_index USING fts5(x)", ' +
                       '"CREATE TABLE IF NOT EXISTS t2 (a)"')).toEqual(["fts5_index", "t2"]);
    expect(ownerTables('"CREATE TABLE a (x)", "CREATE TABLE b (x)", "DROP TABLE a"'))
      .toEqual(["b"]);
    expect(tables).not.toContain("scheduled_work");
    expect(tables).toContain("owner_admission");
  });

  it("finds every manifest table charged by the schema's triggers, and every index charged",
     () => {
    expect(uncharged()).toEqual([]);
    // A trigger that charges nothing, or none, is seen.
    const schema = source("schema");
    expect(uncharged(schema.replaceAll("CREATE TRIGGER file_history_added", "CREATE TRIGGER x")))
      .toEqual(["file_history: file_history_added"]);
    const charge = /(CREATE TRIGGER file_temps_removed AFTER DELETE ON file_temps BEGIN )[^`]*`/;
    expect(uncharged(schema.replace(charge, "$1 END`")))
      .toEqual(["file_temps: file_temps_removed"]);
    expect(manifestIndexes()).toEqual([...MANIFEST_INDEXES].sort());
    expect(manifestIndexes(`${schema} "CREATE INDEX file_entries_by_writer ON file_entries ` +
                           '(writer)"')).toContain("file_entries_by_writer");
  });

  it("keeps its worst case, at the policy's high bounds, under half the 10 GB", () => {
    const bytes = worstCase();
    expect(bytes).toBeLessThan(SHARE * DATABASE_BYTES);
    // The manifest at its high bound and its worst fill, the largest term, is counted whole.
    expect(FILE_SETTINGS.max_manifest_bytes).toBe(512 * 1024 ** 2);
    expect(WORST_FILL).toBe(2.25);
    expect(termBytes("file_manifest", MANIFEST))
      .toBe(FILE_SETTINGS.max_manifest_bytes * WORST_FILL);
    expect(Object.entries(TERMS).every(([name, term]) => termBytes(name, term)
                                         <= termBytes("file_manifest", MANIFEST))).toBe(true);
    console.log(`the owner's worst case: ${bytes} bytes (${(bytes / DATABASE_BYTES * 100)
      .toFixed(1)}% of ${DATABASE_BYTES})`);
  });

  it("counts every row as SQLite lays it out, at worst fill, its overflow in whole pages",
     () => {
    // A small row's share of its page, at a third full; a spilled one's overflow whole.
    expect(termBytes("x", { rows: 3, bytes: 256, by: "" }))
      .toBe(3 * Math.floor(4096 / Math.floor(3066 / 260)) * WORST_FILL);
    expect(termBytes("x", { rows: 1, bytes: 4061 + 1, by: "" }))
      .toBe(Math.floor(4096 / Math.floor(3066 / 493)) * WORST_FILL + 4096);
    // A `WITHOUT ROWID` table's records are kept as an index's: they spill past 1,002.
    expect(termBytes("model_turn_owners", { rows: 1, bytes: 1003, by: "" }))
      .toBe(Math.floor(4096 / Math.floor(3066 / 493)) * WORST_FILL + 4096);
    expect(["model_turns", "model_turn_owners", "agent_usage", "agent_live"]
      .filter((table) => !withoutRowid().has(table))).toEqual([]);
    expect(withoutRowid().has("model_reservations")).toBe(false);
    expect(withoutRowid('"CREATE TABLE a (x) WITHOUT ROWID", "CREATE TABLE b (x)", ' +
                        '"ALTER TABLE a RENAME TO c"')).toEqual(new Set(["c"]));
  });

  it("counts the bounds the sources hold", () => {
    // The turn owners' bound is the schema's check, by the constants this counts.
    const schema = source("schema");
    const full = schema.slice(schema.indexOf("CREATE TRIGGER model_turn_owners_full BEFORE " +
                                             "INSERT ON model_turn_owners"));
    const trigger = full.slice(0, full.indexOf("END\""));
    expect(trigger).toMatch(/WHEN 's' THEN \$\{THREAD_INPUTS\} WHEN 'c' THEN \$\{\s*THREAD_CHILD_INPUTS\} ELSE \$\{THREAD_COMPACTIONS\} END/);
    expect(trigger).toMatch(/COUNT\(DISTINCT thread\) FROM model_turn_owners\) >= \$\{WORKING_THREADS\}/);
    expect([MAX_INTENT_CHUNKS, CHUNK_BYTES, constant("files", "KEEP_CEILING")])
      .toEqual([10_000, 4 * 1024 ** 2, 1000]);
    expect([THREAD_INPUTS, THREAD_COMPACTIONS, CALLS_OPEN, SPEND_KEPT, MODELS_CHARS,
            BLOB_CHARS]).toEqual([8, 7, 1000, 4300, 655_360, 65_536]);
    // A thread's compactions are each of its live conversations' (A5's subagents), and the
    // turn rows are the working threads' (A5), not every live thread's.
    expect([THREAD_CONVERSATIONS, CONVERSATION_COMPACTIONS, THREAD_CHILD_INPUTS,
            WORKING_THREADS]).toEqual([3, 2, 2, 1024]);
    expect(schema).toMatch(/export const THREAD_CHILD_INPUTS = THREAD_CONVERSATIONS - 1;/);
    // The host admits fresh inputs within all but `OPEN_MAX` of the working threads, so a
    // compaction pi starts in an open Session always has a place under the trigger's bound.
    expect(source("thread-host")).toContain("workingMax: WORKING_THREADS - OPEN_MAX,");
    expect(constant("thread-host", "OPEN_MAX")).toBe(64);
    // A6: the compact door's manual compactions, a thread's own bound, counted with them.
    expect(THREAD_MANUAL_COMPACTIONS).toBe(1);
    expect(THREAD_COMPACTIONS).toBe(THREAD_CONVERSATIONS * CONVERSATION_COMPACTIONS
                                    + THREAD_MANUAL_COMPACTIONS);
    expect(schema).toMatch(/export const THREAD_COMPACTIONS = THREAD_CONVERSATIONS \* /);
    expect(source("thread-host")).toContain(
      "if (manual + slot.compacting >= THREAD_MANUAL_COMPACTIONS) {\n" +
      "          throw new Refused(COMPACTIONS_FULL);");
    expect([SCOPE_FIELD, CLIENT_NAME, WAKES_AN_HOUR, GRANT_LIMITS.maxScopes])
      .toEqual([512, 80, 60, 64]);
    expect(TERMS["model_turns"]).toMatchObject({
      rows: 1024 * (8 + 2 + 7),
      bytes: 256 + TURN_BYTES + 39 });
    expect([TURN_BYTES, USAGE_ROW_MAX]).toEqual([96, 16 * 1024]);
    expect(TERMS["model_turn_owners"]).toMatchObject({
      rows: TERMS["model_turns"]!.rows, bytes: 256 + TURN_BYTES + 2 * 39 });
    expect(TERMS["file_intents"]).toMatchObject({
      rows: MAX_OPEN_INTENTS, bytes: 256 + 2 * 640_000 });
    expect(TERMS["model_reservations"]!.rows).toBe(USAGE_LIMITS.receiptsKept + 1000);
    expect(TERMS["model_spend"]!.rows).toBe(4300);
    expect(TERMS["ledger_rings"]!.rows).toBe(LEDGER_LIMITS.maxAgentRows * 60);
    expect(TERMS["connection_secrets"]!.bytes).toBe(256 + 4 * 65_536);
    expect(TERMS["outside_clients"]!.rows * TERMS["outside_clients"]!.bytes)
      .toBe(2000 * (256 + 8) + 1000 * (64 * (3 * 512 * 6 + 64) + 80 * 4));
  });

  it("counts every run whose store is kept, every receipt, the usage of each thread and " +
     "Lifecycle's own rows", () => {
    expect(TERMS["agent_threads"]!.rows).toBe(threadRows);
    expect(threadRows).toBe(THREAD_LIMITS.owner + THREAD_LIMITS.clients + THREAD_LIMITS.runs);
    // A thread row names its registration, never a universe: it fits in place, at its widest.
    expect(KEPT_AS_INDEX.has("agent_threads")).toBe(true);
    expect(TERMS["agent_threads"]!.bytes).toBeGreaterThan(700);
    expect(TERMS["agent_threads"]!.bytes).toBeLessThanOrEqual(INDEX_KEPT);
    expect(source("schema")).toContain("\"authenticated_at REAL, registration TEXT CHECK " +
                                       "(registration IS NULL \" +");
    expect(source("schema")).toContain("\"AND agent_id NOT GLOB '*[^A-Za-z0-9._-]*'), \" +");
    expect(RUNTIME["cf_agents_jobs"]!.rows).toBeGreaterThan(liveRows);
    expect(RUNS_LIVE).toBe(2_000);
    expect(worstCase()).toBe(worstCase(TERMS) + worstCase(RUNTIME));
    expect(TERMS["run_receipts"]!.rows).toBe(THREAD_LIMITS.receipts);
    expect(TERMS["run_receipts"]!.bytes).toBeGreaterThan(RECEIPT_TEXT_MAX);
    // The usage rows' sum is the space bound, not each row's bound times every thread.
    expect(TERMS["agent_usage"]!.rows * TERMS["agent_usage"]!.bytes)
      .toBeLessThanOrEqual(liveRows * ROW + USAGE_SPACE_MAX);
    expect(liveRows * USAGE_ROW_MAX).toBeGreaterThan(USAGE_SPACE_MAX);
  });

  it("counts every registration row, a tombstone a revoke or a thread holds among them, " +
     "which the registry refuses past", () => {
    // Every row is counted as it is made and refused past maxAgentRows; a tombstone is one.
    expect(source("schema")).toContain("\"CREATE TRIGGER outside_agents_added AFTER INSERT ON " +
                                       "outside_agents BEGIN \" +\n      \"UPDATE ledger_usage " +
                                       "SET agents = agents + 1, \" +");
    expect(source("ledger")).toContain("if (usage.agents >= limits.maxAgentRows) {");
    expect(source("schema")).toContain("\"CREATE TRIGGER outside_agents_named BEFORE DELETE ON " +
                                       "outside_agents WHEN EXISTS \" +");
  });

  it("holds one conversation a caller, agent and universe in the recording unit, which no " +
     "index can read the universe for", () => {
    const threads = source("threads");
    const record = threads.slice(threads.indexOf("  record(agent: unknown"));
    const body = record.slice(0, record.indexOf("\n  }\n"));
    const found = body.indexOf("      const held = this.conversing(by, id);");
    expect(found).toBeGreaterThan(0);
    expect(found).toBeLessThan(body.indexOf("INSERT INTO agent_threads"));
    expect(body.indexOf("if (kind === \"converse\") {")).toBeLessThan(found);
    expect(source("schema")).toContain("\"(IFNULL(client, ''), agent_id, " +
                                       "IFNULL(registration, '')) \" +");
  });

  it("holds what it counts settled runs by: a settled run takes no input, refused at the " +
     "host's admission before pi commits one", () => {
    const host = source("thread-host");
    const submit = host.slice(host.indexOf("  submit(thread: string"));
    const body = submit.slice(0, submit.indexOf("\n  }\n"));
    const refused = body.indexOf("if (fresh && this.#parts.settled(thread)) {\n" +
                                 "          throw new Refused(RUN_SETTLED);");
    expect(refused).toBeGreaterThan(0);
    expect(refused).toBeLessThan(body.indexOf("await conversation.submit("));
    // No run settles in a unit that finds an input being admitted, so none is refused late.
    const agent = source("agent");
    const quiet = agent.indexOf("      if (!from.quiet()) {\n        return false;");
    expect(quiet).toBeGreaterThan(0);
    expect(quiet).toBeLessThan(agent.indexOf("      threads.settle(thread, kept,"));
    // Its turn owners go in its settling unit, a lost unit's too.
    expect(source("schema")).toContain("\"DELETE FROM model_turn_owners WHERE thread = " +
                                       "NEW.thread_id; \" +");
    // The usage of a settled run goes to the total; no row of its own is kept.
    const threads = source("threads");
    expect(threads).toContain("\"WHERE thread_id = ? AND settled_at IS NULL \" +");
    expect(agent).toContain("settled: (thread) => parts.threads.settledRun(thread),");
  });

  it("names the app sockets' bounds, which hold memory and no rows (A6)", () => {
    // Their state is each socket's attachment: the sockets module writes no table.
    const sockets = source("sockets");
    expect(sockets).not.toMatch(/\.sql\b|storage\.|CREATE TABLE|kv\./);
    expect(ownerTables().filter((table) => /socket/.test(table))).toEqual([]);
    expect(SOCKET_MEMORY).toEqual({ ownerSockets: 16, threadSockets: 4, window: 16,
                                    clientFrame: 1024, teeText: 1_048_576 });
    // Each watched Session is pinned and counted against OPEN_MAX: an owner's sockets,
    // however many threads they watch, pin at most a quarter of the open Sessions.
    expect(SOCKET_MEMORY.ownerSockets * 4).toBeLessThanOrEqual(constant("thread-host", "OPEN_MAX"));
    expect(SOCKET_MEMORY.threadSockets).toBeLessThanOrEqual(SOCKET_MEMORY.ownerSockets);
  });

  it("sees a bound that would pass half the limit, or a table it no longer covers", () => {
    expect(worstCase({ ...TERMS, file_manifest: { rows: 1, bytes: 5e9, by: "too much" } }))
      .toBeGreaterThan(SHARE * DATABASE_BYTES);
    expect(worstCase({ ...TERMS, model_turns: { rows: 1e7, bytes: 896, by: "too many" } }))
      .toBeGreaterThan(SHARE * DATABASE_BYTES);
    const { model_turns: _turns, ...fewer } = TERMS;
    expect(ownerTables().filter((table) => !covered(fewer).includes(table)))
      .toEqual(["model_turns"]);
    const { file_manifest: _manifest, ...unbounded } = TERMS;
    expect(ownerTables().filter((table) => !covered(unbounded).includes(table)))
      .toEqual(Object.keys(MANIFEST_TABLES).sort());
  });
});
