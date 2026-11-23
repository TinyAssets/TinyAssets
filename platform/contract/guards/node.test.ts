/**
 * Every contract module runs in Node as it is (type stripping), as the deploy kit, the ci kit
 * and the template run it, so the kit imports the contract's own checks and never copies
 * them. Node resolves a specifier only as written: a sibling imported without its
 * `.ts` is not found there, though a bundler finds it, and that one rule is what made the
 * kit copy `checks.ts`'s shape checks six times.
 */
import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";

const SOURCE = join(PLATFORM, "contract", "src");

describe("the contract in Node", () => {
  it("loads every module under Node's type stripping, as the kit runs it", () => {
    const modules = readdirSync(SOURCE).filter((name) => name.endsWith(".ts")).sort()
      .map((name) => pathToFileURL(join(SOURCE, name)).href);
    expect(modules.length).toBeGreaterThan(0);
    // Each module on its own, so every one that fails is named, not only the first.
    const load = `const failed = [];
      for (const url of ${JSON.stringify(modules)}) {
        await import(url).catch((error) =>
          failed.push(url.slice(url.lastIndexOf("/") + 1) + ": " + error.message.split("\\n")[0]));
      }
      console.log(JSON.stringify(failed));`;
    const ran = spawnSync(process.execPath, ["--experimental-strip-types", "--no-warnings",
                                             "--input-type=module", "-e", load],
                          { cwd: PLATFORM, encoding: "utf8" });
    expect([ran.status, ran.stderr]).toEqual([0, ""]);
    expect(JSON.parse(ran.stdout)).toEqual([]);
  });
});
