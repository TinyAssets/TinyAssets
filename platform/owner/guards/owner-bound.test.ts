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
 * (`files-share.test.ts`). The collection history is bounded the same way, in the bytes
 * its entries take (their triggers' charge, `maxHistoryCharge`). Every other table's rows are
 * laid out as SQLite lays them, their share of a b-tree page at that worst fill too (at most
 * the whole page: a record is on its page however thinned), their overflow pages whole
 * (`termBytes`). A body past `PLACE_BYTES` is never in a row (ADR-035): it is in R2, and
 * its row keeps a content id, so a body's own size moves no term. No agent's records live
 * in the owner's database: agents run on unmodified engines, their sessions in the owner's
 * files (ADR-034), so what the owner's object holds is its own bounded rows only.
 *
 * The worst case, with the limits that ship (`limits-audit.md`, `limits-audit.test.ts`), is
 * 44.99% of the limit (4,499,205,219.5 bytes), 500.8 MB under half; the starter slice's
 * receipts (`STARTER_FILES_MAX` paths) and its one state row take 361,189 of it. Slice S's columns
 * (a run's `sandbox_wait`, one byte in `runs`, in place of a recheck time) move no term
 * at its page share, and it adds no table: the sandbox Worker keeps its own. Slice W added
 * `wake_callbacks` (`SUBSCRIPTIONS_PER_AGENT` doorbells for each of `maxAgents` enabled
 * agents, each its sealed blobs and arguments at their widest, and its index) and took out
 * `ledger_rings` and the revoke tombstones' rows. Slice ID added `native_agents` (the
 * owner's own agents in the registry's rows), a mail's authority and attendance, and a
 * run's mail; it adds no index (an identity and a mail thread's conversation are found by
 * their keys, digests). Slice M added each collection row's owner-wide change and its
 * `collection_changes` entry, an erase entry's change, private writer and
 * `collection_erasures` entry (all in the collections' charges), and the one-row
 * `collection_clock`. Untangle H1 keyed the history by its change, so its age index and its
 * erase entries' index went, and moved the deletions to their own table, `collection_erasures`,
 * in the history's charge with no index. A new table, or a raised bound, must stay under half
 * the limit, or the guard fails.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { exported } from "../src/export";
import { LEDGER_LIMITS } from "../src/ledger";
import { ARCHIVE_SEGMENTS_MAX, BLOOM_BYTES, SEGMENT_LEAST_BYTES } from "../src/segments";
import {
  ARGUMENTS_CHARS, AUTHORITY_CHARS, CALLS_OPEN, CHUNK_BYTES, CONNECTION_BLOB_MAX,
  CONVERSATION_ID_CHARS, CONVERSATIONS_KEPT, CURSOR_CHARS, DOORBELL_BLOB_CHARS, EGRESS_JSON_MAX,
  EGRESS_PATH_MAX, ENGINE_SIGNINS, ERROR_BYTES, IMAGE_MAX, INBOUND_ROUTE_MAX, INBOUND_SECRET_MAX,
  INPUT_BYTES, KEY_NAME_MAX, LOG_BYTES, LOG_ROW_BYTES, MANIFEST_PARTS, MAX_PATH_BYTES,
  MESSAGE_ID_CHARS, MODELS_JSON_MAX, NATIVE_ID_CHARS, NOTICE_DETAIL, PART_ENTRY, PLACE_BYTES,
  QUEUE_BYTES, QUEUE_ROWS, RESULT_BYTES, RUN_ID_CHARS, SCHEDULE_ID_CHARS, SCHEDULES_KEPT,
  SCOPES_JSON_MAX, SEGMENT_ENTRY, SIGNIN_CODE_MAX, SIGNIN_LIST_CHARS, SIGNIN_MODELS_CHARS,
  SIGNIN_URL_MAX, SOCKET_TICKETS, SPEND_KEPT, SUBSCRIPTIONS_PER_AGENT,
} from "../../contract/src/limits";
import {
  INDEX_KEPT, MANIFEST_INDEXES, MANIFEST_TABLES, PAGE_BYTES, RECORD_FIXED,
  recordLayout, TABLE_KEPT, WORST_FILL,
} from "../src/manifest";
import { AUTOINDEXES } from "./autoindexes";
import { NOTICES_KEPT } from "../src/notices";
import { MIGRATIONS } from "../src/schema";
import { SESSION_LIMITS } from "../src/sessions";
import { USAGE_LIMITS } from "../src/usage";
import { PLATFORM } from "../../ci/paths";
import { RUNS_CEILING } from "../../contract/src/control";

const SRC = join(PLATFORM, "owner", "src");

function source(module: string): string {
  return readFileSync(join(SRC, `${module}.ts`), "utf8");
}

/** The migration mechanism's source, whose own two tables every object keeps. */
const MIGRATE = readFileSync(join(PLATFORM, "contract", "src", "migrate.ts"), "utf8");

/** The owner's schema: its migrations' SQL, every statement in order, as the object runs it. */
const SCHEMA = MIGRATIONS.flat().join("\n");

/** A numeric constant's expression: `const NAME = <expr>;`, exported or the module's own. */
const NUMERIC = (name: string) =>
  new RegExp(`(?:^|\\n)(?:export )?const ${name}(?:: number)? = ([0-9A-Z_ *+()-]+);`);

/** Where the bounds the guard reads are spelled: the owner's sources and the contract's. */
const BOUND_SOURCES: readonly string[] = Object.freeze([SRC, join(PLATFORM, "contract", "src")]);
/** The policy document's definition, its bounds with it: the contract's (`policy-document.ts`). */
const POLICY_DOCUMENT = "../../contract/src/policy-document";

/**
 * A name a bound is spelled with, read from its source: the module's own constant, else
 * the one constant of that name another owner or contract module exports.
 */
function named(word: string, module: string, from: string = SRC): number {
  const dir = existsSync(join(from, `${module}.ts`)) ? from
    : BOUND_SOURCES.find((at) => existsSync(join(at, `${module}.ts`))) ?? from;
  const own = NUMERIC(word).exec(readFileSync(join(dir, `${module}.ts`), "utf8"));
  if (own !== null) {
    return evaluate(own[1]!, word, module, dir);
  }
  const found = BOUND_SOURCES.flatMap((at) => readdirSync(at)
    .filter((file) => file.endsWith(".ts"))
    .map((file) => [at, file.slice(0, -3), new RegExp(`(?:^|\\n)export const ${word}(?:: number)? = ` +
      "([0-9A-Z_ *+()-]+);").exec(readFileSync(join(at, file), "utf8"))] as const))
    .filter(([, , match]) => match !== null);
  if (found.length !== 1) {
    throw new Error(`${word} is no one numeric constant of the owner's or the contract's sources`);
  }
  return evaluate(found[0]![2]![1]!, word, found[0]![1], found[0]![0]);
}

/** A numeric expression of constants, as `module` spells it. */
function evaluate(expression: string, what: string, module: string, dir: string = SRC): number {
  const value = expression.replace(/(?<=[0-9])_(?=[0-9])/g, "")
    .replace(/[A-Z][A-Z0-9_]*/g, (word) => String(named(word, module, dir)));
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

/**
 * A bound a source spells inline (the schema's SQL for `schema`), by the pattern that finds
 * its number.
 */
function inline(module: string, pattern: RegExp): number {
  const found = pattern.exec(module === "schema" ? SCHEMA : source(module));
  if (found === null) {
    throw new Error(`${module}.ts holds no ${pattern}`);
  }
  return Number(found[1]);
}

const CONNECTION_LIMITS = declared("connections", "CONNECTION_LIMITS", "maxConnections",
                                   "maxRevokedKept", "maxModels", "maxFlowRows");
const EGRESS_LIMITS = declared("egress", "EGRESS_LIMITS", "openPerRun", "receiptsKept",
                               "refusalsKept");
const GRANT_LIMITS = declared("grants", "GRANT_LIMITS", "maxFreeClients", "maxOwnerClients",
                              "maxScopesBytes");
const POLICY_LIMITS = declared(POLICY_DOCUMENT, "POLICY_LIMITS", "maxDocument", "maxChange",
                               "pendingProposals", "appliedProposals", "receiptsPerBucket");
const FILE_SETTINGS = declared(POLICY_DOCUMENT, "FILE_SETTINGS", "max_manifest_bytes");
const CARD_LIMITS = declared("registrations", "CARD_LIMITS", "maxPending", "maxRequest");
const MAX_OPEN_INTENTS = constant("files", "MAX_OPEN_INTENTS");
const MAX_INTENT_CHUNKS = constant("files", "MAX_INTENT_CHUNKS");
/** The runs' bounds (slice R): live runs, settled receipts kept, and their rows' widths. */
const RUNS_KEPT = constant("runs", "RUNS_KEPT");
const EXPORTS_OPEN = constant("export", "EXPORTS_OPEN");
const SPANS_KEPT = constant("sandbox-time", "SPANS_KEPT");
const DAYS_KEPT = constant("sandbox-time", "DAYS_KEPT");
const BILLED_KEPT = constant("sandbox-time", "BILLED_KEPT");
const LIVE_KEPT = constant("sandbox-time", "LIVE_KEPT");
const INLINE_FILE = constant("files", "INLINE_BYTES");
/** The doors' records in the object (slice L): the key store and the inbound bindings. */
const MAX_KEYS = constant("keys", "MAX_KEYS");
const MAX_BINDINGS = constant("inbound", "MAX_BINDINGS");
/** Mail that starts runs (slice ID): a message's id, an own agent's id, a mail's authority. */
/** A stamp's JSON, as the schema's check holds it, in characters (each up to 4 bytes). */
const STAMP_CHARS = inline("schema", /stamp TEXT CHECK \(stamp IS NULL OR length\(stamp\) <= ([0-9]+)\)/);
/** An origin's JSON is visible ASCII (the schema's check), so a character is a byte. */
const ORIGIN_CHARS = inline("schema", /origin TEXT CHECK \(origin IS NULL OR \(length\(origin\) <= ([0-9]+) /);
/**
 * A connection's models, in bytes (review L5-1), its sealed blob, in characters, and its
 * egress scope (migration 11), in bytes: frozen bounds, which the schema's checks spell
 * (the frozen-bounds guard holds the two equal).
 */
const MODELS_BYTES = MODELS_JSON_MAX;
const BLOB_CHARS = CONNECTION_BLOB_MAX;
const EGRESS_BYTES = EGRESS_JSON_MAX;
/** A scope's every field, and an owner-set client's name, in code points (`grants.ts`). */
const SCOPE_FIELD = inline("grants", /codePoints\(v\) > ([0-9]+)/);
/** A grant's scopes' JSON, in bytes: a frozen bound, which the schema's check spells. */
const SCOPES_BYTES = SCOPES_JSON_MAX;
/** A room id, in code points (`ledger.ts`), each up to four bytes. */
const ROOM_CHARS = inline("ledger", /text\(roomId, ([0-9]+), "room_id"\)/);
/** A client message id and a parent id, in code points (`ledger.ts`). */
const CMID_CHARS = inline("ledger", /text\(clientMessageId, ([0-9]+), "client_message_id"\)/);
const PARENT_CHARS = inline("ledger", /text\(parentId, ([0-9]+), "parent_id"\)/);
/** An agent's universe (`command_center_id`) and its recipe id, in code points. */
const UNIVERSE_CHARS = inline("ledger", /text\(request\.command_center_id, ([0-9]+), /);
const RECIPE_CHARS = inline("ledger", /text\(request\.recipe_id, ([0-9]+), /);
/** An agent's display name, in code points (`ledger.ts`'s `displayName`). */
const AGENT_NAME_CHARS = 80;
/** Ids the object mints: an agent's (`agent_` and 16 hex), a message's (`msg_` and 32). */
const AGENT_ID = 22;
const MESSAGE_ID = 36;
/** A proposal's or card's id, in code points (`policy.ts`). */
const PROPOSAL_ID_CHARS = inline("policy", /codePoints\(value\) <= ([0-9]+)/);
/** An integer's or a real's most bytes in a record. */
const NUMBER = 9;
/** A content id: 64 hex (ADR-035's placed body, the files plane's content). */
const CONTENT_ID = 64;
/** A row's files scope: `owner`, or `client:` and 64 hex. */
const FILER = 71;
const CLIENT_NAME = inline("grants", /codePoints\(name\) > ([0-9]+)/);

/** The platform's limit on one object's database. */
export const DATABASE_BYTES = 10_000_000_000;
/** The share of it the owner's worst case may reach. */
export const SHARE = 0.5;
/** A row's fixed overhead past its payload: keys, indexes and SQLite's own record. */
const ROW = 256;
/** A grant row's vendor: an `apps.json` id, at most this many ASCII bytes (migration 4). */
const VENDOR = inline("schema", /length\(vendor\) BETWEEN 1 AND ([0-9]+)/);
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
  /**
   * Each index entry a row makes, by the index's name in `sqlite_master` (an explicit one, or
   * SQLite's `sqlite_autoindex_<table>_<n>` for a key or a `UNIQUE`), and its key's widest
   * bytes, the rowid (or a `WITHOUT ROWID` table's key) among them: each laid out as an
   * index's record. A charged term names the indexes its charge already lays out, at 0.
   */
  readonly indexes?: Readonly<Record<string, number>>;
  /** The tables a charged term covers, when not its own name alone. */
  readonly tables?: readonly string[];
}

/**
 * The tables the schema makes `WITHOUT ROWID`, by their final names: their records are
 * kept as an index's are.
 */
export function withoutRowid(schema: string = SCHEMA): Set<string> {
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
  // A record is on its page however thinned the page: it takes at most the whole page.
  const laid = (payload: number, kept: number) => {
    const { inPlace, overflow } = recordLayout(payload, kept);
    return Math.min(inPlace * WORST_FILL, PAGE_BYTES) + overflow;
  };
  const entries = Object.values(term.indexes ?? {})
    .reduce((sum, key) => sum + laid(key + RECORD_FIXED, INDEX_KEPT), 0);
  return term.rows * (laid(term.bytes, KEPT_AS_INDEX.has(name) ? INDEX_KEPT : TABLE_KEPT)
                      + entries);
}

/** The bounds a term is made from, each module's own: what a proposed raise overrides. */
export interface Bounds {
  readonly collections?: Partial<typeof COLLECTIONS>;
  readonly ledger?: Partial<typeof LEDGER_LIMITS>;
  readonly grants?: Partial<typeof GRANT_LIMITS>;
  readonly sessions?: Partial<typeof SESSION_LIMITS>;
  readonly notices?: Partial<{ kept: number; detail: number }>;
  readonly egress?: Partial<typeof EGRESS_LIMITS>;
  readonly policy?: Partial<typeof POLICY_LIMITS>;
  readonly cards?: Partial<typeof CARD_LIMITS>;
  readonly usage?: Partial<typeof USAGE_LIMITS>;
  readonly connections?: Partial<typeof CONNECTION_LIMITS>;
}

/**
 * Every owner table's worst case at `bounds` (the sources' own unless overridden); the
 * manifest's tables share one term.
 */
export function termsFor(bounds: Bounds = {}): Readonly<Record<string, Term>> {
  const C = { ...COLLECTIONS, ...bounds.collections };
  const L = { ...LEDGER_LIMITS, ...bounds.ledger };
  const G = { ...GRANT_LIMITS, ...bounds.grants };
  const S = { ...SESSION_LIMITS, ...bounds.sessions };
  const N = { kept: NOTICES_KEPT, detail: NOTICE_DETAIL, ...bounds.notices };
  const E = { ...EGRESS_LIMITS, ...bounds.egress };
  const P = { ...POLICY_LIMITS, ...bounds.policy };
  const K = { ...CARD_LIMITS, ...bounds.cards };
  const U = { ...USAGE_LIMITS, ...bounds.usage };
  const CN = { ...CONNECTION_LIMITS, ...bounds.connections };
  const F = FILE_SETTINGS;
  /** The file manifest's tables, together: `max_manifest_bytes` at its high bound, at worst fill. */
  const MANIFEST: Term = Object.freeze({
    rows: 1, bytes: F.max_manifest_bytes, charged: true,
    by: "max_manifest_bytes (high) at WORST_FILL, charged by triggers to " +
      Object.keys(MANIFEST_TABLES).join(", "),
  });
  /** An index of a rowid table: its key's bytes and the rowid. */
  const key = (...bytes: number[]) => bytes.reduce((a, b) => a + b, NUMBER);
  /** An owner-set grant: its scopes, counted in bytes as the schema holds them, and its name. */
  const GRANT_BYTES = Math.min(G.maxScopesBytes, SCOPES_BYTES) + CLIENT_NAME * 4;

  return Object.freeze<Record<string, Term>>({
    object_owner: { rows: 1, bytes: ROW + 4 * KIB, by: "one row" },
    file_manifest: MANIFEST,
    file_space: { rows: 1, bytes: ROW + MAX_PATH_BYTES + 4 * KIB,
                  by: "one row; its retention cursor a path" },
    file_intents: { rows: MAX_OPEN_INTENTS,
                    bytes: ROW + 64 + 2 * MAX_INTENT_CHUNKS * 64,
                    by: "MAX_OPEN_INTENTS open uploads, each at most MAX_INTENT_CHUNKS chunk " +
                      "ids, and as many still to send, and a sync's lease (slice STORE, 32 " +
                      "hex characters, a column's 64 at most)",
                    indexes: { file_intents_due: key(NUMBER, 32) } },
    owner_policy: { rows: 1, bytes: ROW + P.maxDocument * 4,
                    by: "one document of maxDocument code points" },
    policy_receipts: { rows: 3 * P.receiptsPerBucket, bytes: ROW + 1 * KIB,
                       by: "receiptsPerBucket in each of three buckets",
                       indexes: { policy_receipts_by_bucket: key(7, NUMBER) } },
    policy_proposals: { rows: P.pendingProposals + P.appliedProposals,
                        bytes: ROW + 4 * PROPOSAL_ID_CHARS + 64 + P.maxChange * 4,
                        by: "pending and applied proposals of maxChange code points",
                        indexes: { sqlite_autoindex_policy_proposals_1: key(4 * PROPOSAL_ID_CHARS),
                                   proposals_by_age: key(NUMBER),
                                   proposals_applied: key(NUMBER) } },
    proposal_shares: { rows: G.maxFreeClients + G.maxOwnerClients,
                       bytes: ROW + 64, by: "one per client",
                       indexes: { sqlite_autoindex_proposal_shares_1: key(64) } },
    policy_usage: { rows: 1, bytes: ROW, by: "one row" },
    outside_clients: { rows: G.maxFreeClients + 2 * G.maxOwnerClients,
                       bytes: ROW + 8 + VENDOR + 8 + (GRANT_BYTES + CLIENT_NAME * 4)
                         * G.maxOwnerClients / (G.maxFreeClients + 2 * G.maxOwnerClients),
                       by: "free clients (no scopes, no name), live owner-set ones (their " +
                         "scopes' JSON at the schema's byte ceiling, and a name) and as many " +
                         "revoked ones (no scopes, a name; slice L), each with its revision " +
                         "(migration 9) and its vendor, an id of at most VENDOR bytes, and " +
                         "whether it was verified (migration 4, slice BRAIN)",
                       indexes: { sqlite_autoindex_outside_clients_1: key(64),
                                  outside_clients_listed: key(NUMBER, 64),
                                  outside_clients_free: key(NUMBER) } },
    grant_fences: { rows: 1, bytes: ROW, by: "one row" },
    owner_sessions: { rows: S.maxSessions, bytes: ROW + 64, by: "maxSessions",
                      indexes: { sqlite_autoindex_owner_sessions_1: key(64),
                                 sessions_by_expiry: key(NUMBER) } },
    outside_agents: { rows: L.maxAgentRows,
                      bytes: ROW + AGENT_ID + 4 * UNIVERSE_CHARS + 2 * 64 + 64
                        + 4 * AGENT_NAME_CHARS + 4 * RECIPE_CHARS + 64 + NUMBER,
                      by: "maxAgentRows, every row: a disabled agent's too; each its id, " +
                        "universe, client, family, source, name, recipe, reason and wake " +
                        "counter (slice W's `wake_tat`) at the validators' widest",
                      indexes: { sqlite_autoindex_outside_agents_1: key(AGENT_ID),
                                 sqlite_autoindex_outside_agents_2: key(4 * UNIVERSE_CHARS,
                                                                        64, 64),
                                 outside_identities: key(64, 64),
                                 // Slice W: the enabled agents by delivery method.
                                 outside_ringing: key(12) } },
    // The owner's own agents (slice ID): counted with the outside agents in the registry's
    // rows, so the two terms together count it twice over. `WITHOUT ROWID`, keyed by its id.
    native_agents: { rows: L.maxAgentRows, bytes: ROW + 128 + NATIVE_ID_CHARS + 4 * 8,
                     by: "maxAgentRows, live or retired, each its name (ASCII), id and " +
                       "counters" },
    ledger_messages: { rows: L.maxMessages,
                       bytes: ROW + MESSAGE_ID + 4 * UNIVERSE_CHARS + 4 * ROOM_CHARS + AGENT_ID
                         + 4 * CMID_CHARS + 64 + 4 * PARENT_CHARS + MESSAGE_ID + 7
                         + AUTHORITY_CHARS + 8 + 8,
                       by: "maxMessages (the hot ceiling: acked mail moves to archive " +
                         "segments), each its ids, universe, room, client message id, " +
                         "body hash, parent, authority (visible ASCII), whether a person " +
                         "sent it and its lines' bytes, at the validators' widest",
                       indexes: { sqlite_autoindex_ledger_messages_1: key(MESSAGE_ID),
                                  sqlite_autoindex_ledger_messages_2: key(AGENT_ID,
                                                                          4 * CMID_CHARS),
                                  messages_done: key(NUMBER) } },
    // A body past PLACE_BYTES is placed in R2 (ADR-035): the row keeps at most that much.
    message_bodies: { rows: L.maxMessages,
                      bytes: ROW + MESSAGE_ID + 4 * ROOM_CHARS + PLACE_BYTES + CONTENT_ID
                        + FILER + 8,
                      by: "maxMessages, each its room and its body inline up to PLACE_BYTES " +
                        "(a larger one placed in R2) or its content id, and its files scope",
                      indexes: { sqlite_autoindex_message_bodies_1: key(MESSAGE_ID) } },
    // Its key holds a message to one recipient.
    ledger_recipients: { rows: L.maxMessages, bytes: ROW + MESSAGE_ID + AGENT_ID,
                         by: "one a message (the key: message_id)",
                         indexes: { sqlite_autoindex_ledger_recipients_1: key(MESSAGE_ID),
                                    recipients_by_seq: key(AGENT_ID, NUMBER),
                                    recipients_unacked: key(AGENT_ID, NUMBER),
                                    recipients_by_state: key(AGENT_ID, 8) } },
    ledger_readers: { rows: L.maxAgentRows, bytes: ROW + AGENT_ID, by: "one per agent",
                      indexes: { sqlite_autoindex_ledger_readers_1: key(AGENT_ID) } },
    ledger_receipts: { rows: L.maxReceipts, bytes: ROW + 1 * KIB,
                       by: "maxReceipts",
                       indexes: { receipts_by_ref: key(13, 4 * PROPOSAL_ID_CHARS, AGENT_ID),
                                  receipts_by_agent: key(AGENT_ID) } },
    ledger_wakes: { rows: L.maxAgentRows + L.maxSettledWakes,
                    bytes: ROW + 1 * KIB, by: "an open wake per agent, maxSettledWakes settled",
                    indexes: { sqlite_autoindex_ledger_wakes_1: key(40),
                               one_open_wake: key(AGENT_ID), wakes_by_agent: key(AGENT_ID),
                               wakes_open: key(NUMBER), wakes_sending: key(NUMBER),
                               wakes_settled: key(NUMBER) } },
    ledger_usage: { rows: L.maxAgentRows + 1, bytes: ROW, by: "one per agent" },
    // The archive (slice STORE item 2): one fence row a segment, every sealed segment at least
    // `SEGMENT_LEAST_BYTES` of the owner's counted storage, so the rows are at most a tier's
    // storage over it, plus the one small segment a rewrite may leave; the tier record's
    // storage is held to `TIER_STORAGE_MAX` so that this many fit.
    archive_segments: { rows: ARCHIVE_SEGMENTS_MAX,
                        bytes: ROW + 10 * NUMBER + 8 + CONTENT_ID + FILER + 2 * BLOOM_BYTES,
                        by: "ARCHIVE_SEGMENTS_MAX: TIER_STORAGE_MAX over SEGMENT_LEAST_BYTES, " +
                          "plus one; each its keys, times, sizes, content id and bloom",
                        indexes: { archive_segments_by_last: key(8, NUMBER) } },
    // The partial index on the segments under the least: at most one stands at a time (the
    // lone one, or the one a bounded rewrite left for the next pass), so one entry.
    archive_segments_small: { rows: 1, bytes: 0, tables: [],
                              by: "one segment under SEGMENT_LEAST_BYTES at most",
                              indexes: { archive_segments_small: key(NUMBER) } },
    archive_moves: { rows: 2, bytes: ROW + 16 + 32 + 64 + 2 * NUMBER,
                     by: "one move in flight a stream, mail and a rewrite",
                     indexes: { sqlite_autoindex_archive_moves_1: key(16) } },
    archive_purges: { rows: L.maxAgentRows, bytes: ROW + AGENT_ID + 2 * NUMBER,
                      by: "an agent deleted while segments may hold its mail, at most " +
                        "maxAgentRows waiting (a deletion past them is refused)",
                      indexes: { sqlite_autoindex_archive_purges_1: key(AGENT_ID) } },
    ledger_ids: { rows: 1, bytes: ROW + 64, by: "one row: the key ids carry their seq under" },
    // Slice W's doorbell subscriptions: `WITHOUT ROWID`, keyed by the server's id, each its
    // fields at the schema's widest (its sealed blobs visible ASCII, a byte a character; its
    // arguments any text, four bytes a character), and its by-agent index entry.
    wake_callbacks: { rows: L.maxAgents * SUBSCRIPTIONS_PER_AGENT,
                      bytes: ROW + 36 + AGENT_ID + 64 + 64 + 4 * ARGUMENTS_CHARS
                        + 2 * DOORBELL_BLOB_CHARS + 32 + 64 + 8 * NUMBER,
                      by: "SUBSCRIPTIONS_PER_AGENT doorbell subscriptions an enabled agent " +
                        "(only an enabled agent's, a schema trigger), each its id, agent, " +
                        "principal, kind, arguments, sealed blob and the one a refresh " +
                        "replaced, kid, last wake and numbers",
                      indexes: { wake_callbacks_by_agent: key(AGENT_ID, 36) } },
    // The rows in bytes, as SQLite lays them out with their five index entries, whatever
    // their names (their triggers' charge), each collection's seq row and each writer's
    // counter with them: refused past `maxRowsCharge`. A body past PLACE_BYTES is in R2 (ADR-035).
    collection_rows: { rows: 1, bytes: C.maxRowsCharge, charged: true,
                       by: "maxRowsCharge at WORST_FILL, charged by their triggers",
                       tables: ["collection_rows", "collection_seqs", "collection_usage"],
                       indexes: { sqlite_autoindex_collection_rows_1: 0,
                                  sqlite_autoindex_collection_rows_2: 0,
                                  collection_writers: 0, collection_creators: 0,
                                  collection_changes: 0,
                                  sqlite_autoindex_collection_seqs_1: 0 } },
    // The history in bytes, as SQLite lays its entries out (its triggers' charge), and the
    // deletions with it, pruned at it.
    collection_history: { rows: 1, bytes: C.maxHistoryCharge, charged: true,
                          by: "maxHistoryCharge at WORST_FILL, charged by its triggers",
                          tables: ["collection_history", "collection_erasures"],
                          indexes: { sqlite_autoindex_collection_history_1: 0 } },
    collection_space: { rows: 1, bytes: ROW, by: "one row" },
    collection_clock: { rows: 1, bytes: ROW, by: "one row (slice M)" },
    agent_cards: { rows: K.maxPending, bytes: ROW + 37 + 64 + 64 + 64 + K.maxRequest + 64,
                   by: "maxPending cards of maxRequest",
                   indexes: { sqlite_autoindex_agent_cards_1: key(37),
                              sqlite_autoindex_agent_cards_2: key(64, 64),
                              agent_cards_by_age: key(NUMBER) } },
    owner_notices: { rows: N.kept, bytes: ROW + N.detail * 4, by: "N.kept" },
    connections: { rows: CN.maxConnections + CN.maxRevokedKept,
                   bytes: ROW + MODELS_BYTES + EGRESS_BYTES,
                   by: "connections and revoked kept, the schema's models and egress scope " +
                     "ceilings each",
                   indexes: { connections_pending: key(NUMBER, 25),
                              connections_revokes: key(NUMBER, 25) } },
    connection_secrets: { rows: CN.maxConnections
                                + CN.maxRevokedKept,
                          bytes: ROW + 4 * BLOB_CHARS,
                          by: "one sealed blob a connection, at the schema's ceiling" },
    connection_flows: { rows: CN.maxFlowRows, bytes: ROW + 4 * KIB,
                        by: "maxFlowRows",
                        indexes: { connection_flows_by_expiry: key(NUMBER, 64) } },
    model_reservations: { rows: U.receiptsKept + CALLS_OPEN, bytes: ROW + 1 * KIB,
                          by: "receiptsKept settled, and CALLS_OPEN open at once",
                          indexes: { sqlite_autoindex_model_reservations_1: key(33),
                                     model_reservations_open: key(10) } },
    model_spend: { rows: SPEND_KEPT, bytes: ROW, by: "SPEND_KEPT day and month counters" },
    // The sandbox's egress (G4): its lease is `file_space`'s one row; each request a row.
    egress_reservations: { rows: E.receiptsKept + E.refusalsKept
                                  + E.openPerRun * RUNS_CEILING,
                           bytes: ROW + EGRESS_PATH_MAX + 32 + 32 + 64 + 64 + 9 + 64 + 10 + 7
                             + 256 + 25 + 7 + 64 + 30 + 17 * 8 + RUN_ID_CHARS,
                           by: "receiptsKept admitted, refusalsKept refused and openPerRun at " +
                             "once for each of the most runs a tier may give, " +
                             "each its path (visible ASCII, a byte a character), ids, source, " +
                             "charge, rule, periods, served model and numbers",
                           indexes: { sqlite_autoindex_egress_reservations_1: key(32),
                                      egress_reservations_by_state: key(8, NUMBER),
                                      egress_reservations_by_time: key(NUMBER) } },
    // Runs (slice R): settled receipts kept and the live ones, each its widest; a live run's
    // grant; the conversations; the runs lane's schedules; the tier numbers kept; tickets.
    runs: { rows: RUNS_KEPT + RUNS_CEILING + QUEUE_ROWS,
            bytes: ROW + RESULT_BYTES + ERROR_BYTES + 4 * STAMP_CHARS + 256 + 512 + 32 + 16
              + 1 + MESSAGE_ID_CHARS + 9,
            by: "RUNS_KEPT settled receipts, RUNS_CEILING live and QUEUE_ROWS waiting, each " +
              "its result, error, stamp, model, ids, counters, lease, heartbeat, whether it " +
              "waits for a sandbox (slice S), mail and sandbox time at their widest",
            indexes: { sqlite_autoindex_runs_1: key(RUN_ID_CHARS), runs_by_request: key(64),
                       runs_live: key(9), runs_by_conversation: key(CONVERSATION_ID_CHARS,
                                                                   NUMBER),
                       runs_waiting: key(NUMBER), runs_settled: key(NUMBER, NUMBER) } },
    // The sandbox leases (review H-1): one an authority, opened only for a live run.
    sandbox_leases: { rows: RUNS_CEILING,
                      bytes: ROW + 64 + 32 + 8 + 128 + 16 + 3 * 64 + 8 + SIGNIN_LIST_CHARS
                        + 16,
                      by: "RUNS_CEILING, each its authority, lease id, epoch, holder, " +
                        "times, three token hashes, network generation, its sandbox's " +
                        "own signed-in engines (visible ASCII) and its readable set's digest" },
    // The vendor's own sign-ins in progress (slice SIGNIN): one a sandbox, each holding that
    // sandbox's lease, so at most RUNS_CEILING; each its prompt, a URL and a code (visible
    // ASCII, at most SIGNIN_URL_MAX and SIGNIN_CODE_MAX).
    sandbox_signins: { rows: RUNS_CEILING,
                       bytes: ROW + 64 + 35 + 64 + 3 + 64 + 32 + NUMBER + 32
                         + SIGNIN_URL_MAX + SIGNIN_CODE_MAX + 1 + 2 * NUMBER,
                       by: "RUNS_CEILING, each its authority, id, engine, mode, region, " +
                         "lease, state, " +
                         "the vendor's URL and code at their widest, and its times",
                       indexes: { sqlite_autoindex_sandbox_signins_2: key(35) } },
    // The inputs waiting for their conversation (`run_inputs`): QUEUE_ROWS rows holding
    // QUEUE_BYTES of input in all, however it is spread. Each row is counted at its even
    // share and two overflow pages more: a row's in-place share is at most a third of a
    // page (a `WITHOUT ROWID` record keeps 1,002 bytes in place), and its overflow at most a
    // page past its bytes', which the two pages cover whatever the spread.
    run_inputs: { rows: QUEUE_ROWS,
                  bytes: ROW + 36 + ORIGIN_CHARS + QUEUE_BYTES / QUEUE_ROWS + 2 * 4092,
                  by: "QUEUE_ROWS, QUEUE_BYTES of input in all, each its run and origin" },
    // Each engine's sign-in as the runner reports it: presence, times and models, no secret.
    engine_signins: { rows: ENGINE_SIGNINS, bytes: ROW + 64 + SIGNIN_MODELS_CHARS + 16,
                      by: "ENGINE_SIGNINS, each its engine and models (visible ASCII)" },
    run_grants: { rows: RUNS_CEILING,
                  bytes: ROW + 4 * STAMP_CHARS + ORIGIN_CHARS + 512 + 128 + 64,
                  by: "one a live run, its stamp, origin, scopes, connection and secret's " +
                    "hash" },
    // A mail thread's conversation is found by its key, the thread's digest: no index.
    conversations: { rows: CONVERSATIONS_KEPT, bytes: ROW + 128 + 64 + 64 + 256 + 128,
                     by: "CONVERSATIONS_KEPT, each its agent, source, client and session id",
                     indexes: { conversations_by_age: key(NUMBER, CONVERSATION_ID_CHARS) } },
    run_schedules: { rows: SCHEDULES_KEPT,
                     bytes: ROW + INPUT_BYTES + 4 * STAMP_CHARS + ORIGIN_CHARS + 4 * 256 + 256,
                     by: "SCHEDULES_KEPT, each its input, stamp, origin and last error",
                     indexes: { run_schedules_due: key(NUMBER, SCHEDULE_ID_CHARS) } },
    platform_tiers: { rows: 1, bytes: ROW + 4 * KIB, by: "one row" },
    platform_signins: { rows: 1, bytes: ROW + SIGNIN_LIST_CHARS + NUMBER,
                        by: "one row: the switched-on records' ids, joined" },
    socket_tickets: { rows: SOCKET_TICKETS, bytes: ROW + 64 + 35 + 4 * 128 + 16,
                      by: "SOCKET_TICKETS, each a hash, a conversation and a grant id" },
    owner_erasure: { rows: 1, bytes: ROW, by: "one row" },
    // Each applied migration's digest (`migrate`): one row a migration of the code's list.
    schema_digests: { rows: 1024, bytes: ROW + 8 + 64,
                      by: "one a migration; 1,024 past any list the code will hold" },
    // Slice P: the generations (`backup.ts`): the one being written, its cursor and its lists
    // of at most MANIFEST_PARTS base parts and log segments together; and the newest finished
    // and each one an open export pins, their lists cleared (their roots hold them).
    owner_backups: { rows: 1, bytes: ROW + CURSOR_CHARS + MANIFEST_PARTS * SEGMENT_ENTRY + 16 * 8,
                     by: "the generation being written, its cursor and its lists" },
    owner_backups_kept: { rows: 1 + EXPORTS_OPEN, bytes: ROW + 16 + 8 + 6 + 16 * 8,
                          tables: [],
                          by: "the newest finished generation and EXPORTS_OPEN pinned ones" },
    // Slice P: the sandbox meter (`sandbox-time.ts`).
    sandbox_meter: { rows: 1, bytes: ROW + 3 * 8, by: "one row: the log's cursor" },
    owner_backup_state: { rows: 1, bytes: ROW + 2 * 8,
                          by: "one row: changed since the manifest, and told" },
    owner_exports: { rows: EXPORTS_OPEN, bytes: ROW + 32 + 16 + 2 * 8,
                     by: "EXPORTS_OPEN, each its id, opening, end and generation" },
    // The change log (`changelog.ts`, review R4): its capture's one row, and its unshipped
    // images, charged as its trigger counts them (each its text and LOG_ROW_BYTES), held under
    // LOG_BYTES, and one image past it at its widest.
    log_capture: { rows: 1, bytes: ROW + 16 + 9 * 8, by: "one row" },
    change_log: { rows: 1, bytes: LOG_BYTES + IMAGE_MAX + LOG_ROW_BYTES, charged: true,
                  by: "LOG_BYTES of unshipped images and one past it, at WORST_FILL" },
    sandbox_spans: { rows: SPANS_KEPT + 1 + G.maxFreeClients + G.maxOwnerClients,
                     bytes: ROW + 64 + 2 * 8,
                     by: "SPANS_KEPT closed spans, and one open an authority (the owner's " +
                       "and each client's)" },
    // Keyed by an 8-byte digest (review N5): a record of its key, its time and their header.
    sandbox_billed: { rows: BILLED_KEPT, bytes: 8 + NUMBER + 8,
                      by: "BILLED_KEPT E2B executions inside the window, each 8 bytes of " +
                        "its id's digest and the event's time",
                      indexes: { sandbox_billed_by_age: key(8) } },
    sandbox_live: { rows: LIVE_KEPT, bytes: ROW + 128 + 8 + NUMBER,
                    by: "LIVE_KEPT live sandboxes, each its id and its last execution's digest",
                    indexes: { sandbox_live_by_age: key(128) } },
    sandbox_days: { rows: DAYS_KEPT, bytes: ROW + 8 + 5 * 8,
                    by: "DAYS_KEPT days of sandbox seconds" },
    // The owner's admission: one row, its reasons a short fixed list; after the erasure, the
    // erased tombstone, alone in the database.
    owner_admission: { rows: 1, bytes: ROW + 64, by: "one row (the schema's CHECK)" },
    // The key store (slice L): a row only while its key's owner-set grant row stands.
    owner_keys: { rows: Math.max(MAX_KEYS, G.maxOwnerClients),
                bytes: ROW + 16 + 64 + 4 * KEY_NAME_MAX + 3 * 9,
                by: "maxOwnerClients (a key's grant is an owner-set client), each its id, " +
                  "client, name and times",
                indexes: { sqlite_autoindex_owner_keys_1: key(16),
                           sqlite_autoindex_owner_keys_2: key(64) } },
    // The inbound bindings (slice L): each its route, agent, owner-held proof and secret.
    inbound_bindings: { rows: MAX_BINDINGS,
                        bytes: ROW + 32 + 10 + 64 + 9 + INBOUND_ROUTE_MAX + 1024
                          + 4 * INBOUND_SECRET_MAX + 64
                          + 3 * 9,
                        by: "MAX_BINDINGS, each its id, kind, agent, revision, route, proof " +
                          "(ASCII), secret, breaker and times",
                        indexes: { sqlite_autoindex_inbound_bindings_1: key(32),
                                   sqlite_autoindex_inbound_bindings_2: key(INBOUND_ROUTE_MAX) } },
    // The starter package's receipts: one a path of the carried package, its path at its
    // widest and two content ids; and the one row of the version reconciled.
    starter_receipts: { rows: named("STARTER_FILES_MAX", "starter-package"),
                        bytes: ROW + 4 * KIB + 2 * 64 + 2 * 16,
                        by: "STARTER_FILES_MAX paths, each its path and two content ids" },
    starter_state: { rows: 1, bytes: ROW, by: "one row" },
  });
}

/** Every owner table's worst case at the sources' own bounds. */
export const TERMS: Readonly<Record<string, Term>> = termsFor();
/** The file manifest's term at the sources' own bounds. */
const MANIFEST: Term = TERMS["file_manifest"]!;

/**
 * What the runtime and Lifecycle keep in the owner's database beside its own tables, which
 * the schema does not make: Lifecycle's job table, the lanes' jobs and the erasure's; and
 * the key-value store, Lifecycle's own few keys.
 */
export const RUNTIME: Readonly<Record<string, Term>> = Object.freeze<Record<string, Term>>({
  cf_agents_jobs: { rows: 16, bytes: ROW + 256, by: "a job per lane, the erasure's" },
  _cf_KV: { rows: 16, bytes: ROW + 128, by: "Lifecycle's own keys" },
});

/**
 * Every table the owner's schema makes, by its final name: its statements in order, a
 * rename moving a table and a drop taking one away. The migration mechanism's own two
 * (`contract/src/migrate.ts`) are the owner's too.
 */
export function ownerTables(schema = `${MIGRATE}\n${SCHEMA}`): string[] {
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

/** The indexes the schema makes and keeps, each with its table: a dropped one is not. */
function madeIndexes(schema: string = SCHEMA): Map<string, string> {
  const made = new Map<string, string>();
  for (const [, name, table, dropped] of schema.matchAll(
    /CREATE (?:UNIQUE )?INDEX (?:IF NOT EXISTS )?([a-zA-Z_0-9]+) ON ([a-zA-Z_0-9]+)|DROP INDEX (?:IF EXISTS )?([a-zA-Z_0-9]+)/g)) {
    if (dropped === undefined) {
      made.set(name!, table!);
    } else {
      made.delete(dropped);
    }
  }
  return made;
}

/** The indexes the schema makes on `table`, by name. */
function indexesOn(table: string): string[] {
  return [...madeIndexes()].filter(([, on]) => on === table).map(([name]) => name).sort();
}

/** The indexes the schema makes on the manifest's tables. */
export function manifestIndexes(schema: string = SCHEMA): string[] {
  return [...madeIndexes(schema)].filter(([, on]) => Object.hasOwn(MANIFEST_TABLES, on))
    .map(([name]) => name).sort();
}

/** The tables the terms cover: each term's own, and the manifest's. */
export function covered(terms: Readonly<Record<string, Term>> = TERMS): string[] {
  return [...Object.entries(terms).filter(([name]) => name !== "file_manifest")
            .flatMap(([name, term]) => term.tables ?? [name]),
          ...(terms["file_manifest"] === undefined ? [] : Object.keys(MANIFEST_TABLES))];
}

/**
 * Every index the owner's schema makes outside the manifest's (which its charge covers):
 * each `CREATE INDEX` it spells, and each SQLite makes on its own (`AUTOINDEXES`).
 */
export function ownerIndexes(schema: string = SCHEMA): string[] {
  const spelled = [...madeIndexes(schema)].filter(([, on]) => !Object.hasOwn(MANIFEST_TABLES, on))
    .map(([name]) => name);
  return [...new Set([...spelled, ...AUTOINDEXES])].sort();
}

/** The indexes the terms charge, each term's own. */
export function chargedIndexes(terms: Readonly<Record<string, Term>> = TERMS): string[] {
  return Object.values(terms).flatMap((term) => Object.keys(term.indexes ?? {})).sort();
}

/**
 * The manifest tables the schema does not charge: each needs a trigger by its name after
 * an insert into (and a delete from) the table it is charged through, moving
 * `manifest_bytes`. A content's chunk list is charged through its content.
 */
export function uncharged(schema: string = SCHEMA): string[] {
  const missing: string[] = [];
  for (const [table, [added, removed]] of Object.entries(MANIFEST_TABLES)) {
    const on = table === "file_content_chunks" ? "file_contents" : table;
    for (const [trigger, event] of [[added, "INSERT"], [removed, "DELETE"]] as const) {
      const at = schema.lastIndexOf(`CREATE TRIGGER ${trigger} AFTER ${event} ON ${on} `);
      // The trigger's whole statement: the schema holds one a line.
      const end = schema.indexOf("\n", at);
      const body = at < 0 ? "" : schema.slice(at, end < 0 ? schema.length : end);
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

/**
 * A row line's most text past its values' own (`export.ts`): its frame, each column's name
 * and punctuation, and `JSON_VALUE` a value (a number's longest text, `null`, a string's
 * quotes or the `$base64` wrapper), each value's bytes counted apart at `JSON_POINT`.
 */
const JSON_VALUE = 24;
/** A chunk's line in the stream (`{"t":"chunk","sha256":...,"size":...}`), at its widest. */
const CHUNK_LINE = 128;

/**
 * The nightly manifest's stream at its worst (`backup.ts`, review L7): every exported
 * table's rows at their bound, each line its frame (`frameBytes`) plus its values' bytes
 * escaped at JSON's worst (`JSON_POINT`, a control character's `\u00XX`; base64's 4/3 is
 * less); a term charged in bytes (the file manifest, the collections) as its charge in rows
 * of `RECORD_FIXED` each, the least any charged row costs, each at its tables' widest
 * frame, and a chunk's line for each of the file manifest's.
 */
export function worstStream(terms: Readonly<Record<string, Term>> = TERMS): number {
  const tables = exported();
  const frameBytes = (table: string) => `{"t":"row","table":"${table}","row":{}}\n`.length
    + tables[table]!.columns.reduce((sum, column) => sum + column.length + 4 + JSON_VALUE, 0);
  let total = 0;
  for (const [name, term] of Object.entries(terms)) {
    const covers = name === "file_manifest" ? Object.keys(MANIFEST_TABLES)
      : [name, ...(term.tables ?? [])];
    const streamed = [...new Set(covers)].filter((table) => table in tables);
    if (streamed.length === 0) {
      continue;
    }
    if (term.charged === true) {
      const rows = term.rows * term.bytes / RECORD_FIXED;
      const widest = Math.max(...streamed.map(frameBytes))
        + (name === "file_manifest" ? CHUNK_LINE : 0);
      total += rows * widest + JSON_POINT * term.rows * term.bytes;
    } else {
      total += term.rows * (frameBytes(name) + JSON_POINT * term.bytes);
    }
  }
  return total;
}

/** The exported tables no term covers: none, or the stream's worst case misses them. */
function unstreamed(terms: Readonly<Record<string, Term>> = TERMS): string[] {
  const covered = new Set(Object.entries(terms).flatMap(([name, term]) =>
    name === "file_manifest" ? Object.keys(MANIFEST_TABLES) : [name, ...(term.tables ?? [])]));
  return Object.keys(exported()).filter((table) => !covered.has(table));
}

describe("the nightly manifest's stream", () => {
  it("fits MANIFEST_PARTS at its worst, JSON's escaping included", () => {
    expect(unstreamed()).toEqual([]);
    const worst = worstStream();
    expect(worst).toBeGreaterThan(DATABASE_BYTES * SHARE);  // escaping is counted
    expect(worst).toBeLessThan(MANIFEST_PARTS * CHUNK_BYTES);
    // Its lists fit one SQLite row (2 MB, Durable Objects' limits), its parts and segments
    // together (a segment's entry the wider), with its cursor.
    expect(MANIFEST_PARTS * PART_ENTRY).toBeLessThan(2_000_000);
    expect(MANIFEST_PARTS * SEGMENT_ENTRY + CURSOR_CHARS + ROW).toBeLessThan(2_000_000);
  });
});

/**
 * Tables whose wide columns are JSON text the object writes itself (`JSON.stringify`: no raw
 * control character), so escaping one at most doubles an ASCII byte (a quote or a
 * backslash) and leaves any other as it is: at most twice its bytes as the term counts them.
 */
const JSON_TEXT: ReadonlySet<string> = new Set(["connections", "owner_policy"]);
/** The step `IMAGE_MAX` is derived in: the widest image, rounded up to a quarter MiB. */
const IMAGE_STEP = 256 * 1024;
/** A Durable Object's SQLite row, at most: one change log row holds one image. */
const ROW_MAX = 2_000_000;

/** The widest row image one change writes (the capture triggers), every exported table's. */
export function widestImage(terms: Readonly<Record<string, Term>> = TERMS): number {
  const tables = exported();
  const frame = (table: string) => `{"t":"row","table":"${table}","row":{}}`.length
    + tables[table]!.columns.reduce((sum, column) => sum + column.length + 4 + JSON_VALUE, 0);
  // A table charged in bytes has rows of its own widths: a path, a small file's bytes as
  // hex, a placed body's inline part, and their ids, each escaped at JSON's worst.
  const charged = JSON_POINT * (MAX_PATH_BYTES + 255 * 4 + PLACE_BYTES + 4 * KIB)
    + 2 * INLINE_FILE;
  return Math.max(...Object.keys(tables).map((table) => {
    const term = terms[table];
    return frame(table) + (term === undefined || term.charged === true ? charged
      : (JSON_TEXT.has(table) ? 2 : JSON_POINT) * term.bytes);
  }));
}

describe("the change log's images", () => {
  it("are each at most IMAGE_MAX, derived from the widest real row, under a row's 2 MB " +
     "(review L5-1)", () => {
    const widest = widestImage();
    expect(widest).toBeGreaterThan(100_000);
    // Every exported table's widest row, a connection's models at their ceiling among them:
    // no change, a token refresh's included, writes the wide mark.
    expect(IMAGE_MAX).toBe(Math.ceil(widest / IMAGE_STEP) * IMAGE_STEP);
    expect(IMAGE_MAX + LOG_ROW_BYTES).toBeLessThan(ROW_MAX);
    expect(widest).toBeGreaterThan(TERMS["connections"]!.bytes);
  });
});

describe("the owner's database bound", () => {
  it("covers every table the owner's schema makes, with no exceptions", () => {
    const tables = ownerTables();
    expect(tables.length).toBeGreaterThan(30);
    const named = covered();
    expect(tables.filter((table) => !named.includes(table))).toEqual([]);
    expect(named.filter((table) => !tables.includes(table))).toEqual([]);
    expect(new Set(named).size).toBe(named.length);
    expect(tables.filter((table) => table.startsWith("pi_") || table.startsWith("model_turn")
                         || (table.startsWith("agent_") && table !== "agent_cards")))
      .toEqual([]);
    // A virtual table, or a name with a digit, is a table too; a dropped one is not.
    expect(ownerTables('"CREATE VIRTUAL TABLE fts5_index USING fts5(x)", ' +
                       '"CREATE TABLE IF NOT EXISTS t2 (a)"')).toEqual(["fts5_index", "t2"]);
    expect(ownerTables('"CREATE TABLE a (x)", "CREATE TABLE b (x)", "DROP TABLE a"'))
      .toEqual(["b"]);
    expect(tables).not.toContain("scheduled_work");
    expect(tables).not.toContain("run_receipts");
    expect(tables).toContain("owner_admission");
  });

  it("charges every other index the schema makes, each once, at its key's widest", () => {
    const indexes = ownerIndexes();
    expect(indexes.length).toBeGreaterThan(50);
    expect(chargedIndexes()).toEqual(indexes);
    // An index added without a term's entry, or a term's entry for none, is seen.
    expect(ownerIndexes(`${SCHEMA}\nCREATE INDEX runs_by_agent ON runs (agent)`))
      .toContain("runs_by_agent");
    const { runs: _runs, ...fewer } = TERMS;
    expect(indexes.filter((index) => !chargedIndexes(fewer).includes(index)))
      .toContain("runs_by_request");
  });

  it("keeps the partial index on the segments under the least at SEGMENT_LEAST_BYTES", () => {
    // Its one entry (`archive_segments_small`) holds while its bound is the least's.
    expect(SCHEMA).toContain(`ON archive_segments (bytes) WHERE bytes < ${SEGMENT_LEAST_BYTES}`);
  });

  it("finds every manifest table charged by the schema's triggers, and every index charged",
     () => {
    expect(uncharged()).toEqual([]);
    // A trigger that charges nothing, or none, is seen.
    expect(uncharged(SCHEMA.replaceAll("CREATE TRIGGER file_history_added", "CREATE TRIGGER x")))
      .toEqual(["file_history: file_history_added"]);
    const charge = /(CREATE TRIGGER file_temps_removed AFTER DELETE ON file_temps BEGIN ).*? END$/m;
    expect(uncharged(SCHEMA.replace(charge, "$1 END")))
      .toEqual(["file_temps: file_temps_removed"]);
    expect(manifestIndexes()).toEqual([...MANIFEST_INDEXES].sort());
    expect(manifestIndexes(`${SCHEMA}\nCREATE INDEX file_entries_by_writer ON file_entries ` +
                           "(writer)")).toContain("file_entries_by_writer");
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
    expect(termBytes("model_spend", { rows: 1, bytes: 1003, by: "" }))
      .toBe(Math.floor(4096 / Math.floor(3066 / 493)) * WORST_FILL + 4096);
    // A record over a third of a page is on its page however thinned: a page at most.
    expect(termBytes("x", { rows: 1, bytes: 2000, by: "" })).toBe(4096);
    expect(termBytes("x", { rows: 1, bytes: 1200, by: "" })).toBe(4096);
    expect(["model_spend", "run_grants", "connections"]
      .filter((table) => !withoutRowid().has(table))).toEqual([]);
    expect(withoutRowid().has("model_reservations")).toBe(false);
    expect(withoutRowid('"CREATE TABLE a (x) WITHOUT ROWID", "CREATE TABLE b (x)", ' +
                        '"ALTER TABLE a RENAME TO c"')).toEqual(new Set(["c"]));
  });

  it("counts the bounds the sources hold", () => {
    expect([MAX_INTENT_CHUNKS, CHUNK_BYTES, constant("files", "KEEP_CEILING")])
      .toEqual([10_000, 4 * 1024 ** 2, 1000]);
    expect([CALLS_OPEN, SPEND_KEPT, MODELS_BYTES, BLOB_CHARS])
      .toEqual([1000, 4300, 655_360, 65_536]);
    expect([SCOPE_FIELD, CLIENT_NAME]).toEqual([512, 80]);
    // A grant's scopes are counted in bytes: the module's bound is the frozen bound the
    // schema's check spells.
    expect([GRANT_LIMITS.maxScopesBytes, SCOPES_BYTES]).toEqual([65_536, 65_536]);
    expect([PLACE_BYTES, ROOM_CHARS, UNIVERSE_CHARS, CMID_CHARS, PROPOSAL_ID_CHARS])
      .toEqual([2048, 128, 512, 128, 128]);
    expect(TERMS["file_intents"]).toMatchObject({
      rows: MAX_OPEN_INTENTS, bytes: 256 + 64 + 2 * 640_000 });
    expect(TERMS["model_reservations"]!.rows).toBe(USAGE_LIMITS.receiptsKept + 1000);
    expect(TERMS["model_spend"]!.rows).toBe(4300);
    expect(TERMS["wake_callbacks"]!.rows).toBe(LEDGER_LIMITS.maxAgents * 16);
    expect(TERMS["connection_secrets"]!.bytes).toBe(256 + 4 * 65_536);
    expect(TERMS["connections"]!.bytes).toBe(256 + 655_360 + 16_384);  // in bytes (review L5-1)
    expect(TERMS["egress_reservations"]!.rows).toBe(2_000 + 500 + 16 * 1_000);
    expect(EGRESS_PATH_MAX).toBe(2048);
    // The path is visible ASCII, so a character is a byte (the schema's CHECK).
    expect(SCHEMA).toContain("path TEXT NOT NULL CHECK (length(path) BETWEEN 1 AND 2048 AND " +
                             "path NOT GLOB '*[^ -~]*'), ");
    expect(TERMS["outside_clients"]!.rows * TERMS["outside_clients"]!.bytes)
      .toBe(3000 * (256 + 8 + 32 + 8) + 1000 * (65_536 + 80 * 4) + 1000 * 80 * 4);
    // The structural bounds (ADR-035): one recipient a message; history in bytes.
    expect(TERMS["ledger_recipients"]!.rows).toBe(LEDGER_LIMITS.maxMessages);
    expect(TERMS["collection_history"]).toMatchObject({ rows: 1, charged: true,
                                                         bytes: COLLECTIONS.maxHistoryCharge });
    expect(SCHEMA).toContain("message_id TEXT NOT NULL PRIMARY KEY, agent_id TEXT NOT NULL, " +
                             "seq INTEGER NOT NULL, ");
  });

  it("charges every index of slice ID's tables: the identities and the mail threads add none",
     () => {
    // An identity is found by its key (its name's digest), a mail thread's conversation by
    // its key (the thread's digest), and an agent's due mail by `recipients_by_state`: no
    // index past the row's own `ROW` is made for them, so none goes uncharged.
    expect(indexesOn("native_agents")).toEqual([]);
    expect(indexesOn("conversations")).toEqual(["conversations_by_age"]);
    expect(indexesOn("ledger_recipients"))
      .toEqual(["recipients_by_seq", "recipients_by_state", "recipients_unacked"]);
    expect(indexesOn("ledger_messages")).toEqual(["messages_done"]);
    expect(SCHEMA).not.toMatch(/native_agents \([^)]*UNIQUE|agent_id TEXT NOT NULL UNIQUE/);
  });

  it("charges the change orders: the rows' index, and the history's and deletions' keys",
     () => {
    // `collection_changes` is charged by the rows' triggers; the history and the deletions
    // are keyed by their change (untangle H1), so neither has a change index, and a dropped
    // index is no longer counted.
    expect(indexesOn("collection_rows"))
      .toEqual(["collection_changes", "collection_creators", "collection_writers"]);
    expect([indexesOn("collection_history"), indexesOn("collection_erasures")]).toEqual([[], []]);
    expect(ownerIndexes()).not.toContain("collection_history_by_age");
    expect(ownerIndexes(`${SCHEMA}\nCREATE INDEX x ON runs (agent)\nDROP INDEX x`))
      .toEqual(ownerIndexes());
    expect(TERMS["collection_rows"]!.indexes).toHaveProperty("collection_changes", 0);
    expect(TERMS["collection_history"]!.tables)
      .toEqual(["collection_history", "collection_erasures"]);
  });

  it("counts Lifecycle's own rows, apart from the tables", () => {
    expect(worstCase()).toBe(worstCase(TERMS) + worstCase(RUNTIME));
  });

  it("counts every registration row, a disabled agent's among them, which the " +
     "registry refuses past", () => {
    // Every row is counted as it is made and refused past maxAgentRows; a disabled one too.
    expect(SCHEMA).toContain("CREATE TRIGGER outside_agents_added AFTER INSERT ON " +
                             "outside_agents BEGIN UPDATE ledger_usage SET agents = agents + 1, ");
    expect(source("ledger")).toContain("if (usage.agents >= LEDGER_LIMITS.maxAgentRows) {");
  });

  it("sees a bound that would pass half the limit, or a table it no longer covers", () => {
    expect(worstCase({ ...TERMS, file_manifest: { rows: 1, bytes: 5e9, by: "too much" } }))
      .toBeGreaterThan(SHARE * DATABASE_BYTES);
    expect(worstCase({ ...TERMS, model_spend: { rows: 1e7, bytes: 896, by: "too many" } }))
      .toBeGreaterThan(SHARE * DATABASE_BYTES);
    const { model_spend: _spend, ...fewer } = TERMS;
    expect(ownerTables().filter((table) => !covered(fewer).includes(table)))
      .toEqual(["model_spend"]);
    const { file_manifest: _manifest, ...unbounded } = TERMS;
    expect(ownerTables().filter((table) => !covered(unbounded).includes(table)))
      .toEqual(Object.keys(MANIFEST_TABLES).sort());
  });
});
