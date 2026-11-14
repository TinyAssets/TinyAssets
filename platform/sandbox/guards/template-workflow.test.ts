/**
 * sandbox-template.yml and the checks it runs (`template/checks.ts`), which spend money on
 * E2B. The workflow's shape is held: dispatch only, on main, in `production`, one run at a
 * time, the key in its build-and-check step alone, every action and package pinned, no token
 * left in the checkout, and no upload that is not a new record. The checks are run here as
 * the step runs them, on this Node, against a fake E2B (its platform API and envd, every
 * call logged) and a fake `python3` for `image.py`'s build:
 * - each sandbox of the probes' plan, in order: made to be killed at its lifetime, the
 *   runner installed by the loader, its setup, the runner's own `prepare`, the probes
 *   installed, then each step through the loader; every sandbox killed, a failed one too;
 * - nothing recorded or tagged unless every step passed;
 * - `build` builds only a tag E2B holds no ready build of, and an answer it cannot read
 *   stops it before anything is spent.
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { afterAll, describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { readWrangler } from "../../ci/wrangler";
import { workflow } from "../../ci/workflows";
import { LOADER } from "../runner/protocol";
import { NAME, tag } from "../template/image";
import { bundles, checksArgv } from "../template/pair";

const ROOT = join(PLATFORM, "..");
const WORKFLOW = join(ROOT, ".github", "workflows", "sandbox-template.yml");
const PINNED = (readWrangler(join(PLATFORM, "sandbox")) as { vars: { E2B_TEMPLATE: string } })
  .vars.E2B_TEMPLATE;

/**
 * E2B as the checks reach it, by `globalThis.fetch`: the alias and tag lookups (`E2B_READY`:
 * ready, absent or 500), sandboxes made, networks replaced, files uploaded, processes run
 * (each answered by `answer`), sandboxes killed and tags assigned, each call logged a line.
 * `E2B_FAIL` names a step that fails: a probe command, or `prepare`.
 */
const MOCK = `
import { appendFileSync } from "node:fs";
const log = (line) => appendFileSync(process.env.E2B_LOG, line + "\\n");
const fail = process.env.E2B_FAIL ?? "";
let made = 0;
const envelope = (message, flags = 0) => {
  const body = Buffer.from(JSON.stringify(message));
  const head = Buffer.alloc(5);
  head[0] = flags;
  head.writeUInt32BE(body.length, 1);
  return Buffer.concat([head, body]);
};
// E2B_LIE: a failed step that exits 3 yet prints ok ("exit"), or exits 0 yet prints not ok.
const answer = (argv) => {
  const [code, out] = answered(argv);
  const lie = process.env.E2B_LIE;
  if (lie === undefined || code !== 3) return [code, out];
  return lie === "answer" ? [0, out] : [3, out.replace('"ok":false', '"ok":true')];
};
const answered = (argv) => {
  const [, digest, command, ...rest] = argv;
  if (digest === "install") return [0, ""];
  if (argv[0] !== "${LOADER}") return [0, ""];
  if (command === "prepare") return [fail === "prepare" ? 1 : 0, ""];
  const failed = fail !== "" && [command, ...rest].join(" ").startsWith(fail);
  if (command === "login-probe") {
    return [failed ? 3 : 0, JSON.stringify({ engine: rest[0], region: rest[1] ?? null,
      ok: !failed, reason: failed ? "no sign-in URL shown" : null, host: "vendor.example",
      code_shown: false })];
  }
  if (command === "route-probe") {
    return [failed ? 3 : 0, JSON.stringify({ engine: rest[0], provider: rest[1], ok: !failed,
      reason: failed ? "no model request" : null,
      turns: [{ exit: 0, answered: true, resumed: false },
              { exit: 0, answered: true, resumed: true }], compacted: null }) + "\\n"];
  }
  if (command === "matcher-probe") {
    return [failed ? 3 : 0, JSON.stringify({ step_ns: 1.25, ceiling_ns: 6, ok: !failed }) + "\\n"];
  }
  return [2, ""];
};
globalThis.fetch = async (input, init = {}) => {
  const url = new URL(String(input));
  const method = init.method ?? "GET";
  const headers = init.headers ?? {};
  if (headers["X-API-Key"] !== undefined && headers["X-API-Key"] !== "e2b_test_key") {
    throw new Error("the wrong key");
  }
  if (url.host === "api.e2b.app") {
    if (url.pathname.startsWith("/templates/aliases/")) {
      log("lookup alias " + decodeURIComponent(url.pathname.split("/").at(-1)));
      return process.env.E2B_READY === "500" ? new Response("", { status: 500 })
        : Response.json({ templateID: "t1" });
    }
    if (url.pathname === "/templates/t1/tags/exists") {
      log("lookup tag " + url.searchParams.get("tag"));
      return Response.json({ exists: process.env.E2B_READY === "ready" });
    }
    if (url.pathname === "/v2/sandboxes" && method === "POST") {
      const body = JSON.parse(init.body);
      made += 1;
      log("create sb" + made + " " + body.templateID + " timeout=" + body.timeout +
          " autoPause=" + body.autoPause + " network=" + (body.network === undefined ? "open"
            : body.network.allowOut.join(",")));
      return Response.json({ sandboxID: "sb" + made, envdAccessToken: "envd" + made },
                           { status: 201 });
    }
    if (url.pathname.endsWith("/network") && method === "PUT") {
      const body = JSON.parse(init.body);
      log("network " + url.pathname.split("/")[2] + " " + (body.allowOut.join(",") || "none"));
      return new Response(null, { status: 204 });
    }
    if (url.pathname.startsWith("/sandboxes/") && method === "DELETE") {
      log("kill " + url.pathname.split("/")[2]);
      return new Response(null, { status: 204 });
    }
    if (url.pathname === "/templates/tags" && method === "POST") {
      const body = JSON.parse(init.body);
      log("tag " + body.target + " " + body.tags.join(","));
      return Response.json({}, { status: 201 });
    }
  }
  if (url.host === "sandbox.e2b.app") {
    const box = headers["E2b-Sandbox-Id"];
    if (url.pathname === "/files") {
      await new Response(init.body).arrayBuffer();
      log("upload " + box + " " + url.searchParams.get("path"));
      return new Response("[]", { status: 200 });
    }
    if (url.pathname === "/process.Process/Start") {
      const sent = Buffer.from(init.body);
      const { process: started } = JSON.parse(sent.subarray(5).toString("utf8"));
      const argv = [started.cmd, ...started.args];
      const [code, out] = answer(argv);
      log("exec " + box + " " + argv.map((arg) => /^[0-9a-f]{64}$/.test(arg) ? "<" +
        arg.slice(0, 8) + ">" : arg.length > 40 ? "<" + arg.slice(0, 12) + "…>" : arg).join(" ") +
        (headers["Keepalive-Ping-Interval"] === "50" ? " (keepalive)" : ""));
      return new Response(Buffer.concat([
        envelope({ event: { start: { pid: 7 } } }),
        envelope({ event: { data: { stdout: Buffer.from(out).toString("base64") } } }),
        envelope({ event: { end: { exitCode: code, exited: true } } }),
        envelope({}, 2)]), { status: 200 });
    }
  }
  log("unexpected " + method + " " + url);
  return new Response("", { status: 599 });
};
`;

const dir = mkdtempSync(join(tmpdir(), "template-checks-"));
const mock = join(dir, "e2b-mock.mjs");
writeFileSync(mock, MOCK);
afterAll(() => rmSync(dir, { recursive: true, force: true }));

const { runner, probes } = bundles();
const short = (sha: string) => `<${sha.slice(0, 8)}>`;

/** The checks as the workflow's step runs them, against the fake E2B. */
function checks(mode: string, env: Record<string, string> = {}):
    { status: number | null; err: string; calls: string[]; record: string } {
  const name = `${mode}-${Object.values(env).join("-")}`;
  const log = join(dir, `${name}.log`);
  const record = join(dir, `${name}.passed.json`);
  writeFileSync(record, "[]\n");
  writeFileSync(log, "");
  // `python3`, as the build spawns it for `image.py` (given E2B's key, PATH and HOME alone):
  // each call logged to this run's log, and answered 0.
  writeFileSync(join(dir, "python3"), `#!/bin/sh\necho "python3 $*" >> '${log}'\nexit 0\n`,
                { mode: 0o755 });
  const argv = checksArgv(mode as "pair" | "build");
  const run = spawnSync(process.execPath, ["--import", pathToFileURL(mock).href, ...argv,
                                           "--record", record],
                        { cwd: ROOT, encoding: "utf8",
                          env: { PATH: `${dir}:${process.env["PATH"] ?? ""}`,
                                 HOME: process.env["HOME"] ?? "", E2B_API_KEY: "e2b_test_key",
                                 E2B_LOG: log, ...env } });
  return { status: run.status, err: run.stderr, record: readFileSync(record, "utf8"),
           calls: readFileSync(log, "utf8").trim().split("\n").filter((line) => line !== "") };
}

/** One sandbox's calls, from its creation to its kill. */
function sandboxes(calls: readonly string[]): string[][] {
  const out: string[][] = [];
  for (const call of calls) {
    if (call.startsWith("create ")) {
      out.push([]);
    }
    const id = /^create (sb\d+)|^(?:upload|exec|network|kill) (sb\d+)/.exec(call);
    if (id !== null) {
      out.at(-1)!.push(call.replace(/ sb\d+/, ""));
    }
  }
  return out;
}

describe("the template's checks", () => {
  it("run each sandbox in order: the runner installed and prepared, then the probes, then " +
     "each step; every sandbox killed, and the passed pair recorded and tagged", () => {
    const ran = checks("pair");
    expect(ran.status, ran.err).toBe(0);
    const boxes = sandboxes(ran.calls);
    expect(boxes).toHaveLength(3);
    for (const box of boxes) {
      const at = (prefix: string) => box.findIndex((call) => call.startsWith(prefix));
      expect(box[0]).toMatch(new RegExp(`^create ${PINNED} timeout=\\d+ autoPause=false`));
      expect(box.slice(1, 3)).toEqual([
        `upload /var/lib/ta/incoming/${runner.sha}`, `exec ${LOADER} install ${short(runner.sha)}`]);
      const prepare = at(`exec ${LOADER} ${short(runner.sha)} prepare 1.1.1.1`);
      const probesIn = at(`upload /var/lib/ta/incoming/${probes.sha}`);
      expect(prepare).toBeGreaterThan(2);
      expect(probesIn).toBeGreaterThan(prepare);
      expect(box[probesIn + 1]).toBe(`exec ${LOADER} install ${short(probes.sha)}`);
      const steps = box.filter((call) => call.startsWith(`exec ${LOADER} ${short(probes.sha)}`));
      expect(steps.length).toBeGreaterThan(0);
      expect(box.findIndex((call) => call === steps[0])).toBeGreaterThan(probesIn + 1);
      expect(steps.every((call) => call.endsWith("(keepalive)"))).toBe(true);
      expect(box.at(-1)).toBe("kill");
    }
    // The sign-ins under their own networks, the routes after the network closed, the matcher.
    expect(boxes[0]![0]).toContain("network=egress.tinyassets.io");
    expect(boxes[0]!.filter((call) => / login-probe /.test(call)).length).toBeGreaterThan(0);
    expect(boxes[1]!.indexOf("network none")).toBeGreaterThan(
      boxes[1]!.findIndex((call) => / prepare /.test(call)));
    expect(boxes[1]!.filter((call) => / route-probe /.test(call)).length).toBeGreaterThan(0);
    expect(boxes[2]!.filter((call) => / matcher-probe/.test(call))).toHaveLength(1);
    // Only now: the record, then the pair's tag at E2B.
    const pair = `${PINNED.slice(PINNED.indexOf(":") + 1)}-r${runner.sha.slice(0, 16)}-p${
      probes.sha.slice(0, 16)}`;
    expect(ran.calls.at(-1)).toBe(`tag ${PINNED} ${pair}`);
    const [entry] = JSON.parse(ran.record) as Record<string, unknown>[];
    expect(entry).toMatchObject({ tag: PINNED.slice(PINNED.indexOf(":") + 1), runner: runner.sha,
                                  probes: probes.sha, matcher: { step_ns: 1.25, ok: true } });
    expect(entry!["install"]).toMatchObject({ bytes: runner.bytes.length });
  });

  it.each([["a sign-in", "login-probe"], ["a route", "route-probe"],
           ["the matcher", "matcher-probe"], ["the runner's prepare", "prepare"]])(
    "record and tag nothing when %s fails, and kill every sandbox they made", (_what, fail) => {
      const ran = checks("pair", { E2B_FAIL: fail });
      expect(ran.status).toBe(1);
      expect(ran.err).toMatch(fail === "prepare" ? /the runner's prepare exited 1/
        : new RegExp(`the pair failed its standing checks: ${fail}`));
      const made = ran.calls.filter((call) => call.startsWith("create ")).length;
      expect(made).toBeGreaterThan(0);
      expect(ran.calls.filter((call) => call.startsWith("kill "))).toHaveLength(made);
      expect(ran.calls.some((call) => call.startsWith("tag "))).toBe(false);
      expect(ran.record).toBe("[]\n");
    });

  it.each([
    ["exits 3 though it printed ok", "exit"],
    ["prints not ok though it exited 0", "answer"],
  ])(
    "record and tag nothing when a step %s", (_what, lie) => {
      const ran = checks("pair", { E2B_FAIL: "login-probe", E2B_LIE: lie });
      expect(ran.status).toBe(1);
      expect(ran.calls.some((call) => call.startsWith("tag "))).toBe(false);
      expect(ran.record).toBe("[]\n");
    });

  it("build a tag E2B holds no ready build of, once, before its checks", () => {
    const ran = checks("build", { E2B_READY: "absent" });
    expect(ran.status, ran.err).toBe(0);
    const tree = `${NAME}:${tag()}`;
    expect(ran.calls.slice(0, 3)).toEqual([
      `lookup alias ${NAME}`, `lookup tag ${tree.slice(NAME.length + 1)}`,
      expect.stringMatching(new RegExp(`^python3 \\S+/image\\.py build ${tree} \\S+/stage \\S+/spec\\.json$`))]);
    expect(ran.calls.filter((call) => call.startsWith("python3"))).toHaveLength(1);
    expect(ran.calls[3]).toMatch(new RegExp(`^create sb1 ${tree} `));
    expect(ran.calls.at(-1)).toMatch(new RegExp(`^tag ${tree} `));
  });

  it("build nothing for a tag E2B holds a ready build of: its checks alone", () => {
    const ran = checks("build", { E2B_READY: "ready" });
    expect(ran.status, ran.err).toBe(0);
    expect(ran.calls.some((call) => call.startsWith("python3"))).toBe(false);
    expect(ran.calls.at(-1)).toMatch(new RegExp(`^tag ${NAME}:${tag()} `));
  });

  it("stop before anything is spent when E2B's answer cannot be read", () => {
    const ran = checks("build", { E2B_READY: "500" });
    expect(ran.status).toBe(1);
    expect(ran.err).toMatch(/alias lookup answered 500/);
    expect(ran.calls).toEqual([`lookup alias ${NAME}`]);
  });
});

type Step = { name?: string; uses?: string; run?: string; if?: string;
              env?: Record<string, string>; with?: Record<string, unknown> };

describe("sandbox-template.yml", () => {
  const file = workflow("sandbox-template.yml") as {
    on: Record<string, unknown>; concurrency: unknown;
    jobs: { template: { if: string; environment: string; steps: Step[];
                        env?: Record<string, string> } } };
  const job = file.jobs.template;

  it("is dispatched alone, on main, in production, one run at a time", () => {
    expect(Object.keys(file.on)).toEqual(["workflow_dispatch"]);
    expect(job.if).toBe("github.ref == 'refs/heads/main'");
    expect(job.environment).toBe("production");
    expect(file.concurrency).toEqual({ group: "sandbox-template", "cancel-in-progress": false });
  });

  it("holds the E2B key in its build-and-check step alone, which runs the checks", () => {
    const text = readFileSync(WORKFLOW, "utf8");
    expect(text.match(/secrets\./g)).toHaveLength(1);
    const holding = job.steps.filter((step) => JSON.stringify(step).includes("secrets."));
    expect(holding.map((step) => step.name)).toEqual(["Build and check"]);
    expect(holding[0]!.env).toEqual({ E2B_API_KEY: "${{ secrets.E2B_API_KEY }}" });
    // The checks as `pair.ts` runs them (`checksArgv`), from the repository's root.
    const argv = checksArgv("build").slice(0, -1).map((arg) => arg.replace(`${ROOT}/`, ""));
    expect(holding[0]!.run!.replace(/\s+/g, " ").trim()).toBe(
      `node ${argv.join(" ").replace("--import platform/", "--import ./platform/")} "\${MODE}"`);
    expect(job.env).toEqual({ MODE: "${{ inputs.mode }}" });
  });

  it("pins every action and package, and keeps no token in the checkout", () => {
    const uses = job.steps.flatMap((step) => step.uses === undefined ? [] : [step.uses]);
    expect(uses.length).toBeGreaterThan(0);
    expect(uses.every((each) => /^actions\/[a-z-]+@[0-9a-f]{40}$/.test(each))).toBe(true);
    const checkout = job.steps.find((step) => step.uses?.startsWith("actions/checkout@"));
    expect(checkout?.with?.["persist-credentials"]).toBe(false);
    const runs = job.steps.map((step) => step.run ?? "").join("\n");
    expect(runs).toContain("--require-hashes -r platform/sandbox/template/requirements.txt");
    const pins = readFileSync(join(PLATFORM, "sandbox", "template", "requirements.txt"), "utf8")
      .split("\n").filter((line) => /^[a-z0-9_.-]+==/i.test(line));
    expect(pins.some((line) => line.startsWith("e2b=="))).toBe(true);
    expect(pins.every((line) => line.endsWith("\\"))).toBe(true);
  });

  it("installs Python and E2B's SDK for a build alone, the one mode that runs image.py", () => {
    const python = job.steps.filter((step) => step.uses?.startsWith("actions/setup-python@")
                                              || (step.run ?? "").includes("requirements.txt"));
    expect(python).toHaveLength(2);
    expect(python.map((step) => step.if)).toEqual(["env.MODE == 'build'", "env.MODE == 'build'"]);
  });

  it.each([[false, "no"], [true, "yes"]])(
    "uploads passed.json only when this run recorded a pass (changed: %s)", (change, fresh) => {
      const record = job.steps.find((step) => step.name === "Did this run record a pass")!;
      const upload = job.steps.find((step) => step.name === "Keep the record of what passed")!;
      expect(upload.if).toBe("always() && steps.record.outputs.fresh == 'yes'");
      const repo = mkdtempSync(join(dir, "repo-"));
      const passed = join(repo, "platform", "sandbox", "template", "passed.json");
      const git = (...args: string[]) => spawnSync("git", ["-C", repo, "-c", "user.name=t",
                                                           "-c", "user.email=t@t", ...args]);
      spawnSync("mkdir", ["-p", join(repo, "platform", "sandbox", "template")]);
      writeFileSync(passed, "[]\n");
      git("init", "-q");
      git("add", ".");
      git("commit", "-qm", "base");
      if (change) {
        writeFileSync(passed, '[{"tag": "s0"}]\n');
      }
      const out = join(repo, "out");
      const done = spawnSync("bash", ["-e", "-c", record.run!], {
        cwd: repo, encoding: "utf8",
        env: { PATH: "/usr/bin:/bin", GITHUB_OUTPUT: out,
               GITHUB_STEP_SUMMARY: join(repo, "summary"), GIT_CEILING_DIRECTORIES: dir } });
      expect(done.status, done.stderr).toBe(0);
      expect(readFileSync(out, "utf8").trim()).toBe(`fresh=${fresh}`);
    });
});
