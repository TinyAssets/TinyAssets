/**
 * One import-layer table over every file (untangle B1, `ci/kit/layers.ts`): every script
 * under `platform/` and `scripts/` is in exactly one layer, by its path; each of its imports,
 * by every way one module names another, lands where its layer may import; and so does each
 * workflow's inline module. The only exceptions are what each workspace owes
 * (`<workspace>/guards/rules.ts`'s `OWED`), exactly: an import no workspace owes fails, and
 * so does an owed one that no longer holds (`ci/kit/owed.ts`).
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { closure, inlineImports, layerProblems, layersOf, repoLinks, repoScripts }
  from "../kit/imports";
import type { Layer } from "../kit/layers";
import { type Owed, owedLists, ratchet, unheld } from "../kit/owed";
import { PLATFORM, REPO } from "../paths";
import { workerFolders } from "../workers";
import { actionFiles, workflowNames } from "../workflows";

const WORKERS = workerFolders();
const OWED = await owedLists();

/** Every workflow and composite action, by its path from the repository root, with its text. */
const steps = () => new Map([...workflowNames().map((name) => `.github/workflows/${name}`),
                             ...actionFiles()]
  .map((path) => [path, readFileSync(join(REPO, path), "utf8")] as const));

/** The table over the tree, run once for the tests that read it. */
let report: ReturnType<typeof layerProblems> | undefined;
const tree = () => report ??= layerProblems({ files: repoScripts(), workflows: steps(),
                                              workers: WORKERS });

/** What the table finds over `files` (and `workflows`), by paths from the repository root. */
function found(files: Record<string, string>, workflows: Record<string, string> = {}): string[] {
  const { unplaced, violations } = layerProblems({
    files: new Map(Object.entries(files)), workflows: new Map(Object.entries(workflows)),
    workers: WORKERS });
  return [...unplaced, ...violations.map((each) => each.what)];
}

/** A workflow whose one step runs `run`, in `dir` when one is given. */
const workflow = (run: string, dir?: string) => [
  "on: push", "jobs:", "  probe:", "    runs-on: ubuntu-latest", "    steps:",
  ...(dir === undefined ? ["      - run: |"]
    : [`      - working-directory: ${dir}`, "        run: |"]),
  ...run.split("\n").map((line) => `          ${line}`),
].join("\n");

describe("the import layers", () => {
  it("place every script under platform/ and scripts/ in exactly one layer", () => {
    expect(repoScripts().size).toBeGreaterThan(400);
    expect(tree().unplaced).toEqual([]);
    expect(repoLinks()).toEqual([]);
  });

  it("find every import where its layer may import it, but for exactly what is owed", () => {
    expect(tree().violations.length).toBeGreaterThan(0);
    expect(ratchet("layers", tree().violations, OWED)).toEqual([]);
    expect(unheld(OWED)).toEqual([]);
  });

  it("leave a path no other name: no alias, no tsconfig paths, no package imports", () => {
    const tsconfig = JSON.parse(readFileSync(join(PLATFORM, "tsconfig.json"), "utf8")) as
      { compilerOptions: Record<string, unknown> };
    expect(Object.keys(tsconfig.compilerOptions)
      .filter((key) => ["paths", "baseUrl", "rootDirs"].includes(key))).toEqual([]);
    const words: readonly (readonly [string, RegExp])[] = [
      ["vitest.config.ts", /\balias\b/], ["wrangler.jsonc", /"alias"/], ["package.json", /"imports"/]];
    expect(["", ...readdirSync(PLATFORM)].flatMap((ws) => words
      .filter(([name, word]) => existsSync(join(PLATFORM, ws, name))
                                && word.test(readFileSync(join(PLATFORM, ws, name), "utf8")))
      .map(([name]) => join(ws, name)))).toEqual([]);
  });

  it("read a workflow's inline module, its shell quoting undone, in its step's folder", () => {
    expect(inlineImports(workflow(
      "node --input-type=module -e \\\n  \"import { x } from './a.ts'; console.log(\\\"$X\\\");\"",
      "platform"))).toEqual([
      { dir: "platform", code: "import { x } from './a.ts'; console.log(\"$X\");" },
      { dir: "platform", code: 'import "./a.ts";' }]);
  });

  const OWNER_DO = "platform/owner/src/owner-do.ts";
  it.each([
    ["a runner import of the owner's source", {
      "platform/sandbox/runner/probe.ts": 'import { x } from "../../owner/src/owner-do";' },
     [`platform/sandbox/runner/probe.ts imports ${OWNER_DO}`]],
    ["a runner import of the probes", {
      "platform/sandbox/runner/probe.ts": 'import { plan } from "./probes";\nimport "./route";' },
     ["platform/sandbox/runner/probe.ts imports platform/sandbox/runner/probes.ts",
      "platform/sandbox/runner/probe.ts imports platform/sandbox/runner/route.ts"]],
    ["a test reaching another Worker's source", {
      "platform/doors/test/probe.test.ts": 'import { x } from "../../owner/src/ledger";' },
     ["platform/doors/test/probe.test.ts imports platform/owner/src/ledger.ts"]],
    ["a test reaching another Worker's test", {
      "platform/doors/test/probe.test.ts": 'import { x } from "../../owner/test/support";' },
     ["platform/doors/test/probe.test.ts imports platform/owner/test/support.ts"]],
    ["a Worker's source reaching another's", {
      "platform/gateway/src/probe.ts": 'export { x } from "../../files/src/keys";' },
     ["platform/gateway/src/probe.ts imports platform/files/src/keys.ts"]],
    ["a Worker's source reaching its own tests", {
      "platform/owner/src/probe.ts": 'import { x } from "../test/support";' },
     ["platform/owner/src/probe.ts imports platform/owner/test/support.ts"]],
    ["each way one module names another, and a path out and back", {
      "platform/doors/src/probe.ts": [
        '/// <reference path="../../owner/src/launch.ts" />',
        'import type { OwnerDO } from "../../owner/src/owner-do";',
        'export type T = import("../../owner/src/ledger").Ledger;',
        'export const a = () => import("../../owner/src/runs");',
        'export const b = require("../../owner/src/grants");',
        'import c = require("../../owner/src/bodies");',
        'export { d } from "./../src/../../owner/src/inbound";',
      ].join("\n") },
     ["platform/doors/src/probe.ts imports platform/owner/src/launch.ts",
      `platform/doors/src/probe.ts imports ${OWNER_DO}`,
      "platform/doors/src/probe.ts imports platform/owner/src/ledger.ts",
      "platform/doors/src/probe.ts imports platform/owner/src/runs.ts",
      "platform/doors/src/probe.ts imports platform/owner/src/grants.ts",
      "platform/doors/src/probe.ts imports platform/owner/src/bodies.ts",
      "platform/doors/src/probe.ts imports platform/owner/src/inbound.ts"]],
    ["a computed module and an absolute path in a Worker's source", {
      "platform/doors/src/probe.ts":
        'export const f = (n: string) => import(n);\nimport x from "/etc/x.js";' },
     ["platform/doors/src/probe.ts imports /etc/x.js",
      "platform/doors/src/probe.ts imports a computed module"]],
    ["the contract reaching a Worker, the source records, a package and Node's file system", {
      "platform/contract/src/probe.ts": [
        'import { x } from "../../owner/src/text";',
        'import { c } from "../../sources/src/catalog";',
        'import { DurableObject } from "cloudflare:workers";',
        'import { readFileSync } from "node:fs";',
      ].join("\n") },
     ["platform/contract/src/probe.ts imports platform/owner/src/text",
      "platform/contract/src/probe.ts imports platform/sources/src/catalog.ts",
      "platform/contract/src/probe.ts imports cloudflare:workers",
      "platform/contract/src/probe.ts imports node:fs"]],
    ["the source records reaching a file beside them that is none of theirs", {
      "platform/sources/src/probe.ts": 'import x from "../locks/pi/package.json";' },
     ["platform/sources/src/probe.ts imports platform/sources/locks/pi/package.json"]],
    ["the deploy kit reaching a Worker's source, and the test kit", {
      "platform/deploy/probe.ts":
        'import { x } from "../doors/src/config";\nimport "../ci/workers";' },
     ["platform/deploy/probe.ts imports platform/doors/src/config.ts",
      "platform/deploy/probe.ts imports platform/ci/workers.ts"]],
    ["the importer reaching a Worker's source", {
      "platform/import/probe.ts": 'import { x } from "../owner/src/files";' },
     ["platform/import/probe.ts imports platform/owner/src/files.ts"]],
    ["the ci kit reaching a Worker's source, and its guards one", {
      "platform/ci/probe.ts": 'import { x } from "../owner/src/text";',
      "platform/ci/guards/probe.test.ts": 'import { x } from "../../owner/src/text";' },
     ["platform/ci/probe.ts imports platform/owner/src/text",
      "platform/ci/guards/probe.test.ts imports platform/owner/src/text"]],
    ["the repository's scripts reaching the platform", {
      "scripts/probes/probe/heap.ts": 'import { x } from "../../../platform/contract/src/text";' },
     ["scripts/probes/probe/heap.ts imports platform/contract/src/text.ts"]],
    ["a workspace named by its package, and a name a package maps to a path", {
      "platform/doors/test/probe.test.ts": 'import { x } from "@tinyassets/owner/src/ledger";',
      "platform/gateway/src/probe.ts":
        'import type { OwnerDO } from "@tinyassets/owner/src/owner-do.ts";\nimport "#owner/x";' },
     ["platform/doors/test/probe.test.ts imports platform/owner/src/ledger.ts",
      `platform/gateway/src/probe.ts imports ${OWNER_DO}`,
      "platform/gateway/src/probe.ts imports #owner/x, a name its package maps to a path"]],
    ["a Worker's source, the contract and the runner reaching a test beside them", {
      "platform/gateway/src/probe.ts":
        'export * from "./relay.test";\nexport * from "../../contract/src/relay.test";',
      "platform/contract/src/probe.ts": 'export * from "./test/relay";',
      "platform/sandbox/runner/probe.ts": 'export * from "../../sources/src/relay.test";',
      "platform/gateway/src/relay.test.ts": "export {};",
      "platform/contract/src/relay.test.ts": "export {};",
      "platform/contract/src/test/relay.ts": "export {};",
      "platform/sources/src/relay.test.ts": "export {};" },
     ["platform/gateway/src/probe.ts imports platform/gateway/src/relay.test.ts",
      "platform/gateway/src/probe.ts imports platform/contract/src/relay.test.ts",
      "platform/contract/src/probe.ts imports platform/contract/src/test/relay.ts",
      "platform/sandbox/runner/probe.ts imports platform/sources/src/relay.test.ts"]],
    ["a test reaching the ci guards, and the ci guards a workspace's test and the release kit", {
      "platform/doors/test/probe.test.ts": 'export * from "../../ci/guards/fixtures/helper";',
      "platform/ci/guards/probe.test.ts":
        'export * from "../../owner/test/support";\nimport "../../deploy/facts";' },
     ["platform/doors/test/probe.test.ts imports platform/ci/guards/fixtures/helper.ts",
      "platform/ci/guards/probe.test.ts imports platform/owner/test/support.ts",
      "platform/ci/guards/probe.test.ts imports platform/deploy/facts.ts"]],
    ["a test, a memory leg and a tool computing a module, and a loader by another name", {
      "platform/owner/test/probe.test.ts": "export const f = (n: string) => import(n);",
      "platform/owner/memory/probe.mjs":
        'export const all = import.meta.glob("../../gateway/src/*.ts");',
      "platform/owner/probe.ts": [
        'import { createRequire } from "node:module";',
        "const load = createRequire(import.meta.url);",
        'export const a = load("../gateway/src/index.ts");',
        'export const b = createRequire(import.meta.url)("../files/src/keys.ts");',
      ].join("\n"),
      "platform/owner/other.ts": [
        'import module, { createRequire as make } from "node:module";',
        "const one = module.createRequire(import.meta.url), two = make(import.meta.url);",
        "const three = require, four = three;",
        'export const all = [one("../doors/src/tools.ts"), two("../doors/src/api.ts"),',
        '                    four("../doors/src/mcp.ts")];',
      ].join("\n"),
      "platform/doors/test/other.test.ts": [
        '/// <reference types="../../owner/src/launch" />',
        'import { vi } from "vitest";', 'vi.mock("../../owner/src/ledger");',
        'export const m = await vi.importActual("../../owner/src/runs");',
      ].join("\n") },
     ["platform/owner/test/probe.test.ts imports a computed module",
      "platform/owner/memory/probe.mjs imports a computed module",
      "platform/owner/probe.ts imports platform/gateway/src/index.ts",
      "platform/owner/probe.ts imports platform/files/src/keys.ts",
      "platform/owner/other.ts imports platform/doors/src/tools.ts",
      "platform/owner/other.ts imports platform/doors/src/api.ts",
      "platform/owner/other.ts imports platform/doors/src/mcp.ts",
      "platform/doors/test/other.test.ts imports platform/owner/src/launch.ts",
      "platform/doors/test/other.test.ts imports platform/owner/src/ledger.ts",
      "platform/doors/test/other.test.ts imports platform/owner/src/runs.ts"]],
    ["the importer and the source records reaching a socket, a thread and a package", {
      "platform/import/probe.ts": [
        'import net from "net";', 'import { Worker } from "node:worker_threads";',
        'import { request } from "undici";',
      ].join("\n"),
      "platform/sources/src/probe.ts": 'import { readFileSync } from "node:fs";' },
     ["platform/import/probe.ts imports net",
      "platform/import/probe.ts imports node:worker_threads",
      "platform/import/probe.ts imports undici",
      "platform/sources/src/probe.ts imports node:fs"]],
    ["the kits and the runner reaching their own guards and tests, and the probes the Worker", {
      "platform/ci/probe.ts": 'import "./guards/fixtures/helper";',
      "platform/import/probe.ts": 'import "./guards/fixture";',
      "platform/deploy/probe.ts": 'import "./guards/rules";',
      "platform/sandbox/runner/probe.ts": 'import "./test/harness";',
      "platform/sandbox/runner/probes.ts": 'import "../src/control";' },
     ["platform/ci/probe.ts imports platform/ci/guards/fixtures/helper.ts",
      "platform/import/probe.ts imports platform/import/guards/fixture.ts",
      "platform/deploy/probe.ts imports platform/deploy/guards/rules.ts",
      "platform/sandbox/runner/probe.ts imports platform/sandbox/runner/test/harness.ts",
      "platform/sandbox/runner/probes.ts imports platform/sandbox/src/control.ts"]],
    ["the sandbox Worker reaching the E2B tools' calls, and E2B's client a Worker or a package", {
      "platform/sandbox/src/probe.ts": 'import "../e2b/transport";\nimport "../e2b/install";\n'
        + 'import "../e2b/admin";',
      "platform/sandbox/e2b/probe.ts": 'import "../src/e2b";\nimport "node:fs";',
      "platform/deploy/probe.ts": 'import "../sandbox/e2b/admin.ts";\n'
        + 'import "../sandbox/src/e2b.ts";',
      "platform/sandbox/template/probe.ts": 'import "../e2b/admin";\nimport "../src/e2b";' },
     ["platform/sandbox/src/probe.ts imports platform/sandbox/e2b/admin.ts",
      "platform/sandbox/e2b/probe.ts imports platform/sandbox/src/e2b.ts",
      "platform/sandbox/e2b/probe.ts imports node:fs",
      "platform/deploy/probe.ts imports platform/sandbox/src/e2b.ts",
      "platform/sandbox/template/probe.ts imports platform/sandbox/src/e2b.ts"]],
    ["a file in no layer, and one at the platform's top", {
      "platform/doors/lib/probe.ts": "export {};", "platform/probe.ts": "export {};" },
     ["platform/doors/lib/probe.ts is in no layer", "platform/probe.ts is in no layer"]],
  ])("sees %s", (_what, files, expected) => {
    expect(found(files)).toEqual(expected);
  });

  it.each([
    ["an inline import of the release kit's entry", workflow(
      "node --input-type=module -e \\\n  \"import { DARK_CONTROL } from './deploy/deploy.ts';\"",
      "platform"), [".github/workflows/probe.yml imports platform/deploy/deploy.ts"]],
    ["an inline import of a Worker's source, from the root", workflow(
      "node -p 'import(\"./platform/sandbox/src/e2b.ts\")'"),
     [".github/workflows/probe.yml imports platform/sandbox/src/e2b.ts"]],
    ["an inline import of anything by its path", workflow(
      "node --input-type=module -e \"import { REPO } from './ci/paths.ts';\"", "platform"),
     [".github/workflows/probe.yml imports platform/ci/paths.ts"]],
    ["an inline import after a cd", workflow(
      "cd platform\nnode -e \"import './deploy/deploy.ts'\""),
     [".github/workflows/probe.yml imports deploy/deploy.ts"]],
    ["an inline module by another flag, and a computed one", workflow(
      "node --eval=\"import('./a.ts')\"\nnode -pe \"require('./b.ts')\"\n"
      + "deno eval \"import './c.ts'\"\nnode -e 'import(process.env.X)'"),
     [".github/workflows/probe.yml imports a.ts", ".github/workflows/probe.yml imports b.ts",
      ".github/workflows/probe.yml imports c.ts",
      ".github/workflows/probe.yml imports a computed module"].sort()],
    ["a module on a step's standard input, and one written to a file", workflow(
      "node --input-type=module <<'EOF'\nimport { x } from './deploy/deploy.ts';\nEOF\n"
      + "echo \"import './deploy/facts.ts'\" > x.mjs", "platform"),
     [".github/workflows/probe.yml imports platform/deploy/deploy.ts",
      ".github/workflows/probe.yml imports platform/deploy/facts.ts"]],
    ["a step whose shell is node, and a script of github-script", [
      "on: push", "jobs:", "  probe:", "    runs-on: ubuntu-latest", "    steps:",
      "      - shell: node {0}", "        run: |",
      "          import('./platform/deploy/deploy.ts');",
      "      - uses: actions/github-script@v7", "        with:", "          script: |",
      "            require('./platform/owner/src/owner-do.ts');"].join("\n"),
     [".github/workflows/probe.yml imports platform/deploy/deploy.ts",
      `.github/workflows/probe.yml imports ${OWNER_DO}`]],
    ["a path rooted at an expression or at the root, by -e and in a step's own text", [
      "on: push", "jobs:", "  probe:", "    runs-on: ubuntu-latest", "    steps:",
      "      - run: node -e \"import '${{ github.workspace }}/platform/deploy/deploy.ts'\"",
      "      - run: node -e \"import('$GITHUB_WORKSPACE/platform/deploy/facts.ts')\"",
      "      - shell: node {0}", "        run: |",
      "          require('${{ github.workspace }}/platform/owner/src/owner-do.ts');",
      "          await import('/home/runner/work/x/x/platform/deploy/cloudflare.ts');",
    ].join("\n"),
     [".github/workflows/probe.yml imports platform/deploy/deploy.ts",
      ".github/workflows/probe.yml imports platform/deploy/facts.ts",
      `.github/workflows/probe.yml imports ${OWNER_DO}`,
      ".github/workflows/probe.yml imports /home/runner/work/x/x/platform/deploy/cloudflare.ts",
     ].sort()],
  ])("sees, in a workflow, %s", (_what, text, expected) => {
    expect(found({}, { ".github/workflows/probe.yml": text }).sort()).toEqual(expected);
  });

  it("reads every composite action the repository holds, with its workflows", () => {
    expect(actionFiles().length).toBeGreaterThan(0);
    expect(actionFiles().filter((path) => !/^\.github\/actions\/[^/]+\/action\.ya?ml$/.test(path)))
      .toEqual([]);
    expect(readdirSync(join(REPO, ".github", "actions")).length).toBe(actionFiles().length);
    expect([...steps().keys()]).toEqual(expect.arrayContaining(actionFiles()));
    expect(steps().size).toBe(workflowNames().length + actionFiles().length);
  });

  it("allows a step the repository's scripts, and a shell command of an import's words", () => {
    expect(found({}, { ".github/workflows/probe.yml": [
      "on: push", "jobs:", "  probe:", "    runs-on: ubuntu-latest", "    steps:",
      "      - uses: actions/github-script@v7", "        with:", "          script: |",
      "            const root = process.env.GITHUB_WORKSPACE;",
      "            const { x } = require(`${root}/scripts/incident_issue.js`);",
      "      - run: |", "          node -e \"import('yaml')\"",
      "          security import \"$dir/distribution.p12\" -k \"$keychain\"",
    ].join("\n") })).toEqual([]);
  });

  it("sees, in a composite action, an inline import", () => {
    expect(found({}, { ".github/actions/probe/action.yml": [
      "runs:", "  using: composite", "  steps:", "    - shell: bash",
      "      run: node -e \"import './platform/deploy/deploy.ts'\""].join("\n") }))
      .toEqual([".github/actions/probe/action.yml imports platform/deploy/deploy.ts"]);
  });

  it.each([
    ["a Worker its own source and data, the contract and the source records", {
      "platform/doors/src/probe.ts": [
        'import { a } from "./tools";', 'import { b } from "../../contract/src/text";',
        'import { c } from "../../sources/src/catalog";', 'import d from "../shared-apps.json";',
        'import e from "../../contract/identity.json";',
      ].join("\n") }],
    ["the owner the starter's package, and the sandbox Worker the runner's wire", {
      "platform/owner/src/probe.ts": 'import s from "../../starter/starter.json";',
      "platform/sandbox/src/probe.ts": 'import type { StartDoc } from "../runner/protocol";' }],
    ["the probes the route probe, the runner its own modules, the template both", {
      "platform/sandbox/runner/probes.ts": 'import { reply } from "./route";',
      "platform/sandbox/runner/probe.ts": 'import { LOADER } from "./protocol";',
      "platform/sandbox/template/probe.ts": 'import type { Plan } from "../runner/probes";' }],
    ["the deploy kit the pair, its paths and a Worker's own data, and its tests the same", {
      "platform/deploy/probe.ts": [
        'import { pairTag } from "../sandbox/template/pair";',
        'import { REPO } from "../ci/paths";', 'import apps from "../doors/shared-apps.json";',
      ].join("\n"),
      "platform/deploy/guards/probe.test.ts":
        'import { pairTag } from "../../sandbox/template/pair";\nimport "../../ci/workers";' }],
    ["the importer the contract, the deploy facts and the starter's package", {
      "platform/import/probe.ts": [
        'import { x } from "../contract/src/content";', 'import { f } from "../deploy/facts";',
        'import { carried } from "../owner/src/starter-package";',
        'import { REPO } from "../ci/paths";',
      ].join("\n") }],
    ["the owner app its own pages, by the path they are served at", {
      "platform/doors/app/probe.js": 'import { h } from "/app/static/view.js";' }],
    ["a test its own workspace, the contract, the records and the ci kit", {
      "platform/owner/test/probe.test.ts": [
        'import { x } from "../src/ledger";', 'import { y } from "../../contract/src/text";',
        'import { DARK } from "../../ci/fixtures";', 'import { c } from "../../sources/src/catalog";',
        'import { z } from "@tinyassets/contract/src/ids";',
      ].join("\n") }],
    ["a guard the same, and a computed module", {
      "platform/owner/guards/probe.test.ts": [
        'import { x } from "../src/ledger";', 'import { PLATFORM } from "../../ci/paths";',
        "export const f = (n: string) => import(n);",
      ].join("\n"),
      "platform/ci/guards/probe.test.ts":
        'import { REPO } from "../paths";\nexport const f = (n: string) => import(n);' }],
  ])("allows %s", (_what, files) => {
    expect(found(files)).toEqual([]);
  });

  it("allows a workflow's inline module a package, and a step a script in a layer", () => {
    expect(found({}, { ".github/workflows/probe.yml": workflow(
      "node --input-type=module -e \"import { parse } from 'yaml';\"\n"
      + "node --import ./sandbox/template/resolve.mjs sandbox/template/checks.ts", "platform") }))
      .toEqual([]);
  });

  it("sees a file two layers hold, and the last pattern decides", () => {
    const layers: Layer[] = [{ name: "a", files: ["platform/doors/**", "!platform/doors/x/**",
                                                  "platform/doors/x/y.ts"], may: [] },
                             { name: "b", files: ["platform/doors/x/**"], may: [] }];
    expect(layersOf("platform/doors/x/y.ts", layers, WORKERS).map((each) => each.name))
      .toEqual(["a", "b"]);
    expect(layersOf("platform/doors/x/z.ts", layers, WORKERS).map((each) => each.name))
      .toEqual(["b"]);
    expect(layerProblems({ files: new Map([["platform/doors/x/y.ts", "export {};"]]), layers,
                           workers: WORKERS }).unplaced)
      .toEqual(["platform/doors/x/y.ts is in 2 layers: a, b"]);
  });
});

describe("the owed ratchet", () => {
  const owes = (what: string, fix = "B1"): Owed => ({ guard: "layers", what, fix });
  const held = [{ workspace: "doors", what: "a imports b" }];
  const unowed = "a imports b: owed by no one (fix it; platform/doors/guards/rules.ts's OWED " +
                 "lists only what the fix it names will remove)";

  it("passes a violation its workspace owes, by the guard that finds it", () => {
    expect(ratchet("layers", held, new Map([["doors", [owes("a imports b")]],
                                            ["owner", [{ ...owes("x"), guard: "other" }]]])))
      .toEqual([]);
  });

  it.each([
    ["a violation no one owes", new Map<string, Owed[]>(), [unowed]],
    ["one only another workspace owes", new Map([["owner", [owes("a imports b")]]]), [
      'platform/owner/guards/rules.ts owes "a imports b", which no longer holds: delete the entry',
      unowed]],
    ["an owed one that no longer holds", new Map([["doors", [owes("a imports b"), owes("c")]]]),
     ['platform/doors/guards/rules.ts owes "c", which no longer holds: delete the entry']],
    ["one owed twice", new Map([["doors", [owes("a imports b"), owes("a imports b")]]]),
     ['platform/doors/guards/rules.ts owes "a imports b" twice']],
    ["one owed with no fix named", new Map([["doors", [owes("a imports b", "later")]]]),
     ['platform/doors/guards/rules.ts owes "a imports b" with no fix named ("later")']],
  ])("fails %s", (_what, owed, expected) => {
    expect(ratchet("layers", held, owed)).toEqual(expected);
  });

  it("sees an entry owed to a guard no one holds", () => {
    expect(unheld(new Map([["owner", [{ ...owes("x"), guard: "layer" }, owes("y")]]])))
      .toEqual(['platform/owner/guards/rules.ts owes "x" to "layer", a guard no one holds']);
  });
});

describe("the banned names' closure", () => {
  it("walks a module outside every Worker's source, read from disk, on the way", () => {
    const sources = new Map([
      ["doors/src/probe.ts", 'export type { Refusal } from "../../ci/guards/fixtures/helper";'],
      ["contract/src/refusal.ts", "export class Refusal {}"]]);
    expect([...closure("doors", sources).keys()]).toEqual(["doors/src/probe.ts",
      "ci/guards/fixtures/helper.ts", "contract/src/refusal.ts"]);
  });
});
