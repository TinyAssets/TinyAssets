/**
 * No test sets its own timeout: each project's budget is its size class's (`ci/classes.ts`).
 *
 * A hand number beside a test was measured once, under one machine's load, and it hides a
 * hang for as long as it says. 132 of them stood here, 7 equal to the leg's whole limit, so
 * they could never fire before the job was killed. A test that needs more than its project's
 * medium budget is a large test: `{ tags: ["large"] }`, run by the sizes leg.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { BUDGETS, budget, derived, LARGE, SMALL_MS } from "../classes";
import { largeTests, timeoutLiterals } from "../timeouts";
import { json, REPO } from "../workflows";

function tracked(...globs: string[]): string[] {
  return execFileSync("git", ["-C", REPO, "ls-files", ...globs], { encoding: "utf8" })
    .split("\n").filter(Boolean);
}

describe("test timeouts", () => {
  it("no platform test sets one", () => {
    const found = tracked("platform/*.test.ts").flatMap((file) =>
      timeoutLiterals(readFileSync(join(REPO, file), "utf8"), file)
        .map((hit) => `${file}:${hit.line}: ${hit.text}`));
    expect(found).toEqual([]);
  });

  it("finds each form of one", () => {
    const hits = (source: string) => timeoutLiterals(source).map((hit) => hit.text);
    expect(hits('it("a", async () => {}, 60_000);')).toEqual(["60_000"]);
    expect(hits('test("a", () => {}, LONG);')).toEqual(["LONG"]);
    expect(hits('it("a", { timeout: 5 }, () => {});')).toEqual(["timeout: 5"]);
    expect(hits('describe("s", { timeout: 9 }, () => {});')).toEqual(["timeout: 9"]);
    expect(hits("beforeAll(async () => {}, 30_000);")).toEqual(["30_000"]);
    expect(hits('it.each([1])("a %s", (n) => {}, 5);')).toEqual(["5"]);
    expect(hits('it.concurrent("a", function () {}, 7);')).toEqual(["7"]);
    expect(hits("vi.setConfig({ testTimeout: 1 });")).toEqual(["vi.setConfig({ testTimeout: 1 })"]);
    expect(hits('it("a", { tags: ["large"] }, () => {}); it("b", () => {});')).toEqual([]);
    expect(hits('expect(await f(1, () => 2, 3)).toBe(4);')).toEqual([]);
  });

  it("finds a large test", () => {
    expect(largeTests('it("a", { tags: ["large"] }, () => {});')).toEqual([1]);
    expect(largeTests('describe("s", { tags: "large" }, () => {});')).toEqual([1]);
    expect(largeTests('it("a", { tags: ["db"] }, () => {});')).toEqual([]);
  });
});

describe("size classes", () => {
  it("are derived from the measurement, never under the small class", () => {
    expect(derived({ slowest_ms: 100, test: "t" }, 4)).toBe(SMALL_MS);
    expect(derived({ slowest_ms: 3_650, test: "t" }, 4)).toBe(15_000);
    expect(LARGE.timeout).toBe(derived(BUDGETS.large));
    expect(() => budget("nowhere/workerd")).toThrow(/no measured budget/);
  });

  it("set every project's budget from its class, and the large tag, in every config", () => {
    const configs = tracked("platform/*/vitest.config.ts");
    expect(configs.length).toBe((json("package.json").workspaces as string[]).length);
    for (const file of configs) {
      const ws = file.split("/")[1]!;
      const text = readFileSync(join(REPO, file), "utf8");
      expect(text, file).toMatch(/from "(?:\.\.\/ci|\.)\/classes"/);
      expect(text, file).toMatch(/tags: \[LARGE\]/);
      expect(text, file).not.toMatch(/(?:testTimeout|hookTimeout|strictTags)\s*:/);
      const projects = [...text.matchAll(/test: \{\s*name: "([\w-]+)"/g)].map((match) => match[1]);
      expect(projects.length, file).toBeGreaterThan(0);
      for (const project of projects) {
        expect(text, `${file} ${project}`).toContain(`...medium("${ws}/${project}")`);
        expect(BUDGETS.medium[`${ws}/${project}`], `${ws}/${project}`).toBeDefined();
      }
    }
  });
});
