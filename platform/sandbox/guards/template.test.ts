/**
 * The template the sandbox Worker starts (`E2B_TEMPLATE`) is the one built from these
 * sources: its tag is `template/build.py`'s digest of the runner's bundle, the records, their
 * locks and the build script, recomputed here the same way. A change to the runner or a
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

describe("the template", () => {
  it("is the one built from the runner and the records as they stand", () => {
    const config = ts.parseConfigFileTextToJson("wrangler.jsonc", readFileSync(
      join(PLATFORM, "sandbox", "wrangler.jsonc"), "utf8")).config as {
        vars: { E2B_TEMPLATE: string } };
    expect(config.vars.E2B_TEMPLATE).toBe(`tinyassets-sandbox:${tag()}`);
  }, 60_000);
});
