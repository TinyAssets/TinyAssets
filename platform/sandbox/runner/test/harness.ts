/**
 * The runner's test harness, in Node, tests only. Each test sandbox (`Box`) is the
 * template's layout (`layout.ts`) on a root of its own: a tmpfs in the suite's own mount
 * namespace (`root-tests.mjs`) that one long-lived process, in mount, pid and uts namespaces
 * of its own, pivots into (`box.sh`), the host's system directories read-only in it and a
 * /dev, /dev/shm, /tmp and /proc of its own. Every runner command enters it (`nsenter`) as
 * the Worker runs one, `ta-runner <sha> <command>` through the loader as it ships, so the
 * runner runs at the sandbox's real paths; its teardown is one SIGKILL of that process.
 * The test reads and writes the box's files from outside (`path`). A `Sandbox` adds a fake
 * CLI per record at the record's own binary path and the runner the template carries
 * (`template/bundle.mjs`), installed; `Doors` is a fake doors server for the run's event
 * door, the sign-in report and the sync doors.
 */
import { execFileSync, spawn, type SpawnSyncReturns, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmodSync, chownSync, closeSync, mkdirSync, mkdtempSync, openSync, readdirSync,
         readFileSync, readlinkSync, rmSync, statSync, writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EVENTS_DOOR, SIGNIN_EVENTS_DOOR } from "../../../contract/src/egress";
import { bundledSources } from "../../../sources/src/catalog";

import { binPath } from "../engine";
import { incoming, inboxDoc, INSTALL, LEASE_FILE, type LeaseFile, LOADER, RUNNERS,
         type SigninDoc, type StartDoc } from "../protocol";
import { DIRS, EGRESS_CA, GROUPS, NODE, USERS } from "./layout";

export { POOL_UIDS, SYNC_UID, TA_GID } from "./layout";

const HERE = dirname(fileURLToPath(import.meta.url));
const SANDBOX = join(HERE, "..", "..");
const BOX = join(HERE, "box.sh");
/** The loader as the template ships it, byte for byte. */
const SHIPPED_LOADER = join(SANDBOX, "template", "ta-runner");
/** A root shell's standard PATH, the one variable a runner command is given here. */
export const BOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
/**
 * A box's disk: under twice the runner's reserve (`RESERVE_BYTES`), so the runner meets a
 * disk with no room for it and takes its real "no room" path, as a full sandbox would.
 */
const BOX_BYTES = "384m";
/** The room for a box's own writes to its `/etc` and `/usr` (its users, sudoers, modes). */
const WRITES_BYTES = "64m";
/**
 * The host's trees a box sees as its own: each an overlay of the host's, its writes the box's
 * (`#layout`). The rest of the host's system directories are bound read-only (`box.sh`).
 */
const OVERLAID: readonly string[] = Object.freeze(["/etc", "/usr"]);
/** How long a box may take to come up before its making fails loudly. */
const READY_MS = 15_000;
const UNSHARE = ["--mount", "--pid", "--fork", "--kill-child", "--uts", "--propagation",
                 "private"];

let bundled: string | null = null;

/** The runner's bundle, built once per test file (outside every box). */
export function runnerBundle(): string {
  if (bundled === null) {
    const out = mkdtempSync(join(tmpdir(), "ta-runner-bundle-"));
    execFileSync(process.execPath, [join(SANDBOX, "template", "bundle.mjs"), out]);
    bundled = join(out, "runner.mjs");
  }
  return bundled;
}

/** Every box's process this one started, killed when it exits (a test that died left it). */
const inits = new Set<number>();
process.on("exit", () => {
  for (const pid of inits) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      // gone already
    }
  }
});

function pause(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function must(argv: readonly string[]): void {
  const done = spawnSync(argv[0]!, argv.slice(1), { encoding: "utf8" });
  if (done.status !== 0) {
    throw new Error(`${argv.join(" ")} failed (${String(done.status)}): ${done.stderr}`);
  }
}

/** The mounts `/proc/<pid>/mountinfo` lists: each one's device, its target, its options. */
export function mountsOf(pid: number | "self"):
    { device: string; target: string; options: string[]; type: string; super: string[] }[] {
  return readFileSync(`/proc/${pid}/mountinfo`, "utf8").trim().split("\n").map((line) => {
    const [fields, after] = line.split(" - ");
    const parts = fields!.split(" ");
    const [type, , superOptions] = after!.split(" ");
    return { device: parts[2]!, target: parts[4]!, options: parts[5]!.split(","),
             type: type!, super: (superOptions ?? "").split(",") };
  });
}

/**
 * The suite's own mount namespace (`root-tests.mjs` starts vitest in one, and names the one
 * it left in `TA_HOST_MOUNTS`), or a loud failure: a box's root is mounted there, never on
 * the host's.
 */
/**
 * The suite's one Node for its boxes (`root-tests.mjs` `boxNode`: a root-owned, 0755 copy of
 * the host's, the same bytes), or a loud failure: a box binds it, never the host's own.
 */
function boxNode(): string {
  const node = process.env["TA_BOX_NODE"];
  const made = node === undefined ? null : statSync(node, { throwIfNoEntry: false });
  if (node === undefined || made === undefined || made === null || made.uid !== 0
      || (made.mode & 0o7777) !== 0o755) {
    throw new Error("a test sandbox binds the suite's root-owned copy of Node (TA_BOX_NODE): " +
                    "run the runner's tests through root-tests.mjs (`npm run test:root -w " +
                    "sandbox`)");
  }
  return node;
}

function suiteNamespace(): void {
  const host = process.env["TA_HOST_MOUNTS"];
  if (host === undefined || host === readlinkSync("/proc/self/ns/mnt")) {
    throw new Error("the runner's test sandboxes mount their roots in the suite's own mount " +
                    "namespace: run them through root-tests.mjs (`npm run test:root -w " +
                    "sandbox`), as root or with passwordless sudo");
  }
}

/**
 * A real disk of `size` (`8m`: a tmpfs, so `statfs` reads it exactly and a write past it
 * meets a real `ENOSPC`), mounted in the suite's own namespace; `remove` unmounts it.
 */
export function disk(size: string): { readonly dir: string; remove(): void } {
  suiteNamespace();
  const dir = mkdtempSync(join(tmpdir(), "ta-disk-"));
  must(["mount", "-t", "tmpfs", "-o", `size=${size},mode=0755`, "tmpfs", dir]);
  return {
    dir,
    remove: () => {
      spawnSync("umount", ["-l", dir], { stdio: "ignore" });
      if (mountsOf("self").some((mount) => mount.target === dir)) {
        throw new Error(`a test disk is still mounted: ${dir}`);
      }
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

/** A test sandbox: the template's layout on a root of its own, its namespaces entered. */
export class Box {
  readonly base = mkdtempSync(join(tmpdir(), "ta-box-"));
  /** The box's `/`, as the test (outside the box) sees it. */
  readonly root = join(this.base, "root");
  /** Where its `/etc`'s and `/usr`'s own writes go (each overlay's upper), outside its root. */
  readonly writes = join(this.base, "writes");
  /** The host's pid of the box's init: killing it ends every process in the box. */
  readonly init: number;
  /** The harness's end of the init's stdin: the box ends when it closes. */
  lifeline: NodeJS.WritableStream | null = null;

  constructor() {
    try {
      suiteNamespace();
      mkdirSync(this.root);
      must(["mount", "-t", "tmpfs", "-o", `size=${BOX_BYTES},mode=0755`, "tmpfs", this.root]);
      this.#layout();
      this.init = this.#start();
    } catch (error) {
      this.remove();
      throw error;
    }
  }

  /** Where an overlaid tree's writes go in the box (`/etc`: `<writes>/etc/upper`). */
  upperOf(tree: string): string {
    return join(this.writes, tree, "upper");
  }

  /** The template's users, groups and directories (`layout.ts`), on the host's trees. */
  #layout(): void {
    // The host's `/etc` (a trimmed one breaks `sudo -l`) and `/usr`, each as an overlay: the
    // host's beneath, never written, every write the box's own, above, on a disk of the
    // box's (outside its root). So nothing of them is copied (a CI host's /etc/skel holds
    // gigabytes of toolchains), no write reaches the host's, and `prepare` takes another
    // user's write away from a host file as it does a sandbox's own (a CI host's JDKs, in
    // /usr/lib/jvm, are writable by others), in the box alone. `metacopy`: a change of mode
    // or owner copies up the file's metadata alone, never its bytes, however large the file.
    mkdirSync(this.writes);
    must(["mount", "-t", "tmpfs", "-o", `size=${WRITES_BYTES},mode=0700`, "tmpfs", this.writes]);
    for (const tree of OVERLAID) {
      const upper = this.upperOf(tree);
      const work = join(dirname(upper), "work");
      mkdirSync(upper, { recursive: true });
      mkdirSync(work);
      mkdirSync(this.path(tree));
      must(["mount", "-t", "overlay", "overlay", "-o",
            `lowerdir=${tree},upperdir=${upper},workdir=${work},metacopy=on`, this.path(tree)]);
    }
    // The template's users, each file made anew, never written through: the host's may be a
    // link (a systemd-resolved host's resolv.conf), which a write would follow onto the host.
    const own = (path: string, text: string, mode = 0o644) => {
      rmSync(this.path(path), { force: true });
      writeFileSync(this.path(path), text, { mode, flag: "wx" });
    };
    own("/etc/passwd", USERS.map((each) =>
      `${each.name}:x:${each.uid}:${each.gid}::${each.home}:${each.shell}\n`).join(""));
    own("/etc/group", GROUPS.map((each) =>
      `${each.name}:x:${each.gid}:${each.members.join(",")}\n`).join(""));
    own("/etc/gshadow", GROUPS.map((each) =>
      `${each.name}:!::${each.members.join(",")}\n`).join(""), 0o640);
    own("/etc/resolv.conf", "");
    const id = (name: string, of: readonly { name: string }[], key: "uid" | "gid") =>
      (of.find((each) => each.name === name) as Record<string, unknown> | undefined)?.[key];
    for (const dir of DIRS) {
      mkdirSync(this.path(dir.path), { recursive: true });
      chownSync(this.path(dir.path), id(dir.owner, USERS, "uid") as number,
                id(dir.group, GROUPS, "gid") as number);
      chmodSync(this.path(dir.path), dir.mode);
    }
  }

  /** The box's init started, and its host pid once it has pivoted. */
  #start(): number {
    const ready = join(this.base, "ready");
    const log = openSync(join(this.base, "box.log"), "w");
    const child = spawn("unshare", [...UNSHARE, "--", "/bin/sh", BOX, this.root, ready,
                                    SHIPPED_LOADER, LOADER, boxNode(), NODE, EGRESS_CA],
                        { stdio: ["pipe", log, log], detached: true });
    closeSync(log);
    child.unref();
    // The box's lifeline: its init ends at this pipe's end, so a test process killed outright
    // (no exit hook runs) leaves no box behind.
    (child.stdin as unknown as { unref(): void }).unref();
    this.lifeline = child.stdin!;
    for (const until = Date.now() + READY_MS; ;) {
      let text = "";
      try {
        text = readFileSync(ready, "utf8");
      } catch {
        // not made yet
      }
      if (/^[0-9]+\n$/.test(text)) {
        const pid = Number(text);
        inits.add(pid);
        return pid;
      }
      if (Date.now() > until) {
        try {
          process.kill(child.pid!, "SIGKILL");  // and so its box (`--kill-child`)
        } catch {
          // gone already
        }
        throw new Error("a test sandbox did not come up: " +
                        readFileSync(join(this.base, "box.log"), "utf8"));
      }
      pause(10);
    }
  }

  /** A path in the box, as the test (outside it) reaches it. */
  path(path: string): string {
    return join(this.root, path);
  }

  write(path: string, text: string): void {
    mkdirSync(dirname(this.path(path)), { recursive: true });
    writeFileSync(this.path(path), text);
  }

  /** `argv` run in the box, as root, to its end. */
  exec(argv: readonly string[], input?: string): SpawnSyncReturns<string> {
    return spawnSync("nsenter", [...this.#enter(), ...argv], {
      env: { PATH: BOX_PATH }, encoding: "utf8",
      ...(input === undefined ? {} : { input }) });
  }

  /** `argv` run in the box, to its end, the test's own event loop free meanwhile. */
  execAsync(argv: readonly string[]): Promise<number> {
    return new Promise((resolve) => {
      spawn("nsenter", [...this.#enter(), ...argv], {
        env: { PATH: BOX_PATH }, stdio: "ignore" })
        .on("close", (code) => resolve(code ?? 1));
    });
  }

  #enter(): string[] {
    return ["-t", String(this.init), "-m", "-p", "-u", "--"];
  }

  /** A process of the box's own that lives until it is killed: its pid in the box. */
  live(): number {
    const done = this.exec(["/bin/sh", "-c", "sleep 600 </dev/null >/dev/null 2>&1 & echo $!"]);
    const pid = Number(done.stdout.trim());
    if (!Number.isSafeInteger(pid) || pid <= 1) {
      throw new Error(`no live process was started in the box: ${done.stderr}`);
    }
    return pid;
  }

  /** The box's process `pid` (its pid in the box) killed. */
  kill(pid: number): void {
    this.exec(["kill", "-9", String(pid)]);
  }

  /** The box's processes whose real uid is `uid`, by their host pids. */
  processesOf(uid: number): number[] {
    const ns = readlinkSync(`/proc/${this.init}/ns/pid`);
    return readdirSync("/proc").filter((name) => /^[0-9]+$/.test(name)).flatMap((name) => {
      try {
        const real = /^Uid:\s+([0-9]+)/m.exec(readFileSync(`/proc/${name}/status`, "utf8"))?.[1];
        return readlinkSync(`/proc/${name}/ns/pid`) === ns && Number(real) === uid
          ? [Number(name)] : [];
      } catch {
        return [];  // gone, or a zombie's
      }
    });
  }

  /** The box's mounts, as its init sees them. */
  mounts(): ReturnType<typeof mountsOf> {
    return mountsOf(this.init);
  }

  /** Every process of the box ended, its root unmounted and gone. */
  remove(): void {
    const init = (this as { init?: number }).init;
    if (init !== undefined) {
      try {
        process.kill(init, "SIGKILL");
      } catch {
        // gone already
      }
      inits.delete(init);
      for (const until = Date.now() + 10_000; ;) {
        try {
          statSync(`/proc/${init}`);
        } catch {
          break;
        }
        if (Date.now() > until) {
          throw new Error(`a test sandbox's init (${init}) outlived its SIGKILL`);
        }
        pause(10);
      }
    }
    if (process.env["TA_KEEP_ROOT"] !== undefined) {
      return;
    }
    for (const mount of [...OVERLAID.map((tree) => this.path(tree)), this.root, this.writes]) {
      spawnSync("umount", ["-l", mount], { stdio: "ignore" });
    }
    const left = mountsOf("self").filter((mount) => mount.target === this.base
      || mount.target.startsWith(`${this.base}/`));
    if (left.length > 0) {
      throw new Error(`a test sandbox's mounts are still there: ${left.map((mount) =>
        mount.target).join(" ")}`);
    }
    rmSync(this.base, { recursive: true, force: true });
  }
}

/** One fake CLI's behaviour. */
export interface FakeCli {
  readonly lines: readonly unknown[];
  readonly exit?: number;
  readonly sleepMs?: number;
  /** Ignore SIGTERM (so only the kill stops it). */
  readonly stubborn?: boolean;
  /** Files written before printing: from its cwd, absolute, or `~/` from its home. */
  readonly writes?: Readonly<Record<string, string>>;
  /** Links made before printing, path to target, the same way. */
  readonly links?: Readonly<Record<string, string>>;
  /** Files whose text it reports (or `null` when it cannot read one), the same way. */
  readonly dumps?: readonly string[];
  /** Files whose every ancestor's owner it reports, up to `/`. */
  readonly owners?: readonly string[];
  /** A process left running after it exits (to be killed by uid). */
  readonly daemon?: boolean;
  /** What `status_argv` prints and exits with. */
  readonly status?: { readonly out: string; readonly exit: number };
  /**
   * An absolute path it waits to exist before it exits (after its lines and `sleepMs`), so a
   * test holds runs live together by an event, never by a guess at how long a start takes.
   */
  readonly holdUntil?: string;
  /**
   * HTTP requests it makes before anything else, each to a URL (and headers and body) filled
   * from what the run gave it: `{env:NAME}`, `{after:NAME}` (a variable's value after its
   * first `: `), `{argv:KEY}` (the value of its `KEY=...` argument, quotes dropped),
   * `{json:FILE#a.b}` (`*` the first key), `{placeholder}` (the run's `ta_ph_` key).
   */
  readonly requests?: readonly { readonly url: string; readonly method?: string;
                                 readonly headers?: Readonly<Record<string, string>>;
                                 readonly body?: string;
                                 /** Made only when one of its arguments holds this. */
                                 readonly if?: string }[];
  /**
   * A file (`~/...`, under one of the record's session paths) its prompts are kept in, as a
   * CLI keeps its conversation: `{history}` in a request is what it held before this run.
   */
  readonly history?: string;
  /**
   * A sign-in (its record's `login.argv`): `lines` printed at once (stdout, or stderr with
   * `stderr`); with `code`, one line read from stdin, then `writes` made and exit 0; without
   * it, `writes` made at once, then it stays up (a CLI that does not exit once signed in)
   * unless `exit` is given.
   */
  readonly login?: { readonly lines: readonly string[]; readonly code?: boolean;
                     readonly stderr?: boolean; readonly writes?: Readonly<Record<string, string>>;
                     readonly exit?: number; readonly tty?: boolean };
}

/** The fake CLI's own program: what it was run with goes to `/fake-seen`. */
const FAKE = String.raw`#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const me = process.argv[1];
const plan = JSON.parse(fs.readFileSync(me + ".plan.json", "utf8"));
const where = (file) => file.startsWith("~/") ? path.join(process.env.HOME, file.slice(2))
  : file.startsWith("$TA_SCRATCH/") ? path.join(process.env.TA_SCRATCH, file.slice(12))
  : path.resolve(file);
let stdin = "";
const fill = (text) => text
  .replace(/\{env:([A-Z0-9_]+)\}/g, (_, name) => process.env[name] ?? "")
  .replace(/\{after:([A-Z0-9_]+)\}/g, (_, name) => (process.env[name] ?? "").split(": ").slice(1)
    .join(": "))
  .replace(/\{argv:([^}]+)\}/g, (_, key) => {
    const arg = process.argv.find((each) => each.startsWith(key + "="));
    return arg === undefined ? "" : arg.slice(key.length + 1).replace(/^"|"$/g, "");
  })
  .replace(/\{json:([^#}]+)#([^}]+)\}/g, (_, file, dotted) => {
    try {
      let value = JSON.parse(fs.readFileSync(where(file), "utf8"));
      for (const key of dotted.split(".")) {
        value = key === "*" ? Object.values(value)[0] : value[key];
      }
      return String(value);
    } catch { return ""; }
  })
  .replace(/\{placeholder\}/g, () =>
    Object.values(process.env).find((value) => value.startsWith("ta_ph_")) ?? "")
  .replace(/\{history\}/g, () => {
    try { return fs.readFileSync(where(plan.history), "utf8"); } catch { return ""; }
  });
const request = async () => {
  for (const each of plan.requests || []) {
    if (each.if !== undefined && !process.argv.some((arg) => arg.includes(each.if))) {
      continue;
    }
    try {
      await fetch(fill(each.url), { method: each.method || "POST",
        headers: Object.fromEntries(Object.entries(each.headers || {}).map(([name, value]) =>
          [name, fill(value)])),
        body: each.body === undefined ? undefined : fill(each.body),
        signal: AbortSignal.timeout(3000) });
    } catch {}
  }
  if (plan.history) {
    fs.mkdirSync(path.dirname(where(plan.history)), { recursive: true });
    fs.appendFileSync(where(plan.history), stdin + "\n");
  }
};
const done = () => {
  const dumps = {};
  for (const file of plan.dumps || []) {
    try { dumps[file] = fs.readFileSync(where(file), "utf8"); } catch { dumps[file] = null; }
  }
  const owners = {};
  for (const file of plan.owners || []) {
    const chain = [];
    for (let at = where(file); ; at = path.dirname(at)) {
      chain.push({ path: at, uid: fs.statSync(at).uid });
      if (at === "/") { break; }
    }
    owners[file] = chain;
  }
  const at = performance.timeOrigin + performance.now();
  // Written whole, then renamed into place: a test reading the record while a CLI runs
  // never sees half of it.
  const seen = path.join("/fake-seen",
                         path.basename(me) + "." + at.toFixed(3) + "." + process.pid + ".json");
  fs.writeFileSync(seen + ".part",
    JSON.stringify({ at, argv: process.argv.slice(2), env: process.env, cwd: process.cwd(), stdin,
                     uid: process.getuid(), gid: process.getgid(), groups: process.getgroups(),
                     dumps, owners }));
  fs.renameSync(seen + ".part", seen);
  if (process.argv.includes("status")) {
    const status = plan.status || { out: "", exit: 1 };
    process.stdout.write(status.out);
    process.exit(status.exit);
  }
  if (plan.stubborn) { process.on("SIGTERM", () => {}); }
  else { process.on("SIGTERM", () => process.exit(143)); }
  for (const [file, text] of Object.entries(plan.writes || {})) {
    fs.mkdirSync(path.dirname(where(file)), { recursive: true });
    fs.writeFileSync(where(file), text);
  }
  for (const [file, target] of Object.entries(plan.links || {})) {
    fs.mkdirSync(path.dirname(where(file)), { recursive: true });
    try { fs.unlinkSync(where(file)); } catch {}
    fs.symlinkSync(target, where(file));
  }
  if (plan.daemon) {
    spawn("/bin/sh", ["-c", "sleep 600"], { detached: true, stdio: "ignore" }).unref();
  }
  for (const line of plan.lines) {
    process.stdout.write((typeof line === "string" ? line : JSON.stringify(line)) + "\n");
  }
  const exit = () => setTimeout(() => process.exit(plan.exit || 0), plan.sleepMs || 0);
  if (plan.holdUntil) {
    const poll = () => fs.existsSync(plan.holdUntil) ? exit() : setTimeout(poll, 20);
    poll();
  } else {
    exit();
  }
};
if (plan.login && !process.argv.includes("status")) {
  const record = (extra) => {
    const at = performance.timeOrigin + performance.now();
    fs.writeFileSync(path.join("/fake-seen",
                               path.basename(me) + "." + at.toFixed(3) + "." + process.pid + ".json"),
      JSON.stringify({ at, argv: process.argv.slice(2), env: process.env, cwd: process.cwd(),
                       stdin, uid: process.getuid(), gid: process.getgid(),
                       groups: process.getgroups(), dumps: {}, owners: {},
                       tty: process.stdout.isTTY === true,
                       homes: Object.fromEntries(Object.entries(process.env)
                         .filter(([, value]) => value.startsWith(process.env.HOME + "/"))
                         .map(([name, value]) => [name, fs.existsSync(value)])), ...extra }));
  };
  const finish = () => {
    for (const [file, text] of Object.entries(plan.login.writes || {})) {
      fs.mkdirSync(path.dirname(where(file)), { recursive: true });
      fs.writeFileSync(where(file), text);
    }
    record({});
    if (plan.login.code || plan.login.exit !== undefined) {
      process.exit(plan.login.exit || 0);
    }
    setInterval(() => {}, 1000);  // up until stopped
  };
  process.on("SIGTERM", () => process.exit(143));
  for (const line of plan.login.lines) {
    (plan.login.stderr ? process.stderr : process.stdout).write(line + "\n");
  }
  if (plan.login.code) {
    process.stdin.on("data", (d) => {
      stdin += d;
      if (stdin.includes("\n")) { finish(); }
    });
  } else {
    finish();
  }
} else if (!process.argv.includes("status")) {
  const go = () => request().then(done, done);
  process.stdin.on("data", (d) => { stdin += d; });
  process.stdin.on("end", go);
  process.stdin.on("error", go);
} else { done(); }
`;

export interface Posted {
  /** When it came, by the doors' clock. */
  readonly at: number;
  readonly path: string;
  readonly headers: IncomingMessage["headers"];
  readonly body: Record<string, unknown>;
}

/** The fake doors: what the runner posted, and how the doors answer. */
export class Doors {
  readonly posts: Posted[] = [];
  /** Files the sync feed serves, by path: text, and its version. */
  files = new Map<string, { text: string; version: number }>();
  /** How many event posts answer 503 `events_busy` before one is taken. */
  busy = 0;
  /** Event posts answered 403 (the run ended by the object). */
  ended = false;
  /** Upload intents begun, and the chunks they received, by intent id. */
  readonly uploads = new Map<string, { size: number; chunks: string[]; got: string[] }>();
  /** A commit op the object refuses, by its path: its reason, or null to commit it. */
  refuse: (path: string) => string | null = () => null;
  /**
   * An upload's begin the object refuses, by its size: its status and the object's whole
   * message (a full storage's 507 among them), or null to begin it.
   */
  refuseBegin: (size: number) => { status: number; message: string } | null = () => null;
  /** How long the first sync commit waits before it is answered: a slow sync-out. */
  commitDelayMs = 0;
  /** Uploads the sync agent aborted, by intent id. */
  readonly aborted: string[] = [];
  #server: Server | null = null;
  #version = 100;
  origin = "";

  async listen(): Promise<void> {
    this.#server = createServer((request, response) => {
      const parts: Buffer[] = [];
      request.on("data", (chunk: Buffer) => { parts.push(chunk); });
      request.on("end", () => {
        const raw = Buffer.concat(parts).toString("utf8");
        const body = raw === "" ? {} : JSON.parse(raw) as Record<string, unknown>;
        const path = request.url ?? "";
        this.posts.push({ at: Date.now(), path, headers: request.headers, body });
        const json = (status: number, value: unknown, headers: Record<string, string> = {}) => {
          response.writeHead(status, { "content-type": "application/json", ...headers });
          response.end(JSON.stringify(value));
        };
        if (EVENTS_DOOR.test(path) || SIGNIN_EVENTS_DOOR.test(path)) {
          if (this.ended) {
            return json(403, { error: { code: "run_ended" } });
          }
          if (this.busy > 0) {
            this.busy -= 1;
            return json(503, { error: { code: "events_busy" } }, { "retry-after": "1" });
          }
          return json(200, { relayed: (body["frames"] as unknown[]).length });
        }
        if (path === "/signin") {
          return json(200, { reported: true });
        }
        if (path === "/sync/changes") {
          const after = body["after"] as number;
          const changes = [...this.files.entries()].filter(([, file]) => file.version > after)
            .map(([file, { version }]) => ({ version, path: file, op: "write", kind: "file",
                                             content_id: "x", size: 1, mode: 0, writer: "owner",
                                             at: 1 }));
          return json(200, { changes, cursor: Math.max(after, ...changes.map((c) => c.version)),
                             reset: false });
        }
        if (path === "/sync/read") {
          const file = this.files.get(body["path"] as string);
          if (file === undefined) {
            return json(404, { error: { code: "not_found" } });
          }
          return json(200, { entry: { path: body["path"], kind: "file", version: file.version,
                                      etag: "e", size: file.text.length, mode: 0, mtime_ms: 1,
                                      writer: "owner" },
                             inline: Buffer.from(file.text).toString("base64") });
        }
        if ((path === "/sync/upload_begin" || path === "/sync/upload_chunks")
            && (typeof body["lease_id"] !== "string" || typeof body["epoch"] !== "number")) {
          return json(403, { error: { code: "sync_fenced" } });  // a sync upload is the lease's
        }
        if (path === "/sync/upload_begin" && this.refuseBegin(body["size"] as number) !== null) {
          const { status, message } = this.refuseBegin(body["size"] as number)!;
          return json(status, { error: { code: /^[a-z_]+/.exec(message)?.[0] ?? "refused",
                                         message } });
        }
        if (path === "/sync/upload_abort") {
          this.aborted.push(body["intent_id"] as string);
          return json(200, { aborted: true });
        }
        if (path === "/sync/upload_begin") {
          const id = `${"0".repeat(31)}${this.uploads.size + 1}`;
          this.uploads.set(id, { size: body["size"] as number,
                                 chunks: body["chunks"] as string[], got: [] });
          return json(200, { intent_id: id, missing: body["chunks"], expires_at: 1 });
        }
        if (path === "/sync/upload_chunks") {
          const intent = this.uploads.get(body["intent_id"] as string)!;
          for (const chunk of body["chunks"] as { sha256: string }[]) {
            intent.got.push(chunk.sha256);
          }
          return json(200, { created: (body["chunks"] as unknown[]).length });
        }
        if (path === "/sync/commit" && this.commitDelayMs > 0) {
          const wait = this.commitDelayMs;
          this.commitDelayMs = 0;
          const ops = body["ops"] as { path: string; op: string }[];
          setTimeout(() => json(200, { outcomes: ops.map((op) => ({ path: op.path,
            outcome: "committed",
            entry: op.op === "remove" ? null : { version: this.#version += 1 } })) }), wait);
          return;
        }
        if (path === "/sync/commit") {
          const ops = body["ops"] as { path: string; op: string }[];
          return json(200, { outcomes: ops.map((op) => {
            const reason = this.refuse(op.path);
            // The object's refusal, by its words and the code they name (as its own does).
            return reason !== null ? { path: op.path, outcome: "refused", reason,
                                       code: /^[a-z_]+(?=:)/.exec(reason)?.[0] ?? "refused" }
              : { path: op.path, outcome: "committed",
                  entry: op.op === "remove" ? null : { version: this.#version += 1 } };
          }) });
        }
        return json(404, { error: { code: "not_found" } });
      });
    });
    await new Promise<void>((resolve) => this.#server!.listen(0, "127.0.0.1", resolve));
    const address = this.#server.address() as { port: number };
    this.origin = `http://127.0.0.1:${address.port}`;
  }

  close(): void {
    this.#server?.close();
  }

  /** The event posts, in order. */
  events(): Posted[] {
    return this.posts.filter((post) => post.path.endsWith("/events"));
  }

  /** Every event the runner posted, in order. */
  frames(): Record<string, unknown>[] {
    return this.events().flatMap((post) =>
      (post.body["frames"] as { event: Record<string, unknown> }[]).map((frame) => frame.event));
  }

  /** The ops of every commit, in order. */
  ops(): { path: string; op: string; inline?: string; intent_id?: string }[] {
    return this.posts.filter((post) => post.path === "/sync/commit")
      .flatMap((post) => post.body["ops"] as { path: string; op: string }[]);
  }

  /** Until the run's end was posted (or `ms` passed). */
  async ended_(ms = 20_000, run?: string): Promise<Posted> {
    const until = Date.now() + ms;
    for (;;) {
      const end = this.events().find((post) => post.body["end"] !== undefined
        && (run === undefined || post.path.endsWith(`/${run}/events`)));
      if (end !== undefined) {
        return end;
      }
      if (Date.now() > until) {
        throw new Error("the run's end was never posted");
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
}

const sources = bundledSources();

/**
 * A box with a fake CLI per record at the record's own binary path (what each was run with
 * goes to `/fake-seen`) and the runner installed by the loader, as the Worker installs it.
 */
export class Sandbox extends Box {
  /** The runner's digest: every command runs it through the loader, by it. */
  readonly digest: string;

  constructor() {
    super();
    try {
      mkdirSync(this.path("/fake-seen"), { mode: 0o777 });
      chmodSync(this.path("/fake-seen"), 0o777);
      for (const rec of sources.ids().map((id) => sources.get(id)!).filter((each) => each.cli)) {
        const bin = this.path(binPath(rec));
        mkdirSync(dirname(bin), { recursive: true, mode: 0o755 });
        writeFileSync(bin, FAKE, { mode: 0o755 });
        this.cli(rec.id, { lines: [] });
      }
      const bytes = readFileSync(runnerBundle());
      this.digest = createHash("sha256").update(bytes).digest("hex");
      writeFileSync(this.path(incoming(this.digest)), bytes, { mode: 0o600 });
      const installed = this.exec([LOADER, INSTALL, this.digest]);
      if (installed.status !== 0) {
        throw new Error(`the loader did not install the runner: ${installed.stderr}`);
      }
    } catch (error) {
      this.remove();
      throw error;
    }
  }

  /** The installed runner's bytes, as the test reaches them. */
  runnerFile(): string {
    return this.path(`${RUNNERS}/${this.digest}/runner.mjs`);
  }

  cli(engine: string, plan: FakeCli): void {
    writeFileSync(`${this.path(binPath(sources.get(engine)!))}.plan.json`,
                  JSON.stringify(plan), { mode: 0o644 });
  }

  /** What the engine's fake CLI was run with, each time. */
  seen(engine: string): { argv: string[]; env: Record<string, string>; cwd: string;
                          stdin: string; uid: number; gid: number; groups: number[];
                          dumps: Record<string, string | null>;
                          owners: Record<string, { path: string; uid: number }[]> }[] {
    const bin = sources.get(engine)!.cli!.install.bin;
    const dir = this.path("/fake-seen");
    return readdirSync(dir).filter((name) => name.startsWith(`${bin}.`) && name.endsWith(".json"))
      .map((name) => JSON.parse(readFileSync(join(dir, name), "utf8")))
      .sort((a, b) => a.at - b.at);
  }

  /** The engine's runs alone: its status checks left out. */
  ran(engine: string): ReturnType<Sandbox["seen"]> {
    return this.seen(engine).filter((each) => !each.argv.includes("status"));
  }

  /** The runner, one command, to its end: `ta-runner <sha> <command>`, as the Worker runs it. */
  runner(...argv: string[]): Ran {
    const done = this.exec([LOADER, this.digest, ...argv]);
    return ran(argv, done.status ?? -1, done.stdout, done.stderr);
  }

  /** The runner, one command, to its end, the test's own event loop free meanwhile. */
  runnerAsync(...argv: string[]): Promise<number> {
    return this.execAsync([LOADER, this.digest, ...argv]);
  }

  /** A run's start and the lease's secrets, as the sandbox Worker leaves them. */
  start(start: StartDoc, authority = "owner"): void {
    writeFileSync(this.path(inboxDoc(start.run)), JSON.stringify(start));
    const lease: LeaseFile = { lease: start.lease, authority,
      tokens: { runner_token: "r".repeat(64), sync_token: "sync-token", egress_token: "x" } };
    writeFileSync(this.path(LEASE_FILE), JSON.stringify(lease));
  }

  /** A sign-in's document and the lease's secrets, as the sandbox Worker leaves them. */
  login(doc: { id: string; engine: string; region?: string | null; mode?: "in" | "out";
               authority?: string }, doors: Doors): void {
    const signin: SigninDoc = {
      id: doc.id, engine: doc.engine, region: doc.region ?? null, mode: doc.mode ?? "in",
      authority: doc.authority ?? "owner",
      urls: { events: `${doors.origin}/signin/${doc.id}/events`,
              signin: `${doors.origin}/signin` } };
    writeFileSync(this.path(inboxDoc(doc.id)), JSON.stringify(signin));
    const lease: LeaseFile = { lease: { lease_id: "b".repeat(32), epoch: 1 },
      authority: doc.authority ?? "owner",
      tokens: { runner_token: "r".repeat(64), sync_token: "sync-token", egress_token: "x" } };
    writeFileSync(this.path(LEASE_FILE), JSON.stringify(lease));
  }
}

/**
 * A runner command's end: its exit code, what it printed, and `why`, the command with its
 * code, stdout and stderr, for an assertion's message (a failed `prepare` says why there).
 */
export interface Ran {
  readonly code: number;
  readonly out: string;
  readonly err: string;
  readonly why: string;
}

function ran(argv: readonly string[], code: number, out: string, err: string): Ran {
  return { code, out, err,
           why: `ta-runner ${argv.join(" ")} exited ${code}\nstdout: ${out}\nstderr: ${err}` };
}

/**
 * A host simulated in a mount namespace of its own: its /etc and /usr each an overlay of the
 * real one (so the simulation copies nothing, and writes nothing to it), `shape` making them
 * the host's to simulate (`$SIM/etc`, `$SIM/usr`; `$NODE_BIN` the host's Node, which it may
 * replace; `$ARG` the caller's), bound over /etc and /usr there alone. The suite's own
 * command (`root-tests.mjs`, by that Node) runs the test of `file` matching `title` (the
 * guard's, by default) there, which makes a box; `after` then runs there. The namespace's
 * run: the suite's, then `after`'s output.
 */
export function simulated(shape: string, { arg = "", file = "runner/test/box.test.ts",
                                    title = "sees no host path", after = "true" } = {}) {
  const scratch = mkdtempSync(join(tmpdir(), "ta-etc-"));
  try {
    const script = [
      'mount -t tmpfs -o size=1g tmpfs "$SIM"',
      'mkdir "$SIM/etc-upper" "$SIM/etc-work" "$SIM/etc" "$SIM/usr-upper" "$SIM/usr-work" ' +
        '"$SIM/usr"',
      'mount -t overlay overlay -o "lowerdir=/etc,upperdir=$SIM/etc-upper,' +
        'workdir=$SIM/etc-work" "$SIM/etc"',
      'mount -t overlay overlay -o "lowerdir=/usr,upperdir=$SIM/usr-upper,' +
        'workdir=$SIM/usr-work" "$SIM/usr"',
      shape,
      'mount --bind "$SIM/etc" /etc',
      'mount --bind "$SIM/usr" /usr',
      '{ "$NODE_BIN" root-tests.mjs "$FILE" -t "$TITLE"; ran=$?; }',
      `{ ${after}; }`,
      'exit "$ran"',
    ].join(" && ");
    mkdirSync(join(scratch, "sim"));
    return spawnSync("unshare", ["--mount", "--propagation", "private", "--", "/bin/sh", "-c",
                                 script], {
      cwd: SANDBOX, encoding: "utf8",
      env: { ...process.env, SIM: join(scratch, "sim"), NODE_BIN: process.execPath, ARG: arg,
             FILE: file, TITLE: title } });
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

/** A start document for `engine`, its URLs on the fake doors. */
export function startDoc(doors: Doors, engine: string, over: Partial<StartDoc> = {}): StartDoc {
  const run = over.run ?? `run_${crypto.randomUUID().replace(/-/g, "")}`;
  const rec = sources.get(engine)!;
  const credential = over.credential ?? rec.cli!.credential[0]!;
  return {
    run, agent: "main", conversation: `cv_${"c".repeat(32)}`, engine, credential,
    model: null, session_id: null, prompt: "do the thing", unattended: false,
    authority: "owner", lease: { lease_id: "b".repeat(32), epoch: 1 },
    config: { generation: 1, egress: { mode: "open", destinations: [] },
              env: { HOME: "{home}", ...rec.cli!.home_env, ...rec.cli!.env },
              inherit: ["PATH", "LANG"] },
    urls: { mcp: `${doors.origin}/r/${run}/${"e".repeat(64)}/mcp`,
            mcp_bare: `${doors.origin}/r/${run}/mcp`, events: `${doors.origin}/r/${run}/events`,
            signin: `${doors.origin}/signin` },
    ...over,
  };
}
