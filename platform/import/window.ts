/**
 * The import window's steps after staging (`import_research.md` §4, slice 5), each through the
 * import queue and answered in the staging bucket (the dark platform serves no HTTP, and only
 * the doors reach an owner's object), each safe to run again from any point:
 *
 * - `verify`: one `check` message an owner (`contract/src/import-message.ts`); its answer and
 *   pages read back over R2's S3 API and held to the owner's own bundle (`check.ts`), which must
 *   show 0 missing, 0 extra and 0 different. The doors' `importCheck` is the record of what
 *   failed, the dead-letter queue only a hint, since a step the consumer dropped is
 *   acknowledged and never reaches it. Each owner's import must also be done (its marker,
 *   `doneName`: the queue's backlog is no field of the Queues API's queue, so the import's own
 *   marker says it is drained). The result goes to the window's record (`--record`, 0600).
 * - `admit`: only once every bundle verified clean in the record and the dead-letter queue is
 *   empty: one `admit` message an owner, in turn, each answered before the next (the doors set
 *   its tier, admit it, suspend it for each old reason, then write its `o:` record). One owner
 *   that failed blocks every admit: going on without it is the founder's call, never this
 *   step's. It never admits an owner the old stack never admitted (`absent`).
 * - `wipe`: one `discard` message an owner (`importDiscard`: no tombstone, no erased record, no
 *   issuer's deletion), each answer's namespace kept in the record; then the directory's
 *   `o:rehearsal-<run>-` records deleted and the staging bucket's `<run>/` objects; then the
 *   proof: the `o:rehearsal-<run>-` listing, the `<run>/` listing, and each discarded
 *   namespace's listing in the files bucket, each empty. Durable Objects keep a point-in-time
 *   copy for 30 days (`owner/src/erasure.ts`), recoverable by Cloudflare alone.
 * - `dlq`: the dead-letter queue read over its HTTP pull consumer (added when it has none; no
 *   Worker consumes it, and a Worker config may name no pull consumer: wrangler refuses one),
 *   reported by bundle ordinal and op; its messages are deleted only with `--delete`, otherwise
 *   left to come back when their lease ends.
 *
 * Stdout holds counts and ordinals alone, never an owner's name, key or namespace (`cli.ts`);
 * the record keeps the keys and namespaces, 0600. Deleted at the cutover with the job.
 */
import { randomBytes } from "node:crypto";
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import {
  answerName, doneName, importMac, type ImportMessage, type ImportOp, importMessage, NO_ROWS,
  rehearsalRun, rowsSum, RUN,
} from "../contract/src/import-message";

import { DEFAULT_TIER, type Entry, type Row } from "./build";
import { type Answers, clean, type Differences, differences, expectedOf } from "./check";
import {
  ACCOUNT, API, bundlesIn, type Fetcher, type Inputs, type Names, pushMessage, queueId,
  s3Request, type Staged,
} from "./stage";

/** The prefix of every object of an owner's namespace in the files Worker's bucket. */
export const namespacePrefix = (ns: string) => `o/${ns}/`;

/** How a step waits and reads the clock: the process's, or a test's. */
export interface Clock {
  readonly now: () => Date;
  readonly sleep: (ms: number) => Promise<void>;
  /** How long a step waits for each answer, and how often it looks. */
  readonly waitMs: number;
  readonly everyMs: number;
}

export const CLOCK: Clock = Object.freeze({
  now: () => new Date(),
  sleep: (ms: number) => new Promise<void>((done) => setTimeout(done, ms)),
  waitMs: 30 * 60_000, everyMs: 5_000,
});

/** The window's record: what verify found, and what wipe discarded, by owner key. */
export interface WindowRecord {
  readonly run: string;
  readonly verified: Record<string, { readonly bundle: string; readonly clean: boolean;
                                      readonly differences: Differences }>;
  readonly discarded: Record<string, { readonly bundle: string; readonly namespace: string | null }>;
}

/** The record at `file`, or a new one for `run`; one of another run is refused. */
export function readRecord(file: string, run: string): WindowRecord {
  let text: string;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    return { run, verified: {}, discarded: {} };
  }
  const record = JSON.parse(text) as WindowRecord;
  if (record.run !== run) {
    throw new Error("the record is another run's: refused");
  }
  return record;
}

/** The record written whole, 0600, so a step killed while writing leaves the last one. */
export function writeRecord(file: string, record: WindowRecord): void {
  writeFileSync(`${file}.next`, `${JSON.stringify(record, null, 2)}\n`, { mode: 0o600 });
  renameSync(`${file}.next`, file);
}

/** The window's network: its inputs, a fetcher, and the clock. */
export interface Window {
  readonly names: Names;
  readonly inputs: Inputs;
  readonly fetcher: Fetcher;
  readonly clock: Clock;
}

async function ok(window: Window, request: Request, what: string): Promise<Response> {
  const answer = await window.fetcher(request);
  if (!answer.ok) {
    throw new Error(`${what} was refused (${answer.status}): nothing more is done`);
  }
  return answer;
}

/** A Cloudflare API call's result, or a loud failure. */
async function api<T>(window: Window, path: string, init: RequestInit = {}): Promise<T> {
  const answer = await window.fetcher(new Request(`${API}/accounts/${window.inputs.account}${path}`, {
    ...init, headers: { authorization: `Bearer ${window.inputs.apiToken}`,
                        "content-type": "application/json" } }));
  const body = await answer.json().catch(() => null) as { success?: boolean; result?: T } | null;
  if (!answer.ok || body?.success !== true) {
    throw new Error(`a Cloudflare API call was refused (${answer.status}): nothing more is done`);
  }
  return body.result as T;
}

/** An object of a bucket, as text, or null when the bucket has none. */
async function getText(window: Window, bucket: string, name: string): Promise<string | null> {
  const answer = await window.fetcher(s3Request(window.inputs, "GET", bucket, name, {}, null,
                                                window.clock.now()));
  if (answer.status === 404) {
    return null;
  }
  if (!answer.ok) {
    throw new Error(`a read of the ${bucket} bucket was refused (${answer.status})`);
  }
  return answer.text();
}

const XML_ENTITIES: Readonly<Record<string, string>> = Object.freeze({
  amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'" });
const unescaped = (text: string) => text.replace(/&(amp|lt|gt|quot|apos);/g,
                                                  (_m, name: string) => XML_ENTITIES[name]!);

/** Every key a bucket lists under `prefix` (ListObjectsV2), or the first `most` of them. */
export async function listKeys(window: Window, bucket: string, prefix: string,
                               most = Number.MAX_SAFE_INTEGER): Promise<string[]> {
  const keys: string[] = [];
  let token: string | null = null;
  do {
    const query: Record<string, string> = { "list-type": "2", prefix,
                                            "max-keys": String(Math.min(1000, most)) };
    if (token !== null) {
      query["continuation-token"] = token;
    }
    const answer = await ok(window, s3Request(window.inputs, "GET", bucket, "", query, null,
                                              window.clock.now()), `a listing of ${bucket}`);
    const xml = await answer.text();
    keys.push(...[...xml.matchAll(/<Key>([^<]*)<\/Key>/g)].map((m) => unescaped(m[1]!)));
    token = /<IsTruncated>true<\/IsTruncated>/.test(xml)
      ? unescaped(/<NextContinuationToken>([^<]*)<\/NextContinuationToken>/.exec(xml)?.[1] ?? "")
      : null;
  } while (token !== null && token !== "" && keys.length < most);
  return keys.slice(0, most);
}

/** One message for `staged`, of `op`, MAC'd under the window's key, with its ask. */
export function askOf(staged: Staged, run: string, op: Exclude<ImportOp, "import">,
                      importKey: string, ask = randomBytes(8).toString("hex")): ImportMessage {
  return { op, owner: staged.owner, bundle: staged.bundle, prefix: `${run}/${staged.key}`,
           mac: importMac(importKey, op, staged.owner, staged.bundle), ask };
}

/** The answer at `name`, waited for: its JSON, or a loud failure past the wait. */
async function answered(window: Window, name: string): Promise<Record<string, unknown>> {
  const until = window.clock.now().getTime() + window.clock.waitMs;
  for (;;) {
    const text = await getText(window, window.names.staging_bucket, name);
    if (text !== null) {
      return JSON.parse(text) as Record<string, unknown>;
    }
    if (window.clock.now().getTime() >= until) {
      throw new Error("an answer did not come within the wait: read the dead-letter queue " +
                      "(dlq), then run the step again");
    }
    await window.clock.sleep(window.clock.everyMs);
  }
}

/** A message sent, then its answer waited for; a refused one is the doors' word, thrown. */
async function ask(window: Window, queue: string, message: ImportMessage):
    Promise<Record<string, unknown>> {
  const answer = await window.fetcher(pushMessage(window.inputs, queue, message));
  const body = await answer.json().catch(() => null) as { success?: boolean } | null;
  if (!answer.ok || body?.success !== true) {
    throw new Error(`a ${message.op} message was refused (${answer.status}): nothing more is sent`);
  }
  return answered(window, answerName(message.prefix, message.ask!));
}

/** A bundle's manifest entries and rows, from its folder. */
function bundleContent(staged: Staged): { entries: Entry[]; rows: Row[] } {
  const paged = <T>(kind: "manifest" | "rows", key: string): T[] => staged.parts
    .filter((name) => name.startsWith(`${kind}/`))
    .sort((a, b) => parseInt(a.slice(kind.length + 1)) - parseInt(b.slice(kind.length + 1)))
    .flatMap((name) => (JSON.parse(readFileSync(join(staged.dir, name), "utf8")) as
      Record<string, T[]>)[key]!);
  return { entries: paged<Entry>("manifest", "entries"), rows: paged<Row>("rows", "rows") };
}

/** A check's pages of `part`, read back from its first until one says no more follows. */
async function checkPages(window: Window, prefix: string, ask: string, part: string):
    Promise<Record<string, unknown>[]> {
  const pages: Record<string, unknown>[] = [];
  for (let page = 0; ; page += 1) {
    const text = await getText(window, window.names.staging_bucket,
                               answerName(prefix, ask, `${part}-${page}`));
    if (text === null) {
      throw new Error("a check's page is missing from the staging bucket: run verify again");
    }
    const read = JSON.parse(text) as Record<string, unknown>;
    pages.push(read);
    if (read["more"] !== true) {
      return pages;
    }
  }
}

function checkRun(window: Window, run: string): void {
  if (!RUN.test(run)) {
    throw new Error("a run's id is lowercase letters, digits and dashes, at most 64");
  }
  if (!ACCOUNT.test(window.inputs.account)) {
    throw new Error("the account is 32 hex");
  }
}

/** What a step did, by bundle ordinal: counts and words alone. */
export interface StepSummary {
  readonly bundles: number;
  readonly clean: number;
  readonly lines: readonly string[];
}

/**
 * `verify`: each owner's import done, then checked, its answer held to its bundle; the result
 * by ordinal, and in the record. Clean only when every owner is.
 */
export async function verify(out: string, run: string, window: Window, recordFile: string,
                             print: (line: string) => void): Promise<StepSummary> {
  checkRun(window, run);
  const bundles = bundlesIn(out);
  const queue = await queueId(window.inputs, window.fetcher, window.names.queue);
  const record = readRecord(recordFile, run);
  const lines: string[] = [];
  let cleanCount = 0;
  for (const [n, staged] of bundles.entries()) {
    const prefix = `${run}/${staged.key}`;
    const done = await getText(window, window.names.staging_bucket, doneName(prefix));
    let found: Differences;
    if (done === null || (JSON.parse(done) as { bundle?: unknown }).bundle !== staged.bundle) {
      found = { missing: [], extra: [], different: ["import: not done (no marker)"] };
    } else {
      const message = askOf(staged, run, "check", window.inputs.importKey);
      const answer = await ask(window, queue, message);
      if (typeof answer["refused"] === "string" || answer["bundle"] !== staged.bundle) {
        found = { missing: [], extra: [], different: ["check: refused by the doors"] };
      } else {
        const [files, chunks, rows] = await Promise.all(["files", "chunks", "rows"].map(
          (part) => checkPages(window, prefix, message.ask!, part)));
        const answers: Answers = {
          entries: files!.flatMap((page) => page["entries"] as Answers["entries"]),
          chunks: chunks!.flatMap((page) => page["chunks"] as Answers["chunks"]),
          rows: rows!.reduce((sum: { count: number; sum: string }, page) => ({
            count: sum.count + (page["count"] as number),
            sum: rowsSum(sum.sum, page["sum"] as string) }), { count: 0, sum: NO_ROWS }),
          state: answer as unknown as Answers["state"],
        };
        const { entries, rows: bundleRows } = bundleContent(staged);
        found = differences(expectedOf(entries, bundleRows), answers, DEFAULT_TIER);
      }
    }
    const isClean = clean(found);
    cleanCount += isClean ? 1 : 0;
    record.verified[staged.key] = { bundle: staged.bundle, clean: isClean, differences: found };
    writeRecord(recordFile, record);
    const line = `bundle ${n + 1} of ${bundles.length}: ${found.missing.length} missing, ` +
      `${found.extra.length} extra, ${found.different.length} different`;
    lines.push(line);
    print(line);
  }
  return { bundles: bundles.length, clean: cleanCount, lines };
}

/** Every bundle verified clean in the record, by its digest: else why not, counted. */
function unverified(bundles: readonly Staged[], record: WindowRecord): number {
  return bundles.filter((staged) => record.verified[staged.key]?.bundle !== staged.bundle
    || record.verified[staged.key]?.clean !== true).length;
}

/**
 * `admit`: every owner verified clean and the dead-letter queue empty, or nothing is admitted;
 * then each owner admitted in turn, its answer read before the next.
 */
export async function admit(out: string, run: string, window: Window, recordFile: string,
                            print: (line: string) => void): Promise<StepSummary> {
  checkRun(window, run);
  const bundles = bundlesIn(out);
  const record = readRecord(recordFile, run);
  const failed = unverified(bundles, record);
  if (failed > 0) {
    throw new Error(`${failed} of ${bundles.length} owners did not verify clean: no owner is ` +
                    "admitted (going on without one is the founder's call)");
  }
  for (const staged of bundles) {
    const owner = JSON.parse(readFileSync(join(staged.dir, "owner.json"), "utf8")) as
      { admission?: { state?: unknown } };
    if (owner.admission?.state !== "admitted" && owner.admission?.state !== "suspended") {
      throw new Error("a bundle's owner is one the old stack never admitted: no owner is admitted");
    }
  }
  const dead = await deadLetters(window, out, run, false, () => undefined);
  if (dead.messages > 0) {
    throw new Error(`the dead-letter queue holds ${dead.messages} messages: no owner is ` +
                    "admitted until it is read and empty");
  }
  const queue = await queueId(window.inputs, window.fetcher, window.names.queue);
  const lines: string[] = [];
  for (const [n, staged] of bundles.entries()) {
    const answer = await ask(window, queue, askOf(staged, run, "admit", window.inputs.importKey));
    if (typeof answer["refused"] === "string" || answer["bundle"] !== staged.bundle) {
      throw new Error(`bundle ${n + 1}'s admit was refused by the doors: nothing more is admitted`);
    }
    const line = `bundle ${n + 1} of ${bundles.length}: admitted (${String(answer["state"])})`;
    lines.push(line);
    print(line);
  }
  return { bundles: bundles.length, clean: bundles.length, lines };
}

/** The directory's KV namespace id, by its title. */
async function directoryId(window: Window): Promise<string> {
  const found = (await api<{ id: string; title: string }[]>(
    window, "/storage/kv/namespaces?per_page=100"))
    .filter((each) => each.title === window.names.directory);
  if (found.length !== 1) {
    throw new Error(`the account lists no KV namespace ${window.names.directory}: nothing is done`);
  }
  return found[0]!.id;
}

/** Every directory key under `prefix`. */
async function directoryKeys(window: Window, namespace: string, prefix: string):
    Promise<string[]> {
  const keys: string[] = [];
  let cursor = "";
  do {
    const answer = await window.fetcher(new Request(`${API}/accounts/${window.inputs.account}` +
      `/storage/kv/namespaces/${namespace}/keys?prefix=${encodeURIComponent(prefix)}` +
      (cursor === "" ? "" : `&cursor=${encodeURIComponent(cursor)}`),
      { headers: { authorization: `Bearer ${window.inputs.apiToken}` } }));
    const body = await answer.json().catch(() => null) as {
      success?: boolean; result?: { name: string }[]; result_info?: { cursor?: string } } | null;
    if (!answer.ok || body?.success !== true) {
      throw new Error(`the directory's listing was refused (${answer.status})`);
    }
    keys.push(...(body.result ?? []).map((key) => key.name));
    cursor = body.result_info?.cursor ?? "";
  } while (cursor !== "");
  return keys;
}

/** What a wipe proved: each listing's count, every one 0 when the wipe holds. */
export interface WipeProof {
  readonly records: number;
  readonly staged: number;
  readonly namespaces: number;
  readonly objects: number;
}

/**
 * `wipe`: each owner discarded (its namespace kept in the record, so a run again proves it
 * too), then the rehearsal's `o:` records and the staging prefix deleted, then each listing
 * read and proved empty.
 */
export async function wipe(out: string, run: string, window: Window, recordFile: string,
                           print: (line: string) => void): Promise<WipeProof> {
  checkRun(window, run);
  const bundles = bundlesIn(out);
  const record = readRecord(recordFile, run);
  const queue = await queueId(window.inputs, window.fetcher, window.names.queue);
  for (const [n, staged] of bundles.entries()) {
    if (record.discarded[staged.key]?.bundle === staged.bundle) {
      continue;  // discarded on an earlier run: its staging may be gone already
    }
    const answer = await ask(window, queue, askOf(staged, run, "discard",
                                                  window.inputs.importKey));
    if (answer["discarded"] !== true || answer["bundle"] !== staged.bundle) {
      throw new Error(`bundle ${n + 1}'s discard was refused by the doors: nothing more is done`);
    }
    const namespace = answer["namespace"];
    record.discarded[staged.key] = { bundle: staged.bundle,
                                     namespace: typeof namespace === "string" ? namespace : null };
    writeRecord(recordFile, record);
    print(`bundle ${n + 1} of ${bundles.length}: discarded`);
  }
  const directory = await directoryId(window);
  const prefix = `o:rehearsal-${run}-`;
  // The run's own records: a run whose id extends this one's (`r1-x` of `r1`) shares the prefix.
  const runs = async () => (await directoryKeys(window, directory, prefix))
    .filter((key) => rehearsalRun(key.slice(2)) === run);
  const records = await runs();
  for (let at = 0; at < records.length; at += 1000) {
    await api(window, `/storage/kv/namespaces/${directory}/bulk/delete`,
              { method: "POST", body: JSON.stringify(records.slice(at, at + 1000)) });
  }
  const staging = window.names.staging_bucket;
  for (const key of await listKeys(window, staging, `${run}/`)) {
    await ok(window, s3Request(window.inputs, "DELETE", staging, key, {}, null,
                               window.clock.now()), "a staging delete");
  }
  const namespaces = [...new Set(Object.values(record.discarded)
    .flatMap((each) => each.namespace === null ? [] : [each.namespace]))];
  let objects = 0;
  for (const ns of namespaces) {
    objects += (await listKeys(window, window.names.files_bucket, namespacePrefix(ns), 1)).length;
  }
  const proof = { records: (await runs()).length,
                  staged: (await listKeys(window, staging, `${run}/`, 1)).length,
                  namespaces: namespaces.length, objects };
  print(`the directory lists ${proof.records} rehearsal records of the run, the staging bucket ` +
        `${proof.staged} objects of it, and the files bucket ${proof.objects} objects under ` +
        `the ${proof.namespaces} discarded namespaces`);
  return proof;
}

/** What the dead-letter queue held, by bundle ordinal and op. */
export interface DeadLetters {
  readonly messages: number;
  readonly by: Readonly<Record<string, number>>;
  readonly deleted: number;
}

/**
 * `dlq`: every message the dead-letter queue holds, pulled over its HTTP pull consumer (added
 * when it has none) and counted by bundle ordinal (`out`'s, by its key in the message's
 * prefix) and op; acknowledged, so deleted, only with `remove`, else left to come back.
 */
export async function deadLetters(window: Window, out: string | null, run: string | null,
                                  remove: boolean, print: (line: string) => void):
    Promise<DeadLetters> {
  const dead = window.names.dead_letter_queue;
  const queue = await queueId(window.inputs, window.fetcher, dead);
  const consumers = await api<{ type?: string }[]>(window, `/queues/${queue}/consumers`);
  if (consumers.some((consumer) => consumer.type !== "http_pull")) {
    throw new Error(`${dead} has a Worker consumer: it is read by none but this step`);
  }
  if (consumers.length === 0) {
    await api(window, `/queues/${queue}/consumers`, { method: "POST", body: JSON.stringify({
      type: "http_pull", settings: { batch_size: 100, max_retries: 100,
                                     visibility_timeout_ms: 60_000 } }) });
    print(`${dead} had no reader: its HTTP pull consumer is added`);
  }
  const ordinals = new Map((out === null ? [] : bundlesIn(out))
    .map((staged, n) => [staged.key, n + 1]));
  const seen = new Map<string, string>();
  const by: Record<string, number> = {};
  // Every unacknowledged message, the ones an earlier read still leases too (a pull skips them).
  let backlog = 0;
  for (;;) {
    const pulled = await api<{ message_backlog_count?: number;
                               messages?: { id?: string; body?: unknown; lease_id?: string }[] }>(
      window, `/queues/${queue}/messages/pull`,
      { method: "POST", body: JSON.stringify({ batch_size: 100, visibility_timeout_ms: 60_000 }) });
    backlog = Math.max(backlog, Number.isSafeInteger(pulled.message_backlog_count)
      ? pulled.message_backlog_count! : 0);
    const fresh = (pulled.messages ?? []).filter((message) => !seen.has(message.id ?? ""));
    if (fresh.length === 0) {
      break;
    }
    for (const message of fresh) {
      seen.set(message.id ?? "", message.lease_id ?? "");
      let label = "out of shape";
      try {
        const parsed = importMessage(pulledBody(message.body));
        const [at, key] = parsed.prefix.split("/");
        label = run !== null && at !== run ? `another run, ${parsed.op}`
          : `bundle ${ordinals.get(key!) ?? "?"}, ${parsed.op}`;
      } catch {
        // counted as out of shape
      }
      by[label] = (by[label] ?? 0) + 1;
    }
  }
  let deleted = 0;
  if (remove && seen.size > 0) {
    const leases = [...seen.values()].filter((lease) => lease !== "");
    for (let at = 0; at < leases.length; at += 100) {
      await api(window, `/queues/${queue}/messages/ack`, { method: "POST", body: JSON.stringify({
        acks: leases.slice(at, at + 100).map((lease_id) => ({ lease_id })), retries: [] }) });
    }
    deleted = leases.length;
  }
  for (const [label, count] of Object.entries(by).sort()) {
    print(`${label}: ${count} dead-lettered`);
  }
  const messages = Math.max(seen.size, backlog);
  print(`${dead}: ${messages} messages, ${deleted} deleted` +
        (remove ? "" : " (none is deleted without --delete)"));
  return { messages, by, deleted };
}

/**
 * A pulled message's body: the pull API hands a `json` message base64-encoded (Cloudflare's
 * pull consumers docs, "Content types"); plain JSON text is taken too.
 */
function pulledBody(body: unknown): unknown {
  if (typeof body !== "string") {
    return body;
  }
  try {
    return JSON.parse(body);
  } catch {
    return JSON.parse(Buffer.from(body, "base64").toString("utf8"));
  }
}
