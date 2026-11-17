/**
 * The off-account copy (d3 §G; `operator_research.md` §4): every night the files bucket is
 * copied to a Backblaze B2 bucket outside the Cloudflare account, by `offsite-copy.yml`.
 *
 * - **What it holds:** the files bucket alone, byte for byte at the same keys. That bucket is
 *   every owner's chunks and nightly generations (`files/src/keys.ts`), each object sealed
 *   under its owner's data key with its own R2 key as the seal's additional data, and each
 *   generation's root carrying that data key wrapped under `FILES_KEYRING`. So the copy is
 *   ciphertext and wrapped keys: no keyring, no control record and no directory leave the
 *   account, and a restore is a byte copy back to the same keys (`--b2-version-at` for an
 *   older generation), which only those keys open.
 * - **Its keys:** the write key adds and hides and can't delete or read; the read key reads,
 *   for the check and a restore. Each night both are held to exactly their capabilities, and
 *   the bucket to its lock and its one lifecycle rule, before rclone runs (`held`).
 * - **The erasure:** an erased owner's objects leave R2 at the wipe; the first night that
 *   passes hides them here, and the lifecycle deletes each hidden version `KEEP_DAYS` later,
 *   which the lock (as long, from the upload) never outlasts. `HIDE_NIGHTS` for the hide,
 *   `KEEP_DAYS` and the lifecycle's daily pass stay inside the erasure's window
 *   (`RECOVERY_WINDOW_S`), so its `complete_at` holds for the copy too while a night passes
 *   at least once in every `HIDE_NIGHTS`. Only a failure delays a hide: a mass deletion is
 *   hidden whole, and fails the night after.
 * - **Dark until its settings are stored:** with none of `SETTINGS`, `settings` answers null
 *   and the copy is not run; with some, or with all and any error, it stops loudly.
 * - **Watched:** the platform's canary reads how long ago a night last passed (`watch`) and
 *   alarms past two nights, so no hide is late unseen.
 * - **Read back (R8, `docs/decisions/untangle-2026-11/r8-copy-restore.md`):** the kit's mode
 *   `copyback` writes into R2 the objects of one owner's namespace that R2 lacks, as the copy
 *   held them at a time inside its window (`copyBack`), the namespace the one the owner's own
 *   object names, so the doors' erasure records refuse an erased owner before anything is read.
 *   The bucket's own erasure record is read first, and survives the account: the mark the files
 *   Worker makes for each namespace it deletes on purpose (`e/<ns>`, `erasures`), whose objects
 *   no copy back writes. The restore record goes first (`recordRestore`). Once a month a night
 *   is a drill (`drill`): it reads back what R2 held, whole, and what a copy back of the whole
 *   bucket at the window's far edge writes, every marked namespace left out.
 * - **After an account's loss (`recover`, `r8-locate.md`):** mode `recover` writes the marks
 *   back first, then every unmarked namespace with a root, locates each one's owner from its
 *   generation (`restore.locate`) and begins its restore, in one run; a namespace another
 *   holds (`o/<ns>/h/<held>`) goes with its holder, and one whose owner is live is held by the
 *   owner's own (`restore.hold`), so no erasure misses it (`r8-erased-orphans.md`). Until it has, a night
 *   refuses to copy a bucket that lacks a mark the copy holds (`copy`): nothing deletes a mark,
 *   so such a bucket lost them, and its sync would hide every one.
 *
 * `node --experimental-strip-types deploy/offsite.ts settings` writes `configured` for the
 * workflow; `… copy` runs the copy, and `… drill` the copy and then the drill. Node runs this
 * file as it is (type stripping), as the kit does.
 */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { randomInt } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { keyed, whole } from "../contract/src/checks.ts";
import {
  heldNamespace, HOLDS, type LocatedOwner, MARKS, markedNamespace, namespaceOf, objectPrefix,
} from "../contract/src/files.ts";
import { HEX32 } from "../contract/src/ids.ts";
import { RECOVERY_WINDOW_S } from "../contract/src/limits.ts";
import { annotate, github, output, pages, type Reach, summarize } from "./actions.ts";
import type { Cloudflare } from "./cloudflare.ts";
import type { Io } from "./deploy.ts";

/** What the copy acts on: the kit's own outside world. */
export type Effects = Pick<Io, "fetch" | "spawn" | "now">;

/**
 * The copy's credentials, each by the `production` secrets its two halves are stored under:
 * the S3 pair of a Cloudflare token that reads the files bucket alone, and the B2 write and
 * read keys.
 */
const KEYS = Object.freeze({
  r2: Object.freeze(["OFFSITE_R2_ACCESS_KEY_ID", "OFFSITE_R2_SECRET_ACCESS_KEY"] as const),
  write: Object.freeze(["OFFSITE_B2_KEY_ID", "OFFSITE_B2_APPLICATION_KEY"] as const),
  read: Object.freeze(["OFFSITE_B2_READ_KEY_ID", "OFFSITE_B2_READ_KEY"] as const),
});

/** The copy's settings, all of them or none: the bucket's name (a variable) and the keys. */
export const SETTINGS: readonly string[] = Object.freeze(
  ["OFFSITE_BUCKET", ...Object.values(KEYS).flat()]);

/** One credential: the names it is stored under, and its two halves. */
interface Key {
  readonly names: string;
  readonly id: string;
  readonly secret: string;
}

export type Settings = Readonly<Record<keyof typeof KEYS, Key>> & {
  readonly bucket: string;
  /** The Cloudflare account the files bucket is in: no secret, a repository variable. */
  readonly account: string;
};

/** A B2 bucket's name, as Backblaze takes one. */
const BUCKET = /^[A-Za-z0-9-]{6,63}$/;

type Env = Readonly<Record<string, string | undefined>>;

/**
 * `names` from `env`, each set and none holding a space or a control character, the bucket's
 * name a B2 one, else a loud stop naming them; `missing` says what an unset one is.
 */
function stored(env: Env, names: readonly string[], missing: (unset: string) => string):
    (name: string) => string {
  const value = (name: string) => env[name] ?? "";
  const unset = names.filter((name) => value(name) === "");
  if (unset.length > 0) {
    throw new Error(missing(unset.join(", ")));
  }
  const shaped = names.filter((name) => /[\s\p{Cc}]/u.test(value(name)));
  if (shaped.length > 0) {
    throw new Error(`${shaped.join(", ")} hold a space or a control character`);
  }
  if (!BUCKET.test(value("OFFSITE_BUCKET"))) {
    throw new Error("OFFSITE_BUCKET is no B2 bucket name (6 to 63 letters, digits and dashes)");
  }
  return value;
}

/** The credential stored under `names`, as `value` reads them. */
const keyOf = (value: (name: string) => string, [id, secret]: readonly [string, string]): Key =>
  Object.freeze({ names: `${id} and ${secret}`, id: value(id), secret: value(secret) });

/** The copy's settings from `env`: null when none is set, loudly when only some are. */
export function settings(env: Env): Settings | null {
  if (SETTINGS.every((name) => (env[name] ?? "") === "")) {
    return null;
  }
  const value = stored(env, SETTINGS, (unset) => `the off-account copy is configured in part: ` +
    `${unset} not set (platform/deploy/checklist.md, "The off-account copy")`);
  if (!HEX32.test(env["CLOUDFLARE_ACCOUNT_ID"] ?? "")) {
    throw new Error("the repository variable CLOUDFLARE_ACCOUNT_ID is no 32-hex account id");
  }
  const keys = Object.fromEntries(Object.entries(KEYS).map(([use, names]) =>
    [use, keyOf(value, names)])) as Record<keyof typeof KEYS, Key>;
  return Object.freeze({ ...keys, bucket: value("OFFSITE_BUCKET"),
                         account: env["CLOUDFLARE_ACCOUNT_ID"]! });
}

/**
 * What a copy back reads the copy and R2 with: the copy's bucket and read key, the account's
 * R2, and an S3 pair on its files bucket: a nightly's read pair (`drill`), or the write pair
 * minted for one restore (`copyBack`).
 */
export type Back = Pick<Settings, "bucket" | "account" | "read" | "r2">;

/**
 * The R2 write pair a copy back writes with: the S3 pair of a token minted for one restore,
 * ending the next day, and revoked after (`checklist.md`, "A restore from the copy"). No
 * standing key can write the files bucket.
 */
const WRITE_BACK = Object.freeze(["OFFSITE_R2_WRITE_ACCESS_KEY_ID",
                                  "OFFSITE_R2_WRITE_SECRET_ACCESS_KEY"] as const);

/** What mode `copyback` reads (`deploy.ts`): the bucket's name, a variable, and the keys. */
export const BACK_INPUTS = Object.freeze({
  public: Object.freeze(["OFFSITE_BUCKET"]),
  secret: Object.freeze([...KEYS.read, ...WRITE_BACK]),
});

/** A copy back's settings from `env`, in `account`: each one set, else a loud stop naming it. */
export function backFrom(env: Env, account: string): Back {
  const value = stored(env, [...BACK_INPUTS.public, ...BACK_INPUTS.secret], (unset) =>
    `a copy back needs ${unset} (platform/deploy/checklist.md, "A restore from the copy"); ` +
    "nothing is read or written");
  return Object.freeze({ bucket: value("OFFSITE_BUCKET"), account,
                         read: keyOf(value, KEYS.read), r2: keyOf(value, WRITE_BACK) });
}

/**
 * The nights a deletion in R2 may wait to be hidden here: the first night's lag, and red,
 * late or dropped nights after it. The platform's canary alarms well inside it (`watch`).
 */
const HIDE_NIGHTS = 7;

/**
 * How many days the copy keeps a version once R2 no longer has it, and the lock holds each
 * from its upload: the erasure's window less `HIDE_NIGHTS` before a deletion is hidden here
 * and a day for the lifecycle's daily pass.
 */
export const KEEP_DAYS = RECOVERY_WINDOW_S / 86_400 - HIDE_NIGHTS - 1;

/** Each B2 key's capabilities, exactly (Backblaze's application keys). */
export const CAPABILITIES = Object.freeze({
  /** Adds and hides (`b2_hide_file` needs only `writeFiles`); never deletes or reads. */
  write: Object.freeze(["listBuckets", "listFiles", "writeFiles"]),
  /** Reads the objects, and the bucket's lock, which a key without it is not shown. */
  read: Object.freeze(["listBuckets", "listFiles", "readBucketRetentions", "readFiles"]),
});

const B2_API = "https://api.backblazeb2.com/b2api/v3";

type Json = Record<string, any>;

async function answered(response: Response, what: string): Promise<Json> {
  if (!response.ok) {
    throw new Error(`Backblaze refused ${what} (${response.status})`);
  }
  return await response.json() as Json;
}

/**
 * `key` signed in, and held to exactly `capabilities` on the one bucket. Its token and the
 * API's address, for a further call.
 */
async function signedIn(key: Key, capabilities: readonly string[], bucket: string,
                        io: Effects): Promise<{ api: string; token: string; account: string }> {
  const body = await answered(await io.fetch(`${B2_API}/b2_authorize_account`, {
    headers: { authorization: `Basic ${btoa(`${key.id}:${key.secret}`)}` } }),
                              `the key ${key.names}`);
  const storage = body["apiInfo"]?.["storageApi"] ?? {};
  const held = [...(storage["allowed"]?.["capabilities"] ?? [])].sort();
  const buckets = (storage["allowed"]?.["buckets"] ?? []).map((each: Json) => each["name"]);
  const prefix = storage["allowed"]?.["namePrefix"] ?? null;
  if (held.join() !== [...capabilities].sort().join() || buckets.join() !== bucket
      || prefix !== null) {
    throw new Error(`the key ${key.names} holds ${held.join(", ") || "nothing"} on ` +
                    `${buckets.join(", ") || "every bucket"}; it must hold exactly ` +
                    `${capabilities.join(",")} on ${bucket} alone, with no name prefix (b2 key ` +
                    `create --bucket ${bucket} <name> ${capabilities.join(",")})`);
  }
  return { api: storage["apiUrl"], token: body["authorizationToken"],
           account: body["accountId"] };
}

/**
 * Both keys held to their capabilities, and the bucket to its rules: private, locked in
 * compliance mode for `KEEP_DAYS` from each upload, and one lifecycle rule over every name
 * that deletes a hidden version `KEEP_DAYS` after it is hidden and hides nothing itself.
 */
async function held(s: Settings, io: Effects): Promise<void> {
  await signedIn(s.write, CAPABILITIES.write, s.bucket, io);
  const read = await signedIn(s.read, CAPABILITIES.read, s.bucket, io);
  const listed = await answered(await io.fetch(`${read.api}/b2api/v3/b2_list_buckets`, {
    method: "POST", headers: { authorization: read.token, "content-type": "application/json" },
    body: JSON.stringify({ accountId: read.account, bucketName: s.bucket }) }),
                                `the listing of ${s.bucket}`);
  const bucket = (listed["buckets"] ?? []).find((each: Json) => each["bucketName"] === s.bucket);
  if (bucket === undefined) {
    throw new Error(`Backblaze lists no bucket ${s.bucket}`);
  }
  const lock = bucket["fileLockConfiguration"] ?? {};
  const retention = lock["value"]?.["defaultRetention"] ?? {};
  const rules = (bucket["lifecycleRules"] ?? []) as Json[];
  const wrong = [
    bucket["bucketType"] === "allPrivate" ? null : "its files are not private",
    lock["isClientAuthorizedToRead"] === true ? null : "the read key can't read its lock",
    lock["value"]?.["isFileLockEnabled"] === true
      && retention["mode"] === "compliance" && retention["period"]?.["duration"] === KEEP_DAYS
      && retention["period"]?.["unit"] === "days" ? null
      : `its default retention is not compliance, ${KEEP_DAYS} days`,
    rules.length === 1 && rules[0]!["fileNamePrefix"] === ""
      && rules[0]!["daysFromHidingToDeleting"] === KEEP_DAYS
      && rules[0]!["daysFromUploadingToHiding"] === null ? null
      : `its lifecycle is not one rule over every name keeping prior versions ${KEEP_DAYS} days`,
  ].filter((each) => each !== null);
  if (wrong.length > 0) {
    throw new Error(`the B2 bucket ${s.bucket} breaks the copy's rules: ${wrong.join("; ")}`);
  }
}

/**
 * A night that hides more than this share of what the copy held, and more than
 * `MAX_DELETE_FLOOR`, fails once its hides are made: a mass deletion in R2 is mirrored
 * loudly, never quietly. It is never held back, so no erasure waits past its window; every
 * hidden version stays `KEEP_DAYS` under the lifecycle rule, which neither key can change,
 * and `--b2-version-at` reads it back.
 */
const MAX_DELETE_SHARE = 0.1;
const MAX_DELETE_FLOOR = 100;
/** Objects downloaded from both sides and compared byte for byte each night. */
const SAMPLE = 16;

/** The remote `name`, defined in one rclone's environment alone. */
function remote(name: string, fields: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(fields).map(([field, value]) =>
    [`RCLONE_CONFIG_${name.toUpperCase()}_${field}`, value]));
}

/** The remote `name` on the account's R2, with the S3 pair `key`. */
const r2Remote = (name: string, account: string, key: Key) => remote(name, {
  TYPE: "s3", PROVIDER: "Cloudflare", NO_CHECK_BUCKET: "true",
  ENDPOINT: `https://${account}.r2.cloudflarestorage.com`, ACCESS_KEY_ID: key.id,
  SECRET_ACCESS_KEY: key.secret });

/**
 * The remote `name` on the copy with the read key: as it stands, or as it stood at `at`
 * (`--b2-version-at`, a view that writes nothing).
 */
const readRemote = (name: string, key: Key, at: Date | null) => remote(name, {
  TYPE: "b2", ACCOUNT: key.id, KEY: key.secret, ...at === null ? {} : { VERSION_AT: iso(at) } });

/** `at` to the second, as rclone and the summaries take a time. */
const iso = (at: Date): string => at.toISOString().replace(/\.\d+Z$/, "Z");

// R2's own LastModified: an object a Worker wrote carries no rclone time, which rclone would
// otherwise ask R2 for object by object. B2 ignores the flag and keeps the time each upload
// is given.
const TIMES = Object.freeze(["--use-server-modtime", "--fast-list"]);

/**
 * rclone over `remotes`: `run` runs `what` with the remotes `using` names defined in its
 * environment alone, its output when `output`, any failure thrown, naming `what`; `listed`
 * lists every name under `at` on the one remote `using`, after `filters`.
 */
function rcloneOf<R extends string>(io: Effects,
                                    remotes: Readonly<Record<R, Record<string, string>>>) {
  const run = (what: string, using: readonly R[], args: readonly string[],
               output = false): string => {
    const ran = io.spawn("rclone", args, {
      encoding: "utf8", maxBuffer: 2 ** 30,
      stdio: ["ignore", output ? "pipe" : "inherit", "inherit"],
      env: { PATH: process.env["PATH"] ?? "", RCLONE_CONFIG: "/dev/null",
             ...Object.assign({}, ...using.map((name) => remotes[name])) } });
    if (ran.status !== 0) {
      throw new Error(`the copy's ${what} failed (rclone exit ${ran.status ?? ran.signal}; ` +
                      "rclone's lines above say why)");
    }
    return String(ran.stdout ?? "");
  };
  const listed = (what: string, using: R, at: string, filters: readonly string[] = []) =>
    run(what, [using], ["lsf", "--files-only", "-R", ...TIMES, ...filters, at], true)
      .split("\n").filter(Boolean);
  return { run, listed };
}

/** `SAMPLE` of `names`, each once, at random. */
function sampled(names: readonly string[]): string[] {
  const left = [...names];
  return Array.from({ length: Math.min(SAMPLE, left.length) },
                    () => left.splice(randomInt(left.length), 1)[0]!);
}

/** `use` given a file that lists `names`, one a line, removed once `use` returns. */
function withList<T>(names: readonly string[], use: (file: string) => T): T {
  const folder = mkdtempSync(join(tmpdir(), "offsite-"));
  try {
    const file = join(folder, "names");
    writeFileSync(file, names.map((name) => `${name}\n`).join(""));
    return use(file);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}

/** What a night copied: when it began, to the second, and what it did, as summary lines. */
export interface Copied {
  readonly began: string;
  readonly lines: readonly string[];
}

/**
 * The nightly copy of `source`, the files bucket, to the B2 bucket: held first, then
 * `rclone sync`, then a check of every object R2 held when the run began and a sample of them
 * byte for byte. Any failure throws.
 */
export async function copy(s: Settings, source: string, io: Effects): Promise<Copied> {
  await held(s, io);
  const remotes = {
    source: r2Remote("source", s.account, s.r2),
    copy: remote("copy", { TYPE: "b2", ACCOUNT: s.write.id, KEY: s.write.secret,
                           HARD_DELETE: "false" }),
    check: readRemote("check", s.read, null),
  };
  const { run: rclone, listed } = rcloneOf(io, remotes);
  const from = `source:${source}`;
  const to = `copy:${s.bucket}`;
  const sizeOf = (what: string, using: "source" | "copy", at: string) => {
    const { count, bytes } = JSON.parse(rclone(what, [using], ["size", "--json", ...TIMES, at],
                                               true)) as { count: number; bytes: number };
    return { count, bytes };
  };
  const began = iso(io.now());
  const before = listed("listing of the copy", "copy", to);
  const kept = new Set(listed("listing of R2", "source", from));
  // Nothing deletes a mark, so one R2 lacks is one R2 lost: a new account's bucket before mode
  // recover wrote them back. The sync would hide them, and every copy back after would find
  // the marks not whole.
  const lost = before.filter((name) => markedNamespace(name) !== null && !kept.has(name));
  if (lost.length > 0) {
    throw new Error(`R2 lacks ${lost.length} erasure marks the copy holds, and nothing deletes ` +
                    "a mark: a files bucket that lost them (a new account's, before mode " +
                    "recover wrote them back) is never copied over them; nothing is copied");
  }
  const hidden = before.filter((name) => !kept.has(name)).length;
  const limit = Math.max(MAX_DELETE_FLOOR, Math.floor(before.length * MAX_DELETE_SHARE));
  rclone("sync", ["source", "copy"],
         ["sync", from, to, ...TIMES, "--stats-one-line", "--stats", "1m",
          "--stats-log-level", "NOTICE"]);
  // What R2 held when the run began: what it wrote or deleted since is the next night's.
  rclone("check of every object", ["source", "copy"],
         ["check", from, to, "--one-way", "--size-only", ...TIMES, "--min-age", began]);
  const names = listed("listing of R2", "source", from, ["--min-age", began]);
  const sample = sampled(names);
  withList(sample, (file) => rclone("byte check of a sample", ["source", "check"],
    ["check", from, `check:${s.bucket}`, "--one-way", "--download", "--files-from", file]));
  const after = { source: sizeOf("count of R2", "source", from),
                  copy: sizeOf("count of the copy", "copy", to) };
  const lines = [
    `R2 ${source}: ${after.source.count} objects, ${after.source.bytes} bytes`,
    `B2 ${s.bucket}: ${after.copy.count} objects, ${after.copy.bytes} bytes ` +
      `(${before.length} before the run, ${hidden} of them hidden)`,
    `every object R2 held at ${began} is in the copy at its size; ` +
      `${sample.length} compared byte for byte`,
  ];
  if (hidden > limit) {
    throw new Error(`the copy hid ${hidden} of its ${before.length} objects, past ${limit}: ` +
                    `a mass deletion in R2? Each hidden version stays ${KEEP_DAYS} days ` +
                    `(--b2-version-at reads it). ${lines.join("; ")}`);
  }
  return { began, lines };
}

/** The nightly workflow, the branch its schedule runs on, and its two jobs. */
export const NIGHTLY = Object.freeze({ workflow: "offsite-copy.yml", ref: "main",
                                       settings: "settings", copy: "copy" });

/**
 * How long the copy may go without a night that passed before the platform's canary alarms:
 * two nights, and half a day for a night GitHub starts late (its schedules run hours late
 * under load). That leaves four of `HIDE_NIGHTS` to mend it before an erasure's hide is late.
 */
export const OVERDUE_MS = (2 * 24 + 12) * 3600_000;

/** The copy as the canary reads it: a night passed in time, nothing owed yet, or overdue. */
export interface Watch {
  readonly state: "passed" | "waiting" | "overdue";
  readonly line: string;
}

/** One completed night of the copy: its run, and its settings and copy jobs. */
interface Night {
  readonly run: Json;
  readonly read: Json;
  readonly copied: Json;
}

/** The copy's completed nights on `NIGHTLY.ref`, newest first, each with its two jobs. */
async function* nights(reach: Reach): AsyncGenerator<Night> {
  for await (const runs of pages(reach, `/actions/workflows/${NIGHTLY.workflow}/runs?branch=${
    NIGHTLY.ref}&status=completed`, "workflow_runs")) {
    for (const run of runs) {
      const { jobs } = await github(reach, "GET", `/actions/runs/${run["id"]}/jobs`) as
        { jobs: Json[] };
      const job = (name: string): Json => {
        const found = jobs.find((each) => each["name"] === name);
        if (found === undefined) {
          throw new Error(`${NIGHTLY.workflow}'s run ${run["id"]} has no ${name} job`);
        }
        return found;
      };
      yield { run, read: job(NIGHTLY.settings), copied: job(NIGHTLY.copy) };
    }
  }
}

/**
 * The copy as its nights on `NIGHTLY.ref` show it, newest first. A night owes nothing when its
 * copy job passed, or when its settings job found no setting stored, so the copy job was
 * skipped. The newest night finding none stored: the copy isn't configured, and waits. Else
 * the newest night that owed nothing decides: inside `OVERDUE_MS`, passed if it copied, or
 * waiting for the first pass since it was configured; past it, or with none, overdue.
 */
export async function watch(reach: Reach, now: Date): Promise<Watch> {
  const due = now.getTime() - OVERDUE_MS;
  const overdue = (since: string): Watch => ({ state: "overdue", line:
    `no night of ${NIGHTLY.workflow} has passed ${since}, past two nights: an erased owner's ` +
    `objects must be hidden in the copy within ${HIDE_NIGHTS} nights (deploy/offsite.ts)` });
  let newest = true;
  for await (const { run, read, copied } of nights(reach)) {
    const passed = copied["conclusion"] === "success";
    const dark = read["conclusion"] === "success" && copied["conclusion"] === "skipped";
    if (dark && newest) {
      return { state: "waiting", line: `not configured: the night of ${run["created_at"]} ` +
               "found none of the copy's settings stored, so nothing is owed" };
    }
    if (passed || dark) {
      const at = String((passed ? copied : read)["completed_at"]);
      if (Date.parse(at) < due) {
        return overdue(`since ${at}`);
      }
      return passed ? { state: "passed", line: `a night last passed at ${at}` }
        : { state: "waiting", line: `configured since the night of ${at}; its first pass is ` +
            `due by ${new Date(Date.parse(at) + OVERDUE_MS).toISOString()}` };
    }
    if (Date.parse(String(run["updated_at"])) < due) {
      return overdue(`since before ${run["updated_at"]}`);
    }
    newest = false;
  }
  return newest ? { state: "waiting", line: `no night of ${NIGHTLY.workflow} has completed on ` +
                    `${NIGHTLY.ref}, so nothing says the copy is configured` }
    : overdue("ever");
}

/**
 * When the copy first held what R2 held after `at` (a key rotation's pass, `deploy.ts` mode
 * `retire`): `first`, the copy job's `completed_at` of the first night on `NIGHTLY.ref` that
 * passed and whose run was made after `at` (so it listed R2 after it), or null when none has;
 * and `ever`, whether any night has passed at all. A copy that never passed holds nothing.
 */
export async function firstNightAfter(reach: Reach, at: Date):
    Promise<{ readonly first: Date | null; readonly ever: boolean }> {
  let first: Date | null = null;
  for await (const { run, copied } of nights(reach)) {
    if (copied["conclusion"] !== "success") {
      continue;
    }
    if (Date.parse(String(run["created_at"])) <= at.getTime()) {
      return { first, ever: true };
    }
    first = new Date(String(copied["completed_at"]));
  }
  return { first, ever: first !== null };
}

/**
 * Where the latest restore from the copy is recorded, in the control namespace:
 * `{kind, began}`, when it began writing R2, in seconds. Every write from the copy into R2
 * puts it before its first byte (`copyBack`, the one such write;
 * `docs/decisions/untangle-2026-11/r8-retire-gate.md`):
 * a version it reads back may hold a root under a kid a key rotation has since rewrapped
 * away, which the next night uploads again with a fresh `KEEP_DAYS`, so mode `retire` reads
 * the copy's gate from the first pass begun after it.
 */
export const RESTORE_KEY = "deploy:offsite:restore";
const RESTORE_KIND = "tinyassets-offsite-restore";

/**
 * When the restore record `value` (`RESTORE_KEY`, as KV answers it) says the latest restore
 * from the copy began, in seconds, or null when none is recorded; a record out of shape is a
 * loud stop.
 */
export function restoreBegan(value: unknown): number | null {
  if (value === undefined) {
    return null;
  }
  if (!keyed(value, ["kind", "began"]) || value["kind"] !== RESTORE_KIND
      || !whole(value["began"], 0)) {
    throw new Error(`the restore record ${RESTORE_KEY} is out of shape`);
  }
  return value["began"];
}

/** Where the restore record is put: the control namespace, through the kit's client. */
export interface RestoreControl {
  readonly cloudflare: Pick<Cloudflare, "put">;
  readonly account: string;
  readonly namespace: string;
}

/** The restore record put as begun at `now`: its `began`, or a loud stop on any refusal. */
async function recordRestore(control: RestoreControl, now: Date): Promise<number> {
  const began = Math.floor(now.getTime() / 1000);
  const status = await control.cloudflare.put(control.account, control.namespace, RESTORE_KEY,
                                              { kind: RESTORE_KIND, began });
  if (status !== 200) {
    throw new Error(`writing the restore record ${RESTORE_KEY} failed (${status}): nothing is ` +
                    "written into R2");
  }
  return began;
}

/**
 * The oldest time a copy back reads the copy as it stood at: a version gone from R2 since is
 * kept `KEEP_DAYS` after it was hidden, so a view inside them is whole, and a day of them is
 * left for the run itself.
 */
export const BACK_DAYS = KEEP_DAYS - 1;

/** `at` held inside the copy's window at `now`, or a loud stop naming it. */
export function readableAt(at: Date | null, now: Date): void {
  const oldest = now.getTime() - BACK_DAYS * 86_400_000;
  if (at !== null && (at.getTime() < oldest || at > now)) {
    throw new Error(`the copy is read back as it stood at a time from ${iso(new Date(oldest))} ` +
                    `to now, not at ${iso(at)}: a version older than ${KEEP_DAYS} days since ` +
                    "it was hidden is gone");
  }
}

type Run<R extends string> = ReturnType<typeof rcloneOf<R>>["run"];

/**
 * The namespaces the files bucket marks deleted on purpose (`e/<ns>`,
 * `contract/src/files.ts` `MARKS`), as R2 holds them and the copy as it stands: the files
 * Worker makes each before its namespace's first delete, and nothing deletes one, so R2 holds
 * every mark, and the copy every one made before its last night, past the account itself.
 * Read with `--include`, so a bucket with no mark yet lists none. With `at`, every mark the
 * copy held then must still be in it: one gone means a night copied a bucket that had lost its
 * marks (an account lost, and a new one copied), so the marks as it stands are not whole. A
 * listing that fails, a name under `e/` that is no mark, or a mark gone, is a loud stop before
 * anything is written: no copy back runs without the erasures.
 */
function erasures(run: Run<"back" | "now" | "r2">, bucket: string, source: string,
                  at: boolean): ReadonlySet<string> {
  const listed = (using: "back" | "now" | "r2", root: string, where: string) =>
    new Set(run(`listing of the erasure marks in ${where}`, [using],
                ["lsf", "--files-only", "-R", "--include", `/${MARKS}*`, `${using}:${root}`], true)
      .split("\n").filter(Boolean).map((name) => {
        const ns = markedNamespace(name);
        if (ns === null) {
          throw new Error(`the erasure marks in ${where} can't be read: it holds a name under ` +
                          `${MARKS} that is no namespace's mark; nothing is written`);
        }
        return ns;
      }));
  const now = listed(at ? "now" : "back", bucket, "the copy");
  if (at) {
    const lost = [...listed("back", bucket, "the copy as it stood")].filter((ns) => !now.has(ns));
    if (lost.length > 0) {
      throw new Error(`the copy as it stands lacks ${lost.length} erasure marks it held at the ` +
                      "time asked: a night copied a files bucket that had lost them, so its " +
                      "marks are not whole; nothing is written");
    }
  }
  return new Set([...now, ...listed("r2", source, "R2")]);
}

/**
 * `prefix` of the copy as it stood at `at` (null: as it stands) and of `source` in R2, the
 * namespaces marked erased (`erasures`, read first), and what a copy back of it writes: every
 * name under it the copy then held (`held`) that R2 now holds (`there`) or lacks (`lacking`),
 * relative to it.
 */
function readBack(b: Back, source: string, prefix: string, at: Date | null, io: Effects) {
  const rclone = rcloneOf(io, { back: readRemote("back", b.read, at),
                                now: readRemote("now", b.read, null),
                                r2: r2Remote("r2", b.account, b.r2) });
  const erased = erasures(rclone.run, b.bucket, source, at !== null);
  const from = `back:${b.bucket}/${prefix}`;
  const to = `r2:${source}/${prefix}`;
  const there: ReadonlySet<string> = new Set(rclone.listed("listing of R2", "r2", to));
  const held = rclone.listed("listing of the copy", "back", from);
  const lacking = held.filter((name) => !there.has(name));
  return { run: rclone.run, from, to, held, there, lacking, erased };
}

/**
 * What the owner's object names in its namespace (`restore.names`, `owner/src/restore.ts`
 * `restoreNames`): the bucket name of each chunk a row of it holds, in any state but
 * `deleting`, and each finished generation it holds.
 */
export interface Referenced {
  readonly chunks: ReadonlySet<string>;
  readonly generations: ReadonlySet<string>;
}

/**
 * Whether the object `name` under a namespace's prefix is one a copy back writes: a chunk
 * (`c/<name>`) a chunk row of `named` names, a root, part or segment (`m/<gen>…`) of a
 * generation it holds (`files/src/keys.ts`), or a hold (`h/<held>`, `HOLDS`), which names no
 * row and only adds to what the owner's erasure deletes.
 */
function referenced(named: Referenced, name: string): boolean {
  const [kind, id, ...rest] = name.split("/");
  return kind === "c" ? rest.length === 0 && named.chunks.has(id!)
    : kind === "m" ? named.generations.has(id!) : `${kind}/` === HOLDS && rest.length === 0;
}

/**
 * R8, the restore from the copy: the objects of `namespace` that `source`, the files bucket,
 * lacks and the owner's object names (`named`, asked once R2 is listed), written back from
 * the copy as it stood at `at` (null: as it stands), each at its own key, then each read back
 * byte for byte.
 * - **Only what a row names.** The files plane's rule is that every object in the bucket has
 *   a row first, and its collection never lists the bucket, so an object no row names (a
 *   chunk the owner's own collection deleted, a redaction's bytes, a pruned generation) is
 *   never written: nothing would ever delete it again. After the write the object is asked
 *   again, and a written object it no longer names (its row collected during the run) is a
 *   loud stop, for mode `restore`'s sweep.
 * - **Never a marked namespace's** (`erasures`, read first): its erasure stands, whatever
 *   else says the owner is not erased.
 * - **Never one R2 holds,** so no live root goes back to an older wrap.
 * - **The restore record before the first byte,** so mode `retire` holds a rotation's gate to
 *   a pass begun after it. With nothing to write, nothing is written and nothing recorded.
 *
 * The caller names a namespace only as the owner's own object answers it (`deploy.ts` mode
 * `copyback`). What it did, as plan lines.
 */
export async function copyBack(b: Back, source: string, namespace: string, at: Date | null,
                               named: () => Promise<Referenced>, control: RestoreControl,
                               io: Effects): Promise<string[]> {
  readableAt(at, io.now());
  const prefix = objectPrefix(namespace);
  const as = at === null ? "as the copy stands" : `as the copy held them at ${iso(at)}`;
  const back = readBack(b, source, prefix, at, io);
  if (back.erased.has(namespace)) {
    throw new Error(`the files bucket marks the namespace under ${prefix} erased: nothing of it ` +
                    "is written, and no restore is recorded");
  }
  const before = await named();
  const writes = back.lacking.filter((name) => referenced(before, name));
  const left = `${back.lacking.length - writes.length} more it lacks no row names`;
  if (writes.length === 0) {
    return [`R2 lacks no object the copy holds under ${prefix} that the owner's object names, ` +
            `${as} (${left}): nothing is written, and no restore is recorded`];
  }
  const began = await recordRestore(control, io.now());
  withList(writes, (file) => {
    back.run("copy back", ["back", "r2"],
             ["copy", back.from, back.to, "--files-from", file, "--ignore-existing",
              "--stats-one-line", "--stats", "1m", "--stats-log-level", "NOTICE"]);
    back.run("byte check of the copy back", ["back", "r2"],
             ["check", back.from, back.to, "--one-way", "--download", "--files-from", file]);
  });
  const after = await named();
  const orphans = writes.filter((name) => !referenced(after, name));
  if (orphans.length > 0) {
    throw new Error(`the owner's object no longer names ${orphans.length} of the ${
      writes.length} objects written under ${prefix}, collected during the run: only mode ` +
      "restore's sweep deletes them (checklist.md, \"A restore from the copy\")");
  }
  return [`recorded a restore from the copy begun at ${iso(new Date(began * 1000))} in ` +
          `${RESTORE_KEY}, before its first byte`,
          `wrote ${writes.length} objects under ${prefix} into ${source}, ${as}, each one R2 ` +
          `lacked and the owner's object names, and read each back byte for byte (${left})`];
}

/** Whether `name`, under a namespace's prefix, is a generation's root (`o/<ns>/m/<gen>`). */
function rootName(name: string): boolean {
  const [, , kind, gen, ...rest] = name.split("/");
  return kind === "m" && gen !== undefined && rest.length === 0;
}

/**
 * What a copy back of the whole bucket after an account's loss writes (`recover`, and the
 * drill's count of it), of `back` (`readBack`): how many marks R2 lacks, so R2 holds every
 * erasure again first; the namespaces it writes, sorted, with every object of them R2 lacks;
 * and of those the ones whose owner is located and begun (`located`).
 * - **Left out:** every marked namespace, and every one a left-out namespace holds
 *   (`o/<ns>/h/<held>`, `HOLDS`): its owner's erasure took it, or is taking it; and every one
 *   with no root: no wrapped key of it survives, so nothing opens its chunks.
 * - **Written, not begun:** one another written namespace holds. Its owner is the holder's,
 *   whose object holds it again once restored, so its erasure takes it.
 * - **Held in a cycle** (`cycled`): written, held, and held by no namespace begun or held by
 *   one, so it would be begun by none; the caller stops before anything is written.
 * Any other name is a loud stop: the files bucket holds nothing else (`files/src/keys.ts`).
 */
function recovered({ held, there, lacking, erased }:
                     Pick<ReturnType<typeof readBack>, "held" | "there" | "lacking" | "erased">) {
  const kept = new Set([...there].map(markedNamespace));
  const spaces = new Set<string>();
  const roots = new Set<string>();
  const holds: { holder: string; held: string }[] = [];
  for (const name of held.filter((each) => markedNamespace(each) === null)) {
    const ns = namespaceOf(name);
    if (ns === null) {
      throw new Error(`the copy holds an object that is neither a namespace's nor a mark ` +
                      `(under ${name.split("/")[0]}/), which nothing in the files bucket writes`);
    }
    spaces.add(ns);
    if (rootName(name)) {
      roots.add(ns);
    }
    const hold = heldNamespace(name);
    if (hold !== null) {
      holds.push(hold);
    }
  }
  const out = new Set(erased);
  const reach = (from: Set<string>, within: (ns: string) => boolean) => {
    for (let grew = true; grew;) {
      grew = false;
      for (const { holder, held: ns } of holds) {
        if (from.has(holder) && !from.has(ns) && within(ns)) {
          from.add(ns);
          grew = true;
        }
      }
    }
    return from;
  };
  reach(out, () => true);
  const namespaces = [...roots].filter((ns) => !out.has(ns)).sort();
  const taken = new Set(namespaces);
  const heldBy = new Set(holds.filter(({ holder }) => taken.has(holder)).map(({ held: ns }) => ns));
  const located = namespaces.filter((ns) => !heldBy.has(ns));
  const begun = reach(new Set(located), (ns) => taken.has(ns));
  return {
    marks: [...erased].filter((ns) => !kept.has(ns)).length,
    namespaces,
    located,
    cycled: namespaces.filter((ns) => !begun.has(ns)),
    writes: lacking.filter((name) => taken.has(namespaceOf(name) ?? "")),
    erased: [...spaces].filter((ns) => out.has(ns)).length,
    rootless: [...spaces].filter((ns) => !out.has(ns) && !roots.has(ns)).length,
  };
}

/**
 * The monthly drill, on the whole bucket, after the night `night` (`copy`): every object R2
 * held when the night began downloaded from both sides and compared byte for byte, so a copy
 * back as the copy stands writes exactly R2's bytes; then a whole-bucket copy back's own reads
 * (`recover`'s) as the copy stood `BACK_DAYS` ago, the oldest it takes: the erasure marks read
 * whole (`erasures`), what it would write counted (`recovered`), every marked namespace's
 * objects left out, and `SAMPLE` of them read whole. It writes nothing: no standing key can
 * write the files bucket.
 */
export function drill(s: Settings, source: string, night: Copied, io: Effects): string[] {
  const { run } = rcloneOf(io, { source: r2Remote("source", s.account, s.r2),
                                 check: readRemote("check", s.read, null) });
  run("byte check of every object", ["source", "check"],
      ["check", `source:${source}`, `check:${s.bucket}`, "--one-way", "--download",
       "--min-age", night.began]);
  const at = new Date(io.now().getTime() - BACK_DAYS * 86_400_000);
  const back = readBack(s, source, "", at, io);
  const would = recovered(back);
  const sample = sampled(would.writes);
  withList(sample, (file) => back.run("read of a copy back's sample", ["back"],
                                      ["cat", "--discard", "--files-from", file, back.from]));
  return [`every object R2 held at ${night.began} is in the copy, compared byte for byte`,
          `a copy back as the copy stood at ${iso(at)} would write ${would.marks} ` +
          `erasure marks and ${would.writes.length} objects of ${would.namespaces.length} ` +
          `namespaces R2 no longer holds (${would.namespaces.length - would.located.length} ` +
          `held by another), and leave out ${would.erased} namespaces marked erased or held ` +
          `by one (${back.erased.size} marks) and ${would.rootless} with no root; ` +
          `${sample.length} of them read back whole`];
}

/** One owner's restore to begin (`restore.begin`): the owner, and its namespace's generation. */
export interface Begin {
  readonly owner: string;
  readonly namespace: string;
  readonly gen: string;
}

/**
 * What mode `recover` asks the doors through the operator channel (`deploy.ts`): each
 * namespace's owner and newest generation (`restore.locate`), null for one R2 holds no root
 * of; each owner's restore begun (`restore.begin`); and for one not begun, its namespace held
 * by the owner's own (`restore.hold`): each in order, null for one done, else why not.
 */
export interface Recovering {
  readonly locate: (namespaces: readonly string[]) =>
    Promise<ReadonlyMap<string, LocatedOwner | null>>;
  readonly begin: (begins: readonly Begin[]) => Promise<readonly (string | null)[]>;
  readonly hold: (holds: readonly Begin[]) => Promise<readonly (string | null)[]>;
}

/**
 * After an account's loss (mode `recover`, in a new account after a dark deploy): every
 * namespace of the copy as it stood at `at` (null: as it stands) written back into `source`,
 * the new account's files bucket, and its owner's restore begun, in one run
 * (`docs/decisions/untangle-2026-11/r8-locate.md`).
 * 1. The erasure marks read first (`erasures`), loudly, as every copy back reads them; then,
 *    with anything to write, the restore record before the first byte; then every mark the copy
 *    holds that R2 lacks written back, so R2 holds every erasure again and the next night keeps
 *    it in the copy (`copy` refuses a night over marks R2 lacks).
 * 2. Each namespace no mark names, nor a marked one holds, of which the copy held a root
 *    (`recovered`): every object of it R2 lacks written back whole, its holds with it, and read
 *    back byte for byte. No row can be asked here (no object names a namespace yet): the
 *    restore's own sweeps delete what no restored row names. A namespace held only in a cycle
 *    of holds stops the run first, nothing written.
 * 3. Each one no other written namespace holds located (`restore.locate`): two naming one owner
 *    is a loud stop naming both, before any restore begins.
 * 4. Each owner's restore begun from its namespace's newest generation (`restore.begin`), so
 *    every written namespace is named by an object, or held by one's, whose erasure deletes
 *    it; mode `restore` carries each on. One not begun (an owner live in the new account
 *    before the run) is held by the owner's own namespace (`restore.hold`), so its erasure
 *    takes it, and the run stops loudly naming each, the rest begun
 *    (`docs/decisions/untangle-2026-11/r8-erased-orphans.md`).
 * A run cut short is run again with the same `at` (null: the time its restore record names,
 * since a night after the marks are back hides what R2 lacks yet): nothing R2 holds is written
 * again (`--ignore-existing`, and the listing), and a begin asked again answers as it stands.
 * What it did, as plan lines.
 */
export async function recover(b: Back, source: string, at: Date | null, asking: Recovering,
                              control: RestoreControl, io: Effects): Promise<string[]> {
  readableAt(at, io.now());
  const back = readBack(b, source, "", at, io);
  const { marks, namespaces, located: unheld, cycled, writes, erased, rootless } =
    recovered(back);
  if (cycled.length > 0) {
    throw new Error(`the namespaces ${cycled.join(", ")} are each held by another and by none ` +
                    "begun, so no restore would ever name them: nothing is written");
  }
  const as = at === null ? "as the copy stands" : `as the copy held them at ${iso(at)}`;
  const lines: string[] = [];
  if (marks > 0 || writes.length > 0) {
    const began = await recordRestore(control, io.now());
    lines.push(`recorded a restore from the copy begun at ${iso(new Date(began * 1000))} in ` +
               `${RESTORE_KEY}, before its first byte`);
  }
  if (marks > 0) {
    const all = [`now:${b.bucket}`, `r2:${source}`, "--include", `/${MARKS}*`];
    back.run("copy back of the erasure marks", ["now", "r2"],
             ["copy", ...all, "--ignore-existing", "--stats-one-line", "--stats", "1m",
              "--stats-log-level", "NOTICE"]);
    back.run("byte check of the erasure marks", ["now", "r2"],
             ["check", ...all, "--one-way", "--download"]);
    lines.push(`wrote back first the ${marks} erasure marks the copy holds and R2 lacked, ` +
               "each read back byte for byte");
  }
  if (writes.length > 0) {
    withList(writes, (file) => {
      back.run("copy back", ["back", "r2"],
               ["copy", back.from, back.to, "--files-from", file, "--ignore-existing",
                "--stats-one-line", "--stats", "1m", "--stats-log-level", "NOTICE"]);
      back.run("byte check of the copy back", ["back", "r2"],
               ["check", back.from, back.to, "--one-way", "--download", "--files-from", file]);
    });
  }
  lines.push(`wrote ${writes.length} objects of ${namespaces.length} namespaces into ${source}, ` +
             `${as}, each one R2 lacked, read back byte for byte (${namespaces.length -
             unheld.length} held by another); left out ${erased} namespaces marked erased or ` +
             `held by one, and ${rootless} with no root`);
  if (unheld.length === 0) {
    return lines;
  }
  const located = await asking.locate(unheld);
  const begins = unheld.map((namespace) => {
    const found = located.get(namespace) ?? null;
    if (found === null) {
      throw new Error(`R2 holds no root of the namespace ${namespace} after its copy back: ` +
                      "nothing is begun");
    }
    return { owner: found.owner, namespace, gen: found.gen };
  });
  const by = new Map<string, string[]>();
  for (const { owner, namespace } of begins) {
    by.set(owner, [...by.get(owner) ?? [], namespace]);
  }
  const twice = [...by].filter(([, spaces]) => spaces.length > 1);
  if (twice.length > 0) {
    throw new Error(`${twice.map(([owner, spaces]) => `the namespaces ${spaces.join(" and ")} ` +
      `each name ${owner}`).join("; ")}: an owner has one namespace, so nothing is begun ` +
      "(checklist.md, \"A restore after the account's loss\")");
  }
  const answers = await asking.begin(begins);
  lines.push(...begins.filter((_, each) => answers[each] === null)
    .map(({ owner, namespace, gen }) => `began ${owner}'s restore from ${namespace}/${gen}: ` +
         `mode restore --owner ${owner} --generation ${namespace}/${gen} carries it on`));
  const unbegun = begins.filter((_, each) => answers[each] !== null);
  if (unbegun.length === 0) {
    return lines;
  }
  // An owner live in the new account takes no restore, and would take a second namespace: its
  // own holds this one, so its erasure takes both (`r8-erased-orphans.md`).
  const held = await asking.hold(unbegun);
  const failed = unbegun.map((begin, each) => `${begin.owner} from ${begin.namespace}/${
    begin.gen}: ${answers[begins.indexOf(begin)]}; ${held[each] === null
    ? "held by the owner's own namespace, so its erasure takes it, and not restored"
    : `not held either (${held[each]}), so no object names it`}`);
  throw new Error(`${unbegun.length} of ${begins.length} restores were not begun, each written ` +
                  `back: ${failed.join("; ")}. ${lines.join("; ")}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const env = process.env;
  const command = process.argv.slice(2).join(" ");
  const title = "Off-account copy";
  const summary = (lines: readonly string[]) => {
    console.log(lines.join("\n"));
    summarize(env, `${lines.join("\n\n")}\n`);
  };
  (async () => {
    const stored = settings(env);
    if (command === "settings") {
      if (stored === null) {
        const why = `none of ${SETTINGS.join(", ")} is set, so nothing is copied ` +
          "(platform/deploy/checklist.md, \"The off-account copy\")";
        console.log(annotate(env, "notice", title, `not configured: ${why}`));
        summary(["### The off-account copy: not configured", why]);
      }
      output(env, "configured", String(stored !== null));
    } else if (command === "copy" || command === "drill") {
      if (stored === null) {
        throw new Error("the off-account copy is not configured");
      }
      // The kit's facts read wrangler's own package, which only this command's job installs:
      // the settings job, on a dark night, installs nothing.
      const { FILES_BUCKET } = await import("./facts.ts");
      const { IO } = await import("./deploy.ts");
      const night = await copy(stored, FILES_BUCKET, IO);
      summary(["### The off-account copy", ...night.lines]);
      if (command === "drill") {
        summary(["### The drill of a restore from the copy",
                 ...drill(stored, FILES_BUCKET, night, IO)]);
      }
    } else {
      throw new Error("offsite.ts takes settings, copy or drill");
    }
  })().catch((error: Error) => {
    console.error(annotate(env, "error", title, error.message));
    process.exit(1);
  });
}
