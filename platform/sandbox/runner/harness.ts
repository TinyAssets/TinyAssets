/**
 * The runner's test harness, in Node, tests only: a temporary root standing for the
 * sandbox's `/` (`TA_RUNNER_ROOT`) with the template's users as an unprivileged pool
 * (`ta-run-<n>`, uids from 4300, each its own group), the sync agent (`ta-sync`, 4250) and
 * the shared group `ta` (4243), so a root operation that followed a link an engine's user
 * planted shows as a changed owner, mode or content; a fake CLI per record at the record's
 * own binary path; and a fake doors server for the run's event door, the sign-in report and
 * the sync doors. The runner under test is the bundle the template carries
 * (`template/bundle.mjs`), run as its own processes, as root.
 */
import { execFileSync, spawn } from "node:child_process";
import { chmodSync, chownSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync,
         writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { bundledSources } from "../../doors/src/sources";

import { binPath, type StartDoc } from "./engine";

const HERE = dirname(fileURLToPath(import.meta.url));
let bundled: string | null = null;

export const TA_GID = 4243;
export const SYNC_UID = 4250;
export const POOL_UIDS = Object.freeze([4300, 4301, 4302, 4303]);

/** The runner's bundle, built once per test file, readable by the sync agent's user. */
export function runnerBundle(): string {
  if (bundled === null) {
    const out = mkdtempSync(join(tmpdir(), "ta-runner-bundle-"));
    execFileSync(process.execPath, [join(HERE, "..", "template", "bundle.mjs"), out]);
    chmodSync(out, 0o755);
    chmodSync(join(out, "runner.mjs"), 0o644);
    bundled = join(out, "runner.mjs");
  }
  return bundled;
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
}

/** The fake CLI's own program: what it was run with goes to `<root>/fake-seen`. */
const FAKE = String.raw`#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
const me = process.argv[1];
const root = me.split("/opt/ta/engines/")[0];
const plan = JSON.parse(fs.readFileSync(me + ".plan.json", "utf8"));
const where = (file) => file.startsWith("~/") ? path.join(process.env.HOME, file.slice(2))
  : path.resolve(file);
let stdin = "";
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
  fs.writeFileSync(path.join(root, "fake-seen",
                             path.basename(me) + "." + at.toFixed(3) + "." + process.pid + ".json"),
    JSON.stringify({ at, argv: process.argv.slice(2), env: process.env, cwd: process.cwd(), stdin,
                     uid: process.getuid(), gid: process.getgid(), groups: process.getgroups(),
                     dumps, owners }));
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
  setTimeout(() => process.exit(plan.exit || 0), plan.sleepMs || 0);
};
if (!process.argv.includes("status")) {
  process.stdin.on("data", (d) => { stdin += d; });
  process.stdin.on("end", done);
  process.stdin.on("error", done);
} else { done(); }
`;

export interface Posted {
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
        this.posts.push({ path, headers: request.headers, body });
        const json = (status: number, value: unknown, headers: Record<string, string> = {}) => {
          response.writeHead(status, { "content-type": "application/json", ...headers });
          response.end(JSON.stringify(value));
        };
        if (/^\/r\/run_[0-9a-f]{32}\/events$/.test(path)) {
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
        if (path === "/sync/commit") {
          const ops = body["ops"] as { path: string; op: string }[];
          return json(200, { outcomes: ops.map((op) => ({ path: op.path, outcome: "committed",
            entry: op.op === "remove" ? null : { version: this.#version += 1 } })) });
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
        && (run === undefined || post.path === `/r/${run}/events`));
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

/** A sandbox root for one test: users, groups, engines' fake CLIs, the state directory. */
export class Sandbox {
  readonly root = mkdtempSync(join(tmpdir(), "ta-runner-root-"));

  constructor() {
    chmodSync(this.root, 0o755);
    const engines = sources.ids().map((id) => sources.get(id)!).filter((rec) => rec.cli);
    mkdirSync(join(this.root, "etc"), { recursive: true });
    writeFileSync(join(this.root, "etc", "passwd"), [
      "root:x:0:0::/root:/bin/sh",
      `ta-sync:x:${SYNC_UID}:${SYNC_UID}::/nonexistent:/usr/sbin/nologin`,
      ...POOL_UIDS.map((uid, i) => `ta-run-${i}:x:${uid}:${uid}::/nonexistent:/usr/sbin/nologin`),
    ].join("\n"));
    writeFileSync(join(this.root, "etc", "group"), `ta:x:${TA_GID}:\n`);
    writeFileSync(join(this.root, "etc", "resolv.conf"), "");
    mkdirSync(join(this.root, "var", "lib"), { recursive: true, mode: 0o755 });
    mkdirSync(join(this.root, "var", "lib", "ta", "inbox"), { recursive: true, mode: 0o700 });
    chmodSync(join(this.root, "var", "lib", "ta"), 0o700);
    mkdirSync(join(this.root, "var", "lib", "ta-sync"), { mode: 0o700 });
    chownSync(join(this.root, "var", "lib", "ta-sync"), SYNC_UID, SYNC_UID);
    mkdirSync(join(this.root, "workspace"), { mode: 0o2775 });
    chownSync(join(this.root, "workspace"), 0, TA_GID);
    chmodSync(join(this.root, "workspace"), 0o2775);
    for (const dir of ["tmp", "var/tmp", "dev/shm"]) {
      mkdirSync(join(this.root, dir), { recursive: true });
      chmodSync(join(this.root, dir), 0o1777);
    }
    mkdirSync(join(this.root, "fake-seen"), { mode: 0o777 });
    chmodSync(join(this.root, "fake-seen"), 0o777);
    for (const rec of engines) {
      const bin = join(this.root, binPath(rec));
      mkdirSync(dirname(bin), { recursive: true, mode: 0o755 });
      writeFileSync(bin, FAKE, { mode: 0o755 });
      this.cli(rec.id, { lines: [] });
    }
  }

  cli(engine: string, plan: FakeCli): void {
    writeFileSync(`${join(this.root, binPath(sources.get(engine)!))}.plan.json`,
                  JSON.stringify(plan), { mode: 0o644 });
  }

  /** What the engine's fake CLI was run with, each time. */
  seen(engine: string): { argv: string[]; env: Record<string, string>; cwd: string;
                          stdin: string; uid: number; gid: number; groups: number[];
                          dumps: Record<string, string | null>;
                          owners: Record<string, { path: string; uid: number }[]> }[] {
    const bin = sources.get(engine)!.cli!.install.bin;
    const dir = join(this.root, "fake-seen");
    return readdirSync(dir).filter((name) => name.startsWith(`${bin}.`) && name.endsWith(".json"))
      .map((name) => JSON.parse(readFileSync(join(dir, name), "utf8")))
      .sort((a, b) => a.at - b.at);
  }

  /** The engine's runs alone: its status checks left out. */
  ran(engine: string): ReturnType<Sandbox["seen"]> {
    return this.seen(engine).filter((each) => !each.argv.includes("status"));
  }

  path(path: string): string {
    return join(this.root, path);
  }

  write(path: string, text: string): void {
    mkdirSync(dirname(this.path(path)), { recursive: true });
    writeFileSync(this.path(path), text);
  }

  /** The runner, one command, to its end. */
  runner(...argv: string[]): { code: number; out: string } {
    try {
      const out = execFileSync(process.execPath, [runnerBundle(), ...argv], {
        env: { ...process.env, TA_RUNNER_ROOT: this.root }, encoding: "utf8" });
      return { code: 0, out };
    } catch (error) {
      const failed = error as { status: number; stdout: string };
      return { code: failed.status, out: failed.stdout };
    }
  }

  /** The runner, one command, to its end, the test's own event loop free meanwhile. */
  runnerAsync(...argv: string[]): Promise<number> {
    return new Promise((resolve) => {
      spawn(process.execPath, [runnerBundle(), ...argv], {
        env: { ...process.env, TA_RUNNER_ROOT: this.root }, stdio: "ignore" })
        .on("close", (code) => resolve(code ?? 1));
    });
  }

  /** A run's start and the lease's secrets, as the sandbox Worker leaves them. */
  start(start: StartDoc, authority = "owner"): void {
    writeFileSync(this.path(`/var/lib/ta/inbox/${start.run}.json`), JSON.stringify(start));
    writeFileSync(this.path("/var/lib/ta/lease.json"), JSON.stringify({
      lease: start.lease, authority,
      tokens: { runner_token: "r".repeat(64), sync_token: "sync-token", egress_token: "x" } }));
  }

  remove(): void {
    // Every runner process this sandbox left (a launched run outlives its test) killed.
    const mine = `TA_RUNNER_ROOT=${this.root}`;
    for (const pid of readdirSync("/proc").filter((name) => /^[0-9]+$/.test(name))) {
      try {
        if (readFileSync(`/proc/${pid}/environ`, "latin1").split("\0").includes(mine)) {
          process.kill(Number(pid), "SIGKILL");
        }
      } catch {
        // gone already, or not this sandbox's
      }
    }
    if (process.env["TA_KEEP_ROOT"] !== undefined) {
      return;
    }
    rmSync(this.root, { recursive: true, force: true });
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
    config: { generation: 1, env: { HOME: "{home}", ...rec.cli!.home_env, ...rec.cli!.env },
              inherit: ["PATH", "LANG"] },
    urls: { mcp: `${doors.origin}/r/${run}/${"e".repeat(64)}/mcp`,
            mcp_bare: `${doors.origin}/r/${run}/mcp`, events: `${doors.origin}/r/${run}/events`,
            signin: `${doors.origin}/signin` },
    ...over,
  };
}
