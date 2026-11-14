/**
 * The window's steps after staging (`window.ts`, slice 5) on the slice-2 builder's own bundles,
 * against a fake world: R2's S3 API (the staging and files buckets), the Queues HTTP API (the
 * import queue, whose fake doors answer each message as `doors/src/import.ts` does, and the
 * dead-letter queue's pull consumer) and the KV API (the directory). `verify` holds each check
 * to its bundle and keeps the result; `admit` admits nothing until every owner verified clean
 * and the dead-letter queue is empty, then each in turn; `wipe` discards, deletes and proves
 * every listing empty; `dlq` reports by ordinal and deletes only with its flag; and each,
 * killed at any of its steps, converges when run again. The doors' side of each is the doors'
 * own test (`doors/test/import.test.ts`), through the real object. The live names are the
 * deploy kit's, read from the configs (`deploy/facts.ts`), as the window's `facts.json` hands
 * them to the job.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { TIERS } from "../../ci/fixtures";
import { chunkPlan } from "../../contract/src/content";
import {
  answerName, bodyId, type CheckEntry, doneName, importMessage, type ImportMessage, macHolds,
  NO_ROWS, rehearsalName, rehearsalRun, rowLeaf, rowsSum,
} from "../../contract/src/import-message";
import { DEFAULT_POLICY_DIGEST, policyDigest } from "../../contract/src/policy-document";
import { PLACE_BYTES } from "../../contract/src/limits";
import { cut } from "../../contract/src/content";
import { IMPORT_NAMES } from "../../deploy/facts";
import { buildBundles } from "../bundle";
import type { Entry, Row } from "../build";
import { windowMain } from "../cli";
import { bundlesIn, type Fetcher, type Inputs, type Staged } from "../stage";
import {
  admit, type Clock, deadLetters, namespacePrefix, readRecord, verify, type Window, wipe,
} from "../window";
import { FOUNDER, fixture } from "./fixture";

const temps: string[] = [];
afterEach(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

const INPUTS: Inputs = Object.freeze({
  account: "a".repeat(32), accessKeyId: "AKIDEXAMPLE0000", secretAccessKey: "s3-secret-not-real",
  apiToken: "api-token-not-real", importKey: Buffer.alloc(32, 7).toString("base64url") });
const RUN = "r1";
const NAMES = IMPORT_NAMES;
const CLOCK: Clock = { now: () => new Date(Date.UTC(2026, 10, 8)), sleep: async () => undefined,
                       waitMs: 0, everyMs: 0 };

/**
 * The builder's bundles of the fixture, a rehearsal's, in a fresh out folder; a record; and the
 * window's `facts.json`, as its bundle step writes it (`facts.ts --import`).
 */
function bundled(): { out: string; record: string; facts: string; bundles: Staged[] } {
  const f = fixture();
  const out = mkdtempSync(join(tmpdir(), "ta-window-out-"));
  const priv = mkdtempSync(join(tmpdir(), "ta-window-private-"));
  const work = mkdtempSync(join(tmpdir(), "ta-window-work-"));
  temps.push(f.work, out, priv, work);
  buildBundles(f.copy, { out, private: priv, exclude: new Set([FOUNDER]), tiers: TIERS,
                         rehearsal: RUN });
  writeFileSync(join(work, "facts.json"), JSON.stringify(IMPORT_NAMES));
  return { out, record: join(work, "record.json"), facts: join(work, "facts.json"),
           bundles: bundlesIn(out) };
}

const read = <T>(staged: Staged, kind: "manifest" | "rows", key: string): T[] =>
  staged.parts.filter((name) => name.startsWith(`${kind}/`)).sort()
    .flatMap((name) => JSON.parse(readFileSync(join(staged.dir, name), "utf8"))[key] as T[]);

/** What a faithful import leaves in an owner's object, as its check reads it back. */
function heldOf(staged: Staged) {
  const entries = read<Entry>(staged, "manifest", "entries").map((entry): CheckEntry =>
    entry.kind === "dir" ? { path: entry.path, kind: "dir", size: 0, mode: 0, content_id: null,
                             mtime_ms: 7 }
      : { path: entry.path, kind: "file", size: entry.size!, mode: entry.mode!,
          content_id: entry.content_id!, mtime_ms: entry.mtime_ms! });
  const chunks = new Map<string, number>();
  for (const entry of read<Entry>(staged, "manifest", "entries")) {
    entry.chunks?.forEach((sha, n) => chunks.set(sha, chunkPlan(entry.size!)[n]!));
  }
  let policy = DEFAULT_POLICY_DIGEST;
  let count = 0;
  let sum = NO_ROWS;
  for (const row of read<Row>(staged, "rows", "rows")) {
    if (row.kind === "policy") {
      policy = policyDigest(row.document);
      continue;
    }
    count += 1;
    sum = rowsSum(sum, rowLeaf(row.collection, row.row_id, row.private, bodyId(row.body)));
    const bytes = new TextEncoder().encode(row.body);
    if (bytes.byteLength > PLACE_BYTES) {
      cut(bytes).forEach((piece) => chunks.set(piece.sha256, piece.size));
    }
  }
  return { entries, chunks: [...chunks].map(([sha256, size]) => ({ sha256, size, read: sha256 })),
           rows: { count, sum },
           state: { policy_digest: policy, tier: "free", state: "pending", reasons: [],
                    used_bytes: 1, limit_bytes: 100 } };
}

/** The fake world: two buckets, the queues and their fake doors, and the directory. */
function world(bundles: readonly Staged[]) {
  const buckets = new Map<string, Map<string, string>>([[NAMES.staging_bucket, new Map()],
                                                        [NAMES.files_bucket, new Map()]]);
  const staging = buckets.get(NAMES.staging_bucket)!;
  const kv = new Map<string, string>();
  const sent: ImportMessage[] = [];
  const dead: { id: string; body: string; leased: boolean }[] = [];
  const consumers: { type: string }[] = [];
  const held = new Map(bundles.map((staged) => [staged.owner, heldOf(staged)]));
  const admitted = new Set<string>();
  /** A refusal of every message of an op, as the doors would answer it, or a kill. */
  const faults: { kill?: { op: string; after: number }; refuse?: string } = {};
  const namespaces = new Map(bundles.map((staged, n) => [staged.owner, `${String(n).repeat(32)}`]));
  for (const staged of bundles) {
    staging.set(doneName(`${RUN}/${staged.key}`), JSON.stringify({ op: "import",
                                                                   bundle: staged.bundle,
                                                                   done: true }));
    buckets.get(NAMES.files_bucket)!
      .set(`${namespacePrefix(namespaces.get(staged.owner)!)}c/x`, "x");
  }
  const doors = (message: ImportMessage) => {
    expect(macHolds(INPUTS.importKey, message)).toBe(true);
    const at = (page?: string) => answerName(message.prefix, message.ask!, page);
    const put = (name: string, value: unknown) => staging.set(name, JSON.stringify(value));
    if (faults.refuse === message.op) {
      put(at(), { op: message.op, bundle: message.bundle, refused: "refused" });
      return;
    }
    if (message.op === "check") {
      const state = held.get(message.owner)!;
      put(at("files-0"), { part: "files", entries: state.entries, more: false });
      put(at("chunks-0"), { part: "chunks", chunks: state.chunks, more: false });
      put(at("rows-0"), { part: "rows", ...state.rows, more: false });
      put(at(), { op: "check", bundle: message.bundle, part: "state", more: false,
                  ...state.state });
    } else if (message.op === "admit") {
      admitted.add(message.owner);
      kv.set(`o:${message.owner}`, JSON.stringify({ s: "admitted", t: 1 }));
      put(at(), { op: "admit", bundle: message.bundle, tier: "free", state: "admitted",
                  reasons: [] });
    } else if (message.op === "discard") {
      const ns = namespaces.get(message.owner)!;
      for (const key of [...buckets.get(NAMES.files_bucket)!.keys()]) {
        if (key.startsWith(namespacePrefix(ns))) {
          buckets.get(NAMES.files_bucket)!.delete(key);
        }
      }
      put(at(), { op: "discard", bundle: message.bundle, discarded: true, namespace: ns });
    }
  };
  const ok = (result: unknown) => Response.json({ success: true, result });
  const fetcher: Fetcher = async (request) => {
    const url = new URL(request.url);
    const body = request.method === "GET" ? "" : await request.text();
    if (url.hostname === `${INPUTS.account}.r2.cloudflarestorage.com`) {
      const [, bucket, ...rest] = url.pathname.split("/");
      const store = buckets.get(bucket!)!;
      const key = rest.map(decodeURIComponent).join("/");
      if (request.method === "GET" && key === "") {
        const prefix = url.searchParams.get("prefix") ?? "";
        const most = Number(url.searchParams.get("max-keys") ?? "1000");
        const keys = [...store.keys()].filter((each) => each.startsWith(prefix)).sort()
          .slice(0, most);
        return new Response(`<ListBucketResult>${keys.map((each) => `<Key>${each}</Key>`)
          .join("")}<IsTruncated>false</IsTruncated></ListBucketResult>`);
      }
      if (request.method === "DELETE") {
        store.delete(key);
        return new Response(null, { status: 204 });
      }
      const found = store.get(key);
      return found === undefined ? new Response("no", { status: 404 }) : new Response(found);
    }
    const path = url.pathname.replace(`/client/v4/accounts/${INPUTS.account}`, "");
    if (path === "/queues") {
      return ok([{ queue_id: "q-import", queue_name: NAMES.queue },
                 { queue_id: "q-dead", queue_name: NAMES.dead_letter_queue }]);
    }
    if (path === "/queues/q-import/messages") {
      const message = importMessage(JSON.parse(body).body);
      sent.push(message);
      if (faults.kill?.op === message.op && faults.kill.after-- === 0) {
        throw new Error("the step is killed");
      }
      doors(message);
      return ok(null);
    }
    if (path === "/queues/q-dead/consumers") {
      if (request.method === "POST") {
        consumers.push(JSON.parse(body));
      }
      return ok(request.method === "POST" ? null : consumers);
    }
    if (path === "/queues/q-dead/messages/pull") {
      const free = dead.filter((each) => !each.leased).slice(0, 100);
      free.forEach((each) => { each.leased = true; });
      // The API's backlog counts every unacknowledged message, leased ones too.
      return ok({ message_backlog_count: dead.length,
                  messages: free.map((each) => ({ id: each.id, body: each.body,
                                                  lease_id: `lease-${each.id}` })) });
    }
    if (path === "/queues/q-dead/messages/ack") {
      const acked = new Set((JSON.parse(body).acks as { lease_id: string }[])
        .map((ack) => ack.lease_id.slice("lease-".length)));
      dead.splice(0, dead.length, ...dead.filter((each) => !acked.has(each.id)));
      return ok(null);
    }
    if (path === "/storage/kv/namespaces") {
      return ok([{ id: "kv-directory", title: NAMES.directory }]);
    }
    if (path === "/storage/kv/namespaces/kv-directory/keys") {
      const prefix = url.searchParams.get("prefix") ?? "";
      return Response.json({ success: true, result: [...kv.keys()]
        .filter((key) => key.startsWith(prefix)).map((name) => ({ name })),
                             result_info: { cursor: "" } });
    }
    if (path === "/storage/kv/namespaces/kv-directory/bulk/delete") {
      for (const key of JSON.parse(body) as string[]) {
        kv.delete(key);
      }
      return ok(null);
    }
    return new Response(`no route ${request.method} ${path}`, { status: 404 });
  };
  const window: Window = { names: NAMES, inputs: INPUTS, fetcher, clock: CLOCK };
  return { window, buckets, staging, kv, sent, dead, consumers, held, admitted, faults,
           namespaces };
}

const quiet = () => undefined;

describe("the window's rehearsal names", () => {
  it("names every bundle of a rehearsal's run for it, and none of a real issuer's subject", () => {
    const { bundles } = bundled();
    expect(bundles.length).toBeGreaterThan(0);
    expect(bundles.every((staged) => rehearsalRun(staged.owner) === RUN)).toBe(true);
    expect(rehearsalName(RUN, "user_alice")).toMatch(/^rehearsal-r1-[0-9a-f]{24}$/);
    // No AuthKit subject, no foreign issuer's owner, and no other name can be one.
    for (const subject of ["user_01HZX", "user_rehearsal-r1-0123456789abcdef01234567",
                           "iss-0123456789abcdef0123456789abcdef-user_01H",
                           "iss-rehearsal-r1-0123456789abcdef01234567",
                           "rehearsal-r1-0123456789abcdef0123456", "Rehearsal-r1-0123456789abcdef01234567",
                           "rehearsal-r1-0123456789abcdef01234567 "]) {
      expect([subject, rehearsalRun(subject)]).toEqual([subject, null]);
    }
    for (let n = 0; n < 200; n += 1) {
      const name = rehearsalName(`run-${n}`, `user_${n}`);
      expect(name.startsWith("user_") || name.startsWith("iss-")).toBe(false);
      expect(rehearsalRun(name)).toBe(`run-${n}`);
    }
  });
});

describe("the window's verify", () => {
  it("holds each owner's check to its bundle, all clean, and keeps the result", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    const lines: string[] = [];
    expect(await verify(out, RUN, w.window, record, (line) => lines.push(line)))
      .toMatchObject({ bundles: bundles.length, clean: bundles.length });
    expect(lines).toEqual(bundles.map((_b, n) => `bundle ${n + 1} of ${bundles.length}: ` +
                                                 "0 missing, 0 extra, 0 different"));
    expect(Object.values(readRecord(record, RUN).verified).every((each) => each.clean)).toBe(true);
    // Nothing printed names an owner, a key or a namespace.
    for (const staged of bundles) {
      expect(lines.join("\n")).not.toContain(staged.owner);
      expect(lines.join("\n")).not.toContain(staged.key);
    }
  });

  it("finds an owner whose import is not done, or whose check differs, and keeps it", async () => {
    const { out, record, facts, bundles } = bundled();
    const w = world(bundles);
    w.staging.delete(doneName(`${RUN}/${bundles[0]!.key}`));
    const second = w.held.get(bundles[1]!.owner)!;
    second.entries.pop();
    second.chunks.forEach((chunk) => { (chunk as { read: string | null }).read = null; });
    const summary = await verify(out, RUN, w.window, record, quiet);
    expect(summary.clean).toBe(bundles.length - 2);
    const kept = readRecord(record, RUN).verified;
    expect(kept[bundles[0]!.key]!.differences.different).toEqual(["import: not done (no marker)"]);
    expect(kept[bundles[1]!.key]!.differences.missing.length).toBe(1);
    expect(kept[bundles[1]!.key]!.clean).toBe(false);
    // Its exit says so.
    expect(await windowMain(["verify", out, "--run", RUN, "--facts", facts, "--record", record],
                            envOf(), quiet, w.window.fetcher, CLOCK)).toBe(3);
  });

  it("finds an owner whose policy, admission or chunks differ from its bundle", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    w.held.get(bundles[0]!.owner)!.state.policy_digest = `sha256:${"0".repeat(64)}`;
    w.held.get(bundles[1]!.owner)!.state.state = "admitted";
    w.held.get(bundles[2]!.owner)!.chunks.push({ sha256: "e".repeat(64), size: 1,
                                                 read: "e".repeat(64) });
    expect((await verify(out, RUN, w.window, record, quiet)).clean).toBe(0);
    const kept = readRecord(record, RUN).verified;
    expect(kept[bundles[0]!.key]!.differences.different).toEqual(["policy: its digest"]);
    expect(kept[bundles[1]!.key]!.differences.different).toEqual(["admission: not pending"]);
    expect(kept[bundles[2]!.key]!.differences.extra).toEqual([`chunk ${"e".repeat(64)}`]);
  });

  it("holds each check's answer to its own bundle", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    // Every answer read back names another bundle: none is taken for the owner's.
    const fetcher: Fetcher = async (request) => {
      const answer = await w.window.fetcher(request);
      if (request.method !== "GET" || !answer.ok
          || !/\/answers\/[0-9a-f]{16}\.json$/.test(new URL(request.url).pathname)) {
        return answer;
      }
      return Response.json({ ...await answer.json() as object, bundle: "f".repeat(64) });
    };
    expect((await verify(out, RUN, { ...w.window, fetcher }, record, quiet)).clean).toBe(0);
    expect(Object.values(readRecord(record, RUN).verified)
      .map((each) => each.differences.different))
      .toEqual(bundles.map(() => ["check: refused by the doors"]));
  });
});

/** The credentials by their names (`STAGE_INPUTS`), as the job reads them from its stdin. */
function envOf(): Record<string, string> {
  return { CLOUDFLARE_ACCOUNT_ID: INPUTS.account, R2_ACCESS_KEY_ID: INPUTS.accessKeyId,
           R2_SECRET_ACCESS_KEY: INPUTS.secretAccessKey, CLOUDFLARE_API_TOKEN: INPUTS.apiToken,
           IMPORT_KEY: INPUTS.importKey };
}

describe("the window's admit", () => {
  it("admits nothing before a check passes, nor with one owner's check failing", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await expect(admit(out, RUN, w.window, record, quiet)).rejects.toThrow(/did not verify clean/);
    w.held.get(bundles[0]!.owner)!.rows.count += 1;
    await verify(out, RUN, w.window, record, quiet);
    await expect(admit(out, RUN, w.window, record, quiet))
      .rejects.toThrow(/1 of \d+ owners did not verify clean: no owner is admitted/);
    expect(w.sent.filter((message) => message.op === "admit")).toEqual([]);
    expect(w.admitted.size).toBe(0);
  });

  it("admits nothing while the dead-letter queue holds a message", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await verify(out, RUN, w.window, record, quiet);
    w.dead.push({ id: "d1", body: "{}", leased: false });
    await expect(admit(out, RUN, w.window, record, quiet)).rejects.toThrow(/dead-letter queue/);
    expect(w.admitted.size).toBe(0);
    expect(w.dead.length).toBe(1);
  });

  it("admits nothing while a read of the dead-letter queue still leases its message", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await verify(out, RUN, w.window, record, quiet);
    w.dead.push({ id: "d1", body: "{}", leased: false });
    expect((await deadLetters(w.window, out, RUN, false, quiet)).messages).toBe(1);
    // The read's lease hides the message from the next pull, not from the backlog.
    await expect(admit(out, RUN, w.window, record, quiet)).rejects.toThrow(/dead-letter queue/);
    expect(w.admitted.size).toBe(0);
  });

  it("admits each owner in turn once all verified, and converges when killed between them",
     async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await verify(out, RUN, w.window, record, quiet);
    w.faults.kill = { op: "admit", after: 1 };
    await expect(admit(out, RUN, w.window, record, quiet)).rejects.toThrow(/killed/);
    expect(w.admitted.size).toBe(1);
    const lines: string[] = [];
    await admit(out, RUN, w.window, record, (line) => lines.push(line));
    expect(w.admitted.size).toBe(bundles.length);
    expect(lines.length).toBe(bundles.length);
  });

  it("stops at an admit the doors refuse", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await verify(out, RUN, w.window, record, quiet);
    w.faults.refuse = "admit";
    await expect(admit(out, RUN, w.window, record, quiet)).rejects.toThrow(/refused by the doors/);
  });
});

describe("the window's wipe", () => {
  it("discards every owner, deletes the rehearsal's records and staging, and proves each " +
     "listing empty", async () => {
    const { out, record, bundles } = bundled();
    const w = world(bundles);
    await verify(out, RUN, w.window, record, quiet);
    await admit(out, RUN, w.window, record, quiet);
    w.kv.set("o:user_real", JSON.stringify({ s: "admitted", t: 1 }));
    w.kv.set(`o:${rehearsalName("r2", "user_x")}`, JSON.stringify({ s: "admitted", t: 1 }));
    // A run whose id extends this one's shares its listing prefix, `o:rehearsal-r1-`.
    w.kv.set(`o:${rehearsalName("r1-x", "user_y")}`, JSON.stringify({ s: "admitted", t: 1 }));
    expect([...w.kv.keys()].filter((key) => rehearsalRun(key.slice(2)) === RUN).length)
      .toBe(bundles.length);
    const proof = await wipe(out, RUN, w.window, record, quiet);
    expect(proof).toEqual({ records: 0, staged: 0, namespaces: bundles.length, objects: 0 });
    expect([...w.staging.keys()]).toEqual([]);
    expect([...w.buckets.get(NAMES.files_bucket)!.keys()]).toEqual([]);
    // Another run's rehearsal records and every real owner's are left as they were.
    expect([...w.kv.keys()].sort()).toEqual([`o:${rehearsalName("r1-x", "user_y")}`,
                                             `o:${rehearsalName("r2", "user_x")}`, "o:user_real"]);
    expect(w.sent.filter((message) => message.op === "discard").length).toBe(bundles.length);
  });

  it("converges when killed between its discards, and proves a namespace an earlier run " +
     "discarded", async () => {
    const { out, record, facts, bundles } = bundled();
    const w = world(bundles);
    w.faults.kill = { op: "discard", after: 1 };
    await expect(wipe(out, RUN, w.window, record, quiet)).rejects.toThrow(/killed/);
    expect(Object.keys(readRecord(record, RUN).discarded).length).toBe(1);
    // A put that landed late under a discarded namespace: the proof finds it.
    const first = w.namespaces.get(bundles[0]!.owner)!;
    w.buckets.get(NAMES.files_bucket)!.set(`${namespacePrefix(first)}c/late`, "late");
    const proof = await wipe(out, RUN, w.window, record, quiet);
    expect(proof).toMatchObject({ records: 0, staged: 0, namespaces: bundles.length, objects: 1 });
    expect(await windowMain(["wipe", out, "--run", RUN, "--facts", facts, "--record", record],
                            envOf(), quiet, w.window.fetcher, CLOCK)).toBe(3);
    // Each owner discarded once: the record kept the first run's.
    expect(w.sent.filter((message) => message.op === "discard").length).toBe(bundles.length + 1);
  });
});

describe("the window's dead-letter reader", () => {
  it("reports by bundle ordinal and op, and deletes nothing without its flag", async () => {
    const { out, bundles } = bundled();
    const w = world(bundles);
    const message = { op: "import", owner: bundles[0]!.owner, bundle: bundles[0]!.bundle,
                      prefix: `${RUN}/${bundles[0]!.key}`, mac: "A".repeat(43) };
    // A `json` message comes back from the pull API base64-encoded (its docs' "Content types").
    w.dead.push({ id: "d1", body: JSON.stringify(message), leased: false },
                { id: "d2", body: "not json", leased: false },
                { id: "d3", body: Buffer.from(JSON.stringify(message)).toString("base64"),
                  leased: false });
    const lines: string[] = [];
    const read = await deadLetters(w.window, out, RUN, false, (line) => lines.push(line));
    expect(read).toEqual({ messages: 3, by: { "bundle 1, import": 2, "out of shape": 1 },
                           deleted: 0 });
    expect(w.consumers).toEqual([{ type: "http_pull", settings: expect.any(Object) }]);
    expect(w.dead.length).toBe(3);
    expect(lines.join("\n")).not.toContain(bundles[0]!.owner);
    w.dead.forEach((each) => { each.leased = false; });
    expect((await deadLetters(w.window, out, RUN, true, quiet)).deleted).toBe(3);
    expect(w.dead).toEqual([]);
    expect(w.consumers.length).toBe(1);
  });
});
