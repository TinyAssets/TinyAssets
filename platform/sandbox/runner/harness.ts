/**
 * The runner's test harness, in Node, tests only: a temporary root standing for the
 * sandbox's `/` (`TA_RUNNER_ROOT`), its engines' users mapped to the test's own uid, a fake
 * CLI per record at the record's own binary path, and a fake doors server for the run's
 * event door, the sign-in report and the sync doors. The runner under test is the bundle
 * the template carries (`template/bundle.mjs`), run as its own processes.
 */
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { bundledSources } from "../../doors/src/sources";

import { binPath, engineHome, engineUser, type StartDoc } from "./engine";

const HERE = dirname(fileURLToPath(import.meta.url));
let bundled: string | null = null;

/** The runner's bundle, built once per test file. */
export function runnerBundle(): string {
  if (bundled === null) {
    const out = mkdtempSync(join(tmpdir(), "ta-runner-bundle-"));
    execFileSync(process.execPath, [join(HERE, "..", "template", "bundle.mjs"), out]);
    bundled = join(out, "runner.mjs");
  }
  return bundled;
}

/** One fake CLI's behaviour: the lines it prints, its exit code, and how long it lingers. */
export interface FakeCli {
  readonly lines: readonly unknown[];
  readonly exit?: number;
  readonly sleepMs?: number;
  /** Ignore SIGTERM (so only the kill stops it). */
  readonly stubborn?: boolean;
  /** Write these files (path to text) before printing, from its cwd or absolute. */
  readonly writes?: Readonly<Record<string, string>>;
  /** What `status_argv` prints and exits with. */
  readonly status?: { readonly out: string; readonly exit: number };
}

/** The fake CLI's own program: what it was run with goes to `<bin>.seen.jsonl`. */
const FAKE = String.raw`#!/usr/bin/env node
const fs = require("node:fs");
const path = require("node:path");
const me = process.argv[1];
const plan = JSON.parse(fs.readFileSync(me + ".plan.json", "utf8"));
let stdin = "";
const done = () => {
  fs.appendFileSync(me + ".seen.jsonl", JSON.stringify({ argv: process.argv.slice(2),
    env: process.env, cwd: process.cwd(), stdin, uid: process.getuid() }) + "\n");
  if (process.argv.includes("status")) {
    const status = plan.status || { out: "", exit: 1 };
    process.stdout.write(status.out);
    process.exit(status.exit);
  }
  if (plan.stubborn) { process.on("SIGTERM", () => {}); }
  else { process.on("SIGTERM", () => process.exit(143)); }
  for (const [file, text] of Object.entries(plan.writes || {})) {
    fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
    fs.writeFileSync(path.resolve(file), text);
  }
  for (const line of plan.lines) {
    process.stdout.write((typeof line === "string" ? line : JSON.stringify(line)) + "\n");
  }
  setTimeout(() => process.exit(plan.exit || 0), plan.sleepMs || 0);
};
if (process.stdin.isTTY === undefined && !process.argv.includes("status")) {
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
  #server: Server | null = null;
  #version = 100;
  origin = "";

  async listen(): Promise<void> {
    this.#server = createServer((request, response) => {
      let raw = "";
      request.on("data", (chunk: Buffer) => { raw += chunk.toString("utf8"); });
      request.on("end", () => {
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

  /** Until the run's end was posted (or `ms` passed). */
  async ended_(ms = 20_000): Promise<Posted> {
    const until = Date.now() + ms;
    for (;;) {
      const end = this.events().find((post) => post.body["end"] !== undefined);
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
    const uid = process.getuid!();
    const gid = process.getgid!();
    const engines = sources.ids().map((id) => sources.get(id)!).filter((rec) => rec.cli);
    mkdirSync(join(this.root, "etc"), { recursive: true });
    writeFileSync(join(this.root, "etc", "passwd"), engines.map((rec) =>
      `${engineUser(rec.id)}:x:${uid}:${gid}::${engineHome(rec.id)}:/bin/sh`).join("\n"));
    writeFileSync(join(this.root, "etc", "group"), `ta:x:${gid}:\n`);
    mkdirSync(join(this.root, "var", "lib", "ta", "inbox"), { recursive: true });
    for (const rec of engines) {
      const bin = join(this.root, binPath(rec));
      mkdirSync(dirname(bin), { recursive: true });
      writeFileSync(bin, FAKE, { mode: 0o755 });
      this.cli(rec.id, { lines: [] });
    }
  }

  cli(engine: string, plan: FakeCli): void {
    writeFileSync(`${join(this.root, binPath(sources.get(engine)!))}.plan.json`,
                  JSON.stringify(plan));
  }

  /** What the engine's fake CLI was run with, each time. */
  seen(engine: string): { argv: string[]; env: Record<string, string>; cwd: string;
                          stdin: string; uid: number }[] {
    try {
      return readFileSync(`${join(this.root, binPath(sources.get(engine)!))}.seen.jsonl`, "utf8")
        .trim().split("\n").filter((line) => line !== "").map((line) => JSON.parse(line));
    } catch {
      return [];
    }
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
        env: { ...process.env, TA_RUNNER_ROOT: this.root, PATH: process.env["PATH"] ?? "" },
        encoding: "utf8" });
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

  /** The runner, one command, in the background. */
  background(...argv: string[]): void {
    spawn(process.execPath, [runnerBundle(), ...argv], {
      env: { ...process.env, TA_RUNNER_ROOT: this.root }, stdio: "ignore" }).unref();
  }

  /** A run's start and the lease's secrets, as the sandbox Worker leaves them. */
  start(doors: Doors, start: StartDoc, authority = "owner"): void {
    writeFileSync(this.path(`/var/lib/ta/inbox/${start.run}.json`), JSON.stringify(start));
    writeFileSync(this.path("/var/lib/ta/lease.json"), JSON.stringify({
      lease: start.lease, authority,
      tokens: { runner_token: "r".repeat(64), sync_token: "sync-token", egress_token: "x" } }));
    void doors;
  }

  remove(): void {
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
