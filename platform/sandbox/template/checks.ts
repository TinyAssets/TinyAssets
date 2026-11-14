/**
 * The template's standing checks (`sandbox-template.yml`, and the deploy kit's pair check):
 * the image, the runner the Worker ships and the probes built beside it, checked together on
 * throwaway sandboxes of the image, and only once every check passed, the pair recorded
 * (`passed.json`, one entry a pair, newest last) and marked at E2B with its pair tag
 * (`pair.ts`), which the deploy kit looks for. This is plumbing alone, in no tag: what is
 * checked, with what networks and bounds, is the probes' own plan (`runner/probes.ts`
 * `plan`, under the pair's `-p`).
 *
 * Each sandbox of the plan, in order: made from the image, to be killed (never paused) at its
 * lifetime; the shipped runner installed by the loader as the Worker installs it
 * (`src/install.ts`); its setup; the runner's own `prepare`, the production command; the
 * probes installed the same way; then each step, `ta-runner <probes> <step>`, its exit code
 * as envd gives it. Every sandbox is killed whatever happens.
 *
 *   node <pair.ts checksArgv(mode)> [--record <passed.json>]
 *
 * - `pair`: the pinned image (`wrangler.jsonc`'s `E2B_TEMPLATE`) with this tree's bundles;
 * - `build`: this tree's image (`image.ts`); built first (`image.py build`) only when E2B
 *   holds no ready build of its tag in this team, so a check fixed after a build never costs
 *   a second build. A lookup that fails stops it before anything is spent.
 *
 * E2B's key is `E2B_API_KEY`; `image.py` is given only it, `PATH` and `HOME`.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { readWrangler } from "../../ci/wrangler";
import { type E2b, type Handle, runtimeE2b } from "../src/e2b";
import { install } from "../src/install";
import { COMMAND, EXIT, LOADER } from "../runner/protocol";
import type { Plan, PlanSandbox, PlanStep } from "../runner/probes";

import { NAME, spec, stage, tag } from "./image";
import { type Bundle, bundles, pairTag } from "./pair";

const TEMPLATE = dirname(fileURLToPath(import.meta.url));
const PASSED = join(TEMPLATE, "passed.json");

/** One step's outcome: what the probe printed, and whether it passed (exit 0 and `ok`). */
interface Outcome {
  readonly step: PlanStep;
  readonly ok: boolean;
  readonly result: Record<string, unknown>;
}

/** The runner's upload and install, as measured: what a sandbox without it pays. */
interface Installed {
  readonly bytes: number;
  readonly upload_ms: number;
  readonly install_ms: number;
}

function log(value: unknown): void {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

/** The image the sandbox Worker names (`wrangler.jsonc`'s `E2B_TEMPLATE`). */
export function pinned(): string {
  return String((readWrangler(join(TEMPLATE, "..")) as { vars: { E2B_TEMPLATE: string } })
    .vars.E2B_TEMPLATE);
}

/** A probe's answer: its last JSON line, or why it gave none. */
function answer(stdout: string): Record<string, unknown> {
  const line = stdout.trim().split("\n").at(-1) ?? "";
  try {
    const parsed: unknown = JSON.parse(line);
    if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // no answer
  }
  return { ok: false, reason: "no answer" };
}

/** A bundle uploaded and installed by the loader, timed. */
async function installed(e2b: E2b, box: Handle, bundle: Bundle, timeoutS: number):
    Promise<Installed> {
  return { bytes: bundle.bytes.length,
           ...await install(e2b, box, bundle.bytes, bundle.sha, timeoutS * 1000) };
}

/** One sandbox of the plan, run whole; it is killed whatever happens. */
async function sandbox(e2b: E2b, template: string, plan: Plan, each: PlanSandbox,
                       runner: Bundle, probes: Bundle):
    Promise<{ outcomes: Outcome[]; install: Installed }> {
  const box = await e2b.createThrowaway({ template, timeoutS: each.timeout_s,
                                          network: each.network,
                                          metadata: { purpose: "template-check",
                                                      check: each.name } });
  const outcomes: Outcome[] = [];
  try {
    const install = await installed(e2b, box, runner, plan.install_s);
    for (const setup of each.setup) {
      const done = await e2b.exec(box, setup.argv, { timeoutMs: setup.timeout_s * 1000,
                                                     keepalive: true });
      if (done.exitCode !== 0) {
        throw new Error(`the ${each.name} sandbox's setup ${setup.argv.join(" ")} exited ${
          done.exitCode}: ${done.stderr}`);
      }
    }
    const prepared = await e2b.exec(box, [LOADER, runner.sha, COMMAND.prepare,
                                          ...plan.resolvers],
                                    { timeoutMs: plan.prepare_s * 1000, keepalive: true });
    if (prepared.exitCode !== EXIT.ok) {
      throw new Error(`the runner's prepare exited ${prepared.exitCode} in the ${each.name} ` +
                      `sandbox: ${prepared.stderr}`);
    }
    if (each.prepared !== null) {
      await e2b.network(box.sandboxID, each.prepared);
    }
    await installed(e2b, box, probes, plan.install_s);
    for (const step of each.steps) {
      if (step.network !== null) {
        await e2b.network(box.sandboxID, step.network);
      }
      const done = await e2b.exec(box, [LOADER, probes.sha, ...step.argv],
                                  { timeoutMs: step.timeout_s * 1000, keepalive: true });
      const result = answer(done.stdout);
      const outcome = { step, ok: done.exitCode === EXIT.ok && result["ok"] === true, result };
      outcomes.push(outcome);
      log({ check: step.check, argv: step.argv, exit: done.exitCode, result });
    }
    return { outcomes, install };
  } finally {
    await e2b.kill(box.sandboxID);
  }
}

/** The pair's entry in `passed.json`, as each check found it. */
function entry(template: string, runner: Bundle, probes: Bundle, outcomes: readonly Outcome[],
               install: Installed): Record<string, unknown> {
  const of = (check: PlanStep["check"]) => outcomes.filter((each) => each.step.check === check)
    .map((each) => each.result);
  const matcher = of("matcher")[0]!;
  return {
    tag: template.slice(template.lastIndexOf(":") + 1),
    runner: runner.sha,
    probes: probes.sha,
    passed_at: new Date().toISOString().replace(/\.\d{3}Z$/, "Z"),
    logins: of("login").map((r) => ({ engine: r["engine"], region: r["region"], ok: r["ok"],
                                      host: r["host"] ?? null })),
    routes: of("route").map((r) => ({ engine: r["engine"], provider: r["provider"], ok: r["ok"],
                                      turns: r["turns"] ?? null,
                                      compacted: r["compacted"] ?? null })),
    matcher: { step_ns: Math.round(Number(matcher["step_ns"]) * 1000) / 1000,
               ceiling_ns: matcher["ceiling_ns"], ok: matcher["ok"] },
    install,
  };
}

/**
 * `passed.json` with the pair's entry last: added to the file as it stands, or, when the
 * pair passed before (a check run again), in place of its earlier entry.
 */
function recorded(text: string, added: Record<string, unknown>): string {
  const passed = JSON.parse(text) as Record<string, unknown>[];
  const same = (each: Record<string, unknown>) => each["tag"] === added["tag"]
    && each["runner"] === added["runner"] && each["probes"] === added["probes"];
  if (passed.some(same)) {
    return `${JSON.stringify([...passed.filter((each) => !same(each)), added], null, 2)}\n`;
  }
  const end = text.lastIndexOf("]");
  const body = JSON.stringify(added, null, 2).split("\n").map((line) => `  ${line}`).join("\n");
  return `${text.slice(0, end).trimEnd()}${passed.length === 0 ? "" : ","}\n${body}\n]\n`;
}

export async function main(argv: readonly string[]): Promise<number> {
  const [mode, ...rest] = argv;
  const at = rest.indexOf("--record");
  const record = at < 0 ? PASSED : rest[at + 1];
  if ((mode !== "pair" && mode !== "build") || record === undefined
      || rest.length !== (at < 0 ? 0 : 2)) {
    throw new Error("checks.ts pair|build [--record <passed.json>]");
  }
  const key = process.env["E2B_API_KEY"];
  if (key === undefined || key === "") {
    throw new Error("E2B_API_KEY is not set (the production environment's secrets)");
  }
  const e2b = runtimeE2b(key);
  const out = mkdtempSync(join(tmpdir(), "ta-checks-"));
  try {
    const { runner, probes } = bundles(execFileSync, out);
    const plan = JSON.parse(execFileSync(process.execPath, [join(out, "probes.mjs"), "plan"],
                                         { encoding: "utf8" })) as Plan;
    if (plan.runner !== runner.sha) {
      throw new Error(`the probes were built beside the runner ${plan.runner}, not ${runner.sha}`);
    }
    let template = pinned();
    if (mode === "build") {
      const image = spec();
      template = `${NAME}:${tag(image)}`;
      if (await e2b.ready(template)) {
        log({ note: `E2B already holds a ready build of ${template} in this team: no build; ` +
                    "its standing checks run on it" });
      } else {
        stage(join(out, "stage"), image);
        writeFileSync(join(out, "spec.json"), `${JSON.stringify(image, null, 2)}\n`);
        const built = spawnSync("python3", [join(TEMPLATE, "image.py"), "build", template,
                                            join(out, "stage"), join(out, "spec.json")],
                                { stdio: "inherit",
                                  env: { PATH: process.env["PATH"] ?? "",
                                         HOME: process.env["HOME"] ?? "", E2B_API_KEY: key } });
        if (built.status !== 0) {
          throw new Error(`image.py did not build ${template} (${built.status})`);
        }
      }
    }
    const outcomes: Outcome[] = [];
    let measured: Installed | null = null;
    for (const each of plan.sandboxes) {
      const ran = await sandbox(e2b, template, plan, each, runner, probes);
      outcomes.push(...ran.outcomes);
      if (ran.outcomes.some((outcome) => outcome.step.check === "matcher")) {
        measured = ran.install;
      }
    }
    const failed = outcomes.filter((outcome) => !outcome.ok);
    if (failed.length > 0 || measured === null) {
      throw new Error(`the pair failed its standing checks: ${failed.map((outcome) =>
        `${outcome.step.argv.join(" ")} (${String(outcome.result["reason"])})`).join("; ")}`);
    }
    // Every standing check passed: only now the pair's record and its tag at E2B.
    const pair = pairTag(template, runner.sha, probes.sha);
    writeFileSync(record, recorded(readFileSync(record, "utf8"),
                                   entry(template, runner, probes, outcomes, measured)));
    await e2b.tag(template, [pair.slice(pair.lastIndexOf(":") + 1)]);
    log({ template, pair, runner: runner.sha, probes: probes.sha, install: measured });
    return 0;
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

if (process.argv[1] !== undefined
    && realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => process.exit(code), (error: unknown) => {
    process.stderr.write(`checks: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
}
