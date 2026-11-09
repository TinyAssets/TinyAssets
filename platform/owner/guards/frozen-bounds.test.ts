/**
 * The owner's frozen bounds held to the frozen SQL that spells them (decisions A7).
 *
 * The owner's migrations are literal SQL text, frozen from the first deploy and pinned by
 * digest (`schema.ts`, `migrations.json`). The bounds that text enforces are the code's
 * bounds too, read by their TypeScript owners from the contract's frozen bounds
 * (`contract/src/limits.ts`; `ELSEWHERE` names the three kept in their own modules), and the
 * names and charges it spells are the owner modules' own (`manifest.ts`, `changelog.ts`).
 * Nothing ties the two by construction, so this guard does, three ways:
 * - each place the SQL spells a bound is listed (`SPELLED`): its statements, its text at the
 *   bound's value, and how many times the SQL spells it there; every statement named must
 *   hold it, and the count must be exact, so a place changed alone fails;
 * - every number those places spell is accounted for (`unaccounted`): once each listed
 *   place's numbers are set aside, none is left anywhere in the SQL, but for the numbers
 *   the SQL also spells for reasons of its own (`SHARED`, each with its count);
 * - changing any one bound, with every place it is spelled at changed with it but one, fails
 *   (the coverage test below, run over every place).
 * A constant changed without the SQL (which takes a new migration) fails here; SQL changed
 * without the constant fails the digest lock (`migrations.test.ts`) first.
 *
 * The seed rows the code's defaults fill (the default policy, the file settings) are held by
 * the workerd tests that read a fresh object's (`policy.test.ts`, `files-quota.test.ts`).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import * as CONTRACT from "../../contract/src/limits";
import { PLATFORM } from "../../ci/paths";
import { LOG_WIDE } from "../src/changelog";
import {
  CHUNK_ROW_BYTES, CLIENT_WRITER, CLIENTS, CONTENT_BYTES, ENTRY_BYTES, HISTORY_BYTES,
  OWNER_SCOPE, SCOPE_REF_BYTES, SHARE_BYTES, TEMP_BYTES,
} from "../src/manifest";
import type { Migration } from "../src/migrate";
import { MIGRATIONS } from "../src/schema";
import { SEGMENT_LEAST_BYTES } from "../src/segments";
import { RUNS_CEILING } from "../../contract/src/control";

/** The bounds by name, each its value. */
type Values = Readonly<Record<string, number>>;
/**
 * The frozen bounds the SQL spells, by name: the contract's, and the ones kept in their own
 * modules (`ELSEWHERE`).
 */
const LIMITS: Values = Object.freeze({ ...CONTRACT, RUNS_CEILING, SEGMENT_LEAST_BYTES,
                                       SHARE_BYTES });
/**
 * The bounds kept outside the contract's frozen section: `RUNS_CEILING`, the control
 * record's own bound on a tier (`contract/src/control.ts`); `SEGMENT_LEAST_BYTES`, which the
 * segments' seal
 * derives; `SHARE_BYTES`, a share row's own charge in the manifest's layout.
 */
const ELSEWHERE: readonly string[] = Object.freeze(["RUNS_CEILING", "SEGMENT_LEAST_BYTES",
                                                    "SHARE_BYTES"]);

/** Which statements a place is in: those starting with the head and a space, or matching it. */
type Head = string | RegExp;
/**
 * One place the SQL spells a bound: its statements, how many times they spell its text in
 * all, and its text at the bound's value (and the other bounds', for a product of two).
 */
type Place = readonly [head: Head, count: number, text: (n: number, values: Values) => string];
/** Where a statement is: its migration and its index in it. */
type At = readonly [number, number];

/** Every statement of `migrations` that `head` names, with where it is. */
function statements(head: Head, migrations: readonly Migration[] = MIGRATIONS):
    { sql: string; at: At }[] {
  return migrations
    .flatMap((migration, m) => migration.map((sql, s) => ({ sql, at: [m, s] as const })))
    .filter(({ sql }) => typeof head === "string" ? sql.startsWith(`${head} `) : head.test(sql));
}

/** How many times `text` is in `sql`, none overlapping. */
const times = (sql: string, text: string) => sql.split(text).length - 1;

const TABLE = (name: string, ...texts: ((n: number) => string)[]): Place[] =>
  texts.map((text) => [`CREATE TABLE ${name}`, 1, text] as const);
const TRIGGER = (name: string, ...texts: ((n: number) => string)[]): Place[] =>
  texts.map((text) => [`CREATE TRIGGER ${name}`, 1, text] as const);
/** A body's placement (ADR-035): inline within the bound, or placed past it. */
const PLACED = (table: string, body: string) => TABLE(table,
  (n) => `length(CAST(${body} AS BLOB)) <= ${n}`,
  (n) => `${body} = '' AND body_bytes > ${n} AND length(content_id) = 64`);
/** An id's fixed length. */
const ID = (column: string) => (n: number) => `length(${column}) = ${n}`;
/** Every capture trigger (`<table>_logged_<event>`, slice P's change log), and the updates'. */
const CAPTURE = /^CREATE TRIGGER \w+_logged_(?:insert|update|delete) /;
const CAPTURE_UPDATE = /^CREATE TRIGGER \w+_logged_update /;
/** The capture triggers the migrations make, and the updates among them. */
const CAPTURES = 125;
const UPDATES = 42;
/** The triggers that count a content's chunk list's rows from its size. */
const CHUNK_LIST = new RegExp("^CREATE TRIGGER (?:file_contents_(?:added|removed)|" +
  "file_scope_contents_(?:added|removed)|file_content_chunks_planned) ");

/** Each frozen bound, at every place the SQL spells it, with how many times. */
export const SPELLED: Readonly<Record<string, readonly Place[]>> = Object.freeze({
  PLACE_BYTES: [...PLACED("collection_rows", "body"), ...PLACED("collection_history", "body_json"),
                ...PLACED("message_bodies", "body")],
  GC_GRACE_S: TRIGGER("file_chunks_released",
                      (n) => `gc_after = unixepoch('subsec') + ${n} WHERE sha256 = NEW.sha256`),
  CHUNK_BYTES: [[CHUNK_LIST, 5, (n) => `.size + ${n - 1}) / ${n} ELSE 0 END`],
                ...TABLE("file_chunks", (n) => `size BETWEEN 1 AND ${n})`)],
  MANIFEST_PARTS: TABLE("owner_backups", (n) => `parts BETWEEN 0 AND ${n})`,
                        (n) => `CHECK (parts + segments <= ${n})`),
  PART_ENTRY: [["CREATE TABLE owner_backups", 1,
                (n, v) => `length(part_list) <= ${v["MANIFEST_PARTS"]! * n})`]],
  SEGMENT_ENTRY: [["CREATE TABLE owner_backups", 1,
                   (n, v) => `length(segment_list) <= ${v["MANIFEST_PARTS"]! * n})`]],
  CURSOR_CHARS: TABLE("owner_backups", (n) => `cursor IS NULL OR length(cursor) <= ${n})`),
  LOG_BYTES: [[CAPTURE, CAPTURES, (n) => `(SELECT bytes FROM log_capture) < ${n};`],
              [CAPTURE_UPDATE, UPDATES, (n) => `(SELECT bytes FROM log_capture) < ${n} AND NOT (`],
              [CAPTURE, CAPTURES, (n) => `WHERE capturing = 1 AND (bytes >= ${n} OR `]],
  LOG_ROW_BYTES: [
    ...TRIGGER("change_log_added", (n) => `length(CAST(NEW.line AS BLOB)) + ${n}, last = NEW.seq`),
    ...TRIGGER("change_log_removed", (n) => `length(CAST(OLD.line AS BLOB)) - ${n}; END`),
  ],
  IMAGE_MAX: [[CAPTURE, CAPTURES + UPDATES,
               (n) => `length(CAST(image AS BLOB)) <= ${n} THEN image ELSE `]],
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
  // The bounds the old builder never interpolated: the SQL spelled each as a plain number,
  // its twin in code a constant of its own, now one bound.
  INLINE_BYTES: TABLE("file_contents", (n) => `CHECK (inline IS NULL OR size <= ${n})`),
  CONNECTION_BLOB_MAX: TABLE("connection_secrets", (n) => `CHECK (length(blob) <= ${n})`),
  MODELS_JSON_MAX: TABLE("connections", (n) => `length(CAST(models AS BLOB)) <= ${n})`),
  EGRESS_JSON_MAX: TABLE("connections", (n) => `length(CAST(egress AS BLOB)) <= ${n})`),
  REDIRECT_URI_MAX: TABLE("connection_flows", (n) => `length(redirect_uri) <= ${n})`),
  EGRESS_PATH_MAX: TABLE("egress_reservations", (n) => `length(path) BETWEEN 1 AND ${n} AND`),
  KEY_NAME_MAX: TABLE("owner_keys", (n) => `length(name) BETWEEN 1 AND ${n})`),
  INBOUND_SECRET_MAX: TABLE("inbound_bindings", (n) => `length(secret) <= ${n})`),
  INBOUND_ROUTE_MAX: TABLE("inbound_bindings", (n) => `length(route) <= ${n})`),
  NOTICE_DETAIL: TABLE("owner_notices", (n) => `length(detail) <= ${n})`),
  MAX_PATH_BYTES: TABLE("starter_receipts", (n) => `length(CAST(path AS BLOB)) <= ${n})`),
  SCOPES_JSON_MAX: TABLE("outside_clients", (n) => `length(CAST(scopes AS BLOB)) <= ${n})`),
  CARD_REQUEST_MAX: TABLE("agent_cards", (n) => `length(request) <= ${n})`),
  SEGMENT_LEAST_BYTES: [["CREATE INDEX archive_segments_small", 1, (n) => `WHERE bytes < ${n}`]],
  SHARE_BYTES: [
    ["INSERT INTO file_shares", 2, (n) => `0, 0, 0, 0, ${n}, 0)`],
    ["INSERT INTO file_space", 1, (n) => `NULL, ${2 * n}, 0, 0)`],
    ["CREATE TRIGGER file_shares_charged", 2, (n) => `manifest_bytes + ${n}`],
    ["CREATE TRIGGER file_shares_credited", 2, (n) => `manifest_bytes - ${n}`],
    ["CREATE TRIGGER file_shares_emptied", 1, (n) => `NEW.manifest_bytes = ${n} BEGIN`],
  ],
});

/**
 * The numbers the SQL spells for reasons of its own as well as at a bound's places, each with
 * how many times it is left once every place is set aside. A new one, or one more, fails:
 * - 4096 the manifest's page and 64 a hash's or a content id's length, in every charge;
 * - 256 the checks on a model's (two tables), a served model's, an origin's (three tables), a
 *   session id's and a schedule's last error, which code bounds with no constant (`runs.ts`);
 * - 1024 a stamp's (three tables), a binding's profile and the tier record, the same;
 * A number inside a name (`sha256`) is no number: only one standing alone is counted.
 * - 16 a generation's id, a key id and a readable id, 35 a sign-in's id (`si_` and 32 hex)
 *   and 36 a subscription's (`sub_` and 32 hex): ids' lengths, which their grammars hold.
 */
const SHARED: Values = Object.freeze({ 16: 4, 35: 1, 36: 1, 64: 281, 256: 8, 1024: 5,
                                       4096: 240 });

/**
 * The owner modules' names and charges the SQL spells: each place's statements, how many
 * times, and the text, as the module builds it now. The manifest's charges are the files
 * plane's own: `files.ts` reads a history row's bytes with `HISTORY_BYTES`, as its triggers
 * charge.
 */
export const NAMED: readonly (readonly [Head, number, string])[] = Object.freeze([
  ["CREATE TABLE collection_rows", 1, `filer = '${OWNER_SCOPE}' OR (length(filer) = 71 AND ` +
    `filer GLOB '${CLIENT_WRITER}')`],
  ["CREATE TABLE collection_usage", 1, `who = '${CLIENTS}' OR (length(who) = 71 AND who GLOB ` +
    `'${CLIENT_WRITER}')`],
  ["CREATE TABLE file_chunks", 1, `holder TEXT NOT NULL DEFAULT '${OWNER_SCOPE}'`],
  ["CREATE TABLE file_shares", 1, `who IN ('${CLIENTS}', '${OWNER_SCOPE}') OR (length(who) = ` +
    `71 AND who GLOB '${CLIENT_WRITER}')`],
  ["INSERT INTO file_shares", 1, `VALUES ('${CLIENTS}', 0, 0, 0, 0, ${SHARE_BYTES}, 0), ` +
    `('${OWNER_SCOPE}', 0, 0, 0, 0, ${SHARE_BYTES}, 0)`],
  ["CREATE TABLE archive_segments", 1, `filer = '${OWNER_SCOPE}'`],
  ["CREATE TRIGGER file_entries_added", 2, ENTRY_BYTES("NEW")],
  ["CREATE TRIGGER file_entries_removed", 2, ENTRY_BYTES("OLD")],
  ["CREATE TRIGGER file_history_added", 2, HISTORY_BYTES("NEW")],
  ["CREATE TRIGGER file_history_removed", 2, HISTORY_BYTES("OLD")],
  ["CREATE TRIGGER file_contents_added", 1, CONTENT_BYTES("NEW")],
  ["CREATE TRIGGER file_contents_removed", 1, CONTENT_BYTES("OLD")],
  ["CREATE TRIGGER file_chunks_added", 1, CHUNK_ROW_BYTES()],
  ["CREATE TRIGGER file_chunks_removed", 2, CHUNK_ROW_BYTES()],
  ["CREATE TRIGGER file_temps_added", 2, TEMP_BYTES("NEW")],
  ["CREATE TRIGGER file_temps_removed", 2, TEMP_BYTES("OLD")],
  ["CREATE TRIGGER file_scope_contents_added", 2, SCOPE_REF_BYTES("NEW")],
  ["CREATE TRIGGER file_scope_chunks_removed", 2, SCOPE_REF_BYTES("OLD")],
  [CAPTURE, CAPTURES + UPDATES, `THEN image ELSE '${LOG_WIDE}' END`],
  [CAPTURE, CAPTURES, `(SELECT line FROM change_log WHERE seq = log_capture.last) = ` +
    `'${LOG_WIDE}')`],
]);

/** How `head`'s statements fail to spell `text` `count` times, each at least once; or null. */
function miss(head: Head, count: number, text: string, migrations: readonly Migration[]):
    string | null {
  const found = statements(head, migrations);
  if (found.length === 0) {
    return `no statement is ${String(head)}`;
  }
  const spelled = found.reduce((sum, { sql }) => sum + times(sql, text), 0);
  if (!found.every(({ sql }) => sql.includes(text)) || spelled !== count) {
    return `${String(head)} spells ${text} ${spelled} times, not ${count}`;
  }
  return null;
}

/** Each place of `spelled` its statements do not hold at `values`, by bound. */
export function unheld(values: Values,
                       spelled: Readonly<Record<string, readonly Place[]>> = SPELLED,
                       migrations: readonly Migration[] = MIGRATIONS): string[] {
  const problems: string[] = [];
  for (const [bound, places] of Object.entries(spelled)) {
    const value = values[bound];
    if (typeof value !== "number") {
      problems.push(`${bound} is no frozen bound`);
      continue;
    }
    for (const [head, count, text] of places) {
      const missed = miss(head, count, text(value, values), migrations);
      if (missed !== null) {
        problems.push(`${bound} (${value}): ${missed}`);
      }
    }
  }
  return problems;
}

/** Each of `named`'s places its statements do not hold. */
export function unnamed(named: readonly (readonly [Head, number, string])[] = NAMED,
                        migrations: readonly Migration[] = MIGRATIONS): string[] {
  return named.flatMap(([head, count, text]) => miss(head, count, text, migrations) ?? []);
}

/** Where in a place's text its bound is spelled: each number a step of the bound moves. */
function spelledAt(bound: string, text: Place[2], values: Values): number[] {
  const numbers = (spelled: string) => [...spelled.matchAll(/[0-9]+/g)];
  const now = numbers(text(values[bound]!, values));
  const moved = numbers(text(values[bound]! + 1, { ...values, [bound]: values[bound]! + 1 }));
  return now.filter((match, k) => moved[k]?.[0] !== match[0]).map((match) => match.index);
}

/**
 * The numbers the places spell that the SQL spells anywhere else too, each with how many times
 * it is left once every listed place's numbers are set aside.
 */
export function unaccounted(values: Values = LIMITS,
                            spelled: Readonly<Record<string, readonly Place[]>> = SPELLED,
                            migrations: readonly Migration[] = MIGRATIONS):
    Record<string, number> {
  const taken = new Map<string, Set<number>>();
  const numbers = new Set<string>();
  for (const [bound, places] of Object.entries(spelled)) {
    for (const [head, , text] of places) {
      const spelledText = text(values[bound]!, values);
      const within = spelledAt(bound, text, values);
      for (const offset of within) {
        numbers.add(/[0-9]+/.exec(spelledText.slice(offset))![0]);
      }
      for (const { sql, at } of statements(head, migrations)) {
        const marks = taken.get(at.join(".")) ?? new Set<number>();
        for (let from = sql.indexOf(spelledText); from >= 0;
             from = sql.indexOf(spelledText, from + spelledText.length)) {
          for (const offset of within) {
            marks.add(from + offset);
          }
        }
        taken.set(at.join("."), marks);
      }
    }
  }
  const left: Record<string, number> = {};
  migrations.forEach((migration, m) => migration.forEach((sql, s) => {
    const marks = taken.get(`${m}.${s}`) ?? new Set<number>();
    for (const match of sql.matchAll(/(?<![0-9A-Za-z_.])[0-9]+(?![0-9A-Za-z_.])/g)) {
      if (numbers.has(match[0]) && !marks.has(match.index)) {
        left[match[0]] = (left[match[0]] ?? 0) + 1;
      }
    }
  }));
  return left;
}

/**
 * The frozen bounds, as the contract's source marks them: each one after its frozen
 * section's opening, and each one before it whose comment says it is frozen; and the ones
 * kept elsewhere.
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
  return [...[...marked, ...after].map((match) => match[1]!), ...ELSEWHERE].sort();
}

/** One changed text in a stepped schema: its statement, where it starts, and what it was. */
interface Changed {
  readonly at: At;
  readonly from: number;
  readonly old: string;
  readonly text: string;
}

/**
 * The schema as a new migration would spell it with `bound` one more: every place the step
 * moves, of `bound` or of a bound spelled with it, changed; and each changed text.
 */
function stepped(bound: string, values: Values = LIMITS):
    { values: Values; migrations: Migration[]; changed: Changed[]; bounds: string[];
      moved: number } {
  const next: Values = { ...values, [bound]: values[bound]! + 1 };
  const migrations = MIGRATIONS.map((migration) => [...migration]);
  const changed: Changed[] = [];
  const bounds = new Set<string>();
  let moved = 0;
  for (const [other, places] of Object.entries(SPELLED)) {
    for (const [head, count, text] of places) {
      const old = text(values[other]!, values);
      const now = text(next[other]!, next);
      if (old === now) {
        continue;
      }
      bounds.add(other);
      moved += count;
      for (const { at } of statements(head)) {
        migrations[at[0]]![at[1]] = migrations[at[0]]![at[1]]!.split(old).join(now);
      }
      for (const { sql, at } of statements(head, migrations)) {
        for (let from = sql.indexOf(now); from >= 0; from = sql.indexOf(now, from + now.length)) {
          changed.push({ at, from, old, text: now });
        }
      }
    }
  }
  return { values: next, migrations, changed, bounds: [...bounds], moved };
}

describe("the owner's frozen bounds", () => {
  it("are each what every place the frozen SQL spells it says, as many times", () => {
    expect(unheld(LIMITS)).toEqual([]);
  });

  it("list every frozen bound, and only those", () => {
    expect(frozen()).toContain("CHUNK_BYTES");
    expect(frozen()).toContain("INLINE_BYTES");
    expect(Object.keys(SPELLED).sort()).toEqual(frozen());
  });

  it("leave none of their numbers in the SQL past their places, but the shared ones", () => {
    expect(statements(CAPTURE)).toHaveLength(CAPTURES);
    expect(statements(CAPTURE_UPDATE)).toHaveLength(UPDATES);
    expect(unaccounted()).toEqual(SHARED);
  });

  it("hold the owner modules' names and charges where the SQL spells them", () => {
    expect(unnamed()).toEqual([]);
  });

  it("fail when a bound changes with every place it is spelled at but one, for every place",
     () => {
    let places = 0;
    for (const bound of Object.keys(SPELLED)) {
      const step = stepped(bound);
      const only = Object.fromEntries(step.bounds.map((each) => [each, SPELLED[each]!]));
      // Changed everywhere, as a new migration would: it holds.
      expect(unheld(step.values, SPELLED, step.migrations), bound).toEqual([]);
      // Each place it moved, every time the SQL spells it there.
      expect(step.changed.length, bound).toBe(step.moved);
      for (const { at, from, old, text } of step.changed) {
        const hybrid = step.migrations.map((migration) => [...migration]);
        const sql = hybrid[at[0]]![at[1]]!;
        hybrid[at[0]]![at[1]] = sql.slice(0, from) + old + sql.slice(from + text.length);
        expect(unheld(step.values, only, hybrid), `${bound} at ${at.join(".")}:${from}`)
          .not.toEqual([]);
        places++;
      }
    }
    // Every place of every bound, the products of two counted with each.
    expect(places).toBe(551);
  });

  it("see a bound changed without its SQL, a place more or less, or a number left over", () => {
    expect(unheld({ ...LIMITS, CRON_MAX_CHARS: 257 })).toEqual([
      "CRON_MAX_CHARS (257): CREATE TABLE run_schedules spells length(cron) BETWEEN 1 AND 257) " +
      "0 times, not 1"]);
    expect(unheld({ ...LIMITS, RUNS_CEILING: 999 })).toHaveLength(6);
    // One more place than listed: a new capture trigger, spelling the log's bounds.
    const capture = MIGRATIONS[0]!.find((sql) => CAPTURE.test(sql))!;
    const more = [...MIGRATIONS, [capture.replace(/^CREATE TRIGGER \w+?_logged/,
                                                  "CREATE TRIGGER stray_logged")]];
    expect(unheld(LIMITS, SPELLED, more).length).toBeGreaterThan(0);
    // A bound's number spelled where no place is: left over.
    const stray = [...MIGRATIONS, ["CREATE TABLE stray (x TEXT CHECK (length(x) <= 71303168))"]];
    expect(unaccounted(LIMITS, SPELLED, stray)).toEqual({ ...SHARED, 71303168: 1 });
    // A number inside a name is none: a new `sha256` column leaves nothing over; a bare 256
    // is one more.
    const hashed = [...MIGRATIONS, ["CREATE TABLE stray (sha256 TEXT, body_sha256 TEXT)"]];
    expect(unaccounted(LIMITS, SPELLED, hashed)).toEqual(SHARED);
    const bare = [...MIGRATIONS, ["CREATE TABLE stray (x TEXT CHECK (length(x) <= 256))"]];
    expect(unaccounted(LIMITS, SPELLED, bare)).toEqual({ ...SHARED, 256: SHARED[256]! + 1 });
    expect(unheld(LIMITS, { CRON_MAX_CHARS: TABLE("no_such_table", ID("x")) })).toHaveLength(1);
    expect(unheld(LIMITS, { NOT_A_BOUND: [] })).toEqual(["NOT_A_BOUND is no frozen bound"]);
    expect(unnamed([["CREATE TABLE file_chunks", 1, "holder TEXT NOT NULL DEFAULT 'own'"]]))
      .toEqual(["CREATE TABLE file_chunks spells holder TEXT NOT NULL DEFAULT 'own' 0 times, " +
                "not 1"]);
  });
});
