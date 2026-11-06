/**
 * sandbox-template.yml's build step asks E2B, before a paid build, whether this tree's tag
 * already has a ready build. It does that with a Node snippet in the step itself, which
 * imports the deploy kit's `templateReady`. Here that snippet is the one in the workflow,
 * run as the step runs it, on this Node (22, as the workflow's), with E2B's answers mocked:
 * a ready tag answers true, a missing one false, an alias 404 false, and a 500 stops it.
 * The step's branching on that answer is held by tests/test_sandbox_template_workflow.py.
 */
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

import { afterAll, describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/kit/sources";

const ROOT = join(PLATFORM, "..");
const WORKFLOW = join(ROOT, ".github", "workflows", "sandbox-template.yml");

/** The build step's node invocation, flags and code, as the workflow's text has it. */
function snippet(): { flags: string[]; code: string } {
  const text = readFileSync(WORKFLOW, "utf8");
  const start = text.indexOf("\n      - name: Build and check\n");
  expect(start, "the workflow has its build step").toBeGreaterThan(0);
  const end = text.indexOf("\n      - name: ", start + 1);
  const step = text.slice(start, end < 0 ? undefined : end);
  const found = /ready="\$\(node ((?:--[a-z-]+(?:=[a-z]+)? )+)-e '([^']*)' "\$name"\)"/
    .exec(step);
  expect(found, "the build step asks E2B through one node snippet").not.toBeNull();
  return { flags: found![1]!.trim().split(" "), code: found![2]! };
}

// E2B's two answers (`templateReady`: the alias, then the tag), as E2B_MOCK says.
const MOCK = `
import { appendFileSync } from "node:fs";
const answer = process.env.E2B_MOCK;
globalThis.fetch = async (url, init) => {
  appendFileSync(process.env.E2B_MOCK_LOG, init.headers["X-API-Key"] + " " + url + "\\n");
  if (String(url).includes("/templates/aliases/")) {
    return answer === "404" || answer === "500"
      ? new Response("", { status: Number(answer) })
      : Response.json({ templateID: "t1" });
  }
  return Response.json({ exists: answer === "ready" });
};
`;
const ALIAS = "k https://api.e2b.app/templates/aliases/tinyassets-sandbox";
const TAG = "k https://api.e2b.app/templates/t1/tags/exists?tag=s0";

const dir = mkdtempSync(join(tmpdir(), "template-workflow-"));
const mock = join(dir, "fetch-mock.mjs");
writeFileSync(mock, MOCK);
afterAll(() => rmSync(dir, { recursive: true, force: true }));

function ask(answer: string): { status: number | null; out: string; asked: string[] } {
  const log = join(dir, `asked-${answer}`);
  const { flags, code } = snippet();
  const run = spawnSync(process.execPath,
                        ["--import", pathToFileURL(mock).href, ...flags, "-e", code,
                         "tinyassets-sandbox:s0"],
                        { cwd: ROOT, encoding: "utf8",
                          env: { PATH: process.env["PATH"] ?? "", E2B_API_KEY: "k",
                                 E2B_MOCK: answer, E2B_MOCK_LOG: log } });
  let asked: string[] = [];
  try {
    asked = readFileSync(log, "utf8").trim().split("\n");
  } catch {
    asked = [];
  }
  return { status: run.status, out: run.stdout.trim(), asked };
}

describe("sandbox-template.yml's ready-build check", () => {
  it("answers true for a tag E2B holds a ready build of, asking with the key", () => {
    expect(ask("ready")).toEqual({ status: 0, out: "true", asked: [ALIAS, TAG] });
  });

  it("answers false for a tag E2B has no ready build of", () => {
    expect(ask("absent")).toEqual({ status: 0, out: "false", asked: [ALIAS, TAG] });
  });

  it("answers false when E2B has no such template at all (alias 404)", () => {
    expect(ask("404")).toEqual({ status: 0, out: "false", asked: [ALIAS] });
  });

  it("stops, answering nothing, when E2B's answer cannot be read (500)", () => {
    const run = ask("500");
    expect(run.status).not.toBe(0);
    expect(run.out).toBe("");
    expect(run.asked).toEqual([ALIAS]);
  });
});
