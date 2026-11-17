/**
 * B10's literal guard over every folder: a live name, a Worker's, a bucket's, a queue's or a KV
 * namespace's (`facts.ts`, which reads them from the six configs and `platform.json`), is
 * spelled only in those files. Anywhere else in the platform or its workflows (`TREE`), code,
 * comment or prose, it is a copy a rename leaves behind: the file reads the name from the facts
 * or its config, or points at the config. What is left is owed, by the workspace whose file
 * spells it (its `OWED`, under the fix that removes it; the platform's top and the workflows
 * are the ci kit's), or kept for good in `KEPT`, below, each with its reason. A finding that
 * is neither fails, and so does an entry of either that no longer holds.
 */
import { describe, expect, it } from "vitest";

import { type Found, literalBreak, owedLists, ratchet } from "../../ci/kit/owed";
import { treeFiles } from "../../ci/kit/sources";
import { QUEUES as QUEUE_NAMES } from "../../contract/src/queue-names";
import { BUCKETS, CONFIGS, NAMES, NAMESPACES, QUEUES, RETIRED } from "../facts";

/**
 * A finding kept for good, and why: a file and the times it spells a live name, exactly as
 * the guard finds it, so a name added to a kept file fails here until it is read.
 */
interface Kept {
  readonly file: string;
  readonly count: number;
  readonly reason: string;
}

/** The permanent exceptions: one list, each with its reason. */
const KEPT: readonly Kept[] = Object.freeze([
  { file: "platform/contract/src/queue-names.ts", count: Object.keys(QUEUE_NAMES).length,
    reason: "written by `npm run generate` from the configs (`ci/kit/types.ts`), and held to " +
            "them by the generated guard, so its count is its own: a queue's name a binding" },
  { file: "platform/cutover.json", count: 5,
    reason: "the cutover's manifest: each live object it removes, by the name it must still " +
            "have once the config that names it today has lost it, each held to that file by " +
            "`ci/guards/cutover.test.ts`" },
  { file: "platform/sandbox/template/image.ts", count: 1,
    reason: "E2B's template name (`NAME`), an input of the image the template digest pins " +
            "(`s6.md` item 2): E2B's name, not the Worker's" },
  { file: "platform/owner/guards/rules.ts", count: 1,
    reason: "the owner's own statement of the one queue a bound Worker may produce onto " +
            "(`RING_QUEUES`), which by design follows no producer's config" },
]);

/** Every live name, as the facts read it from its home. */
const LIVE: readonly string[] = Object.freeze([...new Set([
  ...Object.values(NAMES), ...RETIRED, ...BUCKETS, ...QUEUES, ...Object.values(NAMESPACES)])]
  .sort());

/** The names' homes, by path from the repository root. */
const HOMES: ReadonlySet<string> = new Set([
  ...[...CONFIGS.keys()].map((worker) => `platform/${worker}/wrangler.jsonc`),
  "platform/deploy/platform.json"]);

/** A name's own spelling: no letter, digit, `-` or `_` touches it. */
const whole = (name: string): RegExp =>
  new RegExp(`(?<![\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`, "g");

/** Each file of `files` but a home that spells any of `names`, and the workspace that owes it. */
function findings(files: ReadonlyMap<string, string>, names: readonly string[]): Found[] {
  const spellings = names.map(whole);
  return [...files].filter(([path]) => !HOMES.has(path)).flatMap(([path, text]) => {
    const count = spellings.reduce((sum, each) => sum + (text.match(each)?.length ?? 0), 0);
    return count === 0 ? [] : [{ workspace: /^platform\/([^/]+)\//.exec(path)?.[1] ?? "ci",
                                 what: literalBreak(path, count) }];
  });
}

/**
 * What is left of `found` once the kept findings are taken out, for the ratchet, and each
 * kept entry that no longer holds or is kept twice.
 */
function keep(found: readonly Found[], kept: readonly Kept[]):
    { left: Found[]; problems: string[] } {
  const whats = kept.map((each) => literalBreak(each.file, each.count));
  const problems = whats.flatMap((what, i) => [
    ...whats.indexOf(what) === i ? [] : [`KEPT keeps "${what}" twice`],
    ...found.some((each) => each.what === what) ? []
      : [`KEPT keeps "${what}", which no longer holds: delete or correct the entry`]]);
  return { left: found.filter((each) => !whats.includes(each.what)), problems };
}

describe("the literal guard", () => {
  it("finds a live name spelled outside its homes only where it is owed or kept", async () => {
    const { left, problems } = keep(findings(treeFiles(), LIVE), KEPT);
    expect([...problems, ...ratchet("literals", left, await owedLists())]).toEqual([]);
  });

  it("reads every kind of live name the facts hold", () => {
    expect([NAMES["owner"], RETIRED[0], BUCKETS[0], QUEUES[0], NAMESPACES["CONTROL"]]
      .every((name) => name !== undefined && LIVE.includes(name))).toBe(true);
  });

  it("finds the whole name in any file but a home, and the workspace that owes it", () => {
    const owner = NAMES["owner"]!;
    const bucket = BUCKETS[0]!;
    const files = new Map([
      ["platform/doors/src/a.ts", `bind("${owner}", "${bucket}"); // ${owner}`],
      ["platform/doors/src/b.ts", `const env = "${owner}-staging", other = "x${owner}";`],
      [".github/workflows/x.yml", `run: wrangler deploy --name ${owner}`],
      ["platform/sandbox/template/c.ts", `"${owner}:s1"`],
      ["platform/owner/wrangler.jsonc", `{ "name": "${owner}", "bucket": "${bucket}" }`],
      ["platform/deploy/platform.json", `{ "bucket": "${bucket}" }`],
    ]);
    expect(findings(files, LIVE)).toEqual([
      { workspace: "doors", what: literalBreak("platform/doors/src/a.ts", 3) },
      { workspace: "ci", what: literalBreak(".github/workflows/x.yml", 1) },
      { workspace: "sandbox", what: literalBreak("platform/sandbox/template/c.ts", 1) },
    ]);
  });

  it("takes out what is kept, and finds a kept entry that no longer holds or is kept twice",
     () => {
    const found = [{ workspace: "doors", what: literalBreak("a", 1) },
                   { workspace: "doors", what: literalBreak("b", 2) }];
    const entry = (file: string, count: number) => ({ file, count, reason: "a probe" });
    expect(keep(found, [entry("a", 1)])).toEqual({ left: [found[1]], problems: [] });
    expect(keep(found, [entry("a", 1), entry("a", 1), entry("b", 1)]).problems).toEqual([
      `KEPT keeps "${literalBreak("a", 1)}" twice`,
      `KEPT keeps "${literalBreak("b", 1)}", which no longer holds: delete or correct the entry`]);
  });
});
