/**
 * The job's entry (`cli.ts`): an image is read only where the kernel says it is mounted
 * read-only XFS, the census and the bundle run end to end from their arguments with
 * counts alone on stdout, and the job, bundled as it ships, runs in plain Node.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { lstatSync, mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { mountProblem } from "../backup";
import { main, tiersOf } from "../cli";
import { ALICE, BOB, fixture, FOUNDER, keyOf, NEWBIE, TIERS, UIDS } from "./fixture";

const temps: string[] = [];
afterEach(() => {
  vi.restoreAllMocks();
  for (const dir of temps.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

/** A mountinfo line for `at`, with its per-mount options and file system. */
const line = (at: string, options: string, type = "xfs", device = "/dev/loop0") =>
  `61 25 7:0 / ${at.replaceAll(" ", "\\040")} ${options} - ${type} ${device} ro,nouuid,norecovery`;

describe("the read-only mount", () => {
  it("is an owner's XFS image mounted read-only, by the kernel's word, or refused", () => {
    expect(mountProblem(line("/w/mnt/300001", "ro,nosuid,nodev,relatime"), "/w/mnt/300001"))
      .toBeNull();
    expect(mountProblem(line("/w/a b", "ro,nosuid"), "/w/a b")).toBeNull();
    expect(mountProblem(line("/w/mnt/300001", "rw,nosuid"), "/w/mnt/300001"))
      .toBe("is mounted writable");
    expect(mountProblem(line("/w/mnt/300001", "ro", "ext4"), "/w/mnt/300001"))
      .toBe("is ext4, not an owner's XFS image");
    expect(mountProblem(line("/w/mnt/300002", "ro"), "/w/mnt/300001")).toBe("is not a mount point");
    // The last mount at a point is the one read: a writable one over a read-only one refuses.
    expect(mountProblem([line("/w/m", "ro"), line("/w/m", "rw")].join("\n"), "/w/m"))
      .toBe("is mounted writable");
    // Tied to its own image: the loop device's backing file is the image the pin hashed.
    const backing = (device: string) => device === "/dev/loop0" ? "/w/300001.img" : null;
    expect(mountProblem(line("/w/mnt/300001", "ro"), "/w/mnt/300001", backing, "/w/300001.img"))
      .toBeNull();
    expect(mountProblem(line("/w/mnt/300001", "ro"), "/w/mnt/300001", backing, "/w/300002.img"))
      .toBe("is not its own image: its loop device's backing file is another");
    expect(mountProblem(line("/w/mnt/300001", "ro", "xfs", "/dev/sda1"), "/w/mnt/300001", backing,
                        "/w/300001.img")).toMatch(/not its own image/);
  });

  it("takes the control record's tiers, each with its storage in bytes, or refuses", () => {
    expect(tiersOf(JSON.stringify({ outside: "off", tiers: TIERS }))).toEqual(TIERS);
    expect(() => tiersOf(JSON.stringify({ outside: "off" }))).toThrow(/no tiers/);
    expect(() => tiersOf(JSON.stringify({ tiers: { free: { runs: 5 } } }))).toThrow(/no tiers/);
  });
});

describe("the job's entry", () => {
  function setup() {
    const f = fixture();
    const scratch = mkdtempSync(join(tmpdir(), "ta-cli-"));
    temps.push(f.work, scratch);
    const control = join(scratch, "control.json");
    writeFileSync(control, JSON.stringify({ outside: "off", tiers: TIERS }));
    const uids = Object.values(UIDS);
    const mounted = uids.map((uid, i) => line(realpathSync(join(f.work, "mnt", String(uid))),
                                              "ro,nosuid,nodev", "xfs", `/dev/loop${i}`)).join("\n");
    // The kernel's loop devices, as these mounts would have them: each its own image.
    const backing = (device: string) => {
      const i = Number(/^\/dev\/loop(\d+)$/.exec(device)?.[1] ?? -1);
      return i >= 0 && i < uids.length ? realpathSync(join(f.work, `${uids[i]}.img`)) : null;
    };
    return { f, scratch, control, mounted, backing };
  }

  it("runs the census from its arguments: counts on stdout, the table 0600, ready", () => {
    const { f, scratch, control, mounted, backing } = setup();
    const out: string[] = [];
    vi.spyOn(process.stdout, "write").mockImplementation((text) => {
      out.push(String(text));
      return true;
    });
    const table = join(scratch, "census.private.tsv");
    expect(main(["census", f.work, "--tiers", control, "--table", table], () => mounted, backing))
      .toBe(0);
    expect(JSON.parse(out.join(""))).toMatchObject({ ready: true, problems: {} });
    for (const principal of [ALICE, BOB, FOUNDER]) {
      expect(out.join("")).not.toContain(principal);
    }
    expect(lstatSync(table).mode & 0o777).toBe(0o600);
    expect(readFileSync(table, "utf8")).toContain(`${keyOf(ALICE)}\tuid\t300001`);
    // Writable mounts are never read: every owner refused, the census not ready.
    const writable = mounted.replaceAll("ro,nosuid", "rw,nosuid");
    expect(main(["census", f.work, "--tiers", control, "--table", join(scratch, "t2")],
                () => writable, backing)).toBe(3);
    expect(JSON.parse(out.slice(-1)[0]!)["problems"])
      .toEqual({ not_mounted: 3, erasure_unread: 4 });
    // Each image mounted where another's should be: never read as that owner's.
    const swapped = (device: string) => backing(device === "/dev/loop0" ? "/dev/loop1"
      : device === "/dev/loop1" ? "/dev/loop0" : device);
    expect(main(["census", f.work, "--tiers", control, "--table", join(scratch, "t3")],
                () => mounted, swapped)).toBe(3);
    expect(JSON.parse(out.slice(-1)[0]!)["problems"])
      .toEqual({ not_mounted: 2, erasure_unread: 2 });
  });

  it("runs the bundle from its arguments, the exclusion list read from its file", () => {
    const { f, scratch, control, mounted, backing } = setup();
    vi.spyOn(process.stdout, "write").mockImplementation(() => true);
    const list = join(scratch, "exclude.txt");
    writeFileSync(list, `# start fresh\n${FOUNDER}\n`);
    const out = join(scratch, "out");
    const priv = join(scratch, "private");
    expect(main(["bundle", f.work, "--tiers", control, "--exclude", list, "--out", out,
                 "--private", priv], () => mounted, backing)).toBe(0);
    expect(readdirSync(out).sort()).toEqual([keyOf(ALICE), keyOf(BOB), keyOf(NEWBIE)].sort());
    expect(readdirSync(priv)).toContain(keyOf(FOUNDER));
    expect(main(["bundle", f.work, "--tiers", control, "--exclude", list, "--dry-run"],
                () => mounted, backing)).toBe(0);
  });

  it("runs bundled as it ships, in plain Node, and refuses images it does not see mounted", () => {
    const { f, scratch, control } = setup();
    const platform = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
    const built = join(scratch, "import.mjs");
    execFileSync(join(platform, "node_modules", ".bin", "esbuild"),
                 [join(platform, "import", "cli.ts"), "--bundle", "--platform=node",
                  "--format=esm", "--target=node22", `--outfile=${built}`, "--log-level=error"]);
    const run = spawnSync(process.execPath, [built, "census", f.work, "--tiers", control,
                                             "--table", join(scratch, "t")], { encoding: "utf8" });
    // This host's own mounts hold none of the fixture's images: each owner is refused.
    expect(run.status).toBe(3);
    expect(JSON.parse(run.stdout))
      .toMatchObject({ ready: false, problems: { not_mounted: 3, erasure_unread: 4 } });
    for (const principal of [ALICE, BOB, FOUNDER]) {
      expect(run.stdout).not.toContain(principal);
    }
    const bad = spawnSync(process.execPath, [built, "bundle", f.work], { encoding: "utf8" });
    expect(bad.status).toBe(2);
    expect(bad.stderr).toMatch(/^refused: --tiers is required$/m);
  });
});
