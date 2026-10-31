/**
 * The owner's own database stays well inside its 10 GB (ADR-032 §5; tasks 5 and 6).
 *
 * Past 10 GB every write fails `SQLITE_FULL`, grants, revokes and policy writes with the
 * rest, and the owner's object stops; so no owner table may grow but by a bound the code
 * enforces. This guard computes the worst case of every table the owner's schema makes,
 * from those bounds at the policy's *high* settings where the owner may raise one, each row
 * at its widest, and holds the sum under half the platform's limit. A table no bound
 * covers fails it: there are no exceptions.
 *
 * The file manifest is bounded in bytes, not rows: every row of its tables is charged what
 * SQLite stores it in, path bytes and overflow pages included (`manifest.ts`), by triggers
 * this guard finds in the schema, against `max_manifest_bytes` (whose high bound it reads),
 * and a workerd test measures the object's database against the charge
 * (`files-share.test.ts`). The model turns' counters are bounded by count and a turn's
 * bytes (migration 10). Until the storage slice (S1-S3) lands, pi's own tables share the
 * object, made by pi rather than this schema, and A4's storage gauge stands for them.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { LEDGER_LIMITS } from "../src/ledger";
import { MANIFEST_TABLES } from "../src/manifest";
import { NOTICE_DETAIL, NOTICES_KEPT } from "../src/notices";
import { SESSION_LIMITS } from "../src/sessions";
import { THREAD_LIMITS } from "../src/threads";
import { USAGE_LIMITS } from "../src/usage";
import { PLATFORM } from "./sources";

/** The names the bounds below are spelled with in their sources. */
const NAMES: Readonly<Record<string, number>> = Object.freeze({
  KIB: 1024, MIB: 1024 ** 2, HOUR_S: 3600, MAX_INTENT_CHUNKS: 10_000,
  CHUNK_BYTES: 4 * 1024 ** 2, KEEP_CEILING: 1000, LANE_HUNG_S: 60, LANE_PASS_S: 45,
});

function source(module: string): string {
  return readFileSync(join(PLATFORM, "owner", "src", `${module}.ts`), "utf8");
}

/** A numeric expression of `NAMES`, as a source spells it. */
function evaluate(expression: string, what: string): number {
  const value = expression.replace(/(?<=[0-9])_(?=[0-9])/g, "")
    .replace(/[A-Z][A-Z0-9_]*/g, (word) => {
      const known = NAMES[word];
      if (known === undefined) {
        throw new Error(`${what} names ${word}`);
      }
      return String(known);
    });
  return Function(`"use strict"; return (${value});`)() as number;
}

/**
 * A frozen object of numbers, as its module declares it (`export const name = ...({ ... })`),
 * read from the source, since the module loads the Agents SDK's runtime with it: each
 * field's value, a numeric expression of `NAMES`, or a `[low, high]` range's high.
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
      continue;  // not a number of NAMES: no bound this guard reads
    }
    fields[key!] = evaluate(expression, `${name}.${key}`);
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
  const found = new RegExp(`export const ${name} = ([0-9A-Z_ *+()-]+);`).exec(source(module));
  if (found === null) {
    throw new Error(`${module}.ts declares no numeric ${name}`);
  }
  return evaluate(found[1]!, name);
}

const CONNECTION_LIMITS = declared("connections", "CONNECTION_LIMITS", "maxConnections",
                                   "maxRevokedKept", "maxModels", "maxFlowRows");
const GRANT_LIMITS = declared("grants", "GRANT_LIMITS", "maxFreeClients", "maxOwnerClients",
                              "maxScopes");
const POLICY_LIMITS = declared("policy", "POLICY_LIMITS", "maxDocument", "maxChange",
                               "pendingProposals", "appliedProposals", "receiptsPerBucket");
const FILE_SETTINGS = declared("policy", "FILE_SETTINGS", "max_manifest_bytes");
const CARD_LIMITS = declared("registrations", "CARD_LIMITS", "maxPending", "maxRequest");
const WORK_LIMITS = declared("scheduled", "WORK_LIMITS", "maxOpen", "maxSettled", "maxPayload");
const MAX_OPEN_INTENTS = constant("files", "MAX_OPEN_INTENTS");
const MAX_PATH_BYTES = constant("paths", "MAX_PATH_BYTES");
const TURNS_KEPT = constant("schema", "TURNS_KEPT");
const TURN_BYTES = constant("schema", "TURN_BYTES");

/** The platform's limit on one object's database. */
export const DATABASE_BYTES = 10_000_000_000;
/** The share of it the owner's worst case may reach. */
export const SHARE = 0.5;
/** A row's fixed overhead past its payload: keys, indexes and SQLite's own record. */
const ROW = 256;
const KIB = 1024;
/** A payload of `bytes` with the overflow pages one past a page's room takes: whole pages. */
export const spilled = (bytes: number): number => Math.ceil(bytes / 4092) * 4096;

/** One table's worst case: its most rows, and the widest each may be, in bytes. */
export interface Term {
  readonly rows: number;
  readonly bytes: number;
  /** What bounds it. */
  readonly by: string;
}

const clients = GRANT_LIMITS.maxFreeClients + GRANT_LIMITS.maxOwnerClients;

/** The file manifest's tables, together: `max_manifest_bytes` at its high bound. */
const MANIFEST: Term = Object.freeze({
  rows: 1, bytes: FILE_SETTINGS.max_manifest_bytes,
  by: "max_manifest_bytes (high), charged by triggers to " +
    Object.keys(MANIFEST_TABLES).join(", "),
});

/** Every owner table's worst case; the manifest's tables share one term. */
export const TERMS: Readonly<Record<string, Term>> = Object.freeze<Record<string, Term>>({
  object_owner: { rows: 1, bytes: ROW + 4 * KIB, by: "one row" },
  file_manifest: MANIFEST,
  file_space: { rows: 1, bytes: ROW + spilled(MAX_PATH_BYTES) + 4 * KIB,
                by: "one row; its retention cursor a path" },
  file_intents: { rows: MAX_OPEN_INTENTS,
                  bytes: ROW + spilled(NAMES["MAX_INTENT_CHUNKS"]! * 64),
                  by: "MAX_OPEN_INTENTS open uploads, each at most MAX_INTENT_CHUNKS chunk ids" },
  owner_policy: { rows: 1, bytes: ROW + POLICY_LIMITS.maxDocument * 4,
                  by: "one document of maxDocument code points" },
  policy_receipts: { rows: 3 * POLICY_LIMITS.receiptsPerBucket, bytes: ROW + 1 * KIB,
                     by: "receiptsPerBucket in each of three buckets" },
  policy_proposals: { rows: POLICY_LIMITS.pendingProposals + POLICY_LIMITS.appliedProposals,
                      bytes: ROW + POLICY_LIMITS.maxChange * 4,
                      by: "pending and applied proposals of maxChange code points" },
  proposal_shares: { rows: clients, bytes: ROW, by: "one per client" },
  policy_usage: { rows: 1, bytes: ROW, by: "one row" },
  outside_clients: { rows: clients, bytes: ROW + GRANT_LIMITS.maxScopes * 512,
                     by: "free and owner-set clients, maxScopes each" },
  grant_fences: { rows: clients, bytes: ROW, by: "one per client" },
  owner_sessions: { rows: SESSION_LIMITS.maxSessions, bytes: ROW, by: "maxSessions" },
  outside_agents: { rows: LEDGER_LIMITS.maxAgentRows, bytes: ROW + 1 * KIB,
                    by: "maxAgentRows" },
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
  ledger_rings: { rows: LEDGER_LIMITS.maxAgentRows * 60, bytes: ROW,
                  by: "one an hour's wakes per agent" },
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
  scheduled_work: { rows: WORK_LIMITS.maxOpen + WORK_LIMITS.maxSettled,
                    bytes: ROW + WORK_LIMITS.maxPayload, by: "open and settled work" },
  owner_notices: { rows: NOTICES_KEPT, bytes: ROW + NOTICE_DETAIL * 4, by: "NOTICES_KEPT" },
  connections: { rows: CONNECTION_LIMITS.maxConnections + CONNECTION_LIMITS.maxRevokedKept,
                 bytes: ROW + CONNECTION_LIMITS.maxModels * 512,
                 by: "connections and revoked kept, maxModels each" },
  connection_secrets: { rows: CONNECTION_LIMITS.maxConnections
                              + CONNECTION_LIMITS.maxRevokedKept,
                        bytes: ROW + 16 * KIB, by: "one sealed blob a connection" },
  connection_flows: { rows: CONNECTION_LIMITS.maxFlowRows, bytes: ROW + 4 * KIB,
                      by: "maxFlowRows" },
  model_reservations: { rows: USAGE_LIMITS.receiptsKept + 1_000, bytes: ROW + 1 * KIB,
                        by: "receiptsKept settled, and the calls open at once" },
  model_spend: { rows: (USAGE_LIMITS.daysKept + USAGE_LIMITS.monthsKept) * 64,
                 bytes: ROW, by: "days and months kept, a row per source" },
  model_turns: { rows: TURNS_KEPT, bytes: ROW + 2 * TURN_BYTES,
                 by: "TURNS_KEPT turns of TURN_BYTES, in the row and its age index" },
  model_usage: { rows: 1, bytes: ROW, by: "one row" },
  agent_threads: { rows: THREAD_LIMITS.owner + THREAD_LIMITS.clients, bytes: ROW + 1 * KIB,
                   by: "THREAD_LIMITS (the owner's, and every client's)" },
});

/** Every table the owner's schema makes, by its final name. */
export function ownerTables(schema: string = source("schema")): string[] {
  const made = [...schema.matchAll(/CREATE TABLE (?:IF NOT EXISTS )?([a-z_]+)/g)]
    .map((match) => match[1]!);
  const renamed = [...schema.matchAll(/ALTER TABLE ([a-z_]+) RENAME TO ([a-z_]+)/g)];
  const tables = new Set(made.filter((name) => name !== "schema_version"));
  for (const [, from, to] of renamed) {
    tables.delete(from!);
    tables.add(to!);
  }
  return [...tables].sort();
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

/** The worst case of the bounded tables, in bytes. */
export function worstCase(terms: Readonly<Record<string, Term>> = TERMS): number {
  return Object.values(terms).reduce((sum, term) => sum + term.rows * term.bytes, 0);
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
  });

  it("finds every manifest table charged by the schema's triggers", () => {
    expect(uncharged()).toEqual([]);
    // A trigger that charges nothing, or none, is seen.
    const schema = source("schema");
    expect(uncharged(schema.replaceAll("CREATE TRIGGER file_history_added", "CREATE TRIGGER x")))
      .toEqual(["file_history: file_history_added"]);
    const charge = /(CREATE TRIGGER file_temps_removed AFTER DELETE ON file_temps BEGIN )[^`]*`/;
    expect(uncharged(schema.replace(charge, "$1 END`")))
      .toEqual(["file_temps: file_temps_removed"]);
  });

  it("keeps its worst case, at the policy's high bounds, under half the 10 GB", () => {
    const bytes = worstCase();
    expect(bytes).toBeLessThan(SHARE * DATABASE_BYTES);
    // The manifest at its high bound, the largest term, is counted whole.
    expect(FILE_SETTINGS.max_manifest_bytes).toBe(2 * 1024 ** 3);
    expect(Object.values(TERMS).every((term) => term.rows * term.bytes
                                         <= MANIFEST.rows * MANIFEST.bytes)).toBe(true);
    console.log(`the owner's worst case: ${bytes} bytes (${(bytes / DATABASE_BYTES * 100)
      .toFixed(1)}% of ${DATABASE_BYTES})`);
  });

  it("counts the bounds the sources hold, a spilled payload in whole pages", () => {
    expect([spilled(1), spilled(4092), spilled(4093)]).toEqual([4096, 4096, 8192]);
    expect(TURNS_KEPT).toBe(100_000);
    expect(TERMS["model_turns"]).toMatchObject({ rows: TURNS_KEPT, bytes: 256 + 2 * TURN_BYTES });
    expect(TERMS["file_intents"]).toMatchObject({
      rows: MAX_OPEN_INTENTS, bytes: 256 + spilled(640_000) });
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
