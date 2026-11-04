/**
 * The sync-ignore matcher (`owner/src/sync-ignore.ts`) as git reads a `.gitignore`, `git
 * check-ignore` the oracle: GitHub's 310 templates (github/gitignore at `df4087095eb4`, CC0,
 * in `test-data/gitignore/`) and random lists, every path's answer git's; the hostile lists
 * of the review's rounds 1 to 6, answered as git does with the lines that fit, at a cost a
 * path within the automaton's own (`STEP_NS` a word step); its memory a list byte within
 * what the sandbox's heap leaves (`LIST_BYTES`).
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
  ignoredBy, LIST_BYTES, SANDBOX_HEAP, STEP_NS, unappliedLines,
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
  const git = spawnSync("git", ["-C", repo, "-c", "core.excludesFile=/dev/null", "check-ignore",
                                "--stdin", "-z"], { input: all.map(([path]) => path).join("\0"),
                                                    env: ENV, encoding: "utf8" });
  rmSync(repo, { recursive: true, force: true });
  return { all, ignored: new Set(git.stdout.split("\0").filter(Boolean)) };
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
  it("answers every path as git does, over all 310 of GitHub's templates", () => {
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
  }, 600_000);

  it("answers random lists as git does: negation, **, classes, escapes, folders only", () => {
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
  }, 600_000);

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
                   (label, list, path) => {
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
      const answer = spawnSync("git", ["-C", repo, "-c", "core.excludesFile=/dev/null",
                                       "check-ignore", "--no-index", "--stdin", "-z"],
                               { input: asked.join("\0"), env: ENV, encoding: "utf8" });
      rmSync(repo, { recursive: true, force: true });
      ignored = new Set(answer.stdout.split("\0").filter(Boolean));
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
      const began = performance.now();
      for (const each of fresh) {
        matcher(each);
      }
      perPath = Math.min(perPath, (performance.now() - began) / fresh.length);
    }
    const fresh = [path(1)];
    const name = bytesOf(fresh[0]!.split("/").at(-1)!);
    // Its folder's remembered Aho–Corasick node may be as deep as its folder (R7-2): the
    // walk from it falls back at most that far, a step an index byte.
    const folder = bytesOf(fresh[0]!.slice(0, Math.max(0, fresh[0]!.lastIndexOf("/"))));
    const bound = ((2 * name + 1) * (words * STEP_NS + INDEX_NS) + folder * INDEX_NS) * 1.5
      / 1e6;
    expect(perPath).toBeLessThanOrEqual(bound);
  }, 300_000);
});

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
     () => {
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
  }, 300_000);
});
