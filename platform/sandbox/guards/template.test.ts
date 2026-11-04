/**
 * The template the sandbox Worker starts (`E2B_TEMPLATE`) is the one built from these
 * sources: its tag is `template/build.py`'s digest of the runner's bundle, the records, their
 * locks, the build script and the run pool's size's source (`owner/src/tier.ts`), recomputed
 * here the same way. A change to the runner or a
 * record without a new template build fails CI, so no sandbox runs a runner older than its
 * code.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../owner/guards/sources";
import { STEP_NS } from "../../owner/src/sync-ignore";

const TEMPLATE = join(PLATFORM, "sandbox", "template");
const SOURCES = join(PLATFORM, "sources");

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

function tag(): string {
  const out = mkdtempSync(join(tmpdir(), "ta-template-"));
  try {
    execFileSync(process.execPath, [join(TEMPLATE, "bundle.mjs"), out]);
    const parts: [string, Buffer][] = [
      ["runner.mjs", readFileSync(join(out, "runner.mjs"))],
      ["build.py", readFileSync(join(TEMPLATE, "build.py"))],
      ["ta-runner", readFileSync(join(TEMPLATE, "ta-runner"))],
      ["tier.ts", readFileSync(join(PLATFORM, "owner", "src", "tier.ts"))],
      ...files(SOURCES).map((path) => relative(SOURCES, path).split("\\").join("/"))
        .sort(byParts).map((name): [string, Buffer] => [name, readFileSync(join(SOURCES, name))]),
    ];
    const digest = createHash("sha256");
    for (const [name, data] of parts) {
      digest.update(Buffer.concat([Buffer.from(`${name}\0`),
                                   createHash("sha256").update(data).digest()]));
    }
    return `s${digest.digest("hex").slice(0, 16)}`;
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

function pinned(): string {
  const config = ts.parseConfigFileTextToJson("wrangler.jsonc", readFileSync(
    join(PLATFORM, "sandbox", "wrangler.jsonc"), "utf8")).config as {
      vars: { E2B_TEMPLATE: string } };
  return config.vars.E2B_TEMPLATE;
}

interface Passed {
  readonly tag: string;
  readonly logins: readonly { engine: string; region: string | null; ok: boolean }[];
  readonly routes: readonly { engine: string; provider: string; ok: boolean;
                              turns: readonly { exit: number; answered: boolean;
                                                resumed: boolean }[] | null;
                              compacted: boolean | null }[];
  readonly matcher?: { readonly step_ns: number; readonly ceiling_ns: number;
                       readonly ok: boolean };
}

describe("the template", () => {
  it("is the one built from the runner and the records as they stand", () => {
    expect(pinned()).toBe(`tinyassets-sandbox:${tag()}`);
  }, 60_000);

  it("is pinned only once it passed both standing checks, for every record (review L-b)",
     () => {
    const passed = JSON.parse(readFileSync(join(TEMPLATE, "passed.json"), "utf8")) as Passed[];
    const entry = passed.find((each) => `tinyassets-sandbox:${each.tag}` === pinned());
    expect(entry, "the pinned tag has no passing record in template/passed.json").toBeDefined();
    const records = files(SOURCES).filter((path) => path.endsWith(".json")
      && !path.includes("/locks/")).map((path) => JSON.parse(readFileSync(path, "utf8")) as {
        id: string; unwired?: string; cli?: { credential: string[]; login?: unknown;
                                              compaction?: unknown;
                                              regions?: Record<string, unknown>;
                                              api_key_env?: Record<string, unknown> } });
    const logins = records.filter((rec) => rec.cli?.login !== undefined
      && rec.unwired === undefined).flatMap((rec) =>
      Object.keys(rec.cli!.regions ?? {}).length === 0 ? [`${rec.id}:`]
        : Object.keys(rec.cli!.regions!).map((region) => `${rec.id}:${region}`)).sort();
    const routes = records.filter((rec) => rec.cli?.credential.includes("api_key") === true
      && rec.unwired === undefined).flatMap((rec) =>
      Object.keys(rec.cli!.api_key_env!).map((provider) => `${rec.id}:${provider}`)).sort();
    // The sync-ignore automaton's step, timed in the sandbox, within `STEP_NS`.
    expect(entry!.matcher, "the pinned tag's record has no matcher check").toBeDefined();
    expect(entry!.matcher!.ok).toBe(true);
    expect(entry!.matcher!.step_ns).toBeLessThanOrEqual(STEP_NS);
    expect(entry!.logins.filter((each) => each.ok)
      .map((each) => `${each.engine}:${each.region ?? ""}`).sort()).toEqual(logins);
    // A record that says how to compact it (`cli.compaction`) passed only once it compacted.
    const compacts = new Set(records.filter((rec) => rec.cli?.compaction !== undefined)
      .map((rec) => rec.id));
    expect(entry!.routes.filter((each) => each.ok && each.turns?.length === 2
      && each.turns.every((one) => one.exit === 0 && one.answered)
      && each.turns[1]!.resumed && (!compacts.has(each.engine) || each.compacted === true))
      .map((each) => `${each.engine}:${each.provider}`).sort()).toEqual(routes);
  });
});
