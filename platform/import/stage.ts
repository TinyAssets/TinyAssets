/**
 * The import job's side of the queue (`import_research.md` §3, slice 4): each owner's bundle
 * (`bundle.ts`) uploaded to the staging bucket over R2's S3 API, then one message for each
 * owner, MAC'd under the window's `IMPORT_KEY` (`contract/src/import-message.ts`), put on the
 * import queue over the Queues HTTP API, for the doors' consumer (`doors/src/import.ts`). A
 * dry run reads and checks every bundle the same way and prints what it would send, sending
 * nothing and needing no credential. Deleted at the cutover with the rest of the job.
 *
 * - The bundles are the out folder's (`<out>/<key>/`), each checked whole before anything is
 *   sent: its index (`bundle.json`) names every part by its SHA-256, each part is there with
 *   its bytes, its key is its folder's, and nothing else is in it. A bundle that fails stops
 *   the run before any upload.
 * - Each goes to `<run>/<key>/` in the bucket, its index last, then its message; a run sent
 *   again puts the same bytes at the same names and the same message, which the consumer
 *   takes idempotently, so a job killed anywhere is run again whole.
 * - The credentials are the run's inputs (`Inputs`), as the operator hands them for the
 *   window (the R2 token's access key pair, the API token, the account, the key), never read
 *   from a file here; nothing prints them, nor any owner's name or key: the run prints counts
 *   and each bundle's ordinal alone (the job's output discipline, `cli.ts`).
 */
import { createHash, createHmac } from "node:crypto";
import { lstatSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { HEX16 } from "../contract/src/ids";
import {
  BUNDLE_FORMAT, BUNDLE_INDEX, bundlePrefix, importMac, type ImportMessage, PART, rehearsalRun,
  RUN, stagedName,
} from "../contract/src/import-message";
import { IMPORT_NAME_KEYS, type ImportNames } from "../contract/src/import-names";
import { QUEUES } from "../contract/src/queue-names";
import { Cloudflare } from "../deploy/cloudflare";

/**
 * The job's live names from `facts.json`'s text, which the window's bundle step writes beside
 * it (the job runs away from this tree and its kit): each of `IMPORT_NAME_KEYS`, a name, and
 * nothing more.
 */
export function namesOf(text: string): ImportNames {
  const given: unknown = JSON.parse(text);
  const keys = IMPORT_NAME_KEYS;
  if (!plain(given) || Object.keys(given).sort().join() !== [...keys].sort().join()
      || keys.some((key) => typeof given[key] !== "string" || given[key] === "")) {
    throw new Error("the facts file is not the import job's names (deploy/facts.ts --import)");
  }
  return given as ImportNames;
}

/** R2's S3 endpoint for an account. */
const r2Host = (account: string) => `${account}.r2.cloudflarestorage.com`;

/** The window's credentials, as the operator hands them to the run. */
export interface Inputs {
  /** The Cloudflare account, 32 hex (`cli.ts`'s `stageInputs` holds it to that). */
  readonly account: string;
  /**
   * An R2 API token's S3 access key pair, with write to the staging bucket (and, for the
   * window's wipe, `window.ts`, read of the files bucket).
   */
  readonly accessKeyId: string;
  readonly secretAccessKey: string;
  /**
   * An API token that may send to the account's queues (and, for the window's steps, read the
   * dead-letter queue and the directory's KV namespace, and delete its rehearsal records).
   */
  readonly apiToken: string;
  /** The window's `IMPORT_KEY`, the doors' Worker secret. */
  readonly importKey: string;
}

/** How the job reaches the network: `fetch` (`NETWORK`), or a test's fake. */
export type Fetcher = (request: Request) => Promise<Response>;
export const NETWORK: Fetcher = (request) => fetch(request);

/** One bundle, checked: its folder, key, owner, digest and parts, the index last. */
export interface Staged {
  readonly dir: string;
  readonly key: string;
  readonly owner: string;
  readonly bundle: string;
  readonly parts: readonly string[];
  readonly bytes: number;
}

/** What a run sent, or would send: counts alone. */
export interface StageSummary {
  readonly dry_run: boolean;
  readonly bundles: number;
  readonly objects: number;
  readonly bytes: number;
  readonly messages: number;
}

const sha256 = (data: string | Uint8Array) => createHash("sha256").update(data).digest("hex");
const plain = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** Every file under `dir`, as names relative to it; a link or anything but a file refused. */
function filesIn(dir: string, at = ""): string[] {
  return readdirSync(join(dir, at)).sort().flatMap((name) => {
    const rel = at === "" ? name : `${at}/${name}`;
    const stat = lstatSync(join(dir, rel));
    if (stat.isDirectory()) {
      return filesIn(dir, rel);
    }
    if (!stat.isFile()) {
      throw new Error("a bundle holds a link or a special file: refused");
    }
    return [rel];
  });
}

/** The bundle at `dir`, checked whole: refused loudly unless it is exactly what its index names. */
export function checkedBundle(dir: string, key: string): Staged {
  if (!HEX16.test(key)) {
    throw new Error("a bundle's folder is its owner's key (16 hex)");
  }
  const files = filesIn(dir);
  const index = readFileSync(join(dir, BUNDLE_INDEX));
  const parsed: unknown = JSON.parse(index.toString("utf8"));
  if (!plain(parsed) || parsed["format"] !== BUNDLE_FORMAT || parsed["key"] !== key
      || typeof parsed["owner"] !== "string" || !plain(parsed["parts"])) {
    throw new Error("a bundle's index is out of shape, or not its folder's");
  }
  const parts = parsed["parts"] as Record<string, unknown>;
  let bytes = index.byteLength;
  for (const [name, want] of Object.entries(parts)) {
    if (!PART.test(name) || typeof want !== "string" || name === BUNDLE_INDEX
        || sha256(readFileSync(join(dir, name))) !== want) {
      throw new Error("a bundle's part is missing, or not what its index names");
    }
  }
  for (const name of files) {
    if (!PART.test(name)) {
      throw new Error("a bundle holds a file no bundle has");
    }
    const content = readFileSync(join(dir, name));
    if (name.startsWith("chunks/") && sha256(content) !== name.slice("chunks/".length)) {
      throw new Error("a bundle's chunk is not its hash");
    }
    if (name !== BUNDLE_INDEX) {
      bytes += content.byteLength;
    }
  }
  return { dir, key, owner: parsed["owner"], bundle: sha256(index),
           parts: [...files.filter((name) => name !== BUNDLE_INDEX), BUNDLE_INDEX], bytes };
}

/** Every bundle in the out folder, each checked whole, before anything is sent. */
export function bundlesIn(out: string): Staged[] {
  return readdirSync(out).sort().map((key) => checkedBundle(join(out, key), key));
}

/** RFC 3986's encoding of one path segment, as S3's canonical URI takes it. */
function segment(text: string): string {
  return encodeURIComponent(text).replace(/[!'()*]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

const hmac = (key: string | Buffer, data: string) => createHmac("sha256", key).update(data).digest();

/**
 * AWS Signature Version 4 over a request's method, path, query, headers (each lowercased and
 * trimmed, every one signed) and payload hash: its `Authorization` header (R2's S3 API takes
 * it with region `auto`). `amzDate` is the request's `x-amz-date`.
 */
export function sigV4(request: { method: string; path: string; query: string;
                                 headers: Readonly<Record<string, string>>; payload: string;
                                 region: string; service: string; amzDate: string;
                                 accessKeyId: string; secretAccessKey: string }): string {
  const names = Object.keys(request.headers).map((name) => name.toLowerCase()).sort();
  const value = (name: string) => Object.entries(request.headers)
    .find(([each]) => each.toLowerCase() === name)![1].trim().replace(/\s+/g, " ");
  const signed = names.join(";");
  const canonical = [request.method, request.path, request.query,
                     ...names.map((name) => `${name}:${value(name)}`), "", signed,
                     request.payload].join("\n");
  const day = request.amzDate.slice(0, 8);
  const scope = `${day}/${request.region}/${request.service}/aws4_request`;
  const toSign = ["AWS4-HMAC-SHA256", request.amzDate, scope, sha256(canonical)].join("\n");
  let key = hmac(`AWS4${request.secretAccessKey}`, day);
  for (const part of [request.region, request.service, "aws4_request"]) {
    key = hmac(key, part);
  }
  return `AWS4-HMAC-SHA256 Credential=${request.accessKeyId}/${scope}, ` +
    `SignedHeaders=${signed}, Signature=${createHmac("sha256", key).update(toSign).digest("hex")}`;
}

/**
 * A request to a bucket over R2's S3 API, signed (SigV4) at `now`: `method` on the object
 * `name` (or the bucket itself, `name` empty), with its query (each name and value encoded as
 * S3's canonical query takes them, sorted) and its body, if any.
 */
export function s3Request(inputs: Inputs, method: "GET" | "PUT" | "DELETE", bucket: string,
                          name: string, query: Readonly<Record<string, string>>,
                          body: Uint8Array | null, now: Date): Request {
  const host = r2Host(inputs.account);
  const path = `/${bucket}${name === "" ? "" : `/${name.split("/").map(segment).join("/")}`}`;
  const search = Object.keys(query).sort()
    .map((key) => `${segment(key)}=${segment(query[key]!)}`).join("&");
  const amzDate = now.toISOString().replace(/[-:]/g, "").replace(/\.[0-9]{3}/, "");
  const payload = sha256(body ?? new Uint8Array(0));
  const headers = { host, "x-amz-content-sha256": payload, "x-amz-date": amzDate };
  const authorization = sigV4({ method, path, query: search, headers, payload,
                                region: "auto", service: "s3", amzDate,
                                accessKeyId: inputs.accessKeyId,
                                secretAccessKey: inputs.secretAccessKey });
  const { host: _host, ...sent } = headers;
  return new Request(`https://${host}${path}${search === "" ? "" : `?${search}`}`, {
    method, ...(body === null ? {} : { body }),
    headers: { ...sent, authorization,
               ...(body === null ? {} : { "content-type": "application/octet-stream" }) } });
}

/** The kit's Cloudflare client with the window's API token, over `fetcher`. */
export function cloudflareOf(inputs: Inputs, fetcher: Fetcher): Cloudflare {
  return new Cloudflare(inputs.apiToken, (url, init) => fetcher(new Request(url, init)));
}

/** A queue's id in the account, by its name, from the Queues API's listing. */
export async function queueId(inputs: Inputs, fetcher: Fetcher, name: string): Promise<string> {
  const id = (await cloudflareOf(inputs, fetcher).queues(inputs.account)).get(name);
  if (id === undefined) {
    throw new Error(`the account lists no queue ${name}: nothing is sent; run the deploy ` +
                    "kit's dark mode first");
  }
  return id;
}

/** A run's id, or a refusal. */
export function checkRun(run: string): void {
  if (!RUN.test(run)) {
    throw new Error("a run's id is lowercase letters, digits and dashes, at most 64");
  }
}

/** A bundle's message, MAC'd under the window's key. */
export function messageOf(staged: Staged, run: string, importKey: string): ImportMessage {
  return { op: "import", owner: staged.owner, bundle: staged.bundle,
           prefix: bundlePrefix(run, staged.key),
           mac: importMac(importKey, "import", staged.owner, staged.bundle) };
}

/** What a run would send, a line a bundle by its ordinal, then the totals: no name, no key. */
export function plannedLines(bundles: readonly Staged[], run: string, names: ImportNames):
    string[] {
  return [
    ...bundles.map((staged, n) => `bundle ${n + 1} of ${bundles.length}: ${staged.parts.length} ` +
      `objects, ${staged.bytes} bytes to ${names.staging_bucket}/${run}/<key>/, its index last; ` +
      `then 1 message to ${QUEUES.IMPORTS}`),
    `in all: ${bundles.reduce((sum, staged) => sum + staged.parts.length, 0)} objects, ` +
    `${bundles.reduce((sum, staged) => sum + staged.bytes, 0)} bytes, ${bundles.length} messages`,
  ];
}

/**
 * The run: every bundle in `out` checked whole, then (unless `inputs` is null, a dry run)
 * each uploaded under `<run>/<key>/`, its index last, then its message sent; refused loudly at
 * the first answer that is not a success, with nothing more sent.
 */
export async function stage(out: string, run: string, names: ImportNames, inputs: Inputs | null,
                            fetcher: Fetcher, print: (line: string) => void,
                            now: () => Date = () => new Date(), rehearsal = false):
    Promise<StageSummary> {
  checkRun(run);
  const bundles = bundlesIn(out);
  // A rehearsal imports under this run's rehearsal names alone, and the window under none.
  if (bundles.some((staged) => rehearsalRun(staged.owner) !== (rehearsal ? run : null))) {
    throw new Error(rehearsal ? "a rehearsal's bundle names a real owner, or another run's: " +
                      "nothing is sent" : "a bundle names a rehearsal's owner: nothing is sent");
  }
  const totals = { bundles: bundles.length, objects: 0, bytes: 0, messages: 0 };
  for (const staged of bundles) {
    totals.objects += staged.parts.length;
    totals.bytes += staged.bytes;
    totals.messages += 1;
  }
  if (inputs === null) {
    plannedLines(bundles, run, names).forEach((line) => print(line));
    return { dry_run: true, ...totals };
  }
  const queue = await queueId(inputs, fetcher, QUEUES.IMPORTS);
  for (const [n, staged] of bundles.entries()) {
    for (const name of staged.parts) {
      const answer = await fetcher(s3Request(inputs, "PUT", names.staging_bucket,
                                             stagedName(bundlePrefix(run, staged.key), name), {},
                                             readFileSync(join(staged.dir, name)), now()));
      if (!answer.ok) {
        throw new Error(`bundle ${n + 1}'s upload was refused (${answer.status}): ` +
                        "nothing more is sent");
      }
    }
    await cloudflareOf(inputs, fetcher).send(inputs.account, queue,
                                             messageOf(staged, run, inputs.importKey));
    print(`bundle ${n + 1} of ${bundles.length}: ${staged.parts.length} objects uploaded, ` +
          "its message sent");
  }
  return { dry_run: false, ...totals };
}
