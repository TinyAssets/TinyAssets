/**
 * The template the sandbox Worker starts (`E2B_TEMPLATE`) is the one built from what the
 * image is made of: its tag is `template/build.py`'s digest of the build script, the runner's
 * loader, the run pool's size (`RUNS_CEILING`), the engines' install pins and their locks,
 * recomputed here the same way and held equal to the script's own. The runner is no part of
 * it: the Worker ships the runner (`src/runner-bytes.ts`) and names it by its digest, so a
 * runner change is a deploy, not a template build. Here: the tag takes no runner input, the
 * runner the Worker ships is the bundle as built, byte for byte, every time, and the pinned
 * tag passed its standing checks. Each runner's behaviour on the pinned template is held at
 * deploy by the pair check (`deploy/deploy.ts`, `build.py --check-pair`).
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync }
  from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { readWrangler } from "../../ci/workers";
import { RUNS_CEILING } from "../../contract/src/control";
import { DNS_SERVERS, EGRESS_HOST } from "../../contract/src/egress";
import { STEP_NS } from "../../owner/src/sync-ignore";

const TEMPLATE = join(PLATFORM, "sandbox", "template");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

/** Python's `sorted(Path.rglob("*"))`: by path parts, in order. */
function byParts(a: string, b: string): number {
  const x = a.split("/");
  const y = b.split("/");
  for (let i = 0; i < Math.min(x.length, y.length); i += 1) {
    if (x[i] !== y[i]) {
      return x[i]! < y[i]! ? -1 : 1;
    }
  }
  return x.length - y.length;
}

/** Python's `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonical((value as Record<string, unknown>)[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** `build.py`'s `engines()`: each served npm record's id, binary and lifecycle scripts. */
function engines(sources: string): { id: string; bin: string; scripts: boolean }[] {
  return readdirSync(sources).filter((name) => name.endsWith(".json")).sort()
    .map((name) => JSON.parse(readFileSync(join(sources, name), "utf8")) as {
      id: string; unwired?: string;
      cli?: { install: { manager: string; bin: string; lifecycle_scripts?: boolean } } })
    .filter((rec) => rec.cli !== undefined && rec.unwired === undefined
                     && rec.cli.install.manager === "npm")
    .map((rec) => ({ id: rec.id, bin: rec.cli!.install.bin,
                     scripts: rec.cli!.install.lifecycle_scripts === true }));
}

/** `build.py`'s `tag()`, from the platform tree at `platform`. */
function tag(platform = PLATFORM): string {
  const template = join(platform, "sandbox", "template");
  const locks = join(platform, "sources", "locks");
  const ceiling = /export const RUNS_CEILING = ([0-9_]+);/.exec(
    readFileSync(join(platform, "owner", "src", "tier.ts"), "utf8"))![1]!.replaceAll("_", "");
  const parts: [string, Buffer][] = [
    ["build.py", readFileSync(join(template, "build.py"))],
    ["ta-runner", readFileSync(join(template, "ta-runner"))],
    ["runs_ceiling", Buffer.from(String(Number(ceiling)))],
    ["engines", Buffer.from(canonical(engines(join(platform, "sources"))))],
    ...files(locks).map((path) => `locks/${relative(locks, path).split("\\").join("/")}`)
      .sort(byParts).map((name): [string, Buffer] =>
        [name, readFileSync(join(platform, "sources", name))]),
  ];
  const digest = createHash("sha256");
  for (const [name, data] of parts) {
    digest.update(Buffer.concat([Buffer.from(`${name}\0`),
                                 createHash("sha256").update(data).digest()]));
  }
  return `s${digest.digest("hex").slice(0, 16)}`;
}

/** The runner as the Worker's build makes it (`bundle.mjs`). */
function bundled(): Buffer {
  const out = mkdtempSync(join(tmpdir(), "ta-runner-"));
  try {
    execFileSync(process.execPath, [join(TEMPLATE, "bundle.mjs"), out]);
    return readFileSync(join(out, "runner.mjs"));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

function pinned(): string {
  return (readWrangler(join(PLATFORM, "sandbox")) as { vars: { E2B_TEMPLATE: string } })
    .vars.E2B_TEMPLATE;
}

interface Passed {
  readonly tag: string;
  readonly runner?: string;
  readonly logins: readonly { engine: string; region: string | null; ok: boolean }[];
  readonly routes: readonly { engine: string; provider: string; ok: boolean;
                              turns: readonly { exit: number; answered: boolean;
                                                resumed: boolean }[] | null;
                              compacted: boolean | null }[];
  readonly matcher?: { readonly step_ns: number; readonly ceiling_ns: number;
                       readonly ok: boolean };
}

describe("the template", () => {
  it("is the one built from what the image is made of, as build.py computes it", () => {
    expect(pinned()).toBe(`tinyassets-sandbox:${tag()}`);
    expect(execFileSync("python3", [join(TEMPLATE, "build.py"), "--tag-only"],
                        { encoding: "utf8" }).trim()).toBe(`tinyassets-sandbox:${tag()}`);
  });

  it("takes no runner input: a change to the runner's code is no new template", () => {
    // The platform's template inputs and the runner's code copied, the runner's changed.
    const copy = mkdtempSync(join(tmpdir(), "ta-template-"));
    try {
      for (const part of ["sandbox/template", "sources", "owner/src/tier.ts",
                          "sandbox/runner", "contract/src/refusal.ts"]) {
        cpSync(join(PLATFORM, part), join(copy, part), { recursive: true });
      }
      writeFileSync(join(copy, "contract/src/refusal.ts"), "export const CHANGED = 1;\n");
      writeFileSync(join(copy, "sandbox/runner/runner.ts"), "export {};\n");
      expect(tag(copy)).toBe(tag());
    } finally {
      rmSync(copy, { recursive: true, force: true });
    }
  });

  it("reads the platform's egress host, resolvers and run ceiling where build.py reads them",
     () => {
    // `build.py`'s `network_constants`, by its own patterns, over the text it reads; the
    // contract's are the platform's one definition (fix A6 moves the build's reads).
    const text = readFileSync(join(PLATFORM, "owner", "src", "network.ts"), "utf8");
    const host = /export const EGRESS_HOST = "([^"]+)";/.exec(text);
    const servers = /export const DNS_SERVERS: readonly string\[\] = Object\.freeze\(\[([^\]]*)\]\);/
      .exec(text);
    expect(host?.[1]).toBe(EGRESS_HOST);
    expect([...(servers?.[1] ?? "").matchAll(/"([^"]+)"/g)].map((found) => found[1]))
      .toEqual([...DNS_SERVERS]);
    expect(readFileSync(join(PLATFORM, "owner", "src", "sync-ignore.ts"), "utf8"))
      .toMatch(/export const STEP_NS = ([0-9.]+);/);
    // The run pool's size, `runs_ceiling`, by its text in `tier.ts`: the contract's bound.
    const ceiling = /export const RUNS_CEILING = ([0-9_]+);/.exec(
      readFileSync(join(PLATFORM, "owner", "src", "tier.ts"), "utf8"));
    expect(Number(ceiling?.[1]?.replaceAll("_", ""))).toBe(RUNS_CEILING);
  });

  it("is given the runner the Worker ships, the same bytes every build", () => {
    const once = bundled();
    expect(bundled().equals(once)).toBe(true);
    // What `src/runner-bytes.ts` imports is this build's own output.
    expect(readFileSync(join(PLATFORM, "sandbox", "dist", "runner.mjs")).equals(once))
      .toBe(true);
  });

  it("is pinned only once it passed its standing checks (review L-b)", () => {
    const passed = JSON.parse(readFileSync(join(TEMPLATE, "passed.json"), "utf8")) as Passed[];
    const entry = passed.findLast((each) => `tinyassets-sandbox:${each.tag}` === pinned());
    expect(entry, "the pinned tag has no passing record in template/passed.json").toBeDefined();
    expect(entry!.logins.length).toBeGreaterThan(0);
    expect(entry!.logins.every((each) => each.ok)).toBe(true);
    expect(entry!.routes.length).toBeGreaterThan(0);
    expect(entry!.routes.every((each) => each.ok && each.turns?.length === 2
      && each.turns.every((one) => one.exit === 0 && one.answered)
      && each.turns[1]!.resumed)).toBe(true);
    // The sync-ignore automaton's step, timed in the sandbox, within `STEP_NS`.
    expect(entry!.matcher, "the pinned tag's record has no matcher check").toBeDefined();
    expect(entry!.matcher!.ok).toBe(true);
    expect(entry!.matcher!.step_ns).toBeLessThanOrEqual(STEP_NS);
  });
});
