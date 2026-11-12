/**
 * Only `files/src/keys.ts` builds an R2 key, and only from hex (ADR-032, plan §6).
 *
 * A path must never become an R2 key: R2 NFC-normalizes keys, so two verbatim Linux
 * names would meet in one object (Fact 9). So across every Worker's source:
 * - no text outside `keys.ts` spells a key's parts (a literal that starts `o/` or holds
 *   `/c/`), and no text names the bucket (`"BLOBS"`);
 * - only the files Worker names the bucket or an R2 type, so the owner object holds no
 *   way to the bucket but the `FILES` service; the one other bucket is the cutover's import
 *   staging (`IMPORT_STAGING`), typed `R2Bucket` in the doors' env alone (`doors/src/env.ts`,
 *   its one binding there), whose keys are a run, an owner's key and a bundle's part names
 *   (hex and page numbers, `doors/src/import.ts`), never a path; deleted at the cutover;
 * - in the files Worker, the bucket is reached in exactly one form,
 *   `this.env.BLOBS.<put|get|head|delete>(key, ...)`, `this.env` is never read for
 *   anything else but the data keys' ring (`this.env.FILES_KEYRING`, handed straight to
 *   `datakey.ts`'s `mint`, `unwrap` and `rewrap`, the one module that reads it), and every
 *   key is a call of a key builder
 *   (`chunkKey`, `manifestKey`, `segmentKey`, `manifestObjectKey`, or `listedManifest`, which rebuilds a
 *   listed key
 *   through `manifestKey`; or a list of them mapped from a list), each the one imported
 *   from `./keys` and nothing shadowing it. No alias (`const b = this.env.BLOBS`,
 *   `const { BLOBS } = env`, `env["BLOBS"]`) passes, and the one list is of one namespace's
 *   prefix (`list({ prefix: chunkPrefix(ns) | manifestPrefix(ns), ... })`, the erasure's).
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { envFile, parse, reporter, workerSources } from "../../ci/kit/sources";

const KEYS = "files/src/keys.ts";
const FILES = "files/src/";
const CALLS: ReadonlySet<string> = new Set(["put", "get", "head", "delete"]);
const KEY_TEXT = /^o\/|\/c\//;
const R2_TYPE = /^R2[A-Z]/;
/** The import window's staging bucket's type, named in the doors' env alone. */
const STAGING_TYPE = Object.freeze({ file: envFile("doors"), name: "R2Bucket" });
/** The key builders `keys.ts` exports, the only calls a bucket key may be. */
const BUILDERS: ReadonlySet<string> = new Set(["chunkKey", "manifestKey", "segmentKey",
                                                "manifestObjectKey", "listedManifest"]);
/** The prefixes `keys.ts` exports, the only lists of the bucket. */
const PREFIXES: ReadonlySet<string> = new Set(["chunkPrefix", "manifestPrefix"]);
/** The one other field of `this.env` the Worker reads, and the calls it may go to. */
const RING = "FILES_KEYRING";
/** The cipher's choice, read only by `cipherOf`. */
const CIPHER = "FILES_CIPHER";
const RING_READERS: ReadonlySet<string> = new Set(["mint", "unwrap", "rewrap"]);

function thisEnv(node: ts.Node): boolean {
  return ts.isPropertyAccessExpression(node) && node.expression.kind === ts.SyntaxKind.ThisKeyword
    && node.name.text === "env";
}

/** `{ prefix: chunkPrefix(...), ... }`: a list of one namespace's prefix. */
function prefixList(node: ts.Node | undefined): boolean {
  return node !== undefined && ts.isObjectLiteralExpression(node) && node.properties.some(
    (property) => ts.isPropertyAssignment(property) && ts.isIdentifier(property.name)
      && property.name.text === "prefix" && ts.isCallExpression(property.initializer)
      && ts.isIdentifier(property.initializer.expression)
      && PREFIXES.has(property.initializer.expression.text));
}

function chunkKeyCall(node: ts.Node | undefined): boolean {
  if (node === undefined || !ts.isCallExpression(node)) {
    return false;
  }
  if (ts.isIdentifier(node.expression) && BUILDERS.has(node.expression.text)) {
    return true;
  }
  // `list.map((x) => chunkKey(...))`
  const [mapper] = node.arguments;
  return ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === "map"
    && mapper !== undefined && ts.isArrowFunction(mapper) && !ts.isBlock(mapper.body)
    && chunkKeyCall(mapper.body);
}

/** Whether `node` is a name declared here as a key builder, other than its one import. */
function shadowsChunkKey(node: ts.Node): boolean {
  const named = (name: ts.Node | undefined) =>
    name !== undefined && ts.isIdentifier(name) && BUILDERS.has(name.text);
  if (ts.isImportSpecifier(node)) {
    const from = node.parent.parent.parent.moduleSpecifier;
    return named(node.name) && !(ts.isStringLiteral(from) && from.text === "./keys"
                                 && (node.propertyName ?? node.name).text === node.name.text);
  }
  return (ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isBindingElement(node)
          || ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node))
    && named(node.name);
}

/** Each place `source` builds or reaches an R2 key around `keys.ts`, as `file:line: what`. */
export function keyProblems(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const inFiles = fileName.startsWith(FILES);
  const visit = (node: ts.Node): void => {
    if (ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node)) {
      if (fileName !== KEYS && KEY_TEXT.test(node.text)) {
        report(node, "spells an R2 key outside keys.ts");
      }
      if (node.text.includes("BLOBS")) {
        report(node, "names the bucket as text");
      }
    }
    if (fileName !== KEYS && shadowsChunkKey(node)) {
      report(node, "declares a chunkKey other than the one from ./keys");
    }
    const staging = fileName === STAGING_TYPE.file && node.getText(file) === STAGING_TYPE.name
      && ts.isTypeReferenceNode(node.parent);
    if (ts.isIdentifier(node) && !inFiles && !staging
        && (node.text === "BLOBS" || R2_TYPE.test(node.text))) {
      report(node, `names ${node.text} outside the files Worker`);
    }
    if (inFiles && thisEnv(node)) {
      const parent = node.parent;
      const call = parent.parent;
      const ring = ts.isPropertyAccessExpression(parent) && parent.name.text === RING
        && call !== undefined && ts.isCallExpression(call) && call.arguments[0] === parent
        && ts.isIdentifier(call.expression) && RING_READERS.has(call.expression.text);
      const cipher = ts.isPropertyAccessExpression(parent) && parent.name.text === CIPHER
        && call !== undefined && ts.isCallExpression(call) && call.arguments[0] === parent
        && ts.isIdentifier(call.expression) && call.expression.text === "cipherOf";
      // The doors' upload entrypoint hands its env whole to the service it narrows, this
      // file's own class, which this guard holds as it holds the rest (review M5-1).
      const service = ts.isNewExpression(parent) && ts.isIdentifier(parent.expression)
        && parent.expression.text === "FilesService";
      if (!service && (!ts.isPropertyAccessExpression(parent) || parent.expression !== node
          || (parent.name.text !== "BLOBS" && !ring && !cipher))) {
        report(node, "reads this.env for other than BLOBS and the ring's own module");
      }
    }
    if (ts.isIdentifier(node) && node.text === RING && fileName !== "files/src/index.ts"
        && !(ts.isPropertySignature(node.parent) && node.parent.name === node)) {
      report(node, `names ${RING} outside the files Worker's entry`);
    }
    if (ts.isStringLiteralLike(node) && node.text.includes(RING)) {
      report(node, `names ${RING} as text`);
    }
    if (inFiles && ts.isIdentifier(node) && node.text === "BLOBS"
        && !(ts.isPropertySignature(node.parent) && node.parent.name === node)) {
      const access = node.parent;
      const method = access.parent;
      const call = method?.parent;
      if (!ts.isPropertyAccessExpression(access) || access.name !== node || !thisEnv(access.expression)
          || !ts.isPropertyAccessExpression(method) || method.expression !== access
          || call === undefined || !ts.isCallExpression(call) || call.expression !== method
          || !(CALLS.has(method.name.text) ? chunkKeyCall(call.arguments[0])
            : method.name.text === "list" && prefixList(call.arguments[0]))) {
        report(node, "reaches BLOBS other than this.env.BLOBS.<put|get|head|delete>(<a key builder>(...))");
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

  const header = 'import { chunkKey, manifestKey } from "./keys";\n';
  it.each([
    ["owner/src/probe.ts", "const key = `o/${ns}/c/${sha}`;"],
    ["owner/src/probe.ts", "const key = 'o/' + ns;"],
    ["files/src/index.ts", "const key = ns + '/c/' + sha;"],
    ["owner/src/probe.ts", "type B = R2Bucket;"],
    ["doors/src/import.ts", "type B = R2Bucket;"],
    [envFile("doors"), "type B = R2ObjectBody;"],
    [envFile("doors"), "const BLOBS = 1;"],
    ["owner/src/probe.ts", "function f(env) { return env.BLOBS; }"],
    ["files/src/index.ts", "class S { async f(path) { await this.env.BLOBS.put(path, b); } }"],
    ["files/src/index.ts", "class S { async f() { await this.env.BLOBS.list({}); } }"],
    ["files/src/index.ts", "class S { async f() { await this.env.BLOBS.list({ prefix: 'o/' }); } }"],
    ["files/src/index.ts", "class S { f() { const b = this.env.BLOBS.get; return b('k'); } }"],
    ["files/src/index.ts", "class S { async f(xs) { await this.env.BLOBS.delete(xs.map((x) => x)); } }"],
    ["files/src/index.ts", "class S { async f(k) { const key = chunkKey(a, b); " +
     "await this.env.BLOBS.get(key); } }"],
    ["files/src/index.ts", "class S { f() { const bucket = this.env.BLOBS; " +
     "return bucket.put(chunkKey(a, b), x); } }"],
    ["files/src/index.ts", "class S { f() { const { BLOBS } = this.env; " +
     "return BLOBS.put(chunkKey(a, b), x); } }"],
    ["files/src/index.ts", "class S { f(env) { return env['BLOBS'].put(chunkKey(a, b), x); } }"],
    ["files/src/index.ts", "class S { f(env) { return env.BLOBS.put(chunkKey(a, b), x); } }"],
    ["files/src/index.ts", "class S { f() { return Reflect.get(this.env, 'BLO' + 'BS'); } }"],
    ["files/src/index.ts", "class S { f() { const e = this.env; return e; } }"],
    ["files/src/index.ts", "function chunkKey(a) { return a; }\nclass S { f(p) { " +
     "return this.env.BLOBS.put(chunkKey(p), x); } }"],
    ["files/src/index.ts", "class S { f(p) { const chunkKey = (a) => a; " +
     "return this.env.BLOBS.put(chunkKey(p), x); } }"],
    ["files/src/index.ts", "import { chunkKey } from './other';\nclass S { f(p) { " +
     "return this.env.BLOBS.put(chunkKey(p), x); } }"],
    ["files/src/index.ts", "class S { f() { return this.env.FILES_KEYRING; } }"],
    ["files/src/index.ts", "class S { f() { return leak(this.env.FILES_KEYRING); } }"],
    ["files/src/index.ts", "class S { f() { return mint(ns, this.env.FILES_KEYRING); } }"],
    ["files/src/keys.ts", "function f(env) { return env.FILES_KEYRING; }"],
    ["owner/src/probe.ts", "const k = 'FILES_KEYRING';"],
    ["files/src/index.ts", "function manifestKey(a) { return a; }\nclass S { f(p) { " +
     "return this.env.BLOBS.put(manifestKey(p), x); } }"],
  ])("sees %s: %s", (path, source) => {
    expect(keyProblems(path, source.startsWith("import") ? source
      : header + source)).not.toEqual([]);
  });

  it.each([
    "class S { async f(ns, sha) { await this.env.BLOBS.put(chunkKey(ns, sha), b); } }",
    "class S { async f(ns, sha) { await this.env.BLOBS.head(chunkKey(ns, sha)); } }",
    "class S { async f(ns, xs) { await this.env.BLOBS.delete(xs.map((x) => chunkKey(ns, x))); } }",
    "class S { async f(ns, d) { await this.env.BLOBS.get(manifestKey(ns, d)); } }",
    "class S { f(ns, k) { return unwrap(this.env.FILES_KEYRING, ns, k); } }",
    "interface FilesEnv { readonly BLOBS: R2Bucket; }",
    "class S { async f(ns) { await this.env.BLOBS.list({ prefix: chunkPrefix(ns) }); } }",
    "class S { async f(ns) { await this.env.BLOBS.list({ prefix: manifestPrefix(ns) }); } }",
  ])("allows %s", (source) => {
    expect(keyProblems("files/src/index.ts", header + source)).toEqual([]);
  });
});
