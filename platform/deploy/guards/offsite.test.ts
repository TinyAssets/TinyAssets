/**
 * The off-account copy (`deploy/offsite.ts`, `offsite-copy.yml`), never run against Backblaze
 * or R2 here: a fake Backblaze answers the two keys' sign-ins and the bucket's listing, and a
 * fake rclone records each call.
 *
 * What it holds:
 * - **Dark:** with none of the copy's settings stored, the settings step notes "not
 *   configured", writes `configured=false` and passes, and the copy job runs only on `true`.
 *   With some of them, or one out of shape, the step fails, naming it.
 * - **The keys and the bucket:** before rclone runs, the write key holds exactly what adds and
 *   hides (no delete, no read), the read key exactly what reads, each on the one bucket, and
 *   the bucket is private, locked in compliance mode and keeps prior versions one rule's
 *   `KEEP_DAYS`, inside the erasure's window. Anything else stops the night, copying nothing.
 * - **rclone:** the sync hides every deletion, and a night that hid more than a tenth of the
 *   copy (at least 100) fails once they are made; every object R2 held when the run began is
 *   checked at its size, and a
 *   sample byte for byte with the read key; each credential is in its one call's environment,
 *   never an argument; any failure stops loudly.
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { afterAll, describe, expect, it } from "vitest";
import { parse } from "yaml";

import { REPO } from "../../ci/paths";
import { workflow } from "../../ci/workflows";
import {
  backFrom, BACK_DAYS, CAPABILITIES, copy, copyBack, drill, type Effects, NIGHTLY, RESTORE_KEY,
  type RestoreControl, SETTINGS, settings,
} from "../offsite";

const BUCKET = "tinyassets-offsite-abcd1234";
const ACCOUNT = "c".repeat(32);
const SOURCE = "tinyassets-files";
/** Every setting, each value its own, so none can hide in another's place. */
const STORED: Record<string, string> = {
  OFFSITE_BUCKET: BUCKET, CLOUDFLARE_ACCOUNT_ID: ACCOUNT,
  OFFSITE_R2_ACCESS_KEY_ID: "r2-id", OFFSITE_R2_SECRET_ACCESS_KEY: "r2-secret-value",
  OFFSITE_B2_KEY_ID: "b2-write-id", OFFSITE_B2_APPLICATION_KEY: "b2-write-secret-value",
  OFFSITE_B2_READ_KEY_ID: "b2-read-id", OFFSITE_B2_READ_KEY: "b2-read-secret-value",
};
const SECRETS = Object.entries(STORED).filter(([name]) => /KEY|SECRET/.test(name))
  .map(([, value]) => value);
const START = new Date("2026-11-15T05:37:12.345Z");
/** The monthly night that drills a restore from the copy. */
const DRILL_NIGHT = "37 6 1 * *";

type Json = Record<string, unknown>;

/** The bucket as Backblaze lists it, kept as the copy's rules want it. */
const KEPT: Json = {
  bucketName: BUCKET, bucketType: "allPrivate",
  fileLockConfiguration: { isClientAuthorizedToRead: true, value: {
    isFileLockEnabled: true, defaultRetention: { mode: "compliance",
                                                 period: { duration: 22, unit: "days" } } } },
  lifecycleRules: [{ fileNamePrefix: "", daysFromHidingToDeleting: 22,
                     daysFromUploadingToHiding: null }],
};

interface Call { readonly args: string[]; readonly env: Record<string, string>;
                 readonly sample?: string[] }

/**
 * A fake Backblaze and rclone: the keys hold `capabilities` (each its own by default) on
 * `buckets`, the bucket is `bucket`, R2 lists `names` and the copy `names` and `gone`, a copy
 * back's listings are `back` and its erasure marks' `marks` (by remote: the copy at the time
 * asked, as it stands, R2), and the rclone call whose arguments start with `failing` exits 7.
 */
function world(options: { readonly capabilities?: Partial<Record<"write" | "read", string[]>>;
                          readonly buckets?: (string | null)[]; readonly prefix?: string;
                          readonly bucket?: Json; readonly signIn?: number;
                          readonly gone?: string[]; readonly names?: string[];
                          readonly failing?: string;
                          readonly back?: { readonly copy: string[]; readonly r2: string[] };
                          readonly marks?: Partial<Record<"back" | "now" | "r2",
                                                          readonly string[]>> }
               = {}) {
  const calls: Call[] = [];
  const asked: string[] = [];
  const keyOf: Record<string, "write" | "read"> =
    { [STORED["OFFSITE_B2_KEY_ID"]!]: "write", [STORED["OFFSITE_B2_READ_KEY_ID"]!]: "read" };
  const fetch: Effects["fetch"] = async (url, init) => {
    const headers = new Headers(init?.headers);
    if (url === "https://api.backblazeb2.com/b2api/v3/b2_authorize_account") {
      const [id, secret] = atob(headers.get("authorization")!.slice("Basic ".length)).split(":");
      const key = keyOf[id!]!;
      asked.push(`sign in ${key}`);
      expect(secret).toBe(STORED[key === "write" ? "OFFSITE_B2_APPLICATION_KEY"
        : "OFFSITE_B2_READ_KEY"]);
      if (options.signIn !== undefined) {
        return Response.json({ code: "unauthorized" }, { status: options.signIn });
      }
      return Response.json({
        accountId: "b2-account", authorizationToken: `token-${key}`,
        apiInfo: { storageApi: {
          apiUrl: "https://api005.backblazeb2.com",
          allowed: { buckets: (options.buckets ?? [BUCKET]).map((name) => ({ id: "b1", name })),
                     capabilities: options.capabilities?.[key] ?? [...CAPABILITIES[key]],
                     namePrefix: options.prefix ?? null } } } });
    }
    if (url === "https://api005.backblazeb2.com/b2api/v3/b2_list_buckets") {
      asked.push(`list as ${headers.get("authorization")}`);
      expect(JSON.parse(String(init!.body))).toEqual({ accountId: "b2-account",
                                                       bucketName: BUCKET });
      return Response.json({ buckets: [options.bucket ?? KEPT] });
    }
    throw new Error(`the fake Backblaze has no ${url}`);
  };
  const spawn = ((command: string, args: string[], spawned: { env: Record<string, string> }) => {
    expect(command).toBe("rclone");
    const env = Object.fromEntries(Object.entries(spawned.env)
      .filter(([name]) => name.startsWith("RCLONE_")));
    const at = args.indexOf("--files-from");
    calls.push({ args, env, ...(at === -1 ? {}
      : { sample: readFileSync(args[at + 1]!, "utf8").split("\n").filter(Boolean) }) });
    if (options.failing !== undefined && args.join(" ").startsWith(options.failing)) {
      return { status: 7, stdout: "" };
    }
    if (args[0] === "size") {
      const count = args.at(-1)!.startsWith("copy:") ? 3000 : 5000;
      return { status: 0, stdout: JSON.stringify({ count, bytes: count * 10, sizeless: 0 }) };
    }
    if (args[0] === "lsf" && args.includes("--include")) {
      const remote = args.at(-1)!.split(":")[0] as "back" | "now" | "r2";
      return { status: 0, stdout: (options.marks?.[remote] ?? []).join("\n") };
    }
    if (args[0] === "lsf" && /^(back|r2):/.test(args.at(-1)!)) {
      return { status: 0, stdout: (args.at(-1)!.startsWith("back:") ? options.back?.copy
        : options.back?.r2)?.join("\n") ?? "" };
    }
    if (args[0] === "lsf") {
      const names = options.names ?? ["o/aa/c/1", "o/aa/m/2"];
      return { status: 0, stdout: (args.at(-1)!.startsWith("copy:")
        ? [...names, ...options.gone ?? []] : names).join("\n") };
    }
    return { status: 0, stdout: "" };
  }) as unknown as Effects["spawn"];
  const io: Effects = { fetch, spawn, now: () => START };
  return { io, calls, asked };
}

const SET = settings(STORED)!;
const run = (fake: ReturnType<typeof world>) => copy(SET, SOURCE, fake.io);

describe("the copy's settings", () => {
  it("are none, all, or a loud stop naming what is missing", () => {
    expect(settings({})).toBeNull();
    expect(settings({ CLOUDFLARE_ACCOUNT_ID: ACCOUNT })).toBeNull();
    expect(SET).toMatchObject({ bucket: BUCKET, account: ACCOUNT,
                                write: { id: "b2-write-id", secret: "b2-write-secret-value" },
                                read: { id: "b2-read-id", secret: "b2-read-secret-value" },
                                r2: { id: "r2-id", secret: "r2-secret-value" } });
    const { OFFSITE_B2_READ_KEY: _, ...part } = STORED;
    expect(() => settings(part)).toThrow(/configured in part: OFFSITE_B2_READ_KEY not set/);
    expect(() => settings({ ...STORED, OFFSITE_B2_KEY_ID: "b2-write-id\r" }))
      .toThrow(/OFFSITE_B2_KEY_ID hold a space or a control character/);
    expect(() => settings({ ...STORED, OFFSITE_BUCKET: "other:bucket/x" }))
      .toThrow(/OFFSITE_BUCKET is no B2 bucket name/);
    expect(() => settings({ ...STORED, CLOUDFLARE_ACCOUNT_ID: "" }))
      .toThrow(/CLOUDFLARE_ACCOUNT_ID is no 32-hex account id/);
  });
});

describe("the copy's keys and bucket", () => {
  it("are held before rclone runs: each key exactly its capabilities on the one bucket, the " +
     "bucket private, locked and keeping prior versions inside the erasure's window", async () => {
    const fake = world();
    await run(fake);
    expect(fake.asked).toEqual(["sign in write", "sign in read", "list as token-read"]);
    expect(CAPABILITIES.write).not.toContain("deleteFiles");
    expect(CAPABILITIES.write).not.toContain("readFiles");
  });

  const lock = (retention: Json) => ({ ...KEPT, fileLockConfiguration: {
    isClientAuthorizedToRead: true,
    value: { isFileLockEnabled: true, defaultRetention: retention } } });
  const refusals: [string, Parameters<typeof world>[0], RegExp][] = [
    ["a write key that can delete",
     { capabilities: { write: [...CAPABILITIES.write, "deleteFiles"] } },
     /OFFSITE_B2_KEY_ID and OFFSITE_B2_APPLICATION_KEY holds .*deleteFiles.*exactly listBuckets,/],
    ["a write key that can read", { capabilities: { write: [...CAPABILITIES.write, "readFiles"] } },
     /OFFSITE_B2_KEY_ID .*holds .*readFiles/],
    ["a read key that can write", { capabilities: { read: [...CAPABILITIES.read, "writeFiles"] } },
     /OFFSITE_B2_READ_KEY_ID .*holds .*writeFiles/],
    ["a key on every bucket", { buckets: [] }, /on every bucket; it must hold exactly/],
    ["a key on a name prefix", { prefix: "o/" }, /with no name prefix/],
    ["a key Backblaze refuses", { signIn: 401 },
     /Backblaze refused the key OFFSITE_B2_KEY_ID .*\(401\)/],
    ["a public bucket", { bucket: { ...KEPT, bucketType: "allPublic" } },
     /its files are not private/],
    ["a lock the read key can't read", { bucket: { ...KEPT, fileLockConfiguration: {
      isClientAuthorizedToRead: false, value: null } } }, /can't read its lock/],
    ["no lock", { bucket: lock({ mode: null, period: null }) }, /not compliance, 22 days/],
    ["a governance lock", { bucket: lock({ mode: "governance",
                                           period: { duration: 22, unit: "days" } }) },
     /not compliance, 22 days/],
    ["a lock past the window", { bucket: lock({ mode: "compliance",
                                                period: { duration: 23, unit: "days" } }) },
     /not compliance, 22 days/],
    ["no lifecycle", { bucket: { ...KEPT, lifecycleRules: [] } }, /its lifecycle is not one rule/],
    ["prior versions kept past the window", { bucket: { ...KEPT, lifecycleRules: [
      { fileNamePrefix: "", daysFromHidingToDeleting: 23, daysFromUploadingToHiding: null }] } },
     /keeping prior versions 22 days/],
    ["a rule on a prefix alone", { bucket: { ...KEPT, lifecycleRules: [
      { fileNamePrefix: "o/", daysFromHidingToDeleting: 22, daysFromUploadingToHiding: null }] } },
     /its lifecycle is not one rule over every name/],
    ["a rule that hides what R2 still holds", { bucket: { ...KEPT, lifecycleRules: [
      { fileNamePrefix: "", daysFromHidingToDeleting: 22, daysFromUploadingToHiding: 7 }] } },
     /its lifecycle is not one rule/],
  ];
  for (const [what, options, refused] of refusals) {
    it(`stop the night, copying nothing, on ${what}`, async () => {
      const fake = world(options);
      await expect(run(fake)).rejects.toThrow(refused);
      expect(fake.calls).toEqual([]);
    });
  }
});

describe("the nightly copy", () => {
  it("syncs R2 to B2, hiding every deletion, then checks what R2 held when the run began, " +
     "and a sample byte for byte", async () => {
    const fake = world();
    const night = await run(fake);
    const { lines } = night;
    const began = "2026-11-15T05:37:12Z";
    expect(night.began).toBe(began);
    const times = ["--use-server-modtime", "--fast-list"];
    expect(fake.calls.map((call) => call.args)).toEqual([
      ["lsf", "--files-only", "-R", ...times, `copy:${BUCKET}`],
      ["lsf", "--files-only", "-R", ...times, `source:${SOURCE}`],
      ["sync", `source:${SOURCE}`, `copy:${BUCKET}`, ...times,
       "--stats-one-line", "--stats", "1m", "--stats-log-level", "NOTICE"],
      ["check", `source:${SOURCE}`, `copy:${BUCKET}`, "--one-way", "--size-only", ...times,
       "--min-age", began],
      ["lsf", "--files-only", "-R", ...times, "--min-age", began, `source:${SOURCE}`],
      ["check", `source:${SOURCE}`, `check:${BUCKET}`, "--one-way", "--download",
       "--files-from", expect.stringMatching(/names$/)],
      ["size", "--json", ...times, `source:${SOURCE}`],
      ["size", "--json", ...times, `copy:${BUCKET}`],
    ]);
    expect(fake.calls[5]!.sample!.sort()).toEqual(["o/aa/c/1", "o/aa/m/2"]);
    expect(lines).toEqual([
      `R2 ${SOURCE}: 5000 objects, 50000 bytes`,
      `B2 ${BUCKET}: 3000 objects, 30000 bytes (2 before the run, 0 of them hidden)`,
      `every object R2 held at ${began} is in the copy at its size; 2 compared byte for byte`]);
  });

  it("gives each rclone call the remotes it reads and no other, and no credential as an " +
     "argument", async () => {
    const fake = world();
    await run(fake);
    const remotes = (call: Call) => [...new Set(Object.keys(call.env)
      .filter((name) => name.startsWith("RCLONE_CONFIG_") && name !== "RCLONE_CONFIG")
      .map((name) => name.split("_")[2]))].sort();
    expect(fake.calls.map(remotes)).toEqual([
      ["COPY"], ["SOURCE"], ["COPY", "SOURCE"], ["COPY", "SOURCE"], ["SOURCE"],
      ["CHECK", "SOURCE"], ["SOURCE"], ["COPY"]]);
    expect(fake.calls[2]!.env).toEqual({
      RCLONE_CONFIG: "/dev/null",
      RCLONE_CONFIG_SOURCE_TYPE: "s3", RCLONE_CONFIG_SOURCE_PROVIDER: "Cloudflare",
      RCLONE_CONFIG_SOURCE_NO_CHECK_BUCKET: "true",
      RCLONE_CONFIG_SOURCE_ENDPOINT: `https://${ACCOUNT}.r2.cloudflarestorage.com`,
      RCLONE_CONFIG_SOURCE_ACCESS_KEY_ID: "r2-id",
      RCLONE_CONFIG_SOURCE_SECRET_ACCESS_KEY: "r2-secret-value",
      RCLONE_CONFIG_COPY_TYPE: "b2", RCLONE_CONFIG_COPY_ACCOUNT: "b2-write-id",
      RCLONE_CONFIG_COPY_KEY: "b2-write-secret-value", RCLONE_CONFIG_COPY_HARD_DELETE: "false",
    });
    expect(fake.calls[5]!.env).toMatchObject({ RCLONE_CONFIG_CHECK_ACCOUNT: "b2-read-id",
                                               RCLONE_CONFIG_CHECK_KEY: "b2-read-secret-value" });
    for (const call of fake.calls) {
      expect(SECRETS.filter((secret) => call.args.some((arg) => arg.includes(secret))))
        .toEqual([]);
    }
  });

  it("fails a night that hid more than a tenth of the copy (at least 100), once every hide is " +
     "made and checked", async () => {
    const named = (count: number, at: string) =>
      Array.from({ length: count }, (_, each) => `o/${at}/c/${each}`);
    for (const [kept, gone, refused] of [
      [0, 100, null], [0, 101, "the copy hid 101 of its 101 objects, past 100"],
      [4050, 450, null], [4050, 451, "the copy hid 451 of its 4501 objects, past 450"],
    ] as const) {
      const fake = world({ names: named(kept, "aa"), gone: named(gone, "bb") });
      const ran = run(fake);
      await (refused === null ? expect(ran).resolves.toBeDefined()
        : expect(ran).rejects.toThrow(`${refused}: a mass deletion in R2? Each hidden version ` +
                                      "stays 22 days"));
      expect(fake.calls.map((call) => call.args[0]))
        .toEqual(["lsf", "lsf", "sync", "check", "lsf", "check", "size", "size"]);
    }
  });

  it("samples 16 of what R2 held, and none of an empty bucket", async () => {
    const names = Array.from({ length: 40 }, (_, at) => `o/aa/c/${at}`);
    const many = world({ names });
    await run(many);
    const sample = many.calls.find((call) => call.sample !== undefined)!.sample!;
    expect([sample.length, new Set(sample).size,
            sample.every((name) => names.includes(name))]).toEqual([16, 16, true]);
    const empty = world({ names: [] });
    expect((await run(empty)).lines.at(-1)).toMatch(/0 compared byte for byte$/);
    expect(empty.calls.find((call) => call.sample !== undefined)!.sample).toEqual([]);
  });

  for (const [step, failing, refused] of [
    ["the sync", "sync", /the copy's sync failed \(rclone exit 7/],
    ["the check", "check source:tinyassets-files copy:", /the copy's check of every object failed/],
    ["the byte check", "check source:tinyassets-files check:", /byte check of a sample failed/],
    ["the listing", "lsf", /listing of the copy failed/],
  ] as const) {
    it(`stops loudly when ${step} fails`, async () => {
      await expect(run(world({ failing }))).rejects.toThrow(refused);
    });
  }
});

describe("a copy back from the copy (R8)", () => {
  const NS = "a".repeat(32);
  const PREFIX = `o/${NS}/`;
  const WRITE = { OFFSITE_R2_WRITE_ACCESS_KEY_ID: "r2-write-id",
                  OFFSITE_R2_WRITE_SECRET_ACCESS_KEY: "r2-write-secret-value" };
  const BACK = backFrom({ ...STORED, ...WRITE }, ACCOUNT);
  const AT = new Date("2026-11-01T00:00:00Z");
  /** The control namespace as Cloudflare answers the record's put with `status`. */
  const control = (fake: ReturnType<typeof world>, status = 200) => {
    const puts: unknown[] = [];
    const held: RestoreControl = { account: ACCOUNT, namespace: "control-ns", cloudflare: {
      put: async (account, namespace, key, value) => {
        expect([account, namespace, key]).toEqual([ACCOUNT, "control-ns", RESTORE_KEY]);
        // Where the put falls among rclone's calls.
        puts.push({ value, after: fake.calls.map((call) => call.args[0]) });
        return status;
      } } };
    return { held, puts };
  };
  const lacking = { copy: ["c/1", "c/2", "m/3"], r2: ["c/1"] };
  /** The erasure marks' three listings: the copy as it stands, as it stood, and R2. */
  const MARKS_LISTED = ["now", "back", "r2"].map((remote) =>
    ["lsf", "--files-only", "-R", "--include", "/e/*",
     `${remote}:${remote === "r2" ? SOURCE : BUCKET}`]);
  const mark = (ns: string) => `e/${ns}`;
  const OTHER = "b".repeat(32);
  /** What the owner's object names: the chunks and generations given, the same each ask. */
  const naming = (chunks: string[] = ["1", "2"], generations: string[] = ["3"]) =>
    async () => ({ chunks: new Set(chunks), generations: new Set(generations) });

  it("records the restore before its first byte, then writes only what R2 lacks, as the " +
     "copy stood at the time asked, and reads each back byte for byte", async () => {
    const fake = world({ back: lacking });
    const { held, puts } = control(fake);
    const lines = await copyBack(BACK, SOURCE, NS, AT, naming(), held, fake.io);
    expect(puts).toEqual([{ value: { kind: "tinyassets-offsite-restore",
                                     began: Math.floor(START.getTime() / 1000) },
                            after: ["lsf", "lsf", "lsf", "lsf", "lsf"] }]);
    expect(fake.calls.map((call) => [call.args, call.sample])).toEqual([
      ...MARKS_LISTED.map((args) => [args, undefined]),
      [["lsf", "--files-only", "-R", "--use-server-modtime", "--fast-list",
        `r2:${SOURCE}/${PREFIX}`], undefined],
      [["lsf", "--files-only", "-R", "--use-server-modtime", "--fast-list",
        `back:${BUCKET}/${PREFIX}`], undefined],
      [["copy", `back:${BUCKET}/${PREFIX}`, `r2:${SOURCE}/${PREFIX}`, "--files-from",
        expect.stringMatching(/names$/), "--ignore-existing", "--stats-one-line", "--stats", "1m",
        "--stats-log-level", "NOTICE"], ["c/2", "m/3"]],
      [["check", `back:${BUCKET}/${PREFIX}`, `r2:${SOURCE}/${PREFIX}`, "--one-way", "--download",
        "--files-from", expect.stringMatching(/names$/)], ["c/2", "m/3"]]]);
    // The copy as it stood at the time, with the read key; R2 with the write pair alone.
    expect(fake.calls.find((call) => call.args[0] === "copy")!.env).toEqual({
      RCLONE_CONFIG: "/dev/null",
      RCLONE_CONFIG_BACK_TYPE: "b2", RCLONE_CONFIG_BACK_ACCOUNT: "b2-read-id",
      RCLONE_CONFIG_BACK_KEY: "b2-read-secret-value",
      RCLONE_CONFIG_BACK_VERSION_AT: "2026-11-01T00:00:00Z",
      RCLONE_CONFIG_R2_TYPE: "s3", RCLONE_CONFIG_R2_PROVIDER: "Cloudflare",
      RCLONE_CONFIG_R2_NO_CHECK_BUCKET: "true",
      RCLONE_CONFIG_R2_ENDPOINT: `https://${ACCOUNT}.r2.cloudflarestorage.com`,
      RCLONE_CONFIG_R2_ACCESS_KEY_ID: "r2-write-id",
      RCLONE_CONFIG_R2_SECRET_ACCESS_KEY: "r2-write-secret-value" });
    for (const call of fake.calls) {
      expect([...SECRETS, ...Object.values(WRITE)]
        .filter((secret) => call.args.some((arg) => arg.includes(secret)))).toEqual([]);
    }
    expect(lines.at(-1)).toBe(`wrote 2 objects under ${PREFIX} into ${SOURCE}, as the copy ` +
                              "held them at 2026-11-01T00:00:00Z, each one R2 lacked and the " +
                              "owner's object names, and read each back byte for byte (0 more " +
                              "it lacks no row names)");
  });

  it("never writes back what no row of the owner's object names: a chunk its own collection " +
     "deleted, or a pruned generation, stays out of R2", async () => {
    // The copy still holds a chunk collected past its grace (or a redaction's) and a pruned
    // generation's root and part; the object names neither.
    const fake = world({ back: { copy: ["c/1", "c/2", "c/collected", "m/3", "m/3/0",
                                        "m/pruned", "m/pruned/0"], r2: ["c/1"] } });
    const lines = await copyBack(BACK, SOURCE, NS, AT, naming(), control(fake).held, fake.io);
    expect(fake.calls.filter((call) => call.args[0] !== "lsf").map((call) => call.sample))
      .toEqual([["c/2", "m/3", "m/3/0"], ["c/2", "m/3", "m/3/0"]]);
    expect(lines.at(-1)).toMatch(/wrote 3 objects .* \(3 more it lacks no row names\)$/);
    // Nothing it names lacking: nothing written, nothing recorded.
    const none = world({ back: { copy: ["c/1", "c/collected"], r2: ["c/1"] } });
    const { held, puts } = control(none);
    expect((await copyBack(BACK, SOURCE, NS, AT, naming(), held, none.io)).at(-1))
      .toMatch(/nothing is written, and no restore is recorded$/);
    expect([puts, none.calls.map((call) => call.args[0])])
      .toEqual([[], ["lsf", "lsf", "lsf", "lsf", "lsf"]]);
  });

  it("stops loudly when the object stops naming what was written, its row collected during " +
     "the run", async () => {
    const fake = world({ back: lacking });
    let asks = 0;
    const named = async () => (asks++ === 0 ? naming() : naming(["1"]))();
    await expect(copyBack(BACK, SOURCE, NS, AT, named, control(fake).held, fake.io))
      .rejects.toThrow(`the owner's object no longer names 1 of the 2 objects written under ` +
                       `${PREFIX}, collected during the run: only mode restore's sweep deletes ` +
                       "them");
  });

  it("reads the copy as it stands when no time is asked", async () => {
    const fake = world({ back: lacking });
    await copyBack(BACK, SOURCE, NS, null, naming(), control(fake).held, fake.io);
    expect(Object.keys(fake.calls.find((call) => call.args[0] === "copy")!.env))
      .not.toContain("RCLONE_CONFIG_BACK_VERSION_AT");
    // The marks read as the copy stands (the one view) and in R2.
    expect(fake.calls.slice(0, 2).map((call) => call.args.at(-1)))
      .toEqual([`back:${BUCKET}`, `r2:${SOURCE}`]);
  });

  it("writes nothing when Cloudflare refuses the restore record", async () => {
    const fake = world({ back: lacking });
    await expect(copyBack(BACK, SOURCE, NS, AT, naming(), control(fake, 500).held, fake.io))
      .rejects.toThrow(`writing the restore record ${RESTORE_KEY} failed (500): nothing is ` +
                       "written into R2");
    expect(fake.calls.map((call) => call.args[0])).toEqual(["lsf", "lsf", "lsf", "lsf", "lsf"]);
  });

  it("writes nothing of a namespace the files bucket marks erased, in the copy or in R2 alone, " +
     "and records no restore", async () => {
    for (const marks of [{ now: [mark(OTHER), mark(NS)] }, { r2: [mark(NS)] },
                         { now: [mark(NS)], back: [mark(NS)] }]) {
      const fake = world({ back: lacking, marks });
      const { held, puts } = control(fake);
      let asked = 0;
      await expect(copyBack(BACK, SOURCE, NS, AT, async () => (asked++, naming()()), held,
                            fake.io))
        .rejects.toThrow(`the files bucket marks the namespace under ${PREFIX} erased: nothing ` +
                         "of it is written, and no restore is recorded");
      expect([puts, asked, fake.calls.filter((call) => call.args[0] !== "lsf")])
        .toEqual([[], 0, []]);
    }
    // Another namespace's mark leaves this one's copy back as it was.
    const fake = world({ back: lacking, marks: { now: [mark(OTHER)], r2: [mark(OTHER)] } });
    expect((await copyBack(BACK, SOURCE, NS, AT, naming(), control(fake).held, fake.io)).at(-1))
      .toMatch(/^wrote 2 objects/);
  });

  it("refuses, writing and recording nothing, when the marks can't be read: a listing that " +
     "fails, a name that is no mark, or a mark the copy held at the time and no longer holds",
     async () => {
    for (const [options, refused] of [
      [{ failing: "lsf --files-only -R --include /e/* now:" },
       /the copy's listing of the erasure marks in the copy failed \(rclone exit 7/],
      [{ failing: "lsf --files-only -R --include /e/* r2:" },
       /the copy's listing of the erasure marks in R2 failed/],
      [{ marks: { r2: ["e/not-a-namespace"] } },
       "the erasure marks in R2 can't be read: it holds a name under e/ that is no " +
       "namespace's mark; nothing is written"],
      [{ marks: { back: [mark(OTHER)] } },
       "the copy as it stands lacks 1 erasure marks it held at the time asked: a night copied " +
       "a files bucket that had lost them, so its marks are not whole; nothing is written"],
    ] as const) {
      const fake = world({ back: lacking, ...options });
      const { held, puts } = control(fake);
      await expect(copyBack(BACK, SOURCE, NS, AT, naming(), held, fake.io))
        .rejects.toThrow(refused);
      expect([puts, fake.calls.filter((call) => call.args[0] !== "lsf")]).toEqual([[], []]);
    }
  });

  it("refuses a time outside the copy's window, and a missing key, before anything", async () => {
    for (const at of [new Date(START.getTime() - BACK_DAYS * 86_400_000 - 1000),
                      new Date(START.getTime() + 1000)]) {
      const fake = world({ back: lacking });
      const { held, puts } = control(fake);
      await expect(copyBack(BACK, SOURCE, NS, at, naming(), held, fake.io))
        .rejects.toThrow(/a version older than 22 days since it was hidden is gone/);
      expect([fake.calls, puts]).toEqual([[], []]);
    }
    for (const name of Object.keys(WRITE)) {
      expect(() => backFrom({ ...STORED, ...WRITE, [name]: "" }, ACCOUNT))
        .toThrow(`a copy back needs ${name} (platform/deploy/checklist.md, "A restore from ` +
                 "the copy\"); nothing is read or written");
    }
  });
});

describe("the monthly drill", () => {
  it("compares every object R2 held at the night's start byte for byte, then reads what a " +
     "copy back at the oldest time it takes would write, writing nothing", () => {
    const [aa, bb, erased] = ["a", "b", "e"].map((hex) => hex.repeat(32));
    // The copy as it stood holds an erased namespace's objects, and a mark R2 lost.
    const fake = world({ back: { copy: [`o/${aa}/c/1`, `o/${aa}/c/2`, `o/${bb}/m/3`,
                                        `o/${erased}/c/4`, `o/${erased}/m/5`, `e/${bb}`],
                                 r2: [`o/${aa}/c/1`] },
                         marks: { now: [`e/${erased}`, `e/${bb}`], back: [`e/${bb}`],
                                  r2: [`e/${erased}`] } });
    const night = { began: "2026-11-15T05:37:12Z", lines: [] };
    const lines = drill(SET, SOURCE, night, fake.io);
    expect(fake.calls.map((call) => call.args)).toEqual([
      ["check", `source:${SOURCE}`, `check:${BUCKET}`, "--one-way", "--download", "--min-age",
       night.began],
      ["lsf", "--files-only", "-R", "--include", "/e/*", `now:${BUCKET}`],
      ["lsf", "--files-only", "-R", "--include", "/e/*", `back:${BUCKET}`],
      ["lsf", "--files-only", "-R", "--include", "/e/*", `r2:${SOURCE}`],
      ["lsf", "--files-only", "-R", "--use-server-modtime", "--fast-list", `r2:${SOURCE}/`],
      ["lsf", "--files-only", "-R", "--use-server-modtime", "--fast-list", `back:${BUCKET}/`],
      ["cat", "--discard", "--files-from", expect.stringMatching(/names$/), `back:${BUCKET}/`]]);
    // The marked namespace's objects left out; bb is marked too, and its mark written first.
    expect(fake.calls[6]!.sample!.sort()).toEqual([`e/${bb}`, `o/${aa}/c/2`]);
    // The copy as it stood BACK_DAYS ago, and R2 with the nightly's read pair.
    expect(fake.calls[5]!.env["RCLONE_CONFIG_BACK_VERSION_AT"]).toBe("2026-10-25T05:37:12Z");
    expect(fake.calls[4]!.env["RCLONE_CONFIG_R2_ACCESS_KEY_ID"]).toBe("r2-id");
    expect(lines).toEqual([
      "every object R2 held at 2026-11-15T05:37:12Z is in the copy, compared byte for byte",
      "a copy back as the copy stood at 2026-10-25T05:37:12Z would write 2 objects R2 no " +
      "longer holds, and leave out 3 of namespaces marked erased (2 marks); 2 of them read " +
      "back whole"]);
  });

  it("stops loudly on a name in the copy that is neither a namespace's object nor a mark, " +
     "and when its marks can't be read", () => {
    const night = { began: "2026-11-15T05:37:12Z", lines: [] };
    expect(() => drill(SET, SOURCE, night, world({ back: { copy: ["x/1"], r2: [] } }).io))
      .toThrow("the copy holds an object that is neither a namespace's nor a mark (under x/), " +
               "which nothing in the files bucket writes");
    expect(() => drill(SET, SOURCE, night, world({ marks: { now: ["e/1"] } }).io))
      .toThrow("the erasure marks in the copy can't be read");
  });

  it("stops loudly when its byte check fails", () => {
    expect(() => drill(SET, SOURCE, { began: "2026-11-15T05:37:12Z", lines: [] },
                       world({ failing: "check" }).io))
      .toThrow(/the copy's byte check of every object failed \(rclone exit 7/);
  });
});

describe("offsite-copy.yml", () => {
  const flow = workflow("offsite-copy.yml");
  const { [NIGHTLY.settings]: settingsJob, [NIGHTLY.copy]: copyJob } = flow.jobs;
  const step = (job: typeof flow, name: string) =>
    job.steps.find((each: { name?: string }) => each.name === name);
  const read = step(settingsJob, "Read the copy's settings");
  const copied = step(copyJob, "Copy the files bucket to B2");

  it("runs nightly off the hour, one at a time, both jobs in production, the copy only " +
     "when its settings are stored", () => {
    expect(flow.on.schedule).toEqual([{ cron: "37 5 * * *" }, { cron: DRILL_NIGHT }]);
    expect(Object.keys(flow.on)).toEqual(["schedule", "workflow_dispatch"]);
    expect(flow.concurrency).toEqual({ group: "offsite-copy", "cancel-in-progress": false });
    // The canary reads the nights by these two jobs' names (`watch`), which a `name:` would
    // replace in GitHub's listing.
    expect(Object.keys(flow.jobs)).toEqual([NIGHTLY.settings, NIGHTLY.copy]);
    expect([settingsJob.name, copyJob.name]).toEqual([undefined, undefined]);
    expect(workflow(NIGHTLY.workflow)).toEqual(flow);
    expect(flow.permissions).toEqual({ contents: "read" });
    expect([settingsJob.environment, copyJob.environment]).toEqual(["production", "production"]);
    expect(settingsJob.outputs).toEqual({ configured: "${{ steps.settings.outputs.configured }}" });
    expect(read.id).toBe("settings");
    expect([copyJob.needs, copyJob.if])
      .toEqual(["settings", "needs.settings.outputs.configured == 'true'"]);
    // A dark night installs nothing: the settings job only checks out and reads.
    expect(settingsJob.steps.map((each: { uses?: string; name?: string }) =>
      each.uses ?? each.name))
      .toEqual(["actions/checkout@v4", "actions/setup-node@v4", "Read the copy's settings"]);
  });

  it("hands both jobs every setting, from the store it is kept in", () => {
    const variables = ["OFFSITE_BUCKET", "CLOUDFLARE_ACCOUNT_ID"];
    expect(flow.env).toEqual(Object.fromEntries([...SETTINGS, "CLOUDFLARE_ACCOUNT_ID"].map(
      (name) => [name, `\${{ ${variables.includes(name) ? "vars" : "secrets"}.${name} }}`])));
    expect(copied.run)
      .toBe('node --experimental-strip-types platform/deploy/offsite.ts "${COMMAND}"');
  });

  it("drills on the monthly night, or a dispatch that asks, and copies on every other", () => {
    expect(flow.on.workflow_dispatch.inputs.drill).toMatchObject({ type: "boolean",
                                                                   default: false });
    expect(copied.env).toEqual({ COMMAND: `\${{ (github.event.schedule == '${DRILL_NIGHT}' || ` +
                                          "inputs.drill) && 'drill' || 'copy' }}" });
  });

  it("runs the rclone the one action pins, by its digest, before each step that runs it", () => {
    const action = parse(readFileSync(join(REPO, ".github/actions/rclone/action.yml"), "utf8"));
    const [install] = action.runs.steps;
    expect(action.runs.steps).toHaveLength(1);
    expect(install.env.RCLONE).toMatch(/^v\d+\.\d+\.\d+$/);
    expect(install.env.RCLONE_SHA256).toMatch(/^[0-9a-f]{64}$/);
    expect(install.run).toContain('| sha256sum -c -');
    const uses = (job: typeof flow) =>
      job.steps.findIndex((each: { uses?: string }) => each.uses === "./.github/actions/rclone");
    const at = (job: typeof flow, text: string) =>
      job.steps.findIndex((each: { run?: string }) => each.run?.includes(text));
    expect([uses(copyJob) >= 0, uses(copyJob) < at(copyJob, "offsite.ts")]).toEqual([true, true]);
    const { apply } = workflow("platform-deploy.yml").jobs;
    expect(apply.steps[uses(apply)].if).toBe("inputs.mode == 'copyback'");
    expect(uses(apply)).toBeLessThan(at(apply, "deploy.ts"));
  });

  const folder = mkdtempSync(join(tmpdir(), "offsite-workflow-"));
  afterAll(() => rmSync(folder, { recursive: true, force: true }));
  /** The settings step run as the job runs it, with `stored` as its secrets and variables. */
  const settle = (stored: Record<string, string>, at: string) => {
    const output = join(folder, `${at}.output`);
    const summary = join(folder, `${at}.summary`);
    writeFileSync(output, "");
    writeFileSync(summary, "");
    const ran = spawnSync("bash", ["-c", read.run], {
      cwd: REPO, encoding: "utf8",
      env: { PATH: `${dirname(process.execPath)}:/usr/bin:/bin`, GITHUB_ACTIONS: "true",
             GITHUB_OUTPUT: output,
             GITHUB_STEP_SUMMARY: summary,
             ...Object.fromEntries(Object.keys(flow.env).flatMap((name) =>
               stored[name] === undefined ? [] : [[name, stored[name]]])) } });
    return { status: ran.status, stdout: ran.stdout, stderr: ran.stderr,
             output: readFileSync(output, "utf8"), summary: readFileSync(summary, "utf8") };
  };

  it("passes a dark night, noting the copy is not configured", () => {
    const dark = settle({ CLOUDFLARE_ACCOUNT_ID: ACCOUNT }, "dark");
    expect([dark.status, dark.output, dark.stderr]).toEqual([0, "configured=false\n", ""]);
    expect(dark.stdout)
      .toMatch(/^::notice title=Off-account copy::not configured: none of OFFSITE_BUCKET, /);
    expect(dark.summary).toMatch(/^### The off-account copy: not configured/);
  });

  it("runs the copy once every setting is stored", () => {
    const stored = settle(STORED, "stored");
    expect([stored.status, stored.output, stored.summary]).toEqual([0, "configured=true\n", ""]);
  });

  it("fails a night whose settings are stored in part, or out of shape, naming them", () => {
    const { OFFSITE_R2_SECRET_ACCESS_KEY: _, ...part } = STORED;
    const partial = settle(part, "part");
    expect([partial.status, partial.output]).toEqual([1, ""]);
    expect(partial.stderr).toContain("::error title=Off-account copy::the off-account copy is " +
                                     "configured in part: OFFSITE_R2_SECRET_ACCESS_KEY not set");
    const spaced = settle({ ...STORED, OFFSITE_B2_READ_KEY: "with space" }, "spaced");
    expect([spaced.status, spaced.output]).toEqual([1, ""]);
    expect(spaced.stderr).toMatch(/OFFSITE_B2_READ_KEY hold a space/);
  });
});
