/**
 * The cutover's manifest (untangle B24, `platform/cutover.json`, read by `ci/kit/cutover.ts`)
 * held to the tree, so the cutover's change is the manifest applied and nothing else:
 * - it is in its shape: three phases, each entry a delete, an edit, a move or a live object;
 * - every entry exists: each path, each name an edit or a move takes out of its file, each
 *   import an edit names, and each live object's name in the file that names it today; and no
 *   path is listed inside one the manifest deletes;
 * - every file under `platform/` and `.github/` that says what is "deleted at the cutover", or
 *   speaks the cutover's vocabulary (`/workspace/imported`, "old TinyAssets", the `import` and
 *   `discard` callers, `IMPORT_*`), is on it;
 * - the layer table's cutover layer (`ci/kit/imports.ts`): what it deletes may import itself,
 *   and nothing that stays imports it but an import an edit names (held over the tree by
 *   `isolation.test.ts`, its cases here).
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { type CutoverLayer, cutoverLayer, type Entry, isGone, LIVE, type Manifest, MANIFEST,
         type Phase, PHASES } from "../kit/cutover";
import { keyed, object } from "../../contract/src/checks";
import { layerProblems, repoScripts } from "../kit/imports";
import { treeFiles } from "../kit/sources";
import { REPO } from "../paths";
import { workerFolders } from "../wrangler";

/** A text that says what is deleted at the cutover, however its comment wraps the words. */
const MARKER = /deleted(?:[\s*#/]|<!--)+at(?:[\s*#/])+the(?:[\s*#/])+cutover/i;
/** The cutover's vocabulary: no file that stays may speak it. */
const VOCABULARY: readonly RegExp[] = Object.freeze([
  /\/workspace\/imported/, /old TinyAssets/i, /["'](?:import|discard)["']/, /\bIMPORT_[A-Z0-9_]+/]);
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
        problems.push(`phase ${phase}'s entry ${n} is no delete, edit, move or live object`);
      }
    });
  }
  return problems;
}

const entries = (manifest: Manifest) => PHASES.flatMap((phase) =>
  manifest.phases[phase].entries.map((entry) => ({ phase, entry })));

/** The paths an entry lists: what it deletes, edits or moves. */
const pathOf = (entry: Entry): string | null =>
  "delete" in entry ? entry.delete : "edit" in entry ? entry.edit
    : "move" in entry ? entry.move : null;

/** How an entry is not what the tree holds today. */
function entryProblems(manifest: Manifest, tree: Tree): string[] {
  const problems: string[] = [];
  const plan = entries(manifest);
  const { gone } = cutoverLayer(manifest);
  const edits = new Map<string, { phase: Phase; entry: Extract<Entry, { edit: string }> }[]>();
  const holds = (path: string, names: readonly string[], what: string) => {
    const found = tree.text(path);
    for (const name of names) {
      if (found === null || !found.includes(name)) {
        problems.push(`${what} names ${name}, which ${path} does not hold`);
      }
    }
  };
  for (const { phase, entry } of plan) {
    const path = pathOf(entry);
    if (path !== null && !tree.exists(path)) {
      problems.push(`${phase}: ${path} is not there`);
      continue;
    }
    if (path !== null && gone.some((each) => each !== path && isGone([each], path))) {
      problems.push(`${phase}: ${path} lies inside a path the manifest deletes`);
    }
    if ("edit" in entry) {
      const earlier = edits.get(entry.edit) ?? [];
      for (const previous of earlier) {
        if (previous.phase === phase) {
          problems.push(`${phase}: ${entry.edit} is edited twice`);
        } else {
          const repeated = entry.names.filter((name) => previous.entry.names.includes(name));
          const imports = (entry.imports ?? []).filter((to) => previous.entry.imports?.includes(to));
          for (const item of [...repeated, ...imports]) {
            problems.push(`${phase}: ${entry.edit} already removes ${item} in ${previous.phase}`);
          }
        }
      }
      earlier.push({ phase, entry });
      edits.set(entry.edit, earlier);
      holds(entry.edit, entry.names, `the edit of ${entry.edit}`);
      for (const to of entry.imports ?? []) {
        const retirement = plan.find(({ entry: item }) => "delete" in item && isGone([item.delete], to));
        if (!retirement) {
          problems.push(`the edit of ${entry.edit} names an import of ${to}, which stays`);
        } else if (PHASES.indexOf(phase) > PHASES.indexOf(retirement.phase)) {
          problems.push(`${phase}: ${entry.edit} removes an import of ${to} after ${retirement.phase} deletes it`);
        }
      }
    } else if ("move" in entry) {
      holds(entry.move, entry.names ?? [], `the move of ${entry.move}`);
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

/** Whether `path` is on the manifest: deleted, or inside what is, or edited or moved. */
function listed(manifest: Manifest, path: string): boolean {
  return entries(manifest).some(({ entry }) => {
    const at = pathOf(entry);
    return at !== null && ("delete" in entry ? isGone([at], path) : at === path);
  });
}

/** Each file of `files` that marks what the cutover deletes, or speaks its vocabulary, unlisted. */
function scopeProblems(manifest: Manifest, files: ReadonlyMap<string, string>): string[] {
  return [...files].flatMap(([path, content]) => {
    if (listed(manifest, path)) {
      return [];
    }
    const said = [MARKER.test(content) ? "deleted at the cutover" : null,
                  ...VOCABULARY.map((word) => word.exec(content)?.[0] ?? null)]
      .filter((each) => each !== null);
    return said.length === 0 ? [] : [`${path} says ${said.join(", ")}, and is not on the manifest`];
  });
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
        .toEqual([why, ["phase cutover's entry 0 is no delete, edit, move or live object"]]);
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
      { live: "queue", name: "tinyassets-probe", in: "platform/b.ts", what }), tree)).toEqual([
      "cutover: platform/gone.ts is not there",
      "the edit of platform/b.ts names GAMMA, which platform/b.ts does not hold",
      "the edit of platform/b.ts names an import of platform/c.ts, which stays",
      "cutover: platform/b.ts is edited twice",
      "cutover: platform/a/x.ts lies inside a path the manifest deletes",
      "cutover: platform/b.ts moves to platform/c.ts, which is there already",
      "cutover: platform/b.ts moves its names into platform/d.ts, not there",
      "the queue tinyassets-probe names tinyassets-probe, which platform/b.ts does not hold",
    ]);
  });

  it("keeps each phase's edits and imports of a file that survives them both", () => {
    const kept = "platform/kept.ts", first = "platform/window.ts", last = "platform/rollback.ts";
    const tree = treeOf({ [kept]: "window rollback", [first]: "", [last]: "" });
    const cutover = manifestOf({ edit: kept, names: ["window"], imports: [first], what: "window" },
                               { delete: first, what: "window" });
    const hold = { why: "the hold ends", entries: [
      { edit: kept, names: ["rollback"], imports: [last], what: "rollback" },
      { delete: last, what: "rollback" },
    ] };
    const both: Manifest = { ...cutover, phases: { ...cutover.phases, hold } };
    expect(entryProblems(both, tree)).toEqual([]);
    expect(cutoverLayer(both).entries.get(kept)).toEqual([first, last]);
    const duplicate = { ...both, phases: { ...both.phases, hold: { ...hold, entries: [
      { edit: kept, names: ["window"], imports: [first], what: "twice" }, ...hold.entries.slice(1),
    ] } } };
    expect(entryProblems(duplicate, tree)).toEqual([
      "hold: platform/kept.ts already removes window in cutover",
      "hold: platform/kept.ts already removes platform/window.ts in cutover",
      "hold: platform/kept.ts removes an import of platform/window.ts after cutover deletes it",
    ]);

    const remaining = { ...both, phases: { ...both.phases, cutover: { why: "done", entries: [] } } };
    expect(entryProblems(remaining, treeOf({ [kept]: "rollback", [last]: "" }))).toEqual([]);
    expect(cutoverLayer(remaining).entries.get(kept)).toEqual([last]);
  });

  it("removes a surviving import no later than its target disappears", () => {
    const kept = "platform/kept.ts", file = "platform/lib/read.ts", other = "platform/lib-old/read.ts";
    const tree = treeOf({ [kept]: "read other", [file]: "", [other]: "" });
    const cutover = manifestOf({ delete: "platform/lib", what: "retire the directory" });
    const hold = { why: "the hold ends", entries: [
      { delete: other, what: "retire the other file" },
      { edit: kept, names: ["read", "other"], imports: [file, other], what: "remove both imports" },
    ] };
    expect(entryProblems({ ...cutover, phases: { ...cutover.phases, hold } }, tree)).toEqual([
      `hold: ${kept} removes an import of ${file} after cutover deletes it`,
    ]);
  });

  it("lists every file under platform/ and .github/ that marks a deletion or speaks the cutover",
     () => {
    expect(scopeProblems(MANIFEST, treeFiles())).toEqual([]);
    const what = "a probe";
    expect(scopeProblems(manifestOf({ delete: "platform/import", what },
                                    { edit: "platform/kept.ts", names: ["IMPORT_X"], what }),
                         new Map([
      ["platform/import/x.ts", "export const IMPORT_X = 1;"],
      ["platform/kept.ts", "export const IMPORT_X = 1;"],
      ["platform/a.ts", "/**\n * Deleted at the\n * cutover with the job.\n */"],
      ["platform/b.md", "Your files from the old TinyAssets are in /workspace/imported."],
      ["platform/c.ts", 'const caller = "discard";'],
      ["platform/d.ts", "export const IMPORTANT_IMPORTS = 1; // imports a module"],
    ]))).toEqual([
      "platform/a.ts says deleted at the cutover, and is not on the manifest",
      "platform/b.md says /workspace/imported, old TinyAssets, and is not on the manifest",
      'platform/c.ts says "discard", and is not on the manifest',
    ]);
  });

  it("names an import from a file that stays only where that file makes it today", () => {
    const named = [...cutoverLayer().entries].flatMap(([from, to]) =>
      to.map((each) => `${from} imports ${each}`));
    const made = new Set(cutoverImports(repoScripts(), cutoverLayer()));
    expect(named.filter((each) => !made.has(each))).toEqual([]);
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
