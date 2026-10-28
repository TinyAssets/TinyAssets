/**
 * Only `files/src/keys.ts` builds an R2 key, and only from hex (ADR-032, plan §6).
 *
 * A path must never become an R2 key: R2 NFC-normalizes keys, so two verbatim Linux
 * names would meet in one object (Fact 9). So across every Worker's source:
 * - no text outside `keys.ts` spells a key's parts (a literal that starts `o/` or holds
 *   `/c/`);
 * - only the files Worker names the bucket (`BLOBS`) or an R2 type, so the owner object
 *   holds no way to the bucket but the `FILES` service;
 * - in the files Worker, every bucket call is a `put`, `get`, `head` or `delete` whose key
 *   is `chunkKey(...)` (directly, through a `const`, or mapped over a list), and nothing
 *   lists the bucket.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "../../owner/guards/sources";

const KEYS = "files/src/keys.ts";
const FILES = "files/src/";
const CALLS: ReadonlySet<string> = new Set(["put", "get", "head", "delete"]);
const KEY_TEXT = /^o\/|\/c\//;
const R2_TYPE = /^R2[A-Z]/;

function keyCall(node: ts.Expression | undefined, consts: Map<string, ts.Expression>): boolean {
  if (node === undefined) {
    return false;
  }
  if (ts.isIdentifier(node)) {
    return keyCall(consts.get(node.text), consts);
  }
  if (!ts.isCallExpression(node)) {
    return false;
  }
  if (ts.isIdentifier(node.expression) && node.expression.text === "chunkKey") {
    return true;
  }
  // `list.map((x) => chunkKey(...))`
  const [mapper] = node.arguments;
  return ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === "map"
    && mapper !== undefined && ts.isArrowFunction(mapper) && !ts.isBlock(mapper.body)
    && keyCall(mapper.body, consts);
}

/** Each place `source` builds or uses an R2 key around `keys.ts`, as `file:line: what`. */
export function keyProblems(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const consts = new Map<string, ts.Expression>();
  const collect = (node: ts.Node): void => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name)
        && node.initializer !== undefined
        && (ts.getCombinedNodeFlags(node) & ts.NodeFlags.Const) !== 0) {
      consts.set(node.name.text, node.initializer);
    }
    ts.forEachChild(node, collect);
  };
  collect(file);
  const inFiles = fileName.startsWith(FILES);
  const visit = (node: ts.Node): void => {
    if ((ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node))
        && fileName !== KEYS && KEY_TEXT.test(node.text)) {
      report(node, "spells an R2 key outside keys.ts");
    }
    if (ts.isIdentifier(node) && !inFiles && (node.text === "BLOBS" || R2_TYPE.test(node.text))) {
      report(node, `names ${node.text} outside the files Worker`);
    }
    if (inFiles && ts.isPropertyAccessExpression(node) && ts.isPropertyAccessExpression(
          node.expression) && node.expression.name.text === "BLOBS") {
      const call = node.parent;
      if (!CALLS.has(node.name.text)) {
        report(node, `calls BLOBS.${node.name.text}`);
      } else if (!ts.isCallExpression(call) || call.expression !== node
                 || !keyCall(call.arguments[0], consts)) {
        report(node, `calls BLOBS.${node.name.text} with a key not from chunkKey`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("r2-keys guard", () => {
  it("finds every R2 key built by chunkKey, and the bucket named only by the files Worker",
     () => {
    const sources = workerSources();
    expect([...sources.keys()]).toEqual(expect.arrayContaining([KEYS, "files/src/index.ts",
                                                                "owner/src/files.ts"]));
    expect([...sources].flatMap(([path, source]) => keyProblems(path, source))).toEqual([]);
  });

  it.each([
    ["owner/src/probe.ts", "const key = `o/${ns}/c/${sha}`;"],
    ["owner/src/probe.ts", "const key = 'o/' + ns;"],
    ["files/src/index.ts", "const key = ns + '/c/' + sha;"],
    ["owner/src/probe.ts", "type B = R2Bucket;"],
    ["owner/src/probe.ts", "function f(env) { return env.BLOBS; }"],
    ["files/src/index.ts", "async function f(env, path) { await env.BLOBS.put(path, b); }"],
    ["files/src/index.ts", "async function f(env) { await env.BLOBS.list({}); }"],
    ["files/src/index.ts", "function f(env) { const b = env.BLOBS.get; return b('k'); }"],
    ["files/src/index.ts",
     "async function f(env, xs) { await env.BLOBS.delete(xs.map((x) => x)); }"],
    ["files/src/index.ts", "async function f(env, p) { let k = chunkKey(a, b); k = p; " +
     "await env.BLOBS.get(k); }"],
  ])("sees %s: %s", (path, source) => {
    expect(keyProblems(path, source)).not.toEqual([]);
  });

  it.each([
    "async function f(env, ns, sha) { await env.BLOBS.put(chunkKey(ns, sha), b); }",
    "async function f(env, ns, sha) { const key = chunkKey(ns, sha); await env.BLOBS.head(key); }",
    "async function f(env, ns, xs) { await env.BLOBS.delete(xs.map((x) => chunkKey(ns, x))); }",
  ])("allows %s", (source) => {
    expect(keyProblems("files/src/index.ts", source)).toEqual([]);
  });
});
