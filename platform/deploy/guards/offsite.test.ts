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

import { REPO } from "../../ci/paths";
import { workflow } from "../../ci/workflows";
import { CAPABILITIES, copy, type Effects, NIGHTLY, SETTINGS, settings } from "../offsite";

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
 * `buckets`, the bucket is `bucket`, R2 lists `names` and the copy `names` and `gone`, and
 * the rclone call whose arguments start with `failing` exits 7.
 */
function world(options: { readonly capabilities?: Partial<Record<"write" | "read", string[]>>;
                          readonly buckets?: (string | null)[]; readonly prefix?: string;
                          readonly bucket?: Json; readonly signIn?: number;
                          readonly gone?: string[]; readonly names?: string[];
                          readonly failing?: string } = {}) {
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
    const lines = await run(fake);
    const began = "2026-11-15T05:37:12Z";
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
       "--files-from", expect.stringMatching(/sample$/)],
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
    expect((await run(empty)).at(-1)).toMatch(/0 compared byte for byte$/);
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

describe("offsite-copy.yml", () => {
  const flow = workflow("offsite-copy.yml");
  const { [NIGHTLY.settings]: settingsJob, [NIGHTLY.copy]: copyJob } = flow.jobs;
  const step = (job: typeof flow, name: string) =>
    job.steps.find((each: { name?: string }) => each.name === name);
  const read = step(settingsJob, "Read the copy's settings");
  const copied = step(copyJob, "Copy the files bucket to B2");

  it("runs nightly off the hour, one at a time, both jobs in production, the copy only " +
     "when its settings are stored", () => {
    expect(flow.on.schedule).toEqual([{ cron: "37 5 * * *" }]);
    expect(flow.on).toEqual({ schedule: [{ cron: "37 5 * * *" }], workflow_dispatch: null });
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
    expect(copied.env).toBeUndefined();
    expect(copied.run).toBe("node --experimental-strip-types platform/deploy/offsite.ts copy");
  });

  it("runs the rclone it pins, by its digest", () => {
    const install = step(copyJob, "Install rclone, pinned by its digest");
    expect(install.env.RCLONE).toMatch(/^v\d+\.\d+\.\d+$/);
    expect(install.env.RCLONE_SHA256).toMatch(/^[0-9a-f]{64}$/);
    expect(install.run).toContain('| sha256sum -c -');
    const order = copyJob.steps.map((each: { name?: string }) => each.name);
    expect(order.indexOf("Install rclone, pinned by its digest"))
      .toBeLessThan(order.indexOf("Copy the files bucket to B2"));
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
