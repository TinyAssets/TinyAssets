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
 *
 * `node --experimental-strip-types deploy/offsite.ts settings` writes `configured` for the
 * workflow; `… copy` runs the copy. Node runs this file as it is (type stripping), as the kit
 * does.
 */
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { randomInt } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { RECOVERY_WINDOW_S } from "../contract/src/limits.ts";
import { annotate, github, output, pages, type Reach, summarize } from "./actions.ts";
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
const ACCOUNT = /^[0-9a-f]{32}$/;

/** The copy's settings from `env`: null when none is set, loudly when only some are. */
export function settings(env: Readonly<Record<string, string | undefined>>): Settings | null {
  const value = (name: string) => env[name] ?? "";
  const missing = SETTINGS.filter((name) => value(name) === "");
  if (missing.length === SETTINGS.length) {
    return null;
  }
  if (missing.length > 0) {
    throw new Error(`the off-account copy is configured in part: ${missing.join(", ")} ` +
                    "not set (platform/deploy/checklist.md, \"The off-account copy\")");
  }
  const shaped = SETTINGS.filter((name) => /[\s\p{Cc}]/u.test(value(name)));
  if (shaped.length > 0) {
    throw new Error(`${shaped.join(", ")} hold a space or a control character`);
  }
  if (!BUCKET.test(value("OFFSITE_BUCKET"))) {
    throw new Error("OFFSITE_BUCKET is no B2 bucket name (6 to 63 letters, digits and dashes)");
  }
  if (!ACCOUNT.test(value("CLOUDFLARE_ACCOUNT_ID"))) {
    throw new Error("the repository variable CLOUDFLARE_ACCOUNT_ID is no 32-hex account id");
  }
  const keys = Object.fromEntries(Object.entries(KEYS).map(([use, [id, secret]]) =>
    [use, { names: `${id} and ${secret}`, id: value(id), secret: value(secret) }])) as
    Record<keyof typeof KEYS, Key>;
  return Object.freeze({ ...keys, bucket: value("OFFSITE_BUCKET"),
                         account: value("CLOUDFLARE_ACCOUNT_ID") });
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

/**
 * The nightly copy of `source`, the files bucket, to the B2 bucket: held first, then
 * `rclone sync`, then a check of every object R2 held when the run began and a sample of them
 * byte for byte. Any failure throws. What it did, as summary lines.
 */
export async function copy(s: Settings, source: string, io: Effects): Promise<string[]> {
  await held(s, io);
  const remotes = {
    source: remote("source", { TYPE: "s3", PROVIDER: "Cloudflare", NO_CHECK_BUCKET: "true",
                               ENDPOINT: `https://${s.account}.r2.cloudflarestorage.com`,
                               ACCESS_KEY_ID: s.r2.id, SECRET_ACCESS_KEY: s.r2.secret }),
    copy: remote("copy", { TYPE: "b2", ACCOUNT: s.write.id, KEY: s.write.secret,
                           HARD_DELETE: "false" }),
    check: remote("check", { TYPE: "b2", ACCOUNT: s.read.id, KEY: s.read.secret }),
  };
  const rclone = (what: string, using: readonly (keyof typeof remotes)[],
                  args: readonly string[], output = false): string => {
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
  // R2's own LastModified: an object a Worker wrote carries no rclone time, which rclone would
  // otherwise ask R2 for object by object. B2 ignores the flag and keeps the time each upload
  // is given.
  const times = ["--use-server-modtime", "--fast-list"];
  const from = `source:${source}`;
  const to = `copy:${s.bucket}`;
  const sizeOf = (what: string, using: "source" | "copy", at: string) => {
    const { count, bytes } = JSON.parse(rclone(what, [using], ["size", "--json", ...times, at],
                                               true)) as { count: number; bytes: number };
    return { count, bytes };
  };

  const listed = (what: string, using: "source" | "copy", at: string,
                  filters: readonly string[] = []) =>
    rclone(what, [using], ["lsf", "--files-only", "-R", ...times, ...filters, at], true)
      .split("\n").filter(Boolean);
  const began = io.now().toISOString().replace(/\.\d+Z$/, "Z");
  const before = listed("listing of the copy", "copy", to);
  const kept = new Set(listed("listing of R2", "source", from));
  const hidden = before.filter((name) => !kept.has(name)).length;
  const limit = Math.max(MAX_DELETE_FLOOR, Math.floor(before.length * MAX_DELETE_SHARE));
  rclone("sync", ["source", "copy"],
         ["sync", from, to, ...times, "--stats-one-line", "--stats", "1m",
          "--stats-log-level", "NOTICE"]);
  // What R2 held when the run began: what it wrote or deleted since is the next night's.
  rclone("check of every object", ["source", "copy"],
         ["check", from, to, "--one-way", "--size-only", ...times, "--min-age", began]);
  const names = listed("listing of R2", "source", from, ["--min-age", began]);
  const sample = Array.from({ length: Math.min(SAMPLE, names.length) },
                            () => names.splice(randomInt(names.length), 1)[0]!);
  const folder = mkdtempSync(join(tmpdir(), "offsite-"));
  try {
    writeFileSync(join(folder, "sample"), sample.map((name) => `${name}\n`).join(""));
    rclone("byte check of a sample", ["source", "check"],
           ["check", from, `check:${s.bucket}`, "--one-way", "--download", "--files-from",
            join(folder, "sample")]);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
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
  return lines;
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
      const [read, copied] = [job(NIGHTLY.settings), job(NIGHTLY.copy)];
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
  }
  return newest ? { state: "waiting", line: `no night of ${NIGHTLY.workflow} has completed on ` +
                    `${NIGHTLY.ref}, so nothing says the copy is configured` }
    : overdue("ever");
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
    } else if (command === "copy") {
      if (stored === null) {
        throw new Error("the off-account copy is not configured");
      }
      // The kit's facts read wrangler's own package, which only this command's job installs:
      // the settings job, on a dark night, installs nothing.
      const { FILES_BUCKET } = await import("./facts.ts");
      const { IO } = await import("./deploy.ts");
      summary(["### The off-account copy",
               ...await copy(stored, FILES_BUCKET, IO)]);
    } else {
      throw new Error("offsite.ts takes settings or copy");
    }
  })().catch((error: Error) => {
    console.error(annotate(env, "error", title, error.message));
    process.exit(1);
  });
}
