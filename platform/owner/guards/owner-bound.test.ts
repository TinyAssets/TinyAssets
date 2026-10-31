/**
 * The owner's own database stays well inside its 10 GB (ADR-032 §5; tasks 5, S3).
 *
 * pi's records live in the thread stores (`thread-store.ts`), never in the owner's
 * database, so what the owner's object holds is a function of its own bounded rows only,
 * whatever its agents do. This guard computes the worst case of every owner table from
 * the bounds the code enforces, at the policy's *high* settings where the owner may raise
 * one, with each row at its widest, and holds the sum under half the platform's limit. It
 * replaces A4's runtime storage gauge, which refused new threads past 90% of a database
 * that pi's conversations filled; a table no bound covers fails the guard. Two kinds of
 * table are named apart, so none joins them unseen: those whose rows a count bounds but
 * whose bytes no bound covers (file paths, `BYTES_UNBOUNDED`), and those whose growth only
 * time bounds (a retention window, `TIME_BOUNDED`). Each is a follow-up of the files and
 * usage modules: a bound in bytes or a count, the guard then counting it.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { LIMITS as COLLECTIONS } from "../src/collections";
import { LEDGER_LIMITS } from "../src/ledger";
import { NOTICE_DETAIL, NOTICES_KEPT } from "../src/notices";
import { SESSION_LIMITS } from "../src/sessions";
import { THREAD_LIMITS, USAGE_ROW_MAX } from "../src/threads";
import { USAGE_LIMITS } from "../src/usage";
import { PLATFORM } from "./sources";

/** The names the bounds below are spelled with in their sources. */
const NAMES: Readonly<Record<string, number>> = Object.freeze({
  KIB: 1024, MIB: 1024 ** 2, HOUR_S: 3600, MAX_INTENT_CHUNKS: 10_000,
  CHUNK_BYTES: 4 * 1024 ** 2, KEEP_CEILING: 1000, LANE_HUNG_S: 60, LANE_PASS_S: 45,
});

/**
 * A frozen object of numbers, as its module declares it (`export const name = ...({ ... })`),
 * read from the source, since the module loads the Agents SDK's runtime with it: each
 * field's value, a numeric expression of `NAMES`, or a `[low, high]` range's high.
 */
function declared<K extends string>(module: string, name: string, ...keys: K[]):
    Readonly<Record<K, number>> {
  const source = readFileSync(join(PLATFORM, "owner", "src", `${module}.ts`), "utf8");
  const start = source.indexOf(`export const ${name}`);
  if (start < 0) {
    throw new Error(`${module}.ts declares no ${name}`);
  }
  const body = source.slice(start, source.indexOf("});", start));
  const fields: Record<string, number> = {};
  const field = /([a-z_A-Z]+): (Object\.freeze\(\[[^\]]*\] as const\)|[^,\n]+)/g;
  for (const [, key, raw] of body.slice(body.indexOf("{")).matchAll(field)) {
    const range = /\[\s*[^,]+,\s*(.+?)\]\s*as const/.exec(raw!);
    const expression = (range?.[1] ?? raw!).replace(/(?<=[0-9])_(?=[0-9])/g, "");
    if (!/^[0-9A-Z_ *+()-]+$/.test(expression)) {
      continue;  // not a number of NAMES: no bound this guard reads
    }
    const value = expression.replace(/[A-Z][A-Z0-9_]*/g, (word) => {
      const known = NAMES[word];
      if (known === undefined) {
        throw new Error(`${name}.${key} names ${word}`);
      }
      return String(known);
    });
    fields[key!] = Function(`"use strict"; return (${value});`)() as number;
  }
  for (const key of keys) {
    const value = fields[key];
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`${name}.${key} is no number the guard read`);
    }
  }
  return fields as Readonly<Record<K, number>>;
}

const CONNECTION_LIMITS = declared("connections", "CONNECTION_LIMITS", "maxConnections",
                                   "maxRevokedKept", "maxModels", "maxFlowRows");
const GRANT_LIMITS = declared("grants", "GRANT_LIMITS", "maxFreeClients", "maxOwnerClients",
                              "maxScopes");
const POLICY_LIMITS = declared("policy", "POLICY_LIMITS", "maxDocument", "maxChange",
                               "pendingProposals", "appliedProposals", "receiptsPerBucket");
const FILE_SETTINGS = declared("policy", "FILE_SETTINGS", "max_bytes", "max_inline_bytes",
                               "max_entries", "max_history");
const CARD_LIMITS = declared("registrations", "CARD_LIMITS", "maxPending", "maxRequest");
const WORK_LIMITS = declared("scheduled", "WORK_LIMITS", "maxOpen", "maxSettled", "maxPayload");
const CHUNK_BYTES = NAMES["CHUNK_BYTES"]!;

/** The platform's limit on one object's database. */
const DATABASE_BYTES = 10_000_000_000;
/** The share of it the owner's worst case may reach. */
const SHARE = 0.5;
/** A row's fixed overhead past its payload: keys, indexes and SQLite's own record. */
const ROW = 256;
const KIB = 1024;

/** One table's worst case: its most rows, and the widest each may be, in bytes. */
interface Term {
  readonly rows: number;
  readonly bytes: number;
  /** What bounds it. */
  readonly by: string;
}

const high = (setting: keyof typeof FILE_SETTINGS): number => FILE_SETTINGS[setting];
const clients = GRANT_LIMITS.maxFreeClients + GRANT_LIMITS.maxOwnerClients;

/** Every owner table's worst case. */
export const TERMS: Readonly<Record<string, Term>> = Object.freeze<Record<string, Term>>({
  object_owner: { rows: 1, bytes: ROW + 4 * KIB, by: "one row" },
  // Owner files at the policy's high bounds: inline bytes, chunks and contents.
  file_space: { rows: 1, bytes: ROW, by: "one row" },
  file_contents: { rows: high("max_entries") + high("max_history"),
                   bytes: ROW + high("max_inline_bytes") / (high("max_entries")
                                                            + high("max_history")),
                   by: "one per entry or kept version; inline bytes at max_inline_bytes (high)" },
  file_chunks: { rows: high("max_bytes") / CHUNK_BYTES, bytes: ROW,
                 by: "max_bytes (high) of unique chunks" },
  file_intents: { rows: 64, bytes: ROW + NAMES["MAX_INTENT_CHUNKS"]! * 64,
                  by: "open uploads, each at most MAX_INTENT_CHUNKS chunk ids" },
  file_temps: { rows: 1_000, bytes: ROW, by: "temporary directories" },
  // The rest of the owner's state, each at its module's bounds.
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
  // The agent's threads: rows only, every transcript in its own store.
  agent_threads: { rows: THREAD_LIMITS.owner + THREAD_LIMITS.clients, bytes: ROW + 1 * KIB,
                   by: "THREAD_LIMITS (the owner's, and every client's)" },
  agent_live: { rows: THREAD_LIMITS.owner + THREAD_LIMITS.clients, bytes: ROW,
                by: "one per thread" },
  agent_usage: { rows: THREAD_LIMITS.owner + THREAD_LIMITS.clients, bytes: ROW + USAGE_ROW_MAX,
                 by: "one per thread, USAGE_ROW_MAX each" },
});

/**
 * Tables whose rows a count bounds but whose bytes no bound covers: each row carries a
 * path of up to `MAX_PATH_BYTES` (4 KiB), twice for history (its row and its path index).
 * At the policy's high counts their worst case alone passes the 10 GB (history: 5,000,000
 * rows of about 8.4 KiB; entries: 1,000,000 of about 4.6 KiB), and at its defaults too.
 * Typical paths keep them near a gigabyte (design-decision §1.5), but a client holding
 * `write_files` can fill the owner's object with long paths and churn: a follow-up for the
 * files module, to count path bytes against a quota.
 */
export const BYTES_UNBOUNDED: Readonly<Record<string, string>> = Object.freeze({
  file_entries: `max_entries (high ${high("max_entries")}) rows, a path of up to 4 KiB each`,
  file_history: `max_history (high ${high("max_history")}) rows, a path of up to 4 KiB ` +
    "each, indexed",
});

/**
 * Tables whose rows only a retention window bounds, not a count: named, with what bounds
 * them, so the guard sees any table that joins them. Each is a follow-up to bound by count.
 */
export const TIME_BOUNDED: Readonly<Record<string, string>> = Object.freeze({
  file_content_chunks: "one per chunk of each kept content: the versions retention keeps " +
    "(max_history), each up to its file's chunks; no count bounds their sum",
  model_turns: `one per turn with a call, until idle for ${USAGE_LIMITS.turnIdleS} s`,
});

/** Every table the owner's schema makes, by its final name. */
function ownerTables(): string[] {
  const schema = readFileSync(join(PLATFORM, "owner", "src", "schema.ts"), "utf8");
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

/** The worst case of the bounded tables, in bytes. */
export function worstCase(terms: Readonly<Record<string, Term>> = TERMS): number {
  return Object.values(terms).reduce((sum, term) => sum + term.rows * term.bytes, 0);
}

describe("the owner's database bound", () => {
  it("covers every table the owner's schema makes, and no pi table", () => {
    const tables = ownerTables();
    expect(tables.length).toBeGreaterThan(30);
    const named = [...Object.keys(TERMS), ...Object.keys(BYTES_UNBOUNDED),
                   ...Object.keys(TIME_BOUNDED)];
    expect(tables.filter((table) => !named.includes(table))).toEqual([]);
    expect(tables.filter((table) => table.startsWith("pi_"))).toEqual([]);
    expect(named.filter((table) => !tables.includes(table))).toEqual([]);
    expect(new Set(named).size).toBe(named.length);
    // The exceptions are exactly these: any other joins them only with a review here.
    expect(Object.keys(BYTES_UNBOUNDED)).toEqual(["file_entries", "file_history"]);
    expect(Object.keys(TIME_BOUNDED)).toEqual(["file_content_chunks", "model_turns"]);
  });

  it("keeps its worst case, at the policy's high bounds, under half the 10 GB", () => {
    const bytes = worstCase();
    expect(bytes).toBeLessThan(SHARE * DATABASE_BYTES);
    // Inline file bytes at their high bound, the largest term, are counted whole.
    expect(high("max_inline_bytes")).toBe(1024 ** 3);
    expect(TERMS["file_contents"]!.rows * TERMS["file_contents"]!.bytes)
      .toBeGreaterThan(high("max_inline_bytes"));
  });

  it("sees a bound that would pass half the limit", () => {
    expect(worstCase({ ...TERMS, file_contents: { rows: 2e9, bytes: ROW, by: "too many" } }))
      .toBeGreaterThan(SHARE * DATABASE_BYTES);
  });
});
