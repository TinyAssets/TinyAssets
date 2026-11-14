/**
 * The job's staging step (`stage.ts`, slice 4) on the slice-2 builder's own bundles, against
 * a fake of R2's S3 API and the Queues HTTP API: every bundle checked whole before anything is
 * sent; each uploaded under its run, its index last, signed (SigV4, held to AWS's own worked
 * example); one message an owner, MAC'd under the window's key over its owner and bundle; a
 * dry run that prints what it would send and sends nothing; and nothing printed but counts
 * and ordinals, never a credential, a name or a key. The live names are the deploy kit's, read
 * from the configs (`deploy/facts.ts`), as the window's `facts.json` hands them to the job.
 */
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { TIERS } from "../../ci/fixtures";
import { macHolds } from "../../contract/src/import-message";
import { IMPORT_NAMES } from "../../deploy/facts";
import { buildBundles } from "../bundle";
import { credentialsFrom, STAGE_INPUTS, stageInputs, stageMain } from "../cli";
import { type Fetcher, type Inputs, namesOf, sigV4, stage } from "../stage";
import { ALICE, BOB, fixture, FOUNDER, keyOf, NEWBIE } from "./fixture";

const temps: string[] = [];
afterEach(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

const INPUTS: Inputs = Object.freeze({
  account: "a".repeat(32), accessKeyId: "AKIDEXAMPLE0000", secretAccessKey: "s3-secret-not-real",
  apiToken: "api-token-not-real", importKey: Buffer.alloc(32, 7).toString("base64url") });
const SECRETS = [INPUTS.secretAccessKey, INPUTS.apiToken, INPUTS.importKey];
const NAMES = IMPORT_NAMES;

/** The window's `facts.json`, as its bundle step writes it (`facts.ts --import`). */
function factsFile(): string {
  const dir = mkdtempSync(join(tmpdir(), "ta-stage-facts-"));
  temps.push(dir);
  writeFileSync(join(dir, "facts.json"), JSON.stringify(IMPORT_NAMES));
  return join(dir, "facts.json");
}

/** The builder's bundles of the fixture, in a fresh out folder (a rehearsal's, for its run). */
function bundled(rehearsal: string | null = null): string {
  const f = fixture();
  const out = mkdtempSync(join(tmpdir(), "ta-stage-out-"));
  const priv = mkdtempSync(join(tmpdir(), "ta-stage-private-"));
  temps.push(f.work, out, priv);
  buildBundles(f.copy, { out, private: priv, exclude: new Set([FOUNDER]), tiers: TIERS,
                         rehearsal });
  return out;
}

/** A fake of the two APIs: what each request was, and the queue the account lists. */
function apis(options: { refuse?: string; queues?: unknown[] } = {}) {
  const calls: { method: string; url: string; auth: string; body: Buffer }[] = [];
  const fetcher: Fetcher = async (request) => {
    const body = Buffer.from(await request.arrayBuffer());
    calls.push({ method: request.method, url: request.url,
                 auth: request.headers.get("authorization") ?? "", body });
    if (options.refuse !== undefined && request.url.includes(options.refuse)) {
      return new Response("no", { status: 403 });
    }
    if (request.method === "GET") {
      return Response.json({ success: true, result: options.queues
        ?? [{ queue_id: "q-other", queue_name: NAMES.dead_letter_queue },
            { queue_id: "q-import", queue_name: NAMES.queue }] });
    }
    return request.url.startsWith("https://api.cloudflare.com/")
      ? Response.json({ success: true, result: null }) : new Response(null, { status: 200 });
  };
  return { calls, fetcher };
}

describe("the staging step's signature", () => {
  it("is AWS Signature Version 4, as AWS's own GET Object example signs it", () => {
    const empty = createHash("sha256").update("").digest("hex");
    const authorization = sigV4({
      method: "GET", path: "/test.txt", query: "",
      headers: { host: "examplebucket.s3.amazonaws.com", range: "bytes=0-9",
                 "x-amz-content-sha256": empty, "x-amz-date": "20130524T000000Z" },
      payload: empty, region: "us-east-1", service: "s3", amzDate: "20130524T000000Z",
      accessKeyId: "AKIAIOSFODNN7EXAMPLE",
      secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" });
    expect(authorization).toBe("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/" +
      "us-east-1/s3/aws4_request, SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, " +
      "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41");
  });
});

describe("the staging step", () => {
  it("uploads each bundle under its run, its index last, then sends one MAC'd message an owner",
     async () => {
    const out = bundled();
    const { calls, fetcher } = apis();
    const lines: string[] = [];
    const summary = await stage(out, "rehearsal-1", NAMES, INPUTS, fetcher,
                                (line) => lines.push(line),
                                () => new Date("2026-11-08T12:00:00.000Z"));
    expect(summary).toMatchObject({ dry_run: false, bundles: 3, messages: 3 });
    expect(calls[0]).toMatchObject({ method: "GET", auth: `Bearer ${INPUTS.apiToken}`,
      url: `https://api.cloudflare.com/client/v4/accounts/${INPUTS.account}/queues?per_page=100` });
    const r2 = `https://${INPUTS.account}.r2.cloudflarestorage.com/${NAMES.staging_bucket}/` +
      "rehearsal-1/";
    const messages = calls.filter((call) => call.method === "POST");
    expect(messages.map((call) => call.url)).toEqual(Array(3).fill(
      `https://api.cloudflare.com/client/v4/accounts/${INPUTS.account}/queues/q-import/messages`));
    for (const owner of [ALICE, BOB, NEWBIE]) {
      const key = keyOf(owner);
      const puts = calls.filter((call) => call.method === "PUT" && call.url.startsWith(`${r2}${key}/`));
      expect(puts.length).toBeGreaterThan(1);
      expect(puts.at(-1)!.url).toBe(`${r2}${key}/bundle.json`);
      expect(puts.every((call) => call.auth.startsWith(
        `AWS4-HMAC-SHA256 Credential=${INPUTS.accessKeyId}/20261108/auto/s3/aws4_request, `)))
        .toBe(true);
      // The message names the bundle by its index's SHA-256, and its MAC holds under the key.
      const sent = messages.map((call) => JSON.parse(call.body.toString("utf8")))
        .find((push) => push.body.owner === owner);
      expect(sent.content_type).toBe("json");
      expect(sent.body.prefix).toBe(`rehearsal-1/${key}`);
      expect(sent.body.bundle).toBe(createHash("sha256").update(puts.at(-1)!.body).digest("hex"));
      expect(macHolds(INPUTS.importKey, sent.body)).toBe(true);
      expect(macHolds(Buffer.alloc(32, 8).toString("base64url"), sent.body)).toBe(false);
      // Each upload comes before its message.
      expect(calls.indexOf(puts.at(-1)!)).toBeLessThan(calls.indexOf(
        messages.find((call) => JSON.parse(call.body.toString("utf8")).body.owner === owner)!));
    }
    // Nothing printed names an owner, a key or a credential.
    const printed = lines.join("\n");
    for (const word of [ALICE, BOB, NEWBIE, keyOf(ALICE), keyOf(BOB), ...SECRETS]) {
      expect(printed).not.toContain(word);
    }
  });

  it("prints what it would send on a dry run, needing no credential and sending nothing",
     async () => {
    const out = bundled();
    const { calls, fetcher } = apis();
    const lines: string[] = [];
    expect(await stageMain(["stage", out, "--run", "rehearsal-1", "--facts", factsFile(),
                            "--dry-run"], {}, (line) => lines.push(line), fetcher)).toBe(0);
    expect(calls).toEqual([]);
    expect(lines.slice(0, 3)).toEqual([1, 2, 3].map((n) => expect.stringMatching(new RegExp(
      `^bundle ${n} of 3: [0-9]+ objects, [0-9]+ bytes to ${NAMES.staging_bucket}/rehearsal-1/` +
      `<key>/, its index last; then 1 message to ${NAMES.queue}$`))));
    expect(lines[3]).toMatch(/^in all: [0-9]+ objects, [0-9]+ bytes, 3 messages$/);
    expect(JSON.parse(lines[4]!)).toMatchObject({ dry_run: true, bundles: 3, messages: 3 });
    expect(lines.join("\n")).not.toContain(keyOf(ALICE));
  });

  it("stages a rehearsal's names alone for a rehearsal, and none for the window", async () => {
    const env = Object.fromEntries(Object.entries(STAGE_INPUTS)
      .map(([input, name]) => [name, INPUTS[input as keyof Inputs]]));
    const real = bundled();
    const rehearsal = bundled("r9");
    const facts = ["--facts", factsFile()];
    const { calls, fetcher } = apis();
    await expect(stageMain(["stage", real, "--run", "r9", ...facts, "--rehearsal"], env,
                           () => undefined, fetcher)).rejects.toThrow(/names a real owner/);
    await expect(stageMain(["stage", rehearsal, "--run", "r9", ...facts], env, () => undefined,
                           fetcher)).rejects.toThrow(/names a rehearsal's owner/);
    await expect(stageMain(["stage", rehearsal, "--run", "r8", ...facts, "--rehearsal"], env,
                           () => undefined, fetcher)).rejects.toThrow(/another run's/);
    // Another run's rehearsal names are no window's either: its wipe would never find them.
    await expect(stageMain(["stage", rehearsal, "--run", "r8", ...facts], env, () => undefined,
                           fetcher)).rejects.toThrow(/names a rehearsal's owner/);
    expect(calls).toEqual([]);
    expect(await stageMain(["stage", rehearsal, "--run", "r9", ...facts, "--rehearsal"], env,
                           () => undefined, fetcher)).toBe(0);
    expect(calls.filter((call) => call.method === "POST").length).toBe(3);
  });

  it("takes its credentials from its stdin alone, each required", async () => {
    expect(() => stageInputs({})).toThrow(Object.values(STAGE_INPUTS).join(", "));
    const env = Object.fromEntries(Object.entries(STAGE_INPUTS)
      .map(([input, name]) => [name, INPUTS[input as keyof Inputs]]));
    expect(stageInputs(env)).toEqual(INPUTS);
    // As stdin hands them: one a line, in order; a short stdin names what it lacks.
    const lines = Object.keys(STAGE_INPUTS).map((input) => INPUTS[input as keyof Inputs]);
    expect(stageInputs(credentialsFrom(`${lines.join("\n")}\n`))).toEqual(INPUTS);
    expect(() => stageInputs(credentialsFrom(lines.slice(0, 4).join("\n"))))
      .toThrow("a staging run takes IMPORT_KEY from its stdin");
    const out = bundled();
    const { calls, fetcher } = apis();
    expect(await stageMain(["stage", out, "--run", "r1", "--facts", factsFile()], env,
                           () => undefined, fetcher)).toBe(0);
    expect(calls.filter((call) => call.method === "POST").length).toBe(3);
  });

  it("refuses a bundle that is not whole before anything is sent", async () => {
    for (const damage of ["part", "extra", "chunk"] as const) {
      const out = bundled();
      const dir = join(out, keyOf(ALICE));
      if (damage === "part") {
        writeFileSync(join(dir, "rows", "0.json"), '{"rows":[]}\n');
      } else if (damage === "extra") {
        writeFileSync(join(dir, "notes.txt"), "x");
      } else {
        writeFileSync(join(dir, "chunks", "0".repeat(64)), "x");
      }
      const { calls, fetcher } = apis();
      await expect(stage(out, "r1", NAMES, INPUTS, fetcher, () => undefined))
        .rejects.toThrow(/bundle/);
      expect(calls).toEqual([]);
    }
  });

  it("stops at the first refusal, and at an account with no import queue", async () => {
    const out = bundled();
    const refused = apis({ refuse: "/bundle.json" });
    await expect(stage(out, "r1", NAMES, INPUTS, refused.fetcher, () => undefined))
      .rejects.toThrow("bundle 1's upload was refused (403): nothing more is sent");
    expect(refused.calls.filter((call) => call.method === "POST")).toEqual([]);
    const none = apis({ queues: [] });
    await expect(stage(out, "r1", NAMES, INPUTS, none.fetcher, () => undefined))
      .rejects.toThrow(`lists no queue ${NAMES.queue} `);
    expect(none.calls.length).toBe(1);
    await expect(stage(out, "Bad Run", NAMES, INPUTS, none.fetcher, () => undefined))
      .rejects.toThrow(/run's id/);
  });
});

describe("the job's live names", () => {
  it("refuse a facts file that is not each name, a name, and nothing more", () => {
    const { directory: _directory, ...short } = IMPORT_NAMES;
    for (const given of [short, { ...IMPORT_NAMES, more: "x" }, { ...IMPORT_NAMES, queue: "" },
                         { ...IMPORT_NAMES, queue: 7 }, [IMPORT_NAMES]]) {
      expect(() => namesOf(JSON.stringify(given))).toThrow(/not the import job's names/);
    }
  });
});
