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

import { classProblem } from "../class-rule";
import { BUDGETS, budget, CLASS_CHECK, derived, LARGE, medium, type ProjectName, PROJECTS,
         SMALL_MS } from "../classes";
import { excluded, project } from "../project";
import { largeTests, timeoutLiterals } from "../timeouts";
import { PLATFORM, REPO } from "../paths";
import { json } from "../workflows";

function tracked(...globs: string[]): string[] {
  return execFileSync("git", ["-C", REPO, "ls-files", ...globs], { encoding: "utf8" })
    .split("\n").filter(Boolean);
}

// A config is read as vitest reads it: its default export, loaded. Each one builds its
// auxiliary Workers only when its project runs (`ci/workers.ts`'s `once`).
type Options = Record<string, unknown>;
type Project = { test: Options & { name: string } } & Options;

/** Every workspace's vitest config, loaded: its root test options and its projects. */
async function configs(): Promise<{ ws: string; root: Options; projects: Project[] }[]> {
  return Promise.all((json("package.json").workspaces as string[]).map(async (ws) => {
    const config = (await import(join(PLATFORM, ws, "vitest.config.ts"))).default as
      { test: Options & { projects: Project[] } };
    const { projects, ...root } = config.test;
    return { ws, root, projects };
  }));
}

/** Each way a project's options are other than its size class's, from its one key. */
function projectProblems(ws: string, each: Project): string[] {
  const { name } = each.test;
  const key = `${ws}/${name}`;
  const want = Object.hasOwn(BUDGETS.medium, key) ? medium(key) : null;
  const problems: string[] = [];
  const say = (what: string) => problems.push(`${key}: ${what}`);
  if (!Object.hasOwn(PROJECTS, name)) {
    say(`not one of the projects ${Object.keys(PROJECTS).join(", ")}`);
  } else if (JSON.stringify(each.test["include"])
             !== JSON.stringify([`${PROJECTS[name as ProjectName]}/**/*.test.ts`])) {
    say("its tests are not its folder's");
  } else if (JSON.stringify(each.test["exclude"])
             !== JSON.stringify(excluded(name as ProjectName))) {
    say("it does not leave out the projects' folders in its own");
  }
  if (want === null) {
    say("no measured budget");
  } else {
    for (const option of ["testTimeout", "hookTimeout", "provide"] as const) {
      if (JSON.stringify(each.test[option]) !== JSON.stringify(want[option])) {
        say(`${option} is not its class's`);
      }
    }
  }
  if (!(each.test["tags"] as unknown[] | undefined)?.includes(LARGE)) {
    say("the large tag is not its");
  }
  if ((each.test["setupFiles"] as unknown[] | undefined)?.[0] !== CLASS_CHECK) {
    say("the class check is not its first setup file");
  }
  for (const option of ["reporters", "strictTags"]) {
    if (each.test[option] !== undefined || each[option] !== undefined) {
      say(`sets ${option}`);
    }
  }
  return problems;
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
    // The reviewer's probes: a hook by name, a quoted key, vitest's own namespace, an alias.
    expect(hits("beforeAll(setup, 60_000);")).toEqual(["60_000"]);
    expect(hits("vitest.beforeEach(setup, LONG);")).toEqual(["LONG"]);
    expect(hits('it("a", body, 60_000);')).toEqual(["60_000"]);
    expect(hits('it("a", { "timeout": 5 }, () => {});')).toEqual(['"timeout": 5']);
    expect(hits('vitest.it("a", () => {}, 5);')).toEqual(["5"]);
    expect(hits("const before = beforeAll;")).toEqual(["before = beforeAll"]);
    expect(hits("vitest.setConfig({ testTimeout: 1 });")).toHaveLength(1);
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

  it("set every project's budget from its class, and the large tag, in every config",
     async () => {
    expect(tracked("platform/*/vitest.config.ts").length)
      .toBe((json("package.json").workspaces as string[]).length);
    for (const { ws, root, projects } of await configs()) {
      expect(Object.keys(root), ws).toEqual([]);
      expect(projects.length, ws).toBeGreaterThan(0);
      expect(projects.flatMap((each) => projectProblems(ws, each)), ws).toEqual([]);
    }
  });

  it("are made from one key each, by project()", () => {
    const made = project("ci", "guards") as Project;
    expect(made.test).toEqual({ name: "guards", include: ["guards/**/*.test.ts"], tags: [LARGE],
                                environment: "node", ...medium("ci/guards") });
    expect(projectProblems("ci", made)).toEqual([]);
    const own = project("owner", "workerd", { test: { setupFiles: ["./x.ts"] } }) as Project;
    expect(own.test["environment"]).toBeUndefined();
    expect(own.test["setupFiles"]).toEqual([CLASS_CHECK, "./x.ts"]);
    expect(() => project("nowhere", "guards")).toThrow(/no measured budget/);
  });

  it("leave out of each project every other project's folder that lies in its own", () => {
    for (const name of Object.keys(PROJECTS) as ProjectName[]) {
      const nested = Object.values(PROJECTS).filter((folder) =>
        folder.startsWith(`${PROJECTS[name]}/`));
      const left = excluded(name) ?? [];
      expect(nested.filter((folder) => !left.includes(`${folder}/**`)), name).toEqual([]);
      expect(left.length === 0, name).toBe(nested.length === 0);
    }
    const nesting = (Object.keys(PROJECTS) as ProjectName[])
      .find((name) => excluded(name) !== undefined);
    if (nesting !== undefined) {
      const made = project("owner", nesting) as Project;
      expect(projectProblems("owner", { ...made, test: { ...made.test, exclude: [] } } as Project))
        .toContain(`owner/${nesting}: it does not leave out the projects' folders in its own`);
    }
  });

  it("see a project that is not its class's", () => {
    const made = project("ci", "guards") as Project;
    const changed = (test: Options) => ({ ...made, test: { ...made.test, ...test } }) as Project;
    for (const test of [{ testTimeout: 60_000 }, { hookTimeout: 1 }, { tags: [] },
                        { setupFiles: [] }, { include: ["test/**/*.test.ts"] },
                        { name: "unit" }, { strictTags: false }, { reporters: ["dot"] }]) {
      expect(projectProblems("ci", changed(test)), JSON.stringify(test)).not.toEqual([]);
    }
  });
});

describe("the run-time check", () => {
  const want = { project: "ci/guards", medium: medium("ci/guards").testTimeout,
                 large: LARGE.timeout };

  it("passes tests at their class's timeout", () => {
    expect(classProblem({ name: "a", timeout: want.medium, tags: [] }, want)).toBeNull();
    expect(classProblem({ name: "b", timeout: LARGE.timeout, tags: ["large"] }, want)).toBeNull();
  });

  it("refuses any other timeout, and a project that provides none", () => {
    expect(classProblem({ name: "a", timeout: 60_000, tags: [] }, want)).toMatch(/60000 ms/);
    expect(classProblem({ name: "b", timeout: want.medium, tags: ["large"] }, want))
      .toMatch(/large/);
    expect(classProblem({ name: "c", timeout: want.medium }, undefined)).toMatch(/provides no/);
  });

  it("is every project's setup file, carried by medium()", async () => {
    expect(medium("ci/guards").setupFiles).toEqual([CLASS_CHECK]);
    expect(medium("ci/guards").provide.classTimeouts).toEqual(want);
    const owner = (await configs()).find(({ ws }) => ws === "owner")!.projects
      .find((each) => each.test.name === "workerd")!;
    // A project's own setup files are kept, after the class check.
    const setup = owner.test["setupFiles"] as string[];
    expect(setup[0]).toBe(CLASS_CHECK);
    expect(setup.slice(1).length).toBeGreaterThan(0);
    for (const { ws, projects } of await configs()) {
      for (const each of projects) {
        expect((each.test["setupFiles"] as string[])[0], `${ws}/${each.test.name}`)
          .toBe(CLASS_CHECK);
      }
    }
  });
});
