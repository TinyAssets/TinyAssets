/**
 * The sync-ignore matcher (`contract/src/sync-ignore.ts`) as git reads a `.gitignore`, `git
 * check-ignore` the oracle: GitHub's 310 templates (github/gitignore at `df4087095eb4`, CC0,
 * in `test-data/gitignore/`) and random lists, every path's answer git's; the hostile lists
 * of the review's rounds 1 to 6, answered as git does with the lines that fit, at a cost a
 * path within the automaton's own (in this host's own units, `overBound`); what it remembers
 * of a tree the same at any size, its answers the same in any order, and a walk's path its
 * own segment's cost. Its memory a list byte, against the sandbox's heap, is the runner's
 * (`sandbox/runner/sync.test.ts`).
 */
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync,
         writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { setFlagsFromString } from "node:v8";
import { runInNewContext } from "node:vm";

import { afterAll, describe, expect, it } from "vitest";

import {
  AUTOMATON_STATES, automatonStates, defaultSyncIgnore, ignoredBy, LIST_BYTES,
  REMEMBERED_NAME_BYTES, unappliedLines,
} from "../src/sync-ignore";

const HERE = dirname(fileURLToPath(import.meta.url));
const TEMPLATES = join(HERE, "test-data", "gitignore");
const scratch = mkdtempSync(join(tmpdir(), "gitignore-"));
afterAll(() => rmSync(scratch, { recursive: true, force: true }));
/** git with no user or system configuration and no global excludes: the file alone. */
const ENV = { ...process.env, HOME: scratch, XDG_CONFIG_HOME: scratch, GIT_CONFIG_NOSYSTEM: "1" };

function templates(dir = TEMPLATES): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? templates(path)
      : name.endsWith(".gitignore") ? [path] : [];
  }).sort();
}

/**
 * What git ignores of `paths` (each a file, or a folder when another is under it) with
 * `lines` as the repository's `.gitignore`: the files made on disk, each path and folder
 * asked.
 */
function gitIgnores(lines: readonly string[], paths: readonly string[]):
    { all: [string, boolean][]; ignored: Set<string> } {
  const repo = mkdtempSync(join(scratch, "r-"));
  execFileSync("git", ["init", "-q", repo], { env: ENV });
  writeFileSync(join(repo, ".gitignore"), lines.join("\n"));
  const folders = new Set<string>();
  for (const path of paths) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) {
      folders.add(parts.slice(0, i).join("/"));
    }
  }
  for (const path of paths.filter((each) => !folders.has(each))) {
    try {
      mkdirSync(join(repo, path, ".."), { recursive: true });
      writeFileSync(join(repo, path), "x");
    } catch {
      // a name the filesystem refuses
    }
  }
  const all: [string, boolean][] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(join(repo, dir))) {
      const rel = dir === "" ? name : `${dir}/${name}`;
      if (rel === ".git" || rel === ".gitignore") {
        continue;
      }
      const isDir = statSync(join(repo, rel)).isDirectory();
      all.push([rel, isDir]);
      if (isDir) {
        walk(rel);
      }
    }
  };
  walk("");
  const ignored = checkIgnore(repo, all.map(([path]) => path), []);
  rmSync(repo, { recursive: true, force: true });
  return { all, ignored };
}

/**
 * What `git check-ignore` says of `paths` in `repo`, whole or not at all: a cut output (past
 * `maxBuffer`) or a failed run would read as "not ignored", so either throws.
 */
function checkIgnore(repo: string, paths: readonly string[], flags: readonly string[]):
    Set<string> {
  const git = spawnSync("git", ["-C", repo, "-c", "core.excludesFile=/dev/null", "check-ignore",
                                ...flags, "--stdin", "-z"],
                        { input: paths.join("\0"), env: ENV, encoding: "utf8",
                          maxBuffer: 1 << 30 });
  // 0: some ignored; 1: none.
  if (git.error !== undefined || git.status === null || git.status > 1) {
    throw new Error(`git check-ignore failed: ${git.error ?? git.stderr}`);
  }
  return new Set(git.stdout.split("\0").filter(Boolean));
}

/** Paths a template's lines name, made concrete, at the top and below a folder. */
function samples(lines: readonly string[]): string[] {
  const out = new Set(["README.md", "src/main.c", "a/b/c.txt", "build/out.o", "Bin/x.dll",
                       ".vscode/settings.json", ".vscode/x.json", "docs/sub/a.md", "logs",
                       "#notes#", ".idea/workspace.xml", ".idea/a/workspace.xml", "x/foo",
                       "node_modules/x/index.js", "deep/x/y/z/a.log"]);
  for (const raw of lines) {
    let line = raw.replace(/\r$/, "").trim();
    if (line === "" || line.startsWith("#")) {
      continue;
    }
    line = line.replace(/^!/, "");
    const folder = line.endsWith("/");
    line = line.replace(/\/+$/, "").replace(/^\//, "");
    for (const star of ["", "foo"]) {
      const made = line.replace(/\\(.)/g, "$1").replace(/\*\*\//g, star === "" ? "" : "x/y/")
        .replace(/\/\*\*/g, "/x/z").replace(/\*+/g, star).replace(/\?/g, "q")
        .replace(/\[([!^]?)([^\]]?)[^\]]*\]/g, (_, not: string, first: string) =>
          not !== "" ? "Z" : first || "z")
        .replace(/\/+/g, "/").replace(/^\//, "").replace(/\/$/, "");
      if (made === "" || made.split("/").some((part) => part === "" || part === "."
          || part === ".." || part.length > 200) || made.split("/")[0] === ".git"
          || made.startsWith(".ta")) {
        continue;
      }
      for (const path of [made, `sub/${made}`, `${made}/inner.txt`,
                          folder ? `sub/${made}/inner.txt` : `${made}.bak`]) {
        out.add(path);
      }
    }
  }
  return [...out];
}

function differences(lines: readonly string[], paths: readonly string[]): string[] {
  const { all, ignored } = gitIgnores(lines, paths);
  const ours = ignoredBy(lines);
  return all.filter(([path, isDir]) => ignored.has(path) !== (ours(path, isDir) !== null))
    .map(([path, isDir]) => `${path}${isDir ? "/" : ""}: git ${ignored.has(path)}`);
}

describe("the sync-ignore, as git reads a .gitignore", () => {
  it("answers every path as git does, over all 310 of GitHub's templates", { tags: ["large"] }, () => {
    const files = templates();
    expect(files).toHaveLength(310);
    let checked = 0;
    const found: string[] = [];
    for (const file of files) {
      const lines = readFileSync(file, "utf8").split("\n");
      const paths = samples(lines);
      checked += paths.length;
      found.push(...differences(lines, paths).map((each) => `${file.slice(TEMPLATES.length)} ${each}`));
    }
    expect(checked).toBeGreaterThan(20_000);
    expect(found).toEqual([]);
  });

  it("answers random lists as git does: negation, **, classes, escapes, folders only", { tags: ["large"] }, () => {
    let seed = 20261103;
    const random = () => ((seed = (Math.imul(seed, 1103515245) + 12345) >>> 0) / 2 ** 32);
    const pick = <T>(xs: readonly T[]) => xs[Math.floor(random() * xs.length)]!;
    const pieces = ["a", "b", "ab", "ä", "*", "?", "**", "[ab]", "[!a]", "[a-c]",
                    "[[:digit:]]", "\\*", "\\#", "1", "x", ".", "-"];
    const pattern = () => {
      let made = Array.from({ length: 1 + Math.floor(random() * 3) }, () =>
        Array.from({ length: 1 + Math.floor(random() * 3) }, () => pick(pieces)).join(""))
        .join("/");
      made = `${random() < 0.2 ? "**/" : ""}${random() < 0.3 ? "/" : ""}${made}${
        random() < 0.15 ? "/**" : ""}${random() < 0.2 ? "/" : ""}`;
      return `${random() < 0.25 ? "!" : ""}${made}${random() < 0.05 ? "  " : ""}`;
    };
    const names = ["a", "b", "ab", "ä", "x1", "a.b", "ba", "*", "#", "ab1", "c", "-"];
    const found: string[] = [];
    for (let round = 0; round < 120; round++) {
      const lines = Array.from({ length: 1 + Math.floor(random() * 8) }, pattern);
      const paths = Array.from({ length: 40 }, () =>
        Array.from({ length: 1 + Math.floor(random() * 4) }, () => pick(names)).join("/"));
      found.push(...differences(lines, paths).map((each) => `${JSON.stringify(lines)} ${each}`));
    }
    expect(found).toEqual([]);
  });

  it("never ignores itself, nor the folders on its way", () => {
    const matcher = ignoredBy(["*", ".ta/", ".ta/sync-ignore"]);
    expect([matcher(".ta", true), matcher(".ta/sync-ignore"), matcher(".ta/other")])
      .toEqual([null, null, "custom"]);
  });
});

describe("its ceilings, each line past one said in file order", () => {
  it("applies each of GitHub's templates whole, on the defaults; all 310 together say " +
     "the automaton lines past its states", () => {
    const files = templates();
    for (const file of files) {
      expect(unappliedLines([...defaultSyncIgnore(), ...readFileSync(file, "utf8").split("\n")]),
             file).toEqual([]);
    }
    // Together they need more than `AUTOMATON_STATES`: the later automaton lines, in file
    // order, each said.
    const all = [...defaultSyncIgnore(), ...files.flatMap((file) =>
      readFileSync(file, "utf8").split("\n"))];
    const past = unappliedLines(all);
    expect(past.length).toBeGreaterThan(0);
    expect(past.every((each) => each.reason === "states")).toBe(true);
    const states = new Set<string>();
    let used = 0;
    for (const line of all) {
      if (!states.has(line.trim()) && automatonStates(line) > 0
          && !past.some((each) => each.line === line.trim())) {
        states.add(line.trim());
        used += automatonStates(line);
      }
    }
    expect(used).toBeLessThanOrEqual(AUTOMATON_STATES);
  });

  it("stops the automaton at its states, every later automaton line said, index lines kept", () => {
    const line = (i: number) => `*${"a".repeat(150)}b${i}c*`;  // about 156 states each
    const lines = [...Array.from({ length: 200 }, (_, i) => line(i)), "*.log", "node_modules"];
    const past = unappliedLines(lines);
    const applied = 200 - past.length;
    const used = Array.from({ length: applied }, (_, i) => automatonStates(line(i)))
      .reduce((sum, each) => sum + each, 0);
    expect(used).toBeLessThanOrEqual(AUTOMATON_STATES);
    expect(used + automatonStates(line(applied))).toBeGreaterThan(AUTOMATON_STATES);
    expect(past.map((each) => each.line)).toEqual(
      Array.from({ length: past.length }, (_, i) => line(applied + i)));
    const matcher = ignoredBy(lines);
    expect(matcher("x.log")).toBe("custom");
    expect(matcher(`${"a".repeat(150)}b${applied - 1}c`)).toBe("custom");
    expect(matcher(`${"a".repeat(150)}b${applied}c`)).toBeNull();
  });

  it("applies the list to `LIST_BYTES`, every line past it said", () => {
    const line = "*.ext-0123456789";
    const lines = Array.from({ length: Math.ceil(LIST_BYTES / (line.length + 1)) + 3 },
                             (_, i) => `${line}${i}`);
    const past = unappliedLines(lines);
    expect(past.length).toBeGreaterThan(0);
    expect(past.every((each) => each.reason === "bytes")).toBe(true);
    let bytes = 0;
    const firstPast = lines.findIndex((each) => {
      bytes += each.length + 1;
      return bytes - 1 > LIST_BYTES;
    });
    expect(past[0]!.line).toBe(lines[firstPast]);
  });

  it("counts a lone high surrogate as the 3 bytes it is matched as, the unit after it its own",
     () => {
    // 7 bytes and its newline, past `LIST_BYTES` by 2; read as 4 bytes for the surrogate and
    // the unit after it, the line would fit.
    const last = "a\ud800一";
    const filler = `#${"x".repeat(LIST_BYTES - 8)}`;
    expect(unappliedLines([filler, last])).toEqual([{ line: last, reason: "bytes" }]);
  });
});

// The hostile lists of the review's rounds 1 to 6 (`rvst2_report.md`), each on the defaults,
// and the path each is matched against (unique each time).
const letters = "abcdefghijklmnopqrst";
const R = (n: number, f: (i: number) => string) => Array.from({ length: n }, (_, i) => f(i));
const HOSTILE: [string, string[], (i: number) => string][] = [
  ["R1 *a*a*...*b, a 4,000-byte name", ["*a*a*a*a*a*a*a*a*a*a*b"],
   (i) => `${"a".repeat(3990)}${String(i).padStart(10, "0")}`],
  ["R2 2,000 literal lines", R(2000, (i) => `n${i}`), (i) => `p/q/r/s/t/file${i}.ts`],
  ["R3 2,000 star lines", R(2000, (i) => `*${i}*x*y*z`), (i) => `p/q/r/s/t/file${i}.ts`],
  ["R3 2,000 ? lines", R(2000, (i) => `?*${i}*q`), (i) => `p/q/r/s/t/file${i}.ts`],
  ["R3 2,000 ? path lines", R(2000, (i) => `?*/*${i}*q`), (i) => `p/q/r/s/t/file${i}.ts`],
  ["R4 passcheck", R(2000, (i) => `*s*t*.*${i}*r*e*b*m*u*n*-*e*l*i*f*-*t*n*e*n*o*p*m*o*c*?`),
   (i) => `p/component-file-number-${i}-${(i * 7919) % 100003}.ts`],
  ["R4 passcheck path", R(2000, (i) => `*e*l*u*d*o*m*/*c*r*s*/*${i}*t*c*e*j*o*r*p*?`),
   (i) => `project/src/module${i}/x.ts`],
  ["R4 ? runs of 100 to 199", R(2000, (i) => `*${"?".repeat(100 + (i % 100))}${
    letters[Math.floor(i / 100)]}*`), (i) => `p/${letters}${String(i).padStart(8, "0")}${
    "x".repeat(222)}`],
  ["R5 long literal middles, emoji names", R(2000, (i) => `*${"a".repeat(150)}b${i}*`),
   (i) => `p/😀b0123456789${"a".repeat(225)}${String(i).padStart(10, "0")}`],
  ["R6 2,000 long literal middles", R(2000, (i) => `*${"a".repeat(150)}b${i}*`),
   (i) => `p/b0123456789${"a".repeat(230)}${String(i).padStart(10, "0")}`],
  ["R6 20,000 long literal middles", R(20000, (i) => `*${"a".repeat(150)}b${i}*`),
   (i) => `p/b0123456789${"a".repeat(230)}${String(i).padStart(10, "0")}`],
  ["R6 2,000 x 100 one-letter pieces", R(2000, (i) => `*${"a*".repeat(100)}b${i}*`),
   (i) => `p/b0123456789${"a".repeat(230)}${String(i).padStart(10, "0")}`],
  ["R6 2,000 short *.a<i>b", R(2000, (i) => `*.a${i}b`),
   (i) => `p/b0123456789${"a".repeat(230)}${String(i).padStart(10, "0")}.x`],
];

/**
 * Round 7 (R7-2): Aho–Corasick lines of differing depth, files under a folder 2,000 deep, each
 * file's segment fed from the folder's remembered node, its fallback the folder's depth. git
 * itself takes minutes over this list (its `wildmatch` tries each `**` at every folder), so
 * its answers are by construction: a path is ignored when it ends `/` + `a/` (1 to 1,000
 * times) + `b`.
 */
const DEEP: [string, string[], (i: number) => string] = [
  "R7 **/a/…/a/b, files 2,000 folders deep", R(1000, (i) => `**/${"a/".repeat(1 + i)}b`),
  (i) => i % 3 === 0 ? `${"a/".repeat(2000)}file${i}` : i % 3 === 1
    ? `${"a/".repeat(1500)}b/x${i}` : `${"a/".repeat(1500 + i % 7)}b`];
const deepIgnored = (path: string) => /(^|\/)(a\/){1,1000}b$/.test(path)
  || /(^|\/)(a\/){1,1000}b\//.test(path);

/** UTF-8 bytes of a string. */
const bytesOf = (text: string) => Buffer.byteLength(text, "utf8");

describe("the review's hostile lists (rounds 1 to 6)", () => {
  it.each([...HOSTILE, DEEP])("%s: git's answers with the lines that fit, at the automaton's cost",
                   { tags: ["large"] }, (label, list, path) => {
    const lines = [...defaultSyncIgnore(), ...list];
    const past = new Set(unappliedLines(lines).map((each) => each.line));
    const applied = lines.filter((each) => !past.has(each.trim()));
    // Answered as git answers the lines that fit (non-existent paths: names past NAME_MAX).
    const asked = Array.from({ length: 60 }, (_, i) => path(i));
    let ignored: Set<string>;
    if (label === DEEP[0]) {
      ignored = new Set(asked.filter(deepIgnored));
      expect(ignored.size).toBeGreaterThan(0);
    } else {
      const repo = mkdtempSync(join(scratch, "h-"));
      execFileSync("git", ["init", "-q", repo], { env: ENV });
      writeFileSync(join(repo, ".gitignore"), applied.join("\n"));
      ignored = checkIgnore(repo, asked, ["--no-index"]);
      rmSync(repo, { recursive: true, force: true });
    }
    const matcher = ignoredBy(lines);
    expect(asked.filter((each) => ignored.has(each) !== (matcher(each) !== null))).toEqual([]);
    // Its cost a path: within the automaton's bound (`pathWork`), in this host's units.
    let states = 0;
    const seen = new Set<string>();
    for (const line of applied) {
      if (!seen.has(line.trim())) {
        seen.add(line.trim());
        states += automatonStates(line);
      }
    }
    expect(states).toBeLessThanOrEqual(AUTOMATON_STATES);
    const words = Math.max(1, Math.ceil(states / 32));
    // Warmed first (the JIT's), then rounds of fresh paths.
    for (let i = 0; i < 300; i++) {
      matcher(path(5000 + i));
    }
    expect(overBound((round) => {
      const fresh = Array.from({ length: 300 }, (_, i) => path(10_000 * (round + 1) + i));
      return [fresh.map((each) => pathWork(each, words)), () => {
        for (const each of fresh) {
          matcher(each);
        }
      }];
    }), "CPU time over the automaton's bound").toBeLessThanOrEqual(1);
  });

  it("R7 in the sync's own orders: each folder stepped once, a path its own segment", () => {
    const [, list, path] = DEEP;
    const lines = [...defaultSyncIgnore(), ...list];
    const words = 1;  // R7's lines are Aho–Corasick's, the defaults indexes': no automaton
    expect(lines.reduce((sum, line) => sum + automatonStates(line), 0)).toBe(0);
    const files = Array.from({ length: 300 }, (_, i) => path(10_000 + i)).sort();
    // The walk's order (`walkFiles`): each folder asked once, before what is under it.
    const walk: [string, boolean][] = [];
    const asked = new Set<string>();
    for (const file of files) {
      for (let cut = file.indexOf("/"); cut >= 0; cut = file.indexOf("/", cut + 1)) {
        if (!asked.has(file.slice(0, cut))) {
          asked.add(file.slice(0, cut));
          walk.push([file.slice(0, cut), true]);
        }
      }
      walk.push([file, false]);
    }
    // The walk's bound an entry (R7's, above), summed: a pass asks each entry once, the
    // folders among them, and the removal pass, asking only the files, steps each of their
    // folders once (the paths under a folder adjacent), so it is held to the same sum.
    const bound = walk.map(([each]) => pathWork(each, words));
    const over = (entries: readonly [string, boolean][]) => overBound(() => {
      const matcher = ignoredBy(lines);
      return [bound, () => {
        for (const [each, isDir] of entries) {
          matcher(each, isDir);
        }
      }];
    });
    expect(over(walk), "the walk's CPU time over its bound").toBeLessThanOrEqual(1);
    expect(over(files.map((file) => [file, false])), "the removal pass's, over the walk's")
      .toBeLessThanOrEqual(1);
  });
});

/** A path's share of the automaton's bound: its word steps and its index bytes. */
interface Work { readonly steps: number; readonly bytes: number }

/**
 * A path's work in the automaton's bound (R7's, the review's): its name, then its `/` and
 * segment, each byte `words` word steps and an index byte; and Aho–Corasick's fallback from
 * its folder's remembered node (R7-2), at most its folder's bytes, an index byte each.
 * `folder` false: a path whose folder the walk has already stepped, its own segment alone.
 */
function pathWork(path: string, words: number, folder = true): Work {
  const name = bytesOf(path.split("/").at(-1)!);
  const above = folder ? bytesOf(path.slice(0, Math.max(0, path.lastIndexOf("/")))) : 0;
  return { steps: (2 * name + 1) * words, bytes: 2 * name + 1 + above };
}

/**
 * An index byte of the bound, in the reference's index bytes (`hostUnits`): what the matcher
 * does a path beside its bare lookups (a segment's bytes made, its states copied from its
 * folder's, its remembered names and literal lines looked up, its folders compared). Measured
 * at most 1.8 (R7's deep folders; R2's literal lines 1.5: no automaton, so all index), over 45
 * runs idle and under load; a path that scans the list's literal lines is 3 times past 5. A
 * word step needs no such allowance: the reference's is the automaton's slowest branch itself.
 */
const INDEX_WORK = 5;

/**
 * How far a cost is within the automaton's bound, whatever CPU the runner has: over five
 * rounds, each its work (`timed(round)`: the paths' `Work`, and the work, made untimed) timed
 * beside this host's units (`hostUnits`), the least of its CPU time over the bound's at the
 * least units seen. At most 1 within the bound.
 */
function overBound(timed: (round: number) => [readonly Work[], () => void]): number {
  const rounds: { took: number; steps: number; bytes: number }[] = [];
  let step = Number.POSITIVE_INFINITY;
  let index = Number.POSITIVE_INFINITY;
  for (let round = 0; round < 5; round++) {
    const [works, work] = timed(round);
    let steps = 0;
    let bytes = 0;
    for (const each of works) {
      steps += each.steps;
      bytes += each.bytes;
    }
    const units = hostUnits();
    step = Math.min(step, units.step);
    index = Math.min(index, units.index);
    rounds.push({ took: cpuNanos(work), steps, bytes });
  }
  return Math.min(...rounds.map((each) =>
    each.took / (each.steps * step + each.bytes * index * INDEX_WORK)));
}

/** The process's CPU time `work` takes, in ns: another process's load does not stretch it. */
function cpuNanos(work: () => void): number {
  const began = process.cpuUsage();
  work();
  const spent = process.cpuUsage(began);
  return (spent.user + spent.system) * 1000;
}

let references: { readonly steps: () => number; readonly lookups: () => number } | undefined;

/**
 * This host's own word step and index byte, in ns, timed as the matcher is (CPU time, the
 * least of three), so the cost bounds are the automaton's in this host's units: the bound is
 * a complexity claim, and CI's runners differ in speed by more than any fixed allowance.
 * (`STEP_NS` is the sandbox's own figure, its template build's check of `stepNanos`.)
 * - A step: a byte of text over one 32-state word on the automaton's slowest branch (a `/`:
 *   its passed states read and written, and re-armed), at `AUTOMATON_STATES`' words.
 * - An index byte: a fresh name made and encoded, each byte one step into a trie of byte maps
 *   (`Trie`'s) from its start and one from its end (a name's `L*` and `*L`), bests read.
 */
function hostUnits(): { readonly step: number; readonly index: number } {
  references ??= makeReferences();
  const least = (run: () => number) => Math.min(run(), run(), run());
  return { step: least(references.steps), index: least(references.lookups) };
}

/** The reference loops of `hostUnits`, each warmed (the JIT's), each its ns a unit. */
function makeReferences(): NonNullable<typeof references> {
  let seed = 20261106;
  const random = () => (seed = (Math.imul(seed, 1103515245) + 12345) >>> 0);
  let sink = 0;
  // The step: Shift-And's `/` branch, word for word.
  const W = AUTOMATON_STATES / 32;
  const mask = Uint32Array.from({ length: 256 * W }, random);
  const loopAll = Uint32Array.from({ length: W }, random);
  const rearm = Uint32Array.from({ length: W }, () => random() & 0x01010101);
  const gadget = Uint32Array.from({ length: W }, () => random() & 0x00010001);
  const text = Buffer.from("a/b/c".repeat(51));
  const RUNS = 8;
  const steps = () => cpuNanos(() => {
    for (let run = 0; run < RUNS; run++) {
      const D = new Uint32Array(W).fill(0xFFFFFFFF);
      const I = new Uint32Array(W);
      for (let k = 0; k < text.length; k++) {
        const off = text[k]! * W;
        let carry = 0;
        let alive = 0;
        for (let w = 0; w < W; w++) {
          const d = D[w]!;
          const inside = I[w]! | (d & gadget[w]!);
          const v = (((d << 1) | carry) & mask[off + w]!) | (d & loopAll[w]!) | rearm[w]!
            | inside;
          carry = d >>> 31;
          D[w] = v;
          I[w] = inside;
          alive |= v | inside;
        }
        sink ^= alive;
      }
    }
  }) / (RUNS * text.length * W);
  // The index byte: two tries of byte maps, every byte of every name found.
  const next: (Map<number, number> | undefined)[] = new Array(256).fill(undefined);
  const best = new Int32Array(1 << 16).fill(-1);
  let nodes = 1;
  const encode = (name: string) => {
    const out = new Uint8Array(name.length * 3);
    for (let i = 0; i < name.length; i++) {
      out[i] = name.charCodeAt(i) & 0x7F;
    }
    return out.subarray(0, name.length);
  };
  const named = (i: number) => `component-file-${i % 200}.ts`;
  for (let i = 0; i < 200; i++) {
    const bytes = encode(named(i));
    for (const order of [bytes, bytes.toReversed()]) {
      let node = 0;
      for (const b of order) {
        let to = next[b]?.get(node) ?? -1;
        if (to < 0) {
          to = nodes++;
          (next[b] ??= new Map()).set(node, to);
          best[to] = to;
        }
        node = to;
      }
    }
  }
  const lookups = () => {
    let bytes = 0;
    return cpuNanos(() => {
      for (let i = 0; i < 4_000; i++) {
        const name = encode(named(i));
        bytes += name.length;
        let from = 0;
        let back = 0;
        let found = -1;
        for (let k = 0; k < name.length; k++) {
          from = next[name[k]!]?.get(from) ?? -1;
          back = next[name[name.length - 1 - k]!]?.get(back) ?? -1;
          found = Math.max(found, best[from]!, best[back]!);
        }
        sink ^= found;
      }
    }) / bytes;
  };
  steps();
  lookups();
  // The loops' results used, so neither is optimised away.
  expect(Number.isInteger(sink)).toBe(true);
  return { steps, lookups };
}

describe("what it remembers of a tree, and what that buys", () => {
  setFlagsFromString("--expose-gc");
  const gc = runInNewContext("gc") as () => void;
  // The live set: the heap and the byte arrays' backing stores, read right after full
  // collections (the owner memory leg's sampler, `owner/memory/leg.mjs`).
  const live = () => {
    gc();
    gc();
    const usage = process.memoryUsage();
    return usage.heapUsed + usage.arrayBuffers;
  };

  /**
   * A tree of `folders` folders, each entry as the sync's walk offers it (`walkFiles`: a
   * folder, then what is under it): `project/module-i/src`, each folder a name every folder
   * has and a short name of its own (a remembered name's worst for its entry's cost beside
   * few characters; the other worst, a long name under a long path, is below).
   */
  function* tree(folders: number): Generator<[string, boolean]> {
    for (let i = 0; i < folders / 2; i++) {
      const module = `project/module-${i}`;
      yield [module, true];
      yield [`${module}/index.ts`, false];
      yield [`${module}/${i.toString(36)}`, false];
      yield [`${module}/src`, true];
      yield [`${module}/src/index.ts`, false];
      yield [`${module}/src/${(i + folders).toString(36)}`, false];
    }
  }

  /**
   * The most the matcher holds over a walk of `folders` folders, sampled every 5,000 paths;
   * `keep` is the control: a matcher that keeps every folder it is asked.
   */
  const held = (folders: number, keep = false) => {
    const matcher = ignoredBy(defaultSyncIgnore());
    const kept: string[] = [];
    matcher("project", true);
    const base = live();
    let peak = 0;
    let asked = 0;
    for (const [path, isDir] of tree(folders)) {
      matcher(path, isDir);
      if (keep && isDir) {
        kept.push(path);
      }
      if (++asked % 5_000 === 0) {
        peak = Math.max(peak, live() - base);
      }
    }
    peak = Math.max(peak, live() - base);
    expect(kept).toHaveLength(keep ? folders : 0);
    return peak;
  };

  it("holds its names within `REMEMBERED_NAME_BYTES` and of its folders only the path's own, " +
     "at 10k, 100k and 200k folders", { tags: ["large"] }, () => {
    // The steps' own objects and the path's few folders: a quarter of the names' memory,
    // as the owner memory leg allows a step (ADR-037).
    const QUARTER = REMEMBERED_NAME_BYTES / 4;
    const bound = REMEMBERED_NAME_BYTES + QUARTER;
    // Each size the least of two runs (a run's peak is the matcher's plus whatever the
    // process made for a moment beside it).
    const least = (folders: number) => Math.min(held(folders), held(folders));
    const sizes = [10_000, 100_000, 200_000].map(least);
    // The control: a matcher that keeps every folder reads as growing, past the bound, or
    // the samples prove nothing.
    const kept = [held(10_000, true), held(100_000, true)];
    expect(kept[1]! - kept[0]!).toBeGreaterThan(QUARTER);
    expect(kept[1]!).toBeGreaterThan(bound);
    for (const size of sizes) {
      expect(size).toBeLessThanOrEqual(bound);
    }
    // O(1) in the tree: twenty times the folders hold at most a quarter more.
    expect(sizes[2]! - sizes[0]!).toBeLessThanOrEqual(QUARTER);
  });

  it("holds its names within `REMEMBERED_NAME_BYTES` under a path near its most", () => {
    // A name of 13 characters or more, cut from its path, is a slice that keeps the whole
    // path alive (V8's sliced string): a folder chain of about 3,900 characters, one of them
    // past Latin-1 (`€`, so each path is two bytes a character, about 8 KiB), and 5,000
    // distinct names of 20 characters under it, each path a fresh string as the walk makes.
    const matcher = ignoredBy(defaultSyncIgnore());
    const folders: string[] = [];
    for (let length = 0, k = 0; length < 3_900; k++) {
      folders.push(`${k === 0 ? "€" : ""}d${k}`.padEnd(40, "x"));
      length += 41;
      matcher(folders.join("/"), true);
    }
    const dir = folders.join("/");
    const base = live();
    let peak = 0;
    for (let i = 0; i < 5_000; i++) {
      matcher([dir, `f${i}`.padEnd(20, "q")].join("/"));
      if (i % 50 === 49) {
        peak = Math.max(peak, live() - base);
      }
    }
    expect(peak).toBeLessThanOrEqual(REMEMBERED_NAME_BYTES * 1.25);
  });

  // Large: git is its oracle over a tree made on disk (12,000 files, some 55,000 entries),
  // and making and removing that tree is nearly all of its time, at the disk's own speed.
  it("answers a large mixed tree as git does, in any order, as with nothing remembered",
     { tags: ["large"] }, () => {
    let seed = 20261104;
    const random = () => ((seed = (Math.imul(seed, 1103515245) + 12345) >>> 0) / 2 ** 32);
    const pick = <T>(xs: readonly T[]) => xs[Math.floor(random() * xs.length)]!;
    // Names the lists name, names that sort before `/` (`-`, `.`, a space) beside a folder of
    // the same head, names of more than one byte, `é` composed and decomposed (two names, by
    // byte, as git reads them), and names that differ by case.
    const names = ["src", "lib", "index.ts", "node_modules", "build", "dist", "gen", "docs",
                   "draft-1.md", "keep.log", "x.log", "a", "a-b", "a.b", "a b", "b", "Library",
                   "Temp", "__pycache__", "m.pyc", ".cache", "top-only", "ü", "😀", "k", "c",
                   "\u00e9", "e\u0301", "A", "Ab", "ab", "Src"];
    const paths = new Set<string>();
    while (paths.size < 12_000) {
      // Most a few folders deep, one in ten 8 to 40.
      const depth = random() < 0.1 ? 8 + Math.floor(random() * 33) : 1 + Math.floor(random() * 7);
      paths.add(Array.from({ length: depth }, (_, i) => i === depth - 1 && random() < 0.5
        ? `f${Math.floor(random() * 500)}.${pick(["ts", "log", "md"])}` : pick(names)).join("/"));
    }
    const lines = [...defaultSyncIgnore(),
                   ...["Node", "Python", "Unity"].flatMap((name) =>
                     readFileSync(join(TEMPLATES, `${name}.gitignore`), "utf8").split("\n")),
                   "**/gen/**", "!keep.log", "docs/*/draft-*", "/top-only", "a/**/b",
                   "*.lo[gk]", "!a/build/", "a?b/", "ü/k",
                   // Literal path lines, one a prefix of another (the segments' trie), one
                   // re-included, one folders only; a name by one form of `é`; a case.
                   "a/b", "a/b/c", "a/b/c/k", "!a/b", "lib/src/", "lib/src/a", "!lib/src",
                   "/src/lib", "docs/a/b/c", "\u00e9/k", "e\u0301", "Src/"];
    // Walk order (git's oracle walks the tree it made, a folder and then what is under it).
    const { all, ignored } = gitIgnores(lines, [...paths]);
    expect(all.length).toBeGreaterThan(12_000);
    const walked = ignoredBy(lines);
    const inWalk = new Map(all.map(([path, isDir]) => [path, walked(path, isDir)]));
    expect(all.filter(([path]) => ignored.has(path) !== (inWalk.get(path) !== null))
      .map(([path, isDir]) => `${path}${isDir ? "/" : ""}`)).toEqual([]);
    expect(new Set(inWalk.values()).size).toBe(3);  // custom, default and null all met
    // Any order answers the same: shuffled (every path a jump), and sorted, as the removal
    // pass asks.
    const shuffled = [...all];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j]!, shuffled[i]!];
    }
    const jumping = ignoredBy(lines);
    expect(shuffled.filter(([path, isDir]) => jumping(path, isDir) !== inWalk.get(path)))
      .toEqual([]);
    const sorting = ignoredBy(lines);
    const sorted = [...all].sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
    expect(sorted.filter(([path, isDir]) => sorting(path, isDir) !== inWalk.get(path)))
      .toEqual([]);
    // And as a matcher with nothing remembered answers, a fresh one each path.
    expect(shuffled.slice(0, 400).filter(([path, isDir]) =>
      ignoredBy(lines)(path, isDir) !== inWalk.get(path))).toEqual([]);
  });

  it("answers paths no walk makes (`.`, `..`, empty segments, a trailing `/`, 40 deep) the " +
     "same in any order as with nothing remembered", () => {
    let seed = 20261105;
    const random = () => ((seed = (Math.imul(seed, 1103515245) + 12345) >>> 0) / 2 ** 32);
    const pick = <T>(xs: readonly T[]) => xs[Math.floor(random() * xs.length)]!;
    const names = ["", ".", "..", "a", "b", "c", "k", "src", "lib", "build", ".ta", "sync-ignore",
                   "\u00e9", "e\u0301", "A", "Ab", "x.log", "node_modules"];
    const asked: [string, boolean][] = [];
    while (asked.length < 6_000) {
      const depth = 1 + Math.floor(random() * 40);
      const path = Array.from({ length: depth }, () => pick(names)).join("/");
      asked.push([random() < 0.1 ? `${path}/` : path, random() < 0.3]);
    }
    const lines = [...defaultSyncIgnore(), "a/b", "a/b/c", "!a/b", "/src/lib", "lib/src/",
                   "!lib/src/a", "a//b", "./a", "../k", "a/./b", "**/k/**", "*.log", "\u00e9/k",
                   "Ab/", "!.ta/x", "build/"];
    // Nothing remembered: a matcher of its own each path.
    const bare = asked.map(([path, isDir]) => ignoredBy(lines)(path, isDir));
    expect(new Set(bare).size).toBe(3);
    const order = (indices: number[]) => {
      const matcher = ignoredBy(lines);
      return indices.filter((i) => matcher(asked[i]![0], asked[i]![1]) !== bare[i]);
    };
    const asMade = asked.map((_, i) => i);
    const sorted = [...asMade].sort((x, y) => asked[x]![0] < asked[y]![0] ? -1
      : asked[x]![0] > asked[y]![0] ? 1 : 0);
    const shuffled = [...asMade];
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j]!, shuffled[i]!];
    }
    expect([order(asMade), order(sorted), order(shuffled)]).toEqual([[], [], []]);
  });

  it("matches a path in a walk at its own segment's cost, whatever its depth", () => {
    // Path lines that keep the automaton alive across every `/` (a leading `**/`, `*` loops):
    // without the path's folders remembered, each path would cost its whole length.
    const lines = [...defaultSyncIgnore()];
    let states = 0;
    const add = (line: string) => {
      if (states + automatonStates(line) > AUTOMATON_STATES) {
        return false;
      }
      lines.push(line);
      states += automatonStates(line);
      return true;
    };
    add(`**/*${"?".repeat(28)}*`);
    for (let i = 0; add(`*/**/${"?".repeat(26)}${letters[i % letters.length]}*`); i++) {
      // filled to the ceiling
    }
    const words = Math.ceil(states / 32);
    expect(words).toBeGreaterThan(200);
    // A walk 40 folders deep, then 100 modules of 5 folders of 10 files.
    const entries: [string, boolean][] = [];
    let top = "";
    for (let level = 0; level < 40; level++) {
      top = top === "" ? `level-${level}` : `${top}/level-${level}`;
      entries.push([top, true]);
    }
    for (let i = 0; i < 100; i++) {
      entries.push([`${top}/module-${i}`, true], [`${top}/module-${i}/src`, true]);
      for (let j = 0; j < 5; j++) {
        const part = `${top}/module-${i}/src/part-${j}`;
        entries.push([part, true]);
        for (let f = 0; f < 10; f++) {
          entries.push([`${part}/file-${f}.ts`, false]);
        }
      }
    }
    // The review's bound a path (`pathWork`): its name and `/` and segment, at ⌈S/32⌉ words a
    // step, none of its folder; a folder twice (the walk's entry, and once more as the folder
    // of the paths under it).
    const bound = entries.flatMap(([path, isDir]) =>
      Array.from({ length: isDir ? 2 : 1 }, () => pathWork(path, words, false)));
    expect(overBound(() => {
      const matcher = ignoredBy(lines);
      return [bound, () => {
        for (const [path, isDir] of entries) {
          matcher(path, isDir);
        }
      }];
    }), "CPU time over the automaton's bound").toBeLessThanOrEqual(1);
  });
});
