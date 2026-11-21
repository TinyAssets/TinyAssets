/**
 * The kernel guard: where the platform has one module that does a thing, no source in that
 * module's reach does it again. Each kernel names its one home, the layers of the import table
 * (`layers.ts`) it holds, by their names there, and the folder it holds them under:
 *
 * - SHA-256 is `contract/src/digest.ts`'s, in the contract beside it (every Worker's code
 *   too), every Worker's source and the runner. An HMAC is a key's, not the digest's. The
 *   template's pin reader and the builds keep their own, since they import nothing by rule:
 *   their layers are not held.
 * - Now is the owner's clock's (`owner/src/clock.ts`), in the owner's source.
 * - The test that a value is a JSON object is `contract/src/checks.ts`'s (`object`, `keyed`),
 *   in every layer that may import the contract: a type guard of `Record<string, unknown>`
 *   anywhere else is its copy. `refusal.ts`'s, the contract's leaf, guards its own `Facts`.
 * - The test that an object has exactly a set of keys is `checks.ts`'s too (`keyed`), in the
 *   same layers: `Object.keys(…).sort()` joined or stringified, then compared or named to
 *   compare, is its copy. Sorted keys in a message or a log line, or kept as a list (to read
 *   in order, or a test's `toEqual`), are not.
 *
 * Read from the syntax tree: a tripwire for our own code (a hash named at run time is not
 * seen), not proof against deliberate evasion. A source's uses of one kernel are one
 * violation that counts them (`owed.ts`'s `kernelBreak`), so a source that owes some can add
 * none unseen. `ci/guards/kernels.test.ts` holds it over the tree, with what each workspace
 * owes (`owed.ts`) the only exceptions.
 */
import ts from "typescript";

import { workerFolders } from "../wrangler.ts";
import { layersOf, workspaceOf } from "./imports.ts";
import { LAYERS } from "./layers.ts";
import { type Found, kernelBreak } from "./owed.ts";
import { parse } from "./sources.ts";

export interface Kernel {
  /** What it does, as a violation names it. */
  readonly name: string;
  /** The one source that may do it, from the repository root. */
  readonly home: string;
  /** The layers it holds, by their names in `layers.ts`. */
  readonly layers: readonly string[];
  /** The folder it holds them under, from the repository root. */
  readonly under: string;
  /** Whether `node` does what the kernel does. */
  readonly does: (node: ts.Node) => boolean;
}

/** A literal naming SHA-256, as `node:crypto` (`"sha256"`) or Web Crypto (`"SHA-256"`) does. */
const namesSha256 = (node: ts.Node | undefined): boolean =>
  node !== undefined && ts.isStringLiteralLike(node) && /^sha-?256$/i.test(node.text);

/** The name a call calls, bare or as a member (`createHash`, `crypto.subtle.digest`). */
function calledName(call: ts.CallExpression): string | null {
  const callee = call.expression;
  return ts.isIdentifier(callee) ? callee.text
    : ts.isPropertyAccessExpression(callee) ? callee.name.text : null;
}

/** A SHA-256 begun: `createHash("sha256")`, or Web Crypto's `digest("SHA-256", …)`. */
function hashesSha256(node: ts.Node): boolean {
  if (!ts.isCallExpression(node)) {
    return false;
  }
  const [first] = node.arguments;
  const name = calledName(node);
  return (name === "createHash" && namesSha256(first))
    || (name === "digest" && (namesSha256(first) || (first !== undefined
      && ts.isObjectLiteralExpression(first) && first.properties.some((each) =>
        ts.isPropertyAssignment(each) && each.name.getText() === "name"
        && namesSha256(each.initializer)))));
}

/** A type guard that its argument is a `Record<string, unknown>`: an object test. */
const testsObject = (node: ts.Node): boolean => ts.isFunctionLike(node) && node.type !== undefined
  && ts.isTypePredicateNode(node.type) && node.type.type !== undefined
  && node.type.type.getText().replace(/\s/g, "") === "Record<string,unknown>";

/** `Object.keys(…).sort()`: an object's keys, sorted. */
function sortsKeys(node: ts.Node | undefined): boolean {
  if (node === undefined || !ts.isCallExpression(node) || node.arguments.length > 0
      || !ts.isPropertyAccessExpression(node.expression) || node.expression.name.text !== "sort") {
    return false;
  }
  const keys = node.expression.expression;
  return ts.isCallExpression(keys) && ts.isPropertyAccessExpression(keys.expression)
    && keys.expression.name.text === "keys" && ts.isIdentifier(keys.expression.expression)
    && keys.expression.expression.text === "Object";
}

/** An object's sorted keys, joined or stringified into one string. */
const joinsKeys = (node: ts.Node | undefined): boolean => node !== undefined
  && ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
  && ((node.expression.name.text === "join" && sortsKeys(node.expression.expression))
      || (node.expression.name.text === "stringify" && ts.isIdentifier(node.expression.expression)
          && node.expression.expression.text === "JSON" && sortsKeys(node.arguments[0])));

const EQUALITY = new Set([ts.SyntaxKind.EqualsEqualsEqualsToken,
                          ts.SyntaxKind.ExclamationEqualsEqualsToken,
                          ts.SyntaxKind.EqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsToken]);

/**
 * That string compared, or kept in a name to compare: a key-set test. One built into a
 * message or a log line is not.
 */
const comparesKeys = (node: ts.Node): boolean =>
  (ts.isBinaryExpression(node) && EQUALITY.has(node.operatorToken.kind)
   && (joinsKeys(node.left) || joinsKeys(node.right)))
  || (ts.isVariableDeclaration(node) && joinsKeys(node.initializer));

/** `Date.now`, called or passed. */
const readsNow = (node: ts.Node): boolean => ts.isPropertyAccessExpression(node)
  && ts.isIdentifier(node.expression) && node.expression.text === "Date"
  && node.name.text === "now";

/** Every layer that may import the contract, where its shape checks are held. */
const CONTRACT_READERS = Object.freeze([
  "the contract", "the source records", "a Worker's source", "the runner", "the probes",
  "the E2B client", "the template", "the deploy kit", "the importer", "the wrangler module",
  "the ci kit", "the ci guards", "a workspace's tools", "a workspace's tests and memory legs",
  "a workspace's guards",
]);

export const KERNELS: readonly Kernel[] = Object.freeze([
  { name: "SHA-256", home: "platform/contract/src/digest.ts",
    layers: ["the contract", "a Worker's source", "the runner"], under: "platform/",
    does: hashesSha256 },
  { name: "Date.now()", home: "platform/owner/src/clock.ts", layers: ["a Worker's source"],
    under: "platform/owner/src/", does: readsNow },
  { name: "the object test", home: "platform/contract/src/checks.ts", layers: CONTRACT_READERS,
    under: "platform/", does: testsObject },
  { name: "the key-set test", home: "platform/contract/src/checks.ts", layers: CONTRACT_READERS,
    under: "platform/", does: comparesKeys },
]);

/** How many times `file` does what each of `kernels` does, in one walk of its tree. */
function uses(kernels: readonly Kernel[], file: ts.SourceFile): number[] {
  const counts = kernels.map(() => 0);
  const visit = (node: ts.Node): void => {
    kernels.forEach((kernel, at) => {
      counts[at]! += kernel.does(node) ? 1 : 0;
    });
    ts.forEachChild(node, visit);
  };
  visit(file);
  return counts;
}

/**
 * Every source of `files` (scripts by path from the repository root) that does a kernel's work
 * outside its home, in a layer the kernel holds: one violation per source and kernel, in the
 * source's workspace. Each source a kernel holds is parsed and walked once, for every kernel
 * that holds it.
 */
export function kernelProblems(files: ReadonlyMap<string, string>): Found[] {
  const named = new Set(LAYERS.map((layer) => layer.name));
  for (const kernel of KERNELS) {
    const unknown = kernel.layers.filter((name) => !named.has(name));
    if (unknown.length > 0) {
      throw new Error(`the ${kernel.name} kernel holds no layer named ${unknown.join(", ")}`);
    }
  }
  const workers = workerFolders();
  const found: Found[] = [];
  for (const [path, source] of [...files].sort(([a], [b]) => a.localeCompare(b))) {
    const workspace = workspaceOf(path);
    if (workspace === null) {
      continue;
    }
    const layers = layersOf(path, LAYERS, workers).map((layer) => layer.name);
    const held = KERNELS.filter((kernel) => path !== kernel.home && path.startsWith(kernel.under)
      && kernel.layers.some((name) => layers.includes(name)));
    if (held.length === 0) {
      continue;
    }
    uses(held, parse(path, source)).forEach((count, at) => {
      if (count > 0) {
        found.push({ workspace, what: kernelBreak(path, held[at]!.name, count) });
      }
    });
  }
  return found;
}
