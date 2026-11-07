/**
 * The owner's frozen bounds held to the frozen SQL that spells them (decisions A7).
 *
 * The owner's migrations are literal SQL text, frozen from the first deploy and pinned by
 * digest (`schema.ts`, `migrations.json`). The bounds that text enforces are the code's
 * bounds too, read by their TypeScript owners from the contract's frozen bounds
 * (`contract/src/limits.ts`), and the names and charges it spells are the owner modules' own
 * (`manifest.ts`, `changelog.ts`). Nothing ties the two by construction, so this guard does:
 * every place the SQL spells one is listed here, the constant's value written into it, and
 * each must be in its statements as the object runs them. A constant changed without the
 * SQL (which takes a new migration) fails here; SQL changed without the constant fails the
 * digest lock (`migrations.test.ts`) first. Every frozen bound must be listed.
 *
 * The seed rows the code's defaults fill (the default policy, the file settings) are held by
 * the workerd tests that read a fresh object's (`policy.test.ts`, `files-quota.test.ts`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import * as LIMITS from "../../contract/src/limits";
import { PLATFORM } from "../../ci/paths";
import { LOG_WIDE } from "../src/changelog";
import {
  CHUNK_ROW_BYTES, CLIENT_WRITER, CLIENTS, CONTENT_BYTES, ENTRY_BYTES, HISTORY_BYTES,
  OWNER_SCOPE, SCOPE_REF_BYTES, SHARE_BYTES, TEMP_BYTES,
} from "../src/manifest";
import type { Migration } from "../src/migrate";
import { MIGRATIONS } from "../src/schema";

/** Which statements a place is in: those starting with the head and a space, or matching it. */
type Head = string | RegExp;
/** One place the SQL spells a bound: its statements, and its text at the bound's value. */
type Place = readonly [head: Head, text: (value: number) => string];

/** Every statement of `migrations` that `head` names. */
function statements(head: Head, migrations: readonly Migration[] = MIGRATIONS): string[] {
  return migrations.flat().filter((sql) =>
    typeof head === "string" ? sql.startsWith(`${head} `) : head.test(sql));
}

const TABLE = (name: string, ...texts: ((n: number) => string)[]): Place[] =>
  texts.map((text) => [`CREATE TABLE ${name}`, text] as const);
const TRIGGER = (name: string, ...texts: ((n: number) => string)[]): Place[] =>
  texts.map((text) => [`CREATE TRIGGER ${name}`, text] as const);
/** A body's placement (ADR-035): inline within the bound, or placed past it. */
const PLACED = (table: string, body: string) => TABLE(table,
  (n) => `length(CAST(${body} AS BLOB)) <= ${n}`,
  (n) => `${body} = '' AND body_bytes > ${n} AND length(content_id) = 64`);
/** An id's fixed length. */
const ID = (column: string) => (n: number) => `length(${column}) = ${n}`;
/** Every capture trigger (`<table>_logged_<event>`, slice P's change log). */
const CAPTURE = /^CREATE TRIGGER \w+_logged_(?:insert|update|delete) /;

/** Each frozen bound, at every place the SQL spells it. */
export const SPELLED: Readonly<Record<string, readonly Place[]>> = Object.freeze({
  PLACE_BYTES: [...PLACED("collection_rows", "body"), ...PLACED("collection_history", "body_json"),
                ...PLACED("message_bodies", "body")],
  GC_GRACE_S: TRIGGER("file_chunks_released",
                      (n) => `gc_after = unixepoch('subsec') + ${n} WHERE sha256 = NEW.sha256`),
  CHUNK_BYTES: TRIGGER("file_content_chunks_planned", (n) => `(c.size + ${n - 1}) / ${n} ELSE 0`),
  MANIFEST_PARTS: TABLE("owner_backups", (n) => `parts BETWEEN 0 AND ${n})`,
                        (n) => `CHECK (parts + segments <= ${n})`),
  PART_ENTRY: TABLE("owner_backups", (n) => `length(part_list) <= ${LIMITS.MANIFEST_PARTS * n})`),
  SEGMENT_ENTRY: TABLE("owner_backups",
                       (n) => `length(segment_list) <= ${LIMITS.MANIFEST_PARTS * n})`),
  CURSOR_CHARS: TABLE("owner_backups", (n) => `cursor IS NULL OR length(cursor) <= ${n})`),
  LOG_BYTES: [[CAPTURE, (n) => `(SELECT bytes FROM log_capture) < ${n};`],
              [CAPTURE, (n) => `WHERE capturing = 1 AND (bytes >= ${n} OR `]],
  LOG_ROW_BYTES: [
    ...TRIGGER("change_log_added", (n) => `length(CAST(NEW.line AS BLOB)) + ${n}, last = NEW.seq`),
    ...TRIGGER("change_log_removed", (n) => `length(CAST(OLD.line AS BLOB)) - ${n}; END`),
  ],
  IMAGE_MAX: [[CAPTURE, (n) => `length(CAST(image AS BLOB)) <= ${n} THEN image ELSE `]],
  CRON_MAX_CHARS: TABLE("run_schedules", (n) => `length(cron) BETWEEN 1 AND ${n})`),
  RUNS_CEILING: [
    ...TRIGGER("runs_live_full", (n) => `>= ${n} BEGIN`, (n) => `'runs_full: ${n} runs are live'`),
    ...TRIGGER("sandbox_leases_full", (n) => `>= ${n} BEGIN`,
               (n) => `'sandbox_leases_full: ${n} sandboxes are leased'`),
    ...TRIGGER("sandbox_signins_full", (n) => `>= ${n} BEGIN`,
               (n) => `'sandbox_signins_full: ${n} sign-ins are live'`),
  ],
  CALLS_OPEN: TRIGGER("model_reservations_open", (n) => `'dispatched')) >= ${n} BEGIN`,
                      (n) => `'model_calls_full: ${n} model calls are open'`),
  SPEND_KEPT: TRIGGER("model_spend_kept", (n) => `FROM model_spend) > ${n} BEGIN`,
                      (n) => `'model_spend_full: ${n} spend counters are kept'`),
  SOCKET_TICKETS: TRIGGER("socket_tickets_full", (n) => `FROM socket_tickets) >= ${n} `),
  ARGUMENTS_CHARS: TABLE("wake_callbacks", (n) => `length(rooms) <= ${n})`),
  SUBSCRIPTIONS_PER_AGENT: TRIGGER("wake_callbacks_full",
                                   (n) => `agent_id = NEW.agent_id) >= ${n} `,
                                   (n) => `'subscription_limit: ${n} doorbell `),
  DOORBELL_BLOB_CHARS: TABLE("wake_callbacks", (n) => `length(blob) BETWEEN 1 AND ${n} `,
                             (n) => `length(previous_blob) <= ${n} `),
  RUN_ID_CHARS: [
    ...TABLE("egress_reservations", ID("run")), ...TABLE("runs", ID("id")),
    ...TABLE("run_inputs", ID("run")), ...TABLE("run_grants", ID("run")),
    ...TABLE("conversations", ID("last_run")), ...TABLE("run_schedules", ID("last_run")),
  ],
  CONVERSATION_ID_CHARS: [...TABLE("runs", ID("conversation")), ...TABLE("conversations", ID("id")),
                          ...TABLE("socket_tickets", ID("conversation"))],
  SCHEDULE_ID_CHARS: TABLE("run_schedules", ID("id")),
  MESSAGE_ID_CHARS: TABLE("runs", ID("mail")),
  NATIVE_ID_CHARS: TABLE("native_agents", ID("agent_id")),
  AUTHORITY_CHARS: TABLE("ledger_messages", (n) => `length(authority) <= ${n} `),
  RESULT_BYTES: TABLE("runs", (n) => `length(CAST(result AS BLOB)) <= ${n})`),
  ERROR_BYTES: TABLE("runs", (n) => `length(CAST(error AS BLOB)) <= ${n})`),
  CONVERSATIONS_KEPT: TRIGGER("conversations_full", (n) => `FROM conversations) >= ${n} BEGIN`,
                              (n) => `'conversations_full: ${n} conversations are kept'`),
  SCHEDULES_KEPT: TRIGGER("run_schedules_full", (n) => `FROM run_schedules) >= ${n} BEGIN`,
                          (n) => `'run_schedules_full: ${n} schedules are kept`),
  INPUT_BYTES: TABLE("run_schedules", (n) => `length(CAST(input AS BLOB)) BETWEEN 1 AND ${n})`),
  QUEUE_ROWS: TRIGGER("run_inputs_full", (n) => `FROM run_inputs) >= ${n} OR`,
                      (n) => `'run_inputs_full: ${n} inputs or `),
  QUEUE_BYTES: TRIGGER("run_inputs_full", (n) => `+ NEW.bytes > ${n} BEGIN`,
                       (n) => ` inputs or ${n} bytes wait already`),
  ENGINE_SIGNINS: TRIGGER("engine_signins_full", (n) => `FROM engine_signins) >= ${n} BEGIN`,
                          (n) => `'engine_signins_full: ${n} engines are known'`),
  SIGNIN_MODELS_CHARS: TABLE("engine_signins", (n) => `length(models) <= ${n} `),
  SIGNIN_LIST_CHARS: [...TABLE("sandbox_leases", (n) => `length(signins) <= ${n} `),
                      ...TABLE("platform_signins", (n) => `length(engines) <= ${n} `)],
  SIGNIN_URL_MAX: TABLE("sandbox_signins", (n) => `url IS NULL OR length(url) <= ${n})`),
  SIGNIN_CODE_MAX: TABLE("sandbox_signins", (n) => `code IS NULL OR length(code) <= ${n})`),
});

/**
 * The owner modules' names and charges the SQL spells: each place's statements and the text
 * they must hold, as the module builds it now. The manifest's charges are the files plane's
 * own: `files.ts` reads a history row's bytes with `HISTORY_BYTES`, as its triggers charge.
 */
export const NAMED: readonly (readonly [Head, string])[] = Object.freeze([
  ["CREATE TABLE collection_rows", `filer = '${OWNER_SCOPE}' OR (length(filer) = 71 AND filer ` +
    `GLOB '${CLIENT_WRITER}')`],
  ["CREATE TABLE collection_usage", `who = '${CLIENTS}' OR (length(who) = 71 AND who GLOB ` +
    `'${CLIENT_WRITER}')`],
  ["CREATE TABLE file_chunks", `holder TEXT NOT NULL DEFAULT '${OWNER_SCOPE}'`],
  ["CREATE TABLE file_shares", `who IN ('${CLIENTS}', '${OWNER_SCOPE}') OR (length(who) = 71 ` +
    `AND who GLOB '${CLIENT_WRITER}')`],
  ["INSERT INTO file_shares", `VALUES ('${CLIENTS}', 0, 0, 0, 0, ${SHARE_BYTES}, 0), ` +
    `('${OWNER_SCOPE}', 0, 0, 0, 0, ${SHARE_BYTES}, 0)`],
  ["INSERT INTO file_space", `NULL, ${2 * SHARE_BYTES}, 0, 0)`],
  ["CREATE TRIGGER file_shares_charged", `manifest_bytes + ${SHARE_BYTES} WHERE who IN ` +
    `(NEW.who, '${CLIENTS}')`],
  ["CREATE TRIGGER file_shares_credited", `manifest_bytes - ${SHARE_BYTES} WHERE who = ` +
    `'${CLIENTS}'`],
  ["CREATE TRIGGER file_shares_emptied", `NEW.manifest_bytes = ${SHARE_BYTES} BEGIN`],
  ["CREATE TABLE archive_segments", `filer = '${OWNER_SCOPE}'`],
  ["CREATE TRIGGER file_entries_added", ENTRY_BYTES("NEW")],
  ["CREATE TRIGGER file_entries_removed", ENTRY_BYTES("OLD")],
  ["CREATE TRIGGER file_history_added", HISTORY_BYTES("NEW")],
  ["CREATE TRIGGER file_history_removed", HISTORY_BYTES("OLD")],
  ["CREATE TRIGGER file_contents_added", CONTENT_BYTES("NEW")],
  ["CREATE TRIGGER file_contents_removed", CONTENT_BYTES("OLD")],
  ["CREATE TRIGGER file_chunks_added", CHUNK_ROW_BYTES()],
  ["CREATE TRIGGER file_chunks_removed", CHUNK_ROW_BYTES()],
  ["CREATE TRIGGER file_temps_added", TEMP_BYTES("NEW")],
  ["CREATE TRIGGER file_temps_removed", TEMP_BYTES("OLD")],
  ["CREATE TRIGGER file_scope_contents_added", SCOPE_REF_BYTES("NEW")],
  ["CREATE TRIGGER file_scope_chunks_removed", SCOPE_REF_BYTES("OLD")],
  [CAPTURE, `THEN image ELSE '${LOG_WIDE}' END`],
  [CAPTURE, `(SELECT line FROM change_log WHERE seq = log_capture.last) = '${LOG_WIDE}')`],
]);

/** Each place of `spelled` its statements do not hold at `values`' bound, by bound. */
export function unheld(values: Readonly<Record<string, unknown>>,
                       spelled: Readonly<Record<string, readonly Place[]>> = SPELLED,
                       migrations: readonly Migration[] = MIGRATIONS): string[] {
  const problems: string[] = [];
  for (const [bound, places] of Object.entries(spelled)) {
    const value = values[bound];
    if (typeof value !== "number") {
      problems.push(`${bound} is no frozen bound`);
      continue;
    }
    for (const [head, text] of places) {
      const found = statements(head, migrations);
      if (found.length === 0 || !found.every((sql) => sql.includes(text(value)))) {
        problems.push(`${bound} (${value}) is not what ${String(head)} spells: ${text(value)}`);
      }
    }
  }
  return problems;
}

/** Each of `named`'s places its statements do not hold. */
export function unnamed(named: readonly (readonly [Head, string])[] = NAMED,
                        migrations: readonly Migration[] = MIGRATIONS): string[] {
  return named.filter(([head, text]) => {
    const found = statements(head, migrations);
    return found.length === 0 || !found.every((sql) => sql.includes(text));
  }).map(([head]) => String(head));
}

/**
 * The frozen bounds, as the contract's source marks them: each one after its frozen
 * section's opening, and each one before it whose comment says it is frozen.
 */
function frozen(): string[] {
  const text = readFileSync(join(PLATFORM, "contract", "src", "limits.ts"), "utf8");
  const at = text.indexOf("// The owner's frozen bounds.");
  if (at < 0) {
    throw new Error("contract/src/limits.ts has no frozen section");
  }
  const after = [...text.slice(at).matchAll(/^export const ([A-Z_]+) =/gm)];
  const marked = [...text.slice(0, at).matchAll(
    /Frozen: the owner's schema spells it\.\n \*\/\nexport const ([A-Z_]+) =/g)];
  return [...marked, ...after].map((match) => match[1]!).sort();
}

describe("the owner's frozen bounds", () => {
  it("are each what every place the frozen SQL spells it says", () => {
    expect(unheld(LIMITS)).toEqual([]);
  });

  it("list every frozen bound, and only those", () => {
    expect(frozen()).toContain("CHUNK_BYTES");
    expect(frozen()).toContain("SIGNIN_CODE_MAX");
    expect(Object.keys(SPELLED).sort()).toEqual(frozen());
  });

  it("hold the change log's bounds in every capture trigger", () => {
    expect(statements(CAPTURE).length).toBeGreaterThan(100);
  });

  it("hold the owner modules' names and charges where the SQL spells them", () => {
    expect(unnamed()).toEqual([]);
  });

  it("see a bound changed without its SQL, or SQL without its bound", () => {
    // A bound changed, at each place it is spelled.
    expect(unheld({ ...LIMITS, CRON_MAX_CHARS: 257 })).toEqual([
      "CRON_MAX_CHARS (257) is not what CREATE TABLE run_schedules spells: " +
      "length(cron) BETWEEN 1 AND 257)"]);
    expect(unheld({ ...LIMITS, RUNS_CEILING: 999 })).toHaveLength(6);
    expect(unheld({ ...LIMITS, IMAGE_MAX: 1 })).toHaveLength(1);
    // A statement edited, or one no statement is.
    const edited = MIGRATIONS.map((migration) => migration.map((sql) =>
      sql.replace("length(cron) BETWEEN 1 AND 256)", "length(cron) BETWEEN 1 AND 512)")));
    expect(unheld(LIMITS, SPELLED, edited)).toHaveLength(1);
    expect(unheld(LIMITS, { CRON_MAX_CHARS: TABLE("no_such_table", ID("x")) })).toHaveLength(1);
    expect(unheld(LIMITS, { NOT_A_BOUND: [] })).toEqual(["NOT_A_BOUND is no frozen bound"]);
    // A name or a charge the SQL no longer spells.
    expect(unnamed([["CREATE TABLE file_chunks", "holder TEXT NOT NULL DEFAULT 'own'"]]))
      .toEqual(["CREATE TABLE file_chunks"]);
  });
});
