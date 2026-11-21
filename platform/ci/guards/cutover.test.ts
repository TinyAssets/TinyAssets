/**
 * The cutover's manifest (`platform/cutover.json`, read by `ci/kit/cutover.ts`) held to the
 * tree, so the cutover's change is the manifest applied and nothing else:
 * - it is in its shape: three phases, each entry a delete, an edit, a move, a live object or
 *   a keep;
 * - every entry exists: each path, each name an edit or a move takes out of its file, each
 *   import an edit names, and each live object's name in the file that names it today; no
 *   delete or edit lies inside a path the manifest deletes, and no file is edited twice in a
 *   phase; a move may (a carry-out, made before the deletes), but never into one;
 * - the tree it leaves, every tracked file with its edits applied (`kit/cutover.ts` `edited`):
 *   no file names a path it deletes or imports a Python module it deletes, unless a keep says
 *   why; under `platform/` and `.github/`, none says what is "deleted at the cutover" unedited or
 *   speaks the cutover's vocabulary (`/workspace/imported`, "old TinyAssets", the `import` and
 *   `discard` callers, `IMPORT_*`); an edited workflow is YAML; and in each phase its Python
 *   compiles and binds every name it reads, and its TOML parses (`kit/python_names.py`). What
 *   is not yet so is owed by file (`ci/guards/rules.ts`), and only shrinks;
 * - the layer table's cutover layer (`ci/kit/imports.ts`): what it deletes may import itself,
 *   and nothing that stays imports it but an import an edit names (held over the tree by
 *   `isolation.test.ts`, its cases here); a Python file that stays the same, by its imports;
 * - its dry run (`ci/dry-run.ts`) makes the tree its cases say.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { join, posix } from "node:path";

import { describe, expect, it } from "vitest";
import { parse } from "yaml";

import { type CutoverLayer, cutoverLayer, dryRun, dryRunLegs, edited, type Entry, isGone, LIVE,
         type Manifest, MANIFEST, type Phase, PHASES, pythonLoads,
         pythonModules, taken } from "../kit/cutover";
import { keyed, object } from "../../contract/src/checks";
import { layerProblems, repoScripts } from "../kit/imports";
import { owedLists, ratchet } from "../kit/owed";
import { treeFiles } from "../kit/sources";
import { REPO } from "../paths";
import { actionFiles, type Data, workflowNames } from "../workflows";
import { workerFolders } from "../wrangler";

/** A text that says what is deleted at the cutover, however its comment wraps the words. */
const MARKER = /deleted(?:[\s*#/]|<!--)+at(?:[\s*#/])+the(?:[\s*#/])+cutover/i;
/** The cutover's vocabulary: no file that stays may speak it. */
const VOCABULARY: readonly RegExp[] = Object.freeze([
  /\/workspace\/imported/, /old TinyAssets/i, /["'](?:import|discard)["']/, /\bIMPORT_[A-Z0-9_]+/]);
/** A path a text names, with a folder in it: from the root, or as a step's local action (`./x`). */
const PATH = /(?<![\w.@/-])(?:\.\/)?((?:[\w-]|\.[\w-])[\w.-]*(?:\/[\w.-]+)+)/g;
/** A link to a path of this repository's main, which names that path from the root. */
const REPO_LINK = /https:\/\/github\.com\/TinyAssets\/TinyAssets\/(?:tree|blob)\/main\//g;

/**
 * Each folder or file of `goes` (what the manifest deletes) that `content`, the file at `path`,
 * names: from the repository root, or under `platform/` from there (`import/bundle.ts`), and
 * never one `stays` holds (the tree the manifest leaves). A one-word folder (`tests`) is named
 * only with a path below it. Under `platform/`, the old stack's own paths are left out: a
 * platform comment cites the code it was ported from as history (b24-manifest item 1; the
 * founder's call, IQ2).
 */
function goneMentions(path: string, content: string, goes: ReadonlySet<string>,
                      stays: ReadonlySet<string>): string[] {
  const found = new Set<string>();
  for (const [, token] of content.replace(REPO_LINK, " ").matchAll(PATH)) {
    const named = token!.replace(/\.+$/, "");
    const forms = [named, `platform/${named}`];
    const hit = forms.some((each) => stays.has(each)) ? undefined
      : forms.find((each) => goes.has(each));
    if (hit !== undefined
        && (!path.startsWith("platform/") || /^(?:platform|\.github|scripts)\//.test(hit))) {
      found.add(named);
    }
  }
  return [...found];
}

/** Each folder and file of `paths`, from the repository root. */
function withFolders(paths: Iterable<string>): Set<string> {
  const all = new Set<string>();
  for (const path of paths) {
    path.split("/").forEach((_, at, parts) => all.add(parts.slice(0, at + 1).join("/")));
  }
  return all;
}

/** The shells of a workflow's or a composite action's `run` steps, one after another. */
function runs(text: string): string {
  const data = (parse(text) ?? {}) as Data;
  return [...Object.values(data.jobs ?? {}) as Data[], data.runs ?? {}]
    .flatMap((job: Data) => (job.steps ?? []) as Data[])
    .flatMap((step: Data) => typeof step.run === "string" ? [step.run as string] : []).join("\n");
}

/**
 * Each script of `gone` a workflow's steps run: named by its path, or as a module
 * (`python -m scripts.x`). A step's script is the workflow's load, as an import is a module's.
 */
function stepLoads(text: string, gone: readonly string[]): string[] {
  const shell = runs(text);
  const escape = (name: string) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return gone.filter((path) => /^scripts\/.+\.py$/.test(path)).filter((path) =>
    new RegExp(`(?<![\\w./-])(?:${escape(path)}|${escape(path.slice(0, -3).replaceAll("/", "."))
    })(?![\\w-])`).test(shell));
}

/** A tree the manifest is held to: whether a path is there, and a file's text. */
interface Tree {
  readonly exists: (path: string) => boolean;
  readonly text: (path: string) => string | null;
}

const REPO_TREE: Tree = {
  exists: (path) => existsSync(join(REPO, path)),
  text: (path) => {
    const at = join(REPO, path);
    return existsSync(at) && statSync(at).isFile() ? readFileSync(at, "utf8") : null;
  },
};

const words = (value: unknown) => Array.isArray(value) && value.length > 0
  && value.every((each) => typeof each === "string" && each !== "");
const text = (value: unknown) => typeof value === "string" && value !== "";

/** A key of an entry: whether it must be there, and what its value is. */
type Key = readonly [boolean, (value: unknown) => boolean];
/** Each entry's keys, by its action. */
const SHAPES: Readonly<Record<string, Readonly<Record<string, Key>>>> = {
  delete: { delete: [true, text], what: [true, text] },
  edit: { edit: [true, text], names: [true, words], imports: [false, words], what: [true, text] },
  move: { move: [true, text], to: [true, text], names: [false, words], what: [true, text] },
  live: { live: [true, (v) => LIVE.includes(v as (typeof LIVE)[number])], name: [true, text],
          in: [true, text], what: [true, text] },
  keep: { keep: [true, text], what: [true, text] },
};

/** How the manifest is out of its shape. */
function shapeProblems(value: unknown): string[] {
  if (!keyed(value, ["about", "phases"]) || !text(value["about"])
      || !object(value["phases"]) || Object.keys(value["phases"]).join() !== PHASES.join()) {
    return [`the manifest is its about and its phases, ${PHASES.join(", ")}, in order`];
  }
  const problems: string[] = [];
  for (const phase of PHASES) {
    const body = value["phases"][phase];
    if (!keyed(body, ["entries", "why"]) || !text(body["why"])
        || !Array.isArray(body["entries"])) {
      problems.push(`phase ${phase} is its why and its entries`);
      continue;
    }
    body["entries"].forEach((entry: unknown, n: number) => {
      const action = object(entry) ? Object.keys(SHAPES).filter((key) => key in entry) : [];
      const shape = action.length === 1 ? SHAPES[action[0]!]! : null;
      if (shape === null || !Object.keys(entry as object).every((key) => key in shape)
          || !Object.entries(shape).every(([key, [needed, valid]]) =>
            (entry as Record<string, unknown>)[key] === undefined ? !needed
              : valid((entry as Record<string, unknown>)[key]))) {
        problems.push(`phase ${phase}'s entry ${n} is no delete, edit, move, live object or keep`);
      }
    });
  }
  return problems;
}

const entries = (manifest: Manifest) => PHASES.flatMap((phase) =>
  manifest.phases[phase].entries.map((entry) => ({ phase, entry })));

/** The paths an entry lists: what it deletes, edits, moves or keeps. */
const pathOf = (entry: Entry): string | null =>
  "delete" in entry ? entry.delete : "edit" in entry ? entry.edit
    : "move" in entry ? entry.move : "keep" in entry ? entry.keep : null;

/** How an entry is not what the tree holds today. */
function entryProblems(manifest: Manifest, tree: Tree): string[] {
  const problems: string[] = [];
  const { gone } = cutoverLayer(manifest);
  const edited = new Set<string>();
  const holds = (path: string, names: readonly string[], what: string) => {
    const found = tree.text(path);
    for (const name of names) {
      if (found === null || !found.includes(name)) {
        problems.push(`${what} names ${name}, which ${path} does not hold`);
      }
    }
  };
  for (const { phase, entry } of entries(manifest)) {
    const path = pathOf(entry);
    if (path !== null && !tree.exists(path)) {
      problems.push(`${phase}: ${path} is not there`);
      continue;
    }
    if (path !== null && !("move" in entry)
        && gone.some((each) => each !== path && isGone([each], path))) {
      problems.push(`${phase}: ${path} lies inside a path the manifest deletes`);
    }
    if ("edit" in entry) {
      if (edited.has(`${phase} ${entry.edit}`)) {
        problems.push(`${phase}: ${entry.edit} is edited twice`);
      }
      edited.add(`${phase} ${entry.edit}`);
      holds(entry.edit, entry.names, `the edit of ${entry.edit}`);
      for (const to of entry.imports ?? []) {
        if (!isGone(gone, to)) {
          problems.push(`the edit of ${entry.edit} names an import of ${to}, which stays`);
        }
      }
    } else if ("move" in entry) {
      holds(entry.move, entry.names ?? [], `the move of ${entry.move}`);
      if (isGone(gone, entry.to)) {
        problems.push(`${phase}: ${entry.move} moves into ${entry.to}, which the manifest deletes`);
      }
      if (tree.exists(entry.to) !== (entry.names !== undefined)) {
        problems.push(`${phase}: ${entry.move} moves ${entry.names === undefined
          ? `to ${entry.to}, which is there already` : `its names into ${entry.to}, not there`}`);
      }
    } else if ("live" in entry) {
      holds(entry.in, [entry.name], `the ${entry.live} ${entry.name}`);
    }
  }
  return problems;
}

/**
 * The tree the manifest leaves once `upTo`'s phase is done, from `files` (each tracked file
 * today, by path, with its text): each file that stays, at its path then (a carry-out at its
 * new one), with its edits of every phase so far applied (`edited`), and what is deleted by then.
 */
function leaves(manifest: Manifest, files: ReadonlyMap<string, string>,
                upTo: Phase = "hold"): { left: Map<string, string>; gone: string[] } {
  const done = entries(manifest).filter(({ phase }) =>
    PHASES.indexOf(phase) <= PHASES.indexOf(upTo)).map(({ entry }) => entry);
  const gone = done.flatMap((entry) => "delete" in entry ? [entry.delete] : []);
  const names = new Map<string, string[]>();
  const carried = new Map<string, string>();
  const into = new Map<string, string>();
  for (const entry of done) {
    if ("edit" in entry) {
      names.set(entry.edit, [...names.get(entry.edit) ?? [], ...entry.names]);
    } else if ("move" in entry && entry.names !== undefined) {
      into.set(entry.to, `${into.get(entry.to) ?? ""}\n${
        taken(entry.move, files.get(entry.move) ?? "", entry.names)}`);
      names.set(entry.move, [...names.get(entry.move) ?? [], ...entry.names]);
    } else if ("move" in entry && isGone(gone, entry.move)) {
      carried.set(entry.move, entry.to);
    }
  }
  const left = new Map<string, string>();
  for (const [path, content] of files) {
    if (carried.has(path) || !isGone(gone, path)) {
      left.set(carried.get(path) ?? path,
               edited(path, content + (into.get(path) ?? ""), names.get(path) ?? []));
    }
  }
  return { left, gone };
}

/**
 * How the tree the manifest leaves still holds what goes: a file that stays, as its edits leave
 * it, that names a path the manifest deletes (`goneMentions`) or imports a Python module it
 * deletes; under `platform/` and `.github/`, that says what is "deleted at the cutover" or
 * speaks the cutover's vocabulary; an edited workflow that is no YAML. A `keep` leaves its files
 * as they are, and must still name what goes.
 */
function leftProblems(manifest: Manifest, files: ReadonlyMap<string, string>): string[] {
  const { left, gone } = leaves(manifest, files);
  const stays = withFolders(left.keys());
  const goes = withFolders([...files.keys()].filter((path) => isGone(gone, path)));
  const keeps = entries(manifest).flatMap(({ entry }) => "keep" in entry ? [entry.keep] : []);
  const edits = new Set(entries(manifest).flatMap(({ entry }) =>
    "edit" in entry ? [entry.edit] : []));
  const kept = new Set<string>();
  const problems = [...left].flatMap(([path, content]) => {
    const said = goneMentions(path, content, goes, stays);
    if (path.endsWith(".py")) {
      said.push(...pythonLoads(path, content, (each) => files.has(each))
        .filter((to) => isGone(gone, to)).map((to) => `an import of ${to}`));
    }
    const edit = edits.has(path) ? " less its edit" : "";
    // In an edited file, the marker marks what its edit takes: an anchor of its own.
    if (/^(?:platform|\.github)\//.test(path)) {
      said.push(...[MARKER.test(content) && edit === "" ? "deleted at the cutover" : null,
                    ...VOCABULARY.map((word) => word.exec(content)?.[0] ?? null)]
        .filter((each) => each !== null));
    }
    const keep = keeps.find((each) => isGone([each], path));
    if (keep !== undefined) {
      if (said.length > 0) kept.add(keep);
      return [];
    }
    if (edit !== "" && /\.ya?ml$/.test(path)) {
      try {
        parse(content);
      } catch (error) {
        said.push(`no YAML (${(error as Error).message.split("\n")[0]})`);
      }
    }
    return said.length === 0 ? [] : [`${path}${edit} says ${said.join(", ")}`];
  });
  return [...problems, ...keeps.filter((each) => !kept.has(each))
    .map((each) => `${each}: a keep that names nothing that goes: drop it`)];
}

/** The file a problem of `leftProblems` or `pythonProblems` is in: what a workspace owes. */
const fileOf = (problem: string) => /^(?:(?:routes|cutover|hold): )?([^\s:]+)/.exec(problem)![1]!;

/**
 * How the Python and TOML files each phase leaves do not hold together (`kit/python_names.py`):
 * a `.py` file that does not compile, reads a name nothing binds, or imports a name its module
 * no longer defines; a `.toml` file that does not parse.
 */
function pythonProblems(manifest: Manifest, files: ReadonlyMap<string, string>): string[] {
  // Each phase's tree of what stays when the hold ends: the old stack's own modules go.
  const final = new Set(leaves(manifest, files).left.keys());
  const trees = Object.fromEntries(PHASES.map((phase) => {
    const tree = [...leaves(manifest, files, phase).left].filter(([path]) =>
      /\.(?:py|toml)$/.test(path) && final.has(path));
    const has = new Set(tree.map(([path]) => path));
    return [phase, Object.fromEntries(tree.map(([path, text]) => [path, {
      text, modules: Object.fromEntries(pythonModules(path, text, (each) => has.has(each))) }]))];
  }));
  const found = JSON.parse(execFileSync("python3",
    ["-I", join(REPO, "platform/ci/kit/python_names.py")],
    { input: JSON.stringify(trees), encoding: "utf8", maxBuffer: 64 * 1024 * 1024 })) as
    Record<Phase, string[]>;
  return PHASES.flatMap((phase) => found[phase].map((each) => `${phase}: ${each}`));
}

/** The imports into what the cutover deletes that files which stay make: `<from> imports <to>`. */
function cutoverImports(files: ReadonlyMap<string, string>, layer: CutoverLayer): string[] {
  const { violations } = layerProblems({ files, workers: workerFolders(),
                                         cutover: { gone: layer.gone, entries: new Map() } });
  return violations.flatMap((each) =>
    /^(\S+ imports \S+), which the cutover deletes/.exec(each.what)?.[1] ?? []);
}

/** A manifest of `entries` in its cutover phase, the other two empty. */
const manifestOf = (...list: Entry[]): Manifest => ({
  about: "a probe", phases: { routes: { why: "a probe", entries: [] },
                               cutover: { why: "a probe", entries: list },
                               hold: { why: "a probe", entries: [] } } });

/** A tree of `files`, by path, with their folders. */
function treeOf(files: Record<string, string>): Tree {
  return {
    exists: (path) => Object.keys(files).some((each) => each === path
      || each.startsWith(`${path}/`)),
    text: (path) => files[path] ?? null,
  };
}

describe("the cutover's manifest", () => {
  it("is in its shape", () => {
    expect(shapeProblems(MANIFEST)).toEqual([]);
    const what = "a probe";
    for (const [why, entry] of [
      ["two actions", { delete: "a", edit: "a", what }],
      ["a key no action has", { delete: "a", why: "b", what }],
      ["an edit that takes out nothing", { edit: "a", names: [], what }],
      ["a kind of live object there is none of", { live: "server", name: "a", in: "b", what }],
      ["no what", { delete: "a" }],
    ] as const) {
      expect([why, shapeProblems(manifestOf(entry as unknown as Entry))])
        .toEqual([why, ["phase cutover's entry 0 is no delete, edit, move, live object or keep"]]);
    }
    expect(shapeProblems({ about: "a", phases: { cutover: { why: "a", entries: [] } } }))
      .toEqual(["the manifest is its about and its phases, routes, cutover, hold, in order"]);
  });

  it("names only what the tree holds today, and nothing inside what it deletes", () => {
    expect(entryProblems(MANIFEST, REPO_TREE)).toEqual([]);
    const tree = treeOf({ "platform/a/x.ts": "export const ALPHA = 1;",
                          "platform/b.ts": "export const BETA = 2;", "platform/c.ts": "" });
    const what = "a probe";
    expect(entryProblems(manifestOf(
      { delete: "platform/a", what }, { delete: "platform/gone.ts", what },
      { edit: "platform/b.ts", names: ["BETA", "GAMMA"], imports: ["platform/c.ts"], what },
      { edit: "platform/b.ts", names: ["BETA"], what },
      { edit: "platform/a/x.ts", names: ["ALPHA"], what },
      { move: "platform/b.ts", to: "platform/c.ts", what },
      { move: "platform/b.ts", to: "platform/d.ts", names: ["BETA"], what },
      { move: "platform/a/x.ts", to: "platform/a/y.ts", what },
      { move: "platform/a/x.ts", to: "platform/e.ts", what },
      { live: "queue", name: "tinyassets-probe", in: "platform/b.ts", what }), tree)).toEqual([
      "cutover: platform/gone.ts is not there",
      "the edit of platform/b.ts names GAMMA, which platform/b.ts does not hold",
      "the edit of platform/b.ts names an import of platform/c.ts, which stays",
      "cutover: platform/b.ts is edited twice",
      "cutover: platform/a/x.ts lies inside a path the manifest deletes",
      "cutover: platform/b.ts moves to platform/c.ts, which is there already",
      "cutover: platform/b.ts moves its names into platform/d.ts, not there",
      "cutover: platform/a/x.ts moves into platform/a/y.ts, which the manifest deletes",
      "the queue tinyassets-probe names tinyassets-probe, which platform/b.ts does not hold",
    ]);
  });

  it("leaves no file that names what goes, its edits applied, and Python that holds together, " +
     "but what ci owes", async () => {
    const files = treeFiles(["."]);
    const found = [...leftProblems(MANIFEST, files), ...pythonProblems(MANIFEST, files)];
    const owed = await owedLists();
    const owes = new Set((owed.get("ci") ?? []).flatMap((each) =>
      each.guard === "cutover" ? [each.what] : []));
    expect(found.filter((each) => !owes.has(fileOf(each)))).toEqual([]);
    expect(ratchet("cutover", [...new Set(found.map(fileOf))].map((what) =>
      ({ workspace: "ci", what })), owed)).toEqual([]);
  });

  it("takes each name with the block it heads, or as its text", () => {
    expect(edited("a.md", "# A\n\n## Old\n\nold\n\n### Deeper\n\nold\n\n## New\n\nkept\n",
                  ["## Old"])).toBe("# A\n\n## New\n\nkept\n");
    expect(edited("a.toml", "[project]\nname = 'x'\n\n[tool.ruff]\nk = 1\n", ["[project]"]))
      .toBe("[tool.ruff]\nk = 1\n");
    expect(edited("a.yml", "jobs:\n  old:\n    steps:\n\n      - run: x\n  new:\n    k: 1\n",
                  ["  old:"])).toBe("jobs:\n  new:\n    k: 1\n");
    expect(edited("a.py", "KEYS = (\n    'A',\n)\nB = 1\n", ["KEYS = ("])).toBe("B = 1\n");
    expect(edited("a.sh", "if x; then\n  y\nfi\nz\n", ["if x; then"])).toBe("z\n");
    // A fragment goes as its text; a name that starts with its indent only where a line does.
    expect(edited("a.py", "from a import b, c\n", ["b, "])).toBe("from a import c\n");
    expect(edited("a.yml", "a:\n    x:\n      k: 1\n  x:\n    k: 2\nb: 1\n", ["  x:"]))
      .toBe("a:\n    x:\n      k: 1\nb: 1\n");
    expect(edited("a.py", "# one\n# two\nX = 1\nY = 2\n", ["# one\n# two\nX = 1"])).toBe("Y = 2\n");
  });

  it("finds what an edit leaves of what goes, and what a file that stays still names", () => {
    const what = "a probe";
    const manifest = manifestOf(
      { delete: "platform/import", what }, { delete: ".github/actions/gone", what },
      { delete: "scripts/old.py", what }, { delete: "tests", what }, { delete: "deploy", what },
      { edit: "platform/kept.ts", names: ["IMPORT_X"], what },
      { edit: "docs/a.md", names: ["## Old"], what },
      { edit: "docs/b.md", names: ["Old"], what },
      { edit: ".github/workflows/w.yml", names: ["  old:"], what },
      { edit: ".github/workflows/x.yml", names: ["a"], what },
      { keep: "notes", what }, { keep: "ideas", what });
    expect(leftProblems(manifest, new Map([
      ["platform/import/bundle.ts", "export const IMPORT_X = 1;"],
      [".github/actions/gone/action.yml", ""], ["tests/test_x.py", ""], ["deploy/x.sh", ""],
      ["scripts/old.py", ""],
      ["platform/deploy/checklist.md", ""],
      ["platform/kept.ts", "export const IMPORT_X = 1;"],
      ["platform/a.ts", "/**\n * Deleted at the\n * cutover with the job.\n */"],
      ["platform/b.md", "Your files from the old TinyAssets are in /workspace/imported."],
      ["platform/c.ts", 'const caller = "discard";'],
      ["platform/d.ts", "export const IMPORTANT_IMPORTS = 1; // imports a module"],
      ["platform/e.ts", "// built by `import/bundle.ts`, ported from `tests/test_x.py`"],
      [".github/workflows/f.yml", "      - uses: ./.github/actions/gone\n      - run: python scripts/old.py"],
      ["platform/g.ts", "// platform/importer/x.ts, deploy/import, scripts/old.pyc, tests/import/x"],
      ["docs/a.md", "# A\n\n## Old\n\nsee tests/test_x.py\n\n## New\n\nkept\n"],
      ["docs/b.md", "## Old\n\nsee scripts/old.py\n"],
      ["docs/c.md", "The kit's own: deploy/checklist.md. The old host's: deploy/x.sh."],
      ["docs/d.md", "[x](https://github.com/TinyAssets/TinyAssets/blob/main/scripts/old.py)"],
      ["scripts/keep.py", "import old\n"],
      [".github/workflows/w.yml", "jobs:\n  old:\n    steps:\n      - run: python scripts/old.py\n" +
                                  "  new:\n    steps: [a, b]\n"],
      [".github/workflows/x.yml", "on: [a, b]\n"],
      ["notes/n.md", "see tests/test_x.py"], ["ideas/i.md", "nothing that goes"],
    ]))).toEqual([
      "platform/a.ts says deleted at the cutover",
      "platform/b.md says /workspace/imported, old TinyAssets",
      'platform/c.ts says "discard"',
      "platform/e.ts says import/bundle.ts",
      ".github/workflows/f.yml says .github/actions/gone, scripts/old.py",
      "docs/b.md less its edit says scripts/old.py",
      "docs/c.md says deploy/x.sh",
      "docs/d.md says scripts/old.py",
      "scripts/keep.py says an import of scripts/old.py",
      ".github/workflows/x.yml less its edit says no YAML (Unexpected , in flow sequence at " +
        "line 1, column 6:)",
      "ideas: a keep that names nothing that goes: drop it",
    ]);
  });

  it("finds Python a phase leaves that reads a name nothing binds, and TOML that does not parse",
     () => {
    const what = "a probe";
    const manifest = manifestOf(
      { delete: "scripts/old.py", what }, { delete: "tinyassets", what },
      { edit: "scripts/q.py", names: ["from old import NO"], what },
      { edit: "scripts/r.py", names: ["from old import NO", "NO,"], what },
      { move: "tinyassets/base.py", to: "scripts/peer.py", names: ["KEYS = (", "def scrub():"],
        what },
      { edit: "scripts/peer.py", names: ["from tinyassets.base import scrub"], what },
      { edit: "pyproject.toml", names: ["[project]"], what },
      { edit: "a.toml", names: ["= 1"], what });
    const found = pythonProblems(manifest, new Map([
      ["scripts/old.py", "NO = 1\n"],
      // The old oracle's line, its import taken and its one use left: a NameError at run time.
      ["scripts/q.py", "from old import NO\n\n\ndef f(line):\n    return line.startswith(NO)\n"],
      ["scripts/r.py", "from old import NO\nLINES = (NO,)\n\n\ndef f(line):\n" +
                       "    return line.startswith(LINES)\n"],
      ["scripts/s.py", "import q as qa\nprint(qa.f, qa.gone, qa.__file__)\n"],
      ["scripts/t.py", "from r import f, g\n"],
      ["scripts/u.py", "from pkg import (\n    sub,\n)\n"], ["scripts/pkg/__init__.py", ""],
      ["scripts/pkg/sub.py", "﻿X = 1\n"],
      ["tinyassets/base.py", "import os\n\nKEYS = (\n    'A',\n)\n\n\ndef scrub():\n" +
                             "    return {k: v for k, v in os.environ.items() if k not in KEYS}\n"],
      ["scripts/peer.py", "import os\nfrom tinyassets.base import scrub\n\nprint(scrub())\n"],
      ["pyproject.toml", "[project]\nname = 'x'\n\n[tool.ruff]\nline-length = 100\n"],
      ["a.toml", "[x]\nk = 1\n"],
    ]));
    expect(found.filter((each) => each.startsWith("cutover: ")).map((each) =>
      each.replace(/(is no TOML).*/, "$1"))).toEqual([
      "cutover: scripts/q.py:4: reads NO, which nothing binds",
      "cutover: a.toml: is no TOML",
      "cutover: scripts/s.py:2: reads qa.gone, which scripts/q.py does not define",
      "cutover: scripts/t.py:1: imports g from scripts/r.py, which does not define it",
    ]);
    expect(found.filter((each) => each.startsWith("routes: "))).toEqual([
      "routes: scripts/s.py:2: reads qa.gone, which scripts/q.py does not define",
      "routes: scripts/t.py:1: imports g from scripts/r.py, which does not define it",
    ]);
  });

  it("names an import from a file that stays only where that file makes it today", () => {
    const named = [...cutoverLayer().entries].flatMap(([from, to]) =>
      to.map((each) => `${from} imports ${each}`));
    const made = new Set(cutoverImports(repoScripts(), cutoverLayer()));
    // Or a Python module it imports, or a path it hands a tool, as a test config its setup file.
    const handed = (each: string) => {
      const [from, to] = each.split(" imports ") as [string, string];
      const source = REPO_TREE.text(from) ?? "";
      return pythonLoads(from, source, REPO_TREE.exists).includes(to)
        || (/\.ya?ml$/.test(from) && stepLoads(source, [to]).includes(to))
        || [...source.matchAll(/["'](\.{1,2}\/[^"']+)["']/g)]
          .some(([, path]) => posix.normalize(posix.join(posix.dirname(from), path!)) === to);
    };
    expect(named.filter((each) => !made.has(each) && !handed(each))).toEqual([]);
  });

  it("names every module of what goes that a Python file which stays imports", () => {
    const { gone, entries } = cutoverLayer();
    const python = execFileSync("git", ["ls-files", "-z", "--", "*.py"], { cwd: REPO,
      encoding: "utf8" }).split("\0").filter((path) => path !== "" && !isGone(gone, path));
    expect(python.length).toBeGreaterThan(20);
    expect(python.flatMap((path) => pythonLoads(path, REPO_TREE.text(path)!, REPO_TREE.exists)
      .filter((to) => isGone(gone, to) && !(entries.get(path) ?? []).includes(to))
      .map((to) => `${path} imports ${to}: name it on the file's edit`))).toEqual([]);
    // A module beside it, a folder above it, or from the root; a package's module; none else.
    const tree = new Set(["scripts/a.py", "scripts/b/__init__.py", "scripts/b/c.py",
                          "scripts/tests/t.py", "tools/d.py"]);
    expect(pythonLoads("scripts/tests/t.py", [
      "import a", "from b import c as see, e", "    from tools.d import x", "import os, a",
      "from . import nothing", "# import gone"].join("\n"), (path) => tree.has(path)))
      .toEqual(["scripts/a.py", "scripts/b/__init__.py", "scripts/b/c.py", "tools/d.py"]);
  });

  it("names every script that goes a workflow which stays runs, on its edit", () => {
    const { gone, entries } = cutoverLayer();
    const flows = [...workflowNames().map((name) => `.github/workflows/${name}`),
                   ...actionFiles()].filter((path) => !isGone(gone, path));
    expect(flows.length).toBeGreaterThan(20);
    expect(flows.flatMap((path) => stepLoads(REPO_TREE.text(path)!, gone)
      .filter((to) => !(entries.get(path) ?? []).includes(to))
      .map((to) => `${path} runs ${to}: name it on the file's edit`))).toEqual([]);
    // By path or as a module, in a job's step or a composite action's; never in a comment.
    const gone2 = ["scripts/a.py", "scripts/b.py", "scripts/c.py", "scripts/d/e.py"];
    expect(stepLoads(["# python scripts/c.py", "jobs:", "  x:", "    steps:",
                      "      - run: python scripts/a.py --x",
                      "      - run: python -m scripts.d.e"].join("\n"), gone2))
      .toEqual(["scripts/a.py", "scripts/d/e.py"]);
    expect(stepLoads("runs:\n  steps:\n    - run: python scripts/b.py\n", gone2))
      .toEqual(["scripts/b.py"]);
  });

  it("makes the tree a dry run applies: carry-outs, the deletes, what an edit loads kept, and " +
     "the tests it edits set aside", () => {
    const what = "a probe";
    const manifest = manifestOf(
      { delete: "platform/old", what }, { delete: "tests", what },
      { move: "tests/test_kept.py", to: "scripts/tests/test_kept.py", what },
      { move: "platform/kept.ts", to: "platform/moved.ts", what },
      { edit: "platform/stays.ts", names: ["x"], imports: ["platform/old/a.ts"], what },
      { edit: "platform/stays.test.ts", names: ["x"], what });
    const loads: Record<string, string[]> = { "platform/old/a.ts": ["platform/old/b.ts"],
                                              "platform/old/b.ts": ["platform/kept.ts"] };
    const tracked = ["platform/old/a.ts", "platform/old/b.ts", "platform/old/c.ts",
                     "tests/test_kept.py", "tests/test_gone.py", "platform/kept.ts",
                     "platform/stays.ts", "platform/stays.test.ts"];
    expect(dryRun(tracked, (path) => loads[path] ?? [], manifest)).toEqual({
      carried: [["tests/test_kept.py", "scripts/tests/test_kept.py"]],
      kept: ["platform/old/a.ts", "platform/old/b.ts"],
      removed: ["platform/old/c.ts", "tests/test_gone.py"],
      setAside: ["platform/stays.test.ts"],
    });
    // It runs platform-tests' legs, less those of a workspace that goes.
    expect(dryRunLegs([{ leg: "old", command: "npm run test -w old -- --shard=1/1" },
                       { leg: "kept", command: "npm run test:workerd -w kept" }], manifest))
      .toEqual([{ leg: "kept", command: "npm run test:workerd -w kept" }]);
    expect(() => dryRunLegs([{ leg: "x", command: "npm test" }], manifest))
      .toThrow("platform-tests' leg npm test names no workspace");
  });

  it("holds the cutover layer: what goes imports itself, and what stays only named imports", () => {
    const layer: CutoverLayer = {
      gone: ["platform/import", "platform/doors/src/import.ts",
             "platform/doors/test/import.test.ts"],
      entries: new Map([["platform/doors/src/index.ts", ["platform/doors/src/import.ts"]]]),
    };
    // What goes reaches across the layers, where nothing else may; what stays reaches what goes
    // only by a named import its own layer allows.
    const files = new Map([
      ["platform/doors/test/import.test.ts", 'import { x } from "../../import/bundle";'],
      ["platform/doors/src/import.ts", 'import { y } from "../../import/bundle";'],
      ["platform/import/bundle.ts", 'import { z } from "../doors/src/import";'],
      ["platform/doors/src/index.ts", 'import { importQueue } from "./import";'],
      ["platform/doors/src/ring.ts", 'import { importQueue } from "./import";'],
      ["platform/owner/src/probe.ts", 'import { x } from "../../import/bundle";'],
    ]);
    const { violations } = layerProblems({ files, workers: workerFolders(), cutover: layer });
    expect(violations.map((each) => each.what)).toEqual([
      "platform/doors/src/ring.ts imports platform/doors/src/import.ts, which the cutover " +
        "deletes: name the import on the file's edit in platform/cutover.json",
      "platform/owner/src/probe.ts imports platform/import/bundle.ts",
    ]);
    expect(cutoverImports(files, layer)).toEqual([
      "platform/doors/src/index.ts imports platform/doors/src/import.ts",
      "platform/doors/src/ring.ts imports platform/doors/src/import.ts",
    ]);
  });
});
