/**
 * The import window's runner (`.github/workflows/import-window.yml`, `window.sh`, `copy.ts`),
 * driven as the workflow drives it, against a fake remote: a synthetic backup in the
 * fixture's shape (`fixture.ts`) behind a fake `rclone`, with the import host's other tools
 * faked (`fake-host.sh`) and the job bundled as it ships. It holds the dispatch's gate (a real
 * run only with rehearsal false and its confirm; offsite never on a rehearsal; a missing
 * secret named, its value never shown), the run step's words and stdin (the kind its
 * rehearsal input says, no secret in an argument, the host key pinned), the host guard (the
 * run's marker, never the old stack's host), the source's read-only key (on stdin, in
 * rclone's environment alone), the copy's refusals (a digest that is not the manifest's, a
 * disk without room for the copy or the bundles, a work folder not the host's own), the run's
 * one Node, what a networked job sees, the host's lock, the off-site copy (the window's alone,
 * read back and held to the manifest, the primary store unchanged), and the output's
 * discipline (counts alone: no principal, no owner key, no path of the copy, and a withheld
 * output never printed).
 */
import { execFileSync, spawn, spawnSync } from "node:child_process";
import {
  appendFileSync, chmodSync, copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync,
  readFileSync, renameSync, rmSync, statSync, symlinkSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parse } from "yaml";

import { MOUNT_OPTIONS } from "../backup";
import { STAGE_INPUTS } from "../cli";
import { checkStores, copyMain, forbidden, released } from "../copy";
import {
  ALICE, ARCHIVE, BOB, DORA, ERASED, ERASING, fixture, FOUNDER, keyOf, NEWBIE, TIERS, UIDS,
} from "./fixture";

const HERE = dirname(fileURLToPath(import.meta.url));
const PLATFORM = join(HERE, "..", "..");
const REPO = join(PLATFORM, "..");
const PRINCIPALS = [ALICE, BOB, FOUNDER, DORA, NEWBIE, ERASED, ...Object.values(ERASING)];
const MANIFEST = "tinyassets-platform-2026-11-07T03-00-00Z.json";
const CREDENTIALS = ["CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                     "CLOUDFLARE_API_TOKEN", "IMPORT_KEY"];
/** The source key's stdin, census's and offsite's first two lines. */
const SOURCE_KEY = "spaces-id-value\nspaces-secret-value\n";
/** The primary store's prefix (`window.sh` SOURCE), and the off-site bucket's, as folders. */
const SOURCE_PATH = join("tasrc", "workflow-backups-jonnyton-sfo3", "workflow-backups");
const OFFSITE_PATH = join("taoff", "tinyassets-final-backup", "workflow-backups");
/** The one Node the fake registry names (`fake-host.sh`). */
const DIGEST = `node@sha256:${"7a".repeat(32)}`;
const WORKFLOW = parse(readFileSync(join(REPO, ".github", "workflows", "import-window.yml"),
                                    "utf8")) as {
  on: { workflow_dispatch: { inputs: Record<string, { default?: unknown }> } };
  concurrency: { group: string };
  jobs: { window: { environment: string; steps: { name?: string; run?: string; env?: Record<string, string> }[] } };
};

const temps: string[] = [];
let stage: string;
let bin: string;

beforeAll(() => {
  // The workflow's own build and stage: the job bundled, the host script, the tiers.
  stage = mkdtempSync(join(tmpdir(), "ta-window-stage-"));
  temps.push(stage);
  execFileSync(join(PLATFORM, "node_modules", ".bin", "esbuild"),
               [join(PLATFORM, "import", "cli.ts"), "--bundle", "--platform=node",
                "--format=esm", "--target=node22", `--outfile=${join(stage, "import.mjs")}`,
                "--log-level=error"]);
  copyFileSync(join(PLATFORM, "import", "window.sh"), join(stage, "window.sh"));
  writeFileSync(join(stage, "control.json"), JSON.stringify({ tiers: TIERS }));
  bin = join(stage, "bin");
  mkdirSync(bin);
  chmodSync(join(HERE, "fake-host.sh"), 0o755);
  for (const tool of ["rclone", "docker", "mount", "umount", "losetup", "df", "ssh", "scp",
                      "ssh-keyscan", "ssh-keygen"]) {
    symlinkSync(join(HERE, "fake-host.sh"), join(bin, tool));
  }
});

afterAll(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/**
 * An import host: its backup remote (the primary store's folder) made from a fresh fixture,
 * its work root, and its own root for the marker and the old stack's paths.
 */
function host() {
  const f = fixture();
  const dir = mkdtempSync(join(tmpdir(), "ta-window-host-"));
  temps.push(f.work, dir);
  const remotes = join(dir, "remote");
  const remote = join(remotes, SOURCE_PATH);
  const root = join(dir, "var-tmp");
  const hostRoot = join(dir, "host-root");
  mkdirSync(join(remote, "platform"), { recursive: true });
  mkdirSync(join(hostRoot, "etc"), { recursive: true });
  mkdirSync(root);
  const manifest = JSON.parse(readFileSync(join(f.work, "manifest.json"), "utf8")) as
    { snapshots: { uid: number; snapshot_id: string }[]; sha256: string };
  const publish = () => {
    execFileSync("tar", ["-czf", join(remote, ARCHIVE), "-C", join(f.work, "data"), "_data"]);
    manifest.sha256 = execFileSync("sha256sum", [join(remote, ARCHIVE)], { encoding: "utf8" })
      .split(" ")[0]!;
    writeFileSync(join(remote, "platform", MANIFEST), JSON.stringify(manifest));
  };
  publish();
  // An older manifest whose archive is gone: the newest is the one read.
  writeFileSync(join(remote, "platform", "tinyassets-platform-2026-11-01T03-00-00Z.json"),
                JSON.stringify({ ...manifest, archive: "tinyassets-data-2026-11-01T03-00-00Z.tar.gz" }));
  writeFileSync(join(remote, "platform", "notes.txt"), "not a manifest");
  for (const pin of manifest.snapshots) {
    mkdirSync(join(remote, String(pin.uid)), { recursive: true });
    writeFileSync(join(remote, String(pin.uid), `${pin.snapshot_id}.img.gz`),
                  gzipSync(readFileSync(join(f.work, `${pin.uid}.img`))));
  }
  const log = join(dir, "calls.log");
  writeFileSync(log, "");
  const disk = { size: 1_000_000_000_000, avail: 900_000_000_000 };
  const work = (run: string) => join(root, `ta-import-${run}`);
  /**
   * `window.sh <args...>`, as the workflow's SSH session runs it, `input` its stdin (census's
   * source key unless given), on a host whose marker names the run unless `marker` says.
   */
  const lock = join(dir, "lock");
  const window = (args: readonly string[], input?: string,
                  options: { marker?: string | null; flip?: string } = {}) => {
    const marker = join(hostRoot, "etc", "tinyassets-import-host");
    rmSync(marker, { force: true });
    const named = options.marker === undefined ? args[1] : options.marker;
    if (named !== null && named !== undefined) {
      writeFileSync(marker, `${named}\n`);
    }
    const result = spawnSync("bash", [join(stage, "window.sh"), ...args], {
      encoding: "utf8", input: input ?? (args[0] === "census" ? SOURCE_KEY : ""),
      env: { PATH: `${bin}:${dirname(process.execPath)}:/usr/bin:/bin`, TA_IMPORT_ROOT: root,
             TA_IMPORT_LOCK: lock, TA_IMPORT_HOST_ROOT: hostRoot,
             FAKE_REMOTE: remotes, FAKE_LOG: log, FAKE_DF_SIZE: String(disk.size),
             FAKE_DF_AVAIL: String(disk.avail), FAKE_DIGEST: DIGEST,
             ...(options.flip === undefined ? {} : { FAKE_FLIP: options.flip }) } });
    return { status: result.status, out: result.stdout + result.stderr };
  };
  const calls = () => readFileSync(log, "utf8").split("\n").filter(Boolean);
  return { f, dir, remote, remotes, hostRoot, manifest, publish, disk, work, window, calls, lock };
}

/** Text that names no owner of the fixture, by principal or key, and no path of `work`. */
function countsOnly(text: string, work: string): void {
  for (const principal of PRINCIPALS) {
    expect(text).not.toContain(principal);
    expect(text).not.toContain(keyOf(principal));
  }
  expect(text).not.toContain(work);
}

describe("the dispatch's gate, the workflow's first step", () => {
  const workflow = WORKFLOW;
  const steps = workflow.jobs.window.steps;
  const SECRETS = { DO_SSH_KEY: "ssh-key-value-1", IMPORT_DO_TOKEN: "do-token-value-8",
                    IMPORT_RESERVED_IP: "203.0.113.7",
                    IMPORT_SPACES_ACCESS_KEY_ID: "spaces-id-value-9",
                    IMPORT_SPACES_SECRET_ACCESS_KEY: "spaces-secret-value-10",
                    IMPORT_EXCLUSION_LIST: "exclusion-value-2",
                    CLOUDFLARE_ACCOUNT_ID: "3c".repeat(16),
                    IMPORT_R2_ACCESS_KEY_ID: "r2-id-value-4",
                    IMPORT_R2_SECRET_ACCESS_KEY: "r2-secret-value-5",
                    IMPORT_CLOUDFLARE_API_TOKEN: "token-value-6", IMPORT_KEY: "key-value-7" };
  const gate = (inputs: Record<string, string>, secrets: Record<string, string> = SECRETS) => {
    const result = spawnSync("bash", ["-c", steps[0]!.run!], {
      encoding: "utf8",
      env: { PATH: process.env["PATH"], MODE: "census", RUN_ID: "r1", REHEARSAL: "true",
             CONFIRM: "", DELETE_DEAD_LETTERS: "false", ...secrets, ...inputs } });
    return { status: result.status, out: result.stdout + result.stderr };
  };

  it("comes before any other step, in production, and a rehearsal is the default", () => {
    expect(steps[0]!.name).toBe("Refuse a run this dispatch may not make");
    expect(workflow.jobs.window.environment).toBe("production");
    // Every mode in its own group: the observation and host-mutation locks stay free.
    expect(workflow.concurrency.group).toBe("import-window");
    expect(workflow.on.workflow_dispatch.inputs["rehearsal"]!.default).toBe(true);
    expect(workflow.on.workflow_dispatch.inputs["confirm"]!.default).toBe("");
    expect(gate({})).toMatchObject({ status: 0 });
  });

  it("refuses a real run without rehearsal false and its own confirm", () => {
    for (const confirm of ["", "window-r2", "window-r1 ", "r1"]) {
      const run = gate({ REHEARSAL: "false", CONFIRM: confirm });
      expect(run.status).toBe(1);
      expect(run.out).toContain("a real window run needs rehearsal false and confirm window-<run>");
    }
    expect(gate({ REHEARSAL: "true", CONFIRM: "window-r1" }).out)
      .toContain("confirm is for a real window run alone");
    expect(gate({ REHEARSAL: "false", CONFIRM: "window-r1" }).status).toBe(0);
    expect(gate({ RUN_ID: "R1;rm" }).status).toBe(1);
    expect(gate({ MODE: "dlq", DELETE_DEAD_LETTERS: "true" }).status).toBe(0);
    expect(gate({ MODE: "wipe", DELETE_DEAD_LETTERS: "true" }).status).toBe(1);
    // The off-site copy is the real window's final backup alone.
    expect(gate({ MODE: "offsite" }).out)
      .toContain("offsite copies the real window's final backup alone, never a rehearsal's");
    expect(gate({ MODE: "offsite", REHEARSAL: "false", CONFIRM: "window-r1" }).status).toBe(0);
  });

  it("names a mode's missing secret, never a value", () => {
    const without = (...names: string[]) => Object.fromEntries(Object.entries(SECRETS)
      .map(([name, value]) => [name, names.includes(name) ? "" : value]));
    const stage = gate({ MODE: "stage" }, without("IMPORT_KEY", "IMPORT_R2_ACCESS_KEY_ID"));
    expect(stage.status).toBe(1);
    expect(stage.out).toContain("IMPORT_R2_ACCESS_KEY_ID IMPORT_KEY");
    const bundle = gate({ MODE: "bundle" }, without("IMPORT_EXCLUSION_LIST"));
    expect(bundle.out).toContain("needs the secret(s) IMPORT_EXCLUSION_LIST");
    // census takes the source's read-only key and no Cloudflare one; clean the host's alone;
    // bundle no Cloudflare one; every mode the host's three.
    const census = gate({ MODE: "census" }, without("IMPORT_SPACES_SECRET_ACCESS_KEY"));
    expect(census.out).toContain("needs the secret(s) IMPORT_SPACES_SECRET_ACCESS_KEY,");
    expect(gate({ MODE: "census" }, without("IMPORT_KEY", "IMPORT_EXCLUSION_LIST",
                                            "CLOUDFLARE_ACCOUNT_ID", "IMPORT_R2_ACCESS_KEY_ID"))
      .status).toBe(0);
    expect(gate({ MODE: "clean" }, without("IMPORT_KEY", "IMPORT_EXCLUSION_LIST",
                                           "IMPORT_SPACES_ACCESS_KEY_ID",
                                           "IMPORT_SPACES_SECRET_ACCESS_KEY",
                                           "CLOUDFLARE_ACCOUNT_ID")).status).toBe(0);
    expect(gate({ MODE: "bundle" }, without("IMPORT_KEY")).status).toBe(0);
    for (const name of ["DO_SSH_KEY", "IMPORT_DO_TOKEN", "IMPORT_RESERVED_IP"]) {
      for (const mode of ["census", "bundle", "verify", "clean"]) {
        expect(gate({ MODE: mode }, without(name)).out).toContain(`needs the secret(s) ${name},`);
      }
    }
    // The account is the repository variable, held to its shape; no deploy-kit token is read.
    expect(gate({ MODE: "verify" }, without("CLOUDFLARE_ACCOUNT_ID")).out)
      .toContain("needs the secret(s) CLOUDFLARE_ACCOUNT_ID,");
    expect(gate({ MODE: "verify" }, { ...SECRETS, CLOUDFLARE_ACCOUNT_ID: "not-an-account" }).out)
      .toContain("refused: the repository variable CLOUDFLARE_ACCOUNT_ID is no 32-hex account id");
    expect(steps[0]!.env!["CLOUDFLARE_ACCOUNT_ID"]).toBe("${{ vars.CLOUDFLARE_ACCOUNT_ID }}");
    expect(JSON.stringify(WORKFLOW)).not.toContain("secrets.CLOUDFLARE_API_TOKEN");
    const offsite = gate({ MODE: "offsite", REHEARSAL: "false", CONFIRM: "window-r1" },
                         without("IMPORT_SPACES_ACCESS_KEY_ID", "IMPORT_R2_SECRET_ACCESS_KEY"));
    expect(offsite.out).toContain(
      "needs the secret(s) IMPORT_SPACES_ACCESS_KEY_ID IMPORT_R2_SECRET_ACCESS_KEY,");
    expect(gate({ MODE: "offsite", REHEARSAL: "false", CONFIRM: "window-r1" },
                without("IMPORT_KEY", "IMPORT_CLOUDFLARE_API_TOKEN")).status).toBe(0);
    for (const run of [stage, bundle, census, offsite]) {
      for (const value of Object.values(SECRETS)) {
        expect(run.out).not.toContain(value);
      }
    }
  });
});

describe("the run step, its ssh faked", () => {
  const step = WORKFLOW.jobs.window.steps.find((each) =>
    each.name === "Run the step on the import host")!;
  const SECRETS = { CLOUDFLARE_ACCOUNT_ID: "acct-v", IMPORT_R2_ACCESS_KEY_ID: "r2id-v",
                    IMPORT_R2_SECRET_ACCESS_KEY: "r2sec-v", IMPORT_CLOUDFLARE_API_TOKEN: "tok-v",
                    IMPORT_KEY: "key-v", IMPORT_EXCLUSION_LIST: "excl-v",
                    IMPORT_SPACES_ACCESS_KEY_ID: "spid-v",
                    IMPORT_SPACES_SECRET_ACCESS_KEY: "spsec-v" };
  const run = (inputs: Record<string, string>) => {
    const dir = mkdtempSync(join(tmpdir(), "ta-window-step-"));
    temps.push(dir);
    const log = join(dir, "calls.log");
    writeFileSync(log, "");
    const summary = join(dir, "summary");
    const result = spawnSync("bash", ["-c", step.run!], {
      encoding: "utf8", cwd: dir,
      env: { PATH: `${bin}:/usr/bin:/bin`, HOME: dir, RUNNER_TEMP: dir,
             GITHUB_STEP_SUMMARY: summary, FAKE_LOG: log, ADDRESS: "203.0.113.7",
             MODE: "stage", RUN_ID: "r1", REHEARSAL: "true",
             DELETE_DEAD_LETTERS: "false", ...SECRETS, ...inputs } });
    const calls = readFileSync(log, "utf8");
    return { status: result.status, out: result.stdout + result.stderr + readFileSync(summary, "utf8"),
             args: calls.split("\n").filter((line) => line.startsWith("ssh ")).join("\n"),
             stdin: calls.split("\n").filter((line) => !line.startsWith("ssh ")).join("\n") };
  };

  it("hands the host the kind its rehearsal input says, and every secret on stdin alone", () => {
    const rehearsal = run({ REHEARSAL: "true" });
    expect(rehearsal.status).toBe(0);
    expect(rehearsal.args).toMatch(/window\.sh stage r1 rehearsal\s*$/m);
    expect(run({ REHEARSAL: "false" }).args).toMatch(/window\.sh stage r1 window\s*$/m);
    expect(run({ MODE: "dlq", DELETE_DEAD_LETTERS: "true" }).args)
      .toMatch(/window\.sh dlq r1 rehearsal delete\s*$/m);
    expect(rehearsal.stdin).toContain("STDIN acct-v\nr2id-v\nr2sec-v\ntok-v\nkey-v");
    expect(run({ MODE: "bundle" }).stdin).toContain("STDIN excl-v");
    expect(run({ MODE: "census" }).stdin).toContain("STDIN spid-v\nspsec-v\n");
    expect(run({ MODE: "offsite", REHEARSAL: "false" }).stdin)
      .toContain("STDIN spid-v\nspsec-v\nacct-v\nr2id-v\nr2sec-v\n");
    expect(run({ MODE: "clean" }).stdin).toMatch(/STDIN \n/);
    // root on the import host by its reserved IP, its key pinned, never the droplet's sudo.
    for (const line of rehearsal.args.split("\n")) {
      expect(line).toContain("-o StrictHostKeyChecking=yes");
      expect(line).toMatch(/-o UserKnownHostsFile=\S+\/import_known_hosts /);
      expect(line).toContain("root@203.0.113.7");
      expect(line).not.toContain("sudo");
    }
    for (const value of Object.values(SECRETS)) {
      expect(rehearsal.args).not.toContain(value);
      expect(rehearsal.out).not.toContain(value);
    }
  });
});

describe("the copy, on a fake droplet", () => {
  it("is made from the newest manifest, checked, and its census leaves counts alone", () => {
    const h = host();
    const run = h.window(["census", "r1", "rehearsal"]);
    const work = h.work("r1");
    expect(run.out).toContain("the archive and 4 images match the manifest's digests");
    expect(run.out).toContain("the copy holds the archive and 4 images");
    // Every store of the shared layer passes integrity_check; the images, not mounted by the
    // fake, are left to the census, which refuses them.
    expect(run.out).toMatch(/"not_intact": 0/);
    expect(run.out).toMatch(/"images_unread": 4/);
    expect(run.status).toBe(3);
    expect(run.out).toMatch(/"not_mounted": 3/);
    expect(run.out).toMatch(/"ready": false/);
    countsOnly(run.out, work);
    // A 0700 work folder of the run's; the table 0600; each image sparse and whole.
    expect(lstatSync(work).mode & 0o777).toBe(0o700);
    expect(lstatSync(join(work, "census", "census.private.tsv")).mode & 0o777).toBe(0o600);
    expect(readFileSync(join(work, "census", "census.private.tsv"), "utf8"))
      .toContain(keyOf(ALICE));
    for (const uid of Object.values(UIDS)) {
      expect(readFileSync(join(work, `${uid}.img`)))
        .toEqual(readFileSync(join(h.f.work, `${uid}.img`)));
      expect(existsSync(join(work, `${uid}.img.gz`))).toBe(false);
    }
    expect(existsSync(join(work, "copy.done"))).toBe(true);
    // The run's one Node: node:22 pulled once, its digest kept, and every job run by it.
    expect(readFileSync(join(work, "image"), "utf8")).toBe(`${DIGEST}\n`);
    // The digest it pinned, printed (public): the rehearsal's and the window's compare.
    expect(run.out).toContain(`the job's Node is ${DIGEST}`);
    const calls = h.calls();
    expect(calls).toContain(
      `rclone cat tasrc:workflow-backups-jonnyton-sfo3/workflow-backups/platform/${MANIFEST}`);
    // The source's key in rclone's environment alone (docker refuses one in its own), its
    // value in no call.
    expect(calls.filter((call) => call.startsWith("rclone-keys")).every((call) =>
      call === "rclone-keys TASRC ")).toBe(true);
    expect(calls.join("\n")).not.toContain("spaces-secret-value");
    // Each image mounted read-only for the job, with the research's line, and unmounted after.
    expect(readFileSync(join(stage, "window.sh"), "utf8"))
      .toContain(`readonly MOUNT_OPTIONS="${MOUNT_OPTIONS}"`);
    for (const uid of Object.values(UIDS)) {
      const mount = calls.indexOf(`mount -t xfs -o ${MOUNT_OPTIONS} ${work}/${uid}.img ` +
                                  `${work}/mnt/${uid}`);
      expect(mount).toBeGreaterThan(-1);
      expect(calls.indexOf(`umount ${work}/mnt/${uid}`)).toBeGreaterThan(mount);
    }
    // Every job without network, the copy read-only, one folder writable at most.
    const jobs = calls.filter((call) => call.startsWith("docker run"));
    expect(jobs.length).toBeGreaterThan(4);
    for (const call of jobs) {
      expect(call).toContain("--network none");
      expect(call).toContain(`-v ${work}:${work}:ro`);
      expect(call).toContain(` ${DIGEST} node `);
      expect(call).not.toMatch(/ -e /);
    }
    expect(jobs.filter((call) => call.includes(" census "))[0])
      .toContain(`-v ${work}/census:${work}/census `);

    // Run again: the same copy, never fetched again.
    const again = h.window(["census", "r1", "rehearsal"]);
    expect(again.status).toBe(3);
    expect(h.calls().filter((call) => call.startsWith("rclone copyto")))
      .toHaveLength(1 + Object.keys(UIDS).length);
    expect(h.calls().filter((call) => call.startsWith("docker pull"))).toEqual(
      ["docker pull -q node:22"]);
    // A copy that names no Node is refused, never run by another.
    renameSync(join(work, "image"), join(work, "image.gone"));
    expect(h.window(["bundle", "r1", "rehearsal"], `${FOUNDER}\n`).out)
      .toContain("this run's copy names no Node image: run clean, then census");
    renameSync(join(work, "image.gone"), join(work, "image"));
    // A copy made for a rehearsal is never the window's, and clean deletes it.
    expect(h.window(["bundle", "r1", "window"], "user_founder\n").out)
      .toContain("this run's copy was not made for a window run");
    expect(h.window(["clean", "r1", "rehearsal"]).out)
      .toContain("the run's copy is unmounted and deleted");
    expect(existsSync(work)).toBe(false);
  });

  it("refuses an archive or an image whose digest is not the manifest's", () => {
    const h = host();
    // The archive published again with other bytes, the manifest's digest left as it was.
    const pinned = h.manifest.sha256;
    appendFileSync(join(h.f.work, "data", "_data", "extra.txt"), "changed after the manifest");
    h.publish();
    h.manifest.sha256 = pinned;
    writeFileSync(join(h.remote, "platform", MANIFEST), JSON.stringify(h.manifest));
    const archive = h.window(["census", "a1", "rehearsal"]);
    expect(archive.status).toBe(2);
    expect(archive.out).toContain("refused: the archive's SHA-256 is not the manifest's");
    expect(existsSync(join(h.work("a1"), "copy.done"))).toBe(false);
    expect(existsSync(join(h.work("a1"), "data"))).toBe(false);
    expect(h.window(["census", "a1", "rehearsal"]).out)
      .toContain("this run's copy was left unfinished: run clean, then census");

    const i = host();
    const second = i.manifest.snapshots[1]!;
    writeFileSync(join(i.remote, String(second.uid), `${second.snapshot_id}.img.gz`),
                  gzipSync(Buffer.from("another image")));
    const image = i.window(["census", "i1", "rehearsal"]);
    expect(image.status).toBe(2);
    expect(image.out).toContain("refused: image 2 of 4's SHA-256 is not its pin");
    countsOnly(archive.out + image.out, i.work("i1"));
    expect(i.calls().some((call) => call.startsWith("mount"))).toBe(false);
  });

  it("refuses to start without room, and again before the images take the disk", () => {
    const h = host();
    h.disk.avail = 200_000_000_000;  // 20% free: any download would pass the disk watch's 80%
    const none = h.window(["census", "d1", "rehearsal"]);
    expect(none.status).toBe(2);
    expect(none.out).toMatch(/refused: the copy needs [0-9]+ bytes for the download; the disk has 200000000000 free of 1000000000000 and keeps 20% free/);
    expect(h.calls().some((call) => call.startsWith("rclone copyto"))).toBe(false);

    // Room for the download, none for what it decompresses to.
    const i = host();
    const downloaded = statSync(join(i.remote, ARCHIVE)).size + i.manifest.snapshots
      .reduce((sum, pin) => sum + statSync(join(i.remote, String(pin.uid),
                                                `${pin.snapshot_id}.img.gz`)).size, 0);
    i.disk.size = 10_000_000;
    i.disk.avail = 2_000_000 + downloaded;
    const late = i.window(["census", "d2", "rehearsal"]);
    expect(late.status).toBe(2);
    expect(late.out).toContain("for the images and the shared layer");
    expect(existsSync(join(i.work("d2"), `${i.manifest.snapshots[0]!.uid}.img`))).toBe(false);
  });

  it("takes credentials on stdin alone, names the one missing, and hands them to the job", () => {
    const h = host();
    const values = CREDENTIALS.map((name, n) => `${name.toLowerCase()}-value-${n}`);
    const missing = h.window(["stage", "s1", "rehearsal"], `${values.slice(0, 4).join("\n")}\n`);
    expect(missing.status).toBe(2);
    expect(missing.out).toContain("refused: the run was handed no IMPORT_KEY");
    // With every one, a run with no copy is refused before any job; no value is ever shown.
    const nocopy = h.window(["stage", "s1", "rehearsal"], `${values.join("\n")}\n`);
    expect(nocopy.out).toContain("this run has no copy: run census first");
    expect(h.calls().some((call) => call.startsWith("docker run"))).toBe(false);
    // On a copy with bundles, the job takes them on its stdin, none in an argument, with the
    // network; this account is no account, so the job refuses before any request.
    h.window(["census", "s1", "rehearsal"]);
    mkdirSync(join(h.work("s1"), "bundle", "out"), { recursive: true });
    const staged = h.window(["stage", "s1", "rehearsal"], `${values.join("\n")}\n`);
    expect(staged.status).toBe(2);
    expect(staged.out).toContain("refused: the account is 32 hex");
    const call = h.calls().filter((each) => each.startsWith("docker run")
                                            && each.includes(" stage "))[0]!;
    expect(call).toContain(`--network bridge -v ${stage}:${stage}:ro`);
    expect(call).toContain(` -i ${DIGEST} node `);
    expect(call).not.toMatch(/ -e /);
    expect(call).toContain("--rehearsal");
    for (const value of values) {
      expect(missing.out + nocopy.out + staged.out + h.calls().join("\n")).not.toContain(value);
    }
  });

  it("bundles from the exclusion list on stdin, into its one writable folder, once", () => {
    const h = host();
    h.window(["census", "b1", "rehearsal"]);
    const work = h.work("b1");
    const bundle = h.window(["bundle", "b1", "rehearsal"], `# start fresh\n${FOUNDER}\n`);
    countsOnly(bundle.out, work);
    const call = h.calls().filter((each) => each.startsWith("docker run")
                                            && each.includes(" bundle "))[0]!;
    expect(call).toContain(`--network none -v ${stage}:${stage}:ro -v ${work}:${work}:ro ` +
                           `-v ${work}/bundle:${work}/bundle -i `);
    expect(call).toContain("--exclude -");
    expect(call).toContain("--rehearsal b1");
    // The list reached the builder (an empty one is its own first refusal), which then
    // refuses a copy whose images the fake never mounts: nothing is bundled, nothing kept.
    expect(bundle.status).toBe(2);
    expect(bundle.out).toContain("refused: the backup copy is refused: erasure_unread");
    expect(bundle.out).toContain("nothing was bundled");
    expect(existsSync(join(work, "bundle"))).toBe(false);
    const empty = h.window(["bundle", "b1", "rehearsal"], "\n");
    expect(empty.out).toContain("refused: the exclusion list names no owner");
    // Bundles once made stand as made.
    mkdirSync(join(work, "bundle"));
    expect(h.window(["bundle", "b1", "rehearsal"], `${FOUNDER}\n`).out)
      .toContain("this run's bundles are made already");
  });
});

describe("the runner's guards, on a fake droplet", () => {
  it("refuses a bundle the disk has no room for, before any is written", () => {
    const h = host();
    h.window(["census", "f1", "rehearsal"]);
    writeFileSync(join(h.work("f1"), "mnt", String(Object.values(UIDS)[0]), "a-file"),
                  "x".repeat(4096));
    h.disk.avail = h.disk.size * 0.2 + 1000;
    const run = h.window(["bundle", "f1", "rehearsal"], `${FOUNDER}\n`);
    expect(run.status).toBe(2);
    expect(run.out).toMatch(/refused: the copy needs [0-9]+ bytes for the bundles/);
    expect(existsSync(join(h.work("f1"), "bundle"))).toBe(false);
    expect(h.calls().some((call) => call.includes(" bundle "))).toBe(false);
  });

  it("lets a networked job see the bundles alone, never the copy", () => {
    const h = host();
    h.window(["census", "f2", "rehearsal"]);
    const work = h.work("f2");
    mkdirSync(join(work, "bundle", "out"), { recursive: true });
    for (const mode of ["stage", "verify", "dlq"]) {
      h.window([mode, "f2", "rehearsal"], "a\nb\nc\nd\ne\n");
    }
    const networked = h.calls().filter((call) => call.startsWith("docker run")
                                                 && call.includes("--network bridge"));
    expect(networked).toHaveLength(3);
    for (const call of networked) {
      const mounts = [...call.matchAll(/-v (\S+)/g)].map((match) => match[1]);
      expect(mounts.filter((each) => each !== `${stage}:${stage}:ro`
                                     && each !== `${work}/window:${work}/window`))
        .toEqual([`${work}/bundle/out:${work}/bundle/out:ro`]);
      for (const hidden of ["data", "mnt", "bundle/private", "census", "manifest.json"]) {
        expect(call).not.toContain(`${work}/${hidden}`);
      }
      expect(call).not.toContain(`-v ${work}:`);
    }
  });

  it("withholds a job's output that names a path, and never prints it", () => {
    const h = host();
    h.window(["census", "f6", "rehearsal"]);
    const work = h.work("f6");
    rmSync(join(work, "manifest.json"));
    const run = h.window(["census", "f6", "rehearsal"]);
    expect(run.status).toBe(4);
    expect(run.out).toContain("1 of the job's outputs withheld");
    countsOnly(run.out, work);
    expect(readFileSync(join(work, "job.err.private"), "utf8")).toContain(work);
  });

  it("runs one import window run at a time on the host", async () => {
    const h = host();
    const holder = spawn("flock", [h.lock, "sleep", "20"], { stdio: "ignore" });
    try {
      // Wait until the holder has the lock.
      while (spawnSync("flock", ["-n", h.lock, "true"]).status === 0) {
        await new Promise((done) => setTimeout(done, 20));
      }
      const run = h.window(["clean", "l1", "rehearsal"]);
      expect(run.status).toBe(2);
      expect(run.out).toContain("refused: another import window run holds this host");
    } finally {
      holder.kill();
    }
  });

  it("refuses a work folder that is not this host's own 0700 folder", () => {
    const h = host();
    h.window(["census", "f7", "rehearsal"]);
    chmodSync(h.work("f7"), 0o755);
    for (const mode of ["census", "clean"]) {
      const run = h.window([mode, "f7", "rehearsal"]);
      expect(run.status).toBe(2);
      expect(run.out).toContain("this run's work folder is not this host's own 0700 folder");
    }
    expect(existsSync(h.work("f7"))).toBe(true);
  });
});

describe("the copy's checks", () => {
  it("runs every store through integrity_check, an image's too, and counts one that fails", () => {
    const f = fixture();
    temps.push(f.work);
    const whole = checkStores(f.copy);
    expect(whole).toMatchObject({ not_intact: 0, images_unread: 0 });
    expect(whole.stores).toBeGreaterThan(10);
    const store = join(f.tree(FOUNDER), ".centers.db");
    const bytes = readFileSync(store);
    bytes.fill(0x55, 100, bytes.length);
    writeFileSync(store, bytes);
    expect(checkStores(f.copy)).toMatchObject({ stores: whole.stores, not_intact: 1 });
  });

  it("withholds output that names an owner, an owner's key or a path of the copy", () => {
    const f = fixture();
    temps.push(f.work);
    const names = forbidden(f.work);
    expect(released('{"owners": 7, "problems": {}}', names)).toBe('{"owners": 7, "problems": {}}');
    expect(released(`bundle 1 of 3: ${ALICE}`, names)).toBeNull();
    expect(released(`plans/${keyOf(BOB)}`, names)).toBeNull();
    expect(released(`rehearsal-r1-${keyOf(DORA)}12345678`, names)).toBeNull();
    expect(released(`ENOENT, open '${f.work}/manifest.json'`, names)).toBeNull();
    // Before the shared layer is extracted, the copy's path alone.
    expect(forbidden(join(f.work, "none"))).toEqual([join(f.work, "none")]);
  });

  it("refuses the release whole when the shared layer's identities cannot be read", () => {
    const f = fixture();
    temps.push(f.work);
    const store = join(f.work, "data", "_data", ".broker", "state", "owner-identities.db");
    const out = join(f.work, "job.out.private");
    writeFileSync(out, `owner ${ALICE} differs\n`);
    const printed: string[] = [];
    const release = () => copyMain(["copy", "release", f.work, out],
                                   () => { throw new Error("unused"); },
                                   (line) => printed.push(line));
    expect(release()).toBe(4);
    // The store's header gone: not a database.
    const bytes = readFileSync(store);
    writeFileSync(store, Buffer.concat([Buffer.alloc(100, 0x55), bytes.subarray(100)]));
    expect(() => forbidden(f.work)).toThrow();
    expect(release).toThrow();
    // The store gone, the shared layer still there.
    rmSync(store);
    expect(() => forbidden(f.work)).toThrow();
    expect(release).toThrow();
    expect(printed.join("\n")).not.toContain(ALICE);
  });
});

describe("the host guard, on a fake import host", () => {
  it("runs on this run's import host alone: its marker names the run", () => {
    const h = host();
    for (const marker of [null, "r2", "r1-other", ""]) {
      const run = h.window(["census", "r1", "rehearsal"], undefined, { marker });
      expect(run.status).toBe(2);
      expect(run.out).toContain(
        "refused: this host is not this run's import host (its marker does not name the run)");
    }
    // Refused before the lock, any stdin or any tool.
    expect(h.calls()).toEqual([]);
    expect(existsSync(h.work("r1"))).toBe(false);
    expect(h.window(["clean", "r1", "rehearsal"]).status).toBe(0);
  });

  it("refuses any host that holds the old stack, whatever its marker says", () => {
    for (const path of [["var", "lib", "boxhostd"], ["etc", "tinyassets", "env"]]) {
      const h = host();
      mkdirSync(join(h.hostRoot, ...path.slice(0, -1)), { recursive: true });
      if (path[0] === "var") {
        mkdirSync(join(h.hostRoot, ...path));
      } else {
        writeFileSync(join(h.hostRoot, ...path), "BACKUP_DEST=spaces:anything\n");
      }
      for (const mode of ["census", "clean", "stage"]) {
        const run = h.window([mode, "r1", "rehearsal"]);
        expect(run.status).toBe(2);
        expect(run.out).toContain(`refused: this host holds the old stack (/${path.join("/")})`);
      }
      expect(h.calls()).toEqual([]);
    }
  });

  it("takes the source's read-only key on census's stdin, and refuses a census without it", () => {
    const h = host();
    const none = h.window(["census", "k1", "rehearsal"], "spaces-id-value\n");
    expect(none.status).toBe(2);
    expect(none.out).toContain("refused: the run was handed no SPACES_SECRET_ACCESS_KEY");
    expect(h.calls()).toEqual([]);
    // The script reads no env file: the droplet's own BACKUP_DEST is nothing to it.
    expect(readFileSync(join(stage, "window.sh"), "utf8")).not.toMatch(/BACKUP_DEST=|TA_IMPORT_ENV/);
  });
});

describe("the off-site copy of the window's final backup, on a fake import host", () => {
  const OFFSITE_KEY = `${SOURCE_KEY}${"a".repeat(32)}\nr2-id-value\nr2-secret-value\n`;
  /** Every file under `dir`, by its path, with its bytes' digest. */
  const tree = (dir: string): Record<string, string> => Object.fromEntries(
    execFileSync("find", [dir, "-type", "f"], { encoding: "utf8" }).split("\n").filter(Boolean)
      .sort().map((path) => [path.slice(dir.length),
                             execFileSync("sha256sum", [path], { encoding: "utf8" }).slice(0, 64)]));

  it("refuses a rehearsal before anything else", () => {
    const h = host();
    h.window(["census", "o1", "rehearsal"]);
    const before = h.calls().length;
    const run = h.window(["offsite", "o1", "rehearsal"], OFFSITE_KEY);
    expect(run.status).toBe(2);
    expect(run.out).toContain(
      "refused: offsite copies the real window's final backup alone, never a rehearsal's");
    expect(h.calls().length).toBe(before);
    expect(existsSync(join(h.remotes, "taoff"))).toBe(false);
  });

  it("copies the copy's manifest, archive and every pin to R2, each read back and held to " +
     "the manifest, and changes nothing in the primary store", () => {
    const h = host();
    h.window(["census", "o2", "window"]);
    const primary = tree(h.remote);
    const run = h.window(["offsite", "o2", "window"], OFFSITE_KEY);
    expect(run.status).toBe(0);
    expect(run.out).toContain("the archive and 4 images match the manifest's digests");
    expect(run.out).toContain("the off-site copy holds the manifest, the archive and 4 images, " +
                              "each read back and held to the manifest's digests");
    countsOnly(run.out, h.work("o2"));
    // The same objects at the same keys: the manifest, its archive and every pinned snapshot.
    const offsite = tree(join(h.remotes, OFFSITE_PATH));
    expect(Object.keys(offsite).sort()).toEqual([
      `/${ARCHIVE}`,
      ...h.manifest.snapshots.map((pin) => `/${pin.uid}/${pin.snapshot_id}.img.gz`),
      `/platform/${MANIFEST}`].sort());
    for (const [path, digest] of Object.entries(offsite)) {
      expect(primary[path]).toBe(digest);
    }
    expect(tree(h.remote)).toEqual(primary);
    // The read-back is gone; the copy stays as it was.
    expect(existsSync(join(h.work("o2"), "offsite"))).toBe(false);
    expect(existsSync(join(h.work("o2"), "copy.done"))).toBe(true);
    // Each key in rclone's environment alone: the source's listing holds its own, the copy
    // both, the read-back R2's alone.
    const calls = h.calls();
    const from = calls.findIndex((call) => call.startsWith("rclone copyto tasrc:")
                                           && call.includes(" taoff:"));
    expect(from).toBeGreaterThan(-1);
    expect(calls.slice(0, from).filter((call) => call.startsWith("rclone-keys")).at(-1))
      .toBe("rclone-keys TASRC ");
    const keys = new Set(calls.slice(from).filter((call) => call.startsWith("rclone-keys")));
    expect([...keys].sort()).toEqual(["rclone-keys TAOFF ", "rclone-keys TAOFF TASRC "]);
    expect(calls.filter((call) => call.startsWith("rclone cat taoff:"))).toEqual(
      [`rclone cat taoff:tinyassets-final-backup/workflow-backups/platform/${MANIFEST}`]);
    expect(calls.join("\n")).not.toMatch(/r2-secret-value|spaces-secret-value/);
    // No key in any file the run leaves, anywhere on the fake host (its work root, its own
    // root, the remotes, the call log) or in the stage.
    for (const dir of [h.dir, stage]) {
      const found = spawnSync("grep", ["-rlE", "r2-secret-value|spaces-secret-value", dir],
                              { encoding: "utf8" });
      expect(found.stdout).toBe("");
    }
    // No job of it is networked.
    expect(calls.filter((call) => call.startsWith("docker run") && call.includes("bridge")))
      .toEqual([]);
  });

  it("fails on an object whose read-back is not the manifest's", () => {
    const h = host();
    h.window(["census", "o3", "window"]);
    const second = h.manifest.snapshots[1]!;
    const image = h.window(["offsite", "o3", "window"], OFFSITE_KEY,
                           { flip: `${second.uid}/${second.snapshot_id}.img.gz` });
    expect(image.status).toBe(2);
    expect(image.out).toContain("refused: image 2 of 4's SHA-256 is not its pin");
    expect(image.out).not.toContain("the off-site copy holds");
    const archive = h.window(["offsite", "o3", "window"], OFFSITE_KEY, { flip: ARCHIVE });
    expect(archive.status).toBe(2);
    expect(archive.out).toContain("refused: the archive's SHA-256 is not the manifest's");
    const manifest = h.window(["offsite", "o3", "window"], OFFSITE_KEY, { flip: MANIFEST });
    expect(manifest.status).toBe(2);
    expect(manifest.out).toContain("refused: the off-site manifest read back is not the copy's");
    // Copied again unflipped, it holds.
    expect(h.window(["offsite", "o3", "window"], OFFSITE_KEY).status).toBe(0);
  });

  it("refuses when the store's newest manifest is not the copy's, or the key is short", () => {
    const h = host();
    h.window(["census", "o4", "window"]);
    expect(h.window(["offsite", "o4", "window"], `${SOURCE_KEY}${"a".repeat(32)}\nr2-id\n`).out)
      .toContain("refused: the run was handed no R2_SECRET_ACCESS_KEY");
    writeFileSync(join(h.remote, "platform", "tinyassets-platform-2026-11-08T03-00-00Z.json"),
                  JSON.stringify(h.manifest));
    const newer = h.window(["offsite", "o4", "window"], OFFSITE_KEY);
    expect(newer.status).toBe(2);
    expect(newer.out).toContain(
      "refused: the backup remote's newest manifest is not the one this run's copy was made from");
    expect(existsSync(join(h.remotes, "taoff"))).toBe(false);
  });
});

describe("the job's credentials, in window.sh's own order", () => {
  it("are CREDENTIALS, which is STAGE_INPUTS, and verify, wipe and admit take them on stdin", () => {
    const sh = readFileSync(join(stage, "window.sh"), "utf8");
    const listed = /readonly CREDENTIALS=\(([^)]*)\)/.exec(sh)![1]!.split(/\s+/).filter(Boolean);
    expect(listed).toEqual(Object.values(STAGE_INPUTS));
    expect(listed).toEqual(CREDENTIALS);
    // The job's pipe is made from the list, never a second hard-coded order.
    expect(sh).toContain("for name in \"${CREDENTIALS[@]}\"; do printf '%s\\n' \"${!name}\"; done");
    const record = mkdtempSync(join(tmpdir(), "ta-window-record-"));
    temps.push(record);
    for (const step of ["verify", "wipe", "admit"]) {
      const run = spawnSync(process.execPath, [join(stage, "import.mjs"), step, tmpdir(),
                                               "--run", "r1", "--record", join(record, "r")],
                            { input: "not-hex\nb\nc\nd\ne\n", encoding: "utf8",
                              env: { PATH: process.env["PATH"] } });
      expect(run.stderr).toContain("the account is 32 hex");
    }
  });
});
