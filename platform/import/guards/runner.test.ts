/**
 * The import window's runner (`.github/workflows/import-window.yml`, `window.sh`, `copy.ts`),
 * driven as the workflow drives it, against a fake remote: a synthetic backup in the
 * fixture's shape (`fixture.ts`) behind a fake `rclone`, with the droplet's other tools faked
 * (`fake-host.sh`) and the job bundled as it ships. It holds the dispatch's gate (a real run
 * only with rehearsal false and its confirm; a missing secret named, its value never shown),
 * the copy's refusals (a digest that is not the manifest's, a disk without room), and the
 * output's discipline (counts alone: no principal, no owner key, no path of the copy).
 */
import { execFileSync, spawnSync } from "node:child_process";
import {
  appendFileSync, chmodSync, copyFileSync, existsSync, lstatSync, mkdirSync, mkdtempSync,
  readFileSync, rmSync, statSync, symlinkSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { parse } from "yaml";

import { MOUNT_OPTIONS } from "../backup";
import { checkStores, forbidden, released } from "../copy";
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
  for (const tool of ["rclone", "docker", "mount", "umount", "losetup", "df"]) {
    symlinkSync(join(HERE, "fake-host.sh"), join(bin, tool));
  }
});

afterAll(() => {
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** A droplet: its backup remote made from a fresh fixture, its work root, its env file. */
function host() {
  const f = fixture();
  const dir = mkdtempSync(join(tmpdir(), "ta-window-host-"));
  temps.push(f.work, dir);
  const remote = join(dir, "remote");
  const root = join(dir, "var-tmp");
  mkdirSync(join(remote, "platform"), { recursive: true });
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
  writeFileSync(join(dir, "env"), "BACKUP_DEST=fake:\nNOT A SHELL LINE\n");
  const log = join(dir, "calls.log");
  writeFileSync(log, "");
  const disk = { size: 1_000_000_000_000, avail: 900_000_000_000 };
  const work = (run: string) => join(root, `ta-import-${run}`);
  /** `window.sh <args...>`, as the workflow's SSH session runs it, `input` its stdin. */
  const window = (args: readonly string[], input = "") => {
    const result = spawnSync("bash", [join(stage, "window.sh"), ...args], {
      encoding: "utf8", input,
      env: { PATH: `${bin}:${dirname(process.execPath)}:/usr/bin:/bin`, TA_IMPORT_ROOT: root,
             TA_IMPORT_ENV: join(dir, "env"), TA_IMPORT_LOCK: join(dir, "lock"),
             FAKE_REMOTE: remote, FAKE_LOG: log, FAKE_DF_SIZE: String(disk.size),
             FAKE_DF_AVAIL: String(disk.avail) } });
    return { status: result.status, out: result.stdout + result.stderr };
  };
  const calls = () => readFileSync(log, "utf8").split("\n").filter(Boolean);
  return { f, remote, manifest, publish, disk, work, window, calls };
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
  const workflow = parse(readFileSync(join(REPO, ".github", "workflows", "import-window.yml"),
                                      "utf8")) as {
    on: { workflow_dispatch: { inputs: Record<string, { default?: unknown }> } };
    jobs: { window: { environment: string; steps: { name?: string; run?: string }[] } };
  };
  const steps = workflow.jobs.window.steps;
  const SECRETS = { DO_DROPLET_HOST: "host.example", DO_SSH_USER: "deploy",
                    DO_SSH_KEY: "ssh-key-value-1", IMPORT_EXCLUSION_LIST: "exclusion-value-2",
                    CLOUDFLARE_ACCOUNT_ID: "account-value-3",
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
  });

  it("names a mode's missing secret, never a value", () => {
    const without = (...names: string[]) => Object.fromEntries(Object.entries(SECRETS)
      .map(([name, value]) => [name, names.includes(name) ? "" : value]));
    const stage = gate({ MODE: "stage" }, without("IMPORT_KEY", "IMPORT_R2_ACCESS_KEY_ID"));
    expect(stage.status).toBe(1);
    expect(stage.out).toContain("IMPORT_R2_ACCESS_KEY_ID IMPORT_KEY");
    const bundle = gate({ MODE: "bundle" }, without("IMPORT_EXCLUSION_LIST"));
    expect(bundle.out).toContain("needs the secret(s) IMPORT_EXCLUSION_LIST");
    // census and clean need no credential; bundle no Cloudflare one.
    expect(gate({ MODE: "census" }, without("IMPORT_KEY", "IMPORT_EXCLUSION_LIST")).status).toBe(0);
    expect(gate({ MODE: "bundle" }, without("IMPORT_KEY")).status).toBe(0);
    expect(gate({ MODE: "verify" }, without("DO_SSH_KEY")).out).toContain("DO_SSH_KEY");
    for (const run of [stage, bundle]) {
      for (const value of Object.values(SECRETS)) {
        expect(run.out).not.toContain(value);
      }
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
    const calls = h.calls();
    expect(calls).toContain(`rclone cat fake:/platform/${MANIFEST}`);
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
      expect(call).not.toMatch(/ -e /);
    }
    expect(jobs.filter((call) => call.includes(" census "))[0])
      .toContain(`-v ${work}/census:${work}/census `);

    // Run again: the same copy, never fetched again.
    const again = h.window(["census", "r1", "rehearsal"]);
    expect(again.status).toBe(3);
    expect(h.calls().filter((call) => call.startsWith("rclone copyto")))
      .toHaveLength(1 + Object.keys(UIDS).length);
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
    // On a copy with bundles, the job takes them as its environment, by name alone, with the
    // network; this account is no account, so the job refuses before any request.
    h.window(["census", "s1", "rehearsal"]);
    mkdirSync(join(h.work("s1"), "bundle", "out"), { recursive: true });
    const staged = h.window(["stage", "s1", "rehearsal"], `${values.join("\n")}\n`);
    expect(staged.status).toBe(2);
    expect(staged.out).toContain("refused: the account is 32 hex");
    const call = h.calls().filter((each) => each.startsWith("docker run")
                                            && each.includes(" stage "))[0]!;
    expect(call).toContain(`--network bridge -v ${stage}:${stage}:ro`);
    expect(call).toContain(CREDENTIALS.map((name) => `-e ${name}`).join(" "));
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
});
