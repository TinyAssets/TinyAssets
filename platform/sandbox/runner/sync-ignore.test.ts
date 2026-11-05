/**
 * The sync-ignore matcher (`owner/src/sync-ignore.ts`) as git reads a `.gitignore`, `git
 * check-ignore` the oracle: GitHub's 310 templates (github/gitignore at `df4087095eb4`, CC0,
 * in `test-data/gitignore/`) and random lists, every path's answer git's; the hostile lists
 * of the review's rounds 1 to 6, answered as git does with the lines that fit, at a cost a
 * path within the automaton's own (`STEP_NS` a word step); its memory a list byte within
 * what the sandbox's heap leaves (`LIST_BYTES`); what it remembers of a tree the same at
 * any size, its answers the same in any order, and a walk's path its own segment's cost.
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
  AGENT_PEAK, AUTOMATON_STATES, automatonStates, defaultSyncIgnore, HEAP_PER_LIST_BYTE,
  ignoredBy, LIST_BYTES, REMEMBERED_NAME_BYTES, SANDBOX_HEAP, STEP_NS, unappliedLines,
} from "../../owner/src/sync-ignore";

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
    // Its cost a path: within the automaton's bound, (name + segment + 1) bytes of ⌈S/32⌉
    // words at `STEP_NS`, half again for this host, and the indexes' walk of the same bytes.
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
    // Warmed first (the JIT's), then the best of three rounds of fresh paths.
    for (let i = 0; i < 300; i++) {
      matcher(path(5000 + i));
    }
    let perPath = Number.POSITIVE_INFINITY;
    for (let round = 0; round < 3; round++) {
      const fresh = Array.from({ length: 300 }, (_, i) => path(10_000 * (round + 1) + i));
      // The process's CPU time, not wall-clock: another process's load does not stretch it.
      const began = process.cpuUsage();
      for (const each of fresh) {
        matcher(each);
      }
      const spent = process.cpuUsage(began);
      perPath = Math.min(perPath, (spent.user + spent.system) / 1000 / fresh.length);
    }
    const fresh = [path(1)];
    const name = bytesOf(fresh[0]!.split("/").at(-1)!);
    // Its folder's remembered Aho–Corasick node may be as deep as its folder (R7-2): the
    // walk from it falls back at most that far, a step an index byte.
    const folder = bytesOf(fresh[0]!.slice(0, Math.max(0, fresh[0]!.lastIndexOf("/"))));
    const bound = ((2 * name + 1) * (words * STEP_NS + INDEX_NS) + folder * INDEX_NS) * 1.5
      / 1e6;
    expect(perPath).toBeLessThanOrEqual(bound);
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
    const bound = walk.reduce((sum, [each]) => sum + walkCost(each, words), 0);
    const took = (entries: readonly [string, boolean][]) => {
      let least = Number.POSITIVE_INFINITY;
      for (let round = 0; round < 3; round++) {
        const matcher = ignoredBy(lines);
        const began = performance.now();
        for (const [each, isDir] of entries) {
          matcher(each, isDir);
        }
        least = Math.min(least, performance.now() - began);
      }
      return least;
    };
    expect(took(walk)).toBeLessThanOrEqual(bound);
    expect(took(files.map((file) => [file, false]))).toBeLessThanOrEqual(bound);
  });
});

/**
 * The walk's bound a path, in ms (R7's, the review's): its name and its `/` and segment at
 * `words` words of `STEP_NS` and the indexes' walk, Aho–Corasick's fallback from its folder's
 * node (at most its folder's depth), half again for this host.
 */
function walkCost(path: string, words: number): number {
  const name = bytesOf(path.split("/").at(-1)!);
  const folder = bytesOf(path.slice(0, Math.max(0, path.lastIndexOf("/"))));
  return ((2 * name + 1) * (words * STEP_NS + INDEX_NS) + folder * INDEX_NS) * 1.5 / 1e6;
}

/**
 * What the indexes take a byte of text, in ns: a map lookup a byte of a name's tries and of
 * a path's trie and automaton of lines (an index's cost does not grow with its lines).
 */
const INDEX_NS = 200;

describe("its memory", () => {
  // `gc` for this test alone, so a list's heap is read after the garbage of its making.
  setFlagsFromString("--expose-gc");
  const gc = runInNewContext("gc") as () => void;
  // Heads the lines share, and heads that diverge (a trie's and Aho–Corasick's worst, R7-3).
  const head = (i: number) => (Math.imul(i + 1, 2654435761) >>> 0).toString(36);
  const shapes: Record<string, (i: number) => string> = {
    literalName: (i) => `name-${i.toString(36)}-xxxxxxxx`,
    literalPath: (i) => `dir/${i.toString(36)}/name-xxxx`,
    suffix: (i) => `*.ext-${i.toString(36)}-xxxx`,
    prefix: (i) => `pre-${i.toString(36)}-xxxx*`,
    pathPrefix: (i) => `dir/${i.toString(36)}/x*`,
    anywhere: (i) => `**/dir/${i.toString(36)}/x`,
    automaton: (i) => `*${i.toString(36)}?x*y`,
    negated: (i) => `!name-${i.toString(36)}-xxxx`,
    literalNameDiverging: (i) => `${head(i)}${i.toString(36)}`,
    literalPathDiverging: (i) => `${head(i)}/${i.toString(36)}`,
    suffixDiverging: (i) => `*${head(i)}${i.toString(36)}`,
    prefixDiverging: (i) => `${head(i)}${i.toString(36)}*`,
    pathPrefixDiverging: (i) => `${head(i)}/${i.toString(36)}*`,
    anywhereDiverging: (i) => `**/${head(i)}/${i.toString(36)}`,
  };

  it("holds, at `LIST_BYTES` of any class (each measured at a quarter of it, a list byte's " +
     "heap), what the sandbox's heap leaves beside the agent",
     { tags: ["large"] }, () => {
    expect(LIST_BYTES * HEAP_PER_LIST_BYTE).toBeLessThanOrEqual(SANDBOX_HEAP - AGENT_PEAK);
    const used = () => process.memoryUsage().heapUsed + process.memoryUsage().arrayBuffers;
    // Two full collections settle what a collection leaves for the next (weak maps, the
    // code a first call compiles), so a sample is the matcher's own heap.
    const settled = () => {
      gc();
      gc();
      return used();
    };
    for (const [label, shape] of Object.entries(shapes)) {
      // The least of three samples: a sample is the matcher's heap plus whatever else the
      // process held for a moment (its own lazily made structures, measured once in a
      // whole-suite run at 53 against 45 alone); the least is the matcher's own, the bound
      // unchanged.
      let perByte = Number.POSITIVE_INFINITY;
      for (let sample = 0; sample < 3; sample++) {
        const base = settled();
        const lines: string[] = [];
        let bytes = 0;
        for (let i = 0; bytes < LIST_BYTES / 4; i++) {
          lines.push(shape(i));
          bytes += shape(i).length + 1;
        }
        const matcher = ignoredBy(lines);
        matcher("a/b.c");
        perByte = Math.min(perByte, (settled() - base) / bytes);
        expect(lines.length + (typeof matcher === "function" ? 0 : 1)).toBeGreaterThan(0);
      }
      expect(perByte, label).toBeLessThanOrEqual(HEAP_PER_LIST_BYTE);
    }
  });
});

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

  it("answers a large mixed tree as git does, in any order, as with nothing remembered", () => {
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
    // The review's bound a path (above): its name and `/` and segment, at ⌈S/32⌉ words of
    // `STEP_NS` and the indexes' walk, half again for this host; a folder twice (the walk's
    // entry, and once more as the folder of the paths under it).
    const bound = entries.reduce((sum, [path, isDir]) => sum + (isDir ? 2 : 1)
      * (2 * bytesOf(path.split("/").at(-1)!) + 1) * (words * STEP_NS + INDEX_NS) * 1.5, 0) / 1e6;
    let took = Number.POSITIVE_INFINITY;
    for (let round = 0; round < 3; round++) {
      const matcher = ignoredBy(lines);
      const began = performance.now();
      for (const [path, isDir] of entries) {
        matcher(path, isDir);
      }
      took = Math.min(took, performance.now() - began);
    }
    expect(took).toBeLessThanOrEqual(bound);
  });
});
