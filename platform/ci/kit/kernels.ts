/**
 * The kernel guard (untangle B7): where the platform has one module that does a thing, no
 * source in that module's reach does it again. Each kernel names its one home, the layers of
 * the import table (`layers.ts`) it holds, by their names there, and the folder it holds them
 * under:
 *
 * - SHA-256 is `contract/src/digest.ts`'s, in the contract beside it (every Worker's code
 *   too), every Worker's source and the runner. An HMAC
 *   is a key's, not the digest's. The template's pin reader and the builds keep their own,
 *   since they import nothing by rule: their layers are not held.
 * - Now is the owner's clock's (`owner/src/clock.ts`), in the owner's source.
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

/** `Date.now`, called or passed. */
const readsNow = (node: ts.Node): boolean => ts.isPropertyAccessExpression(node)
  && ts.isIdentifier(node.expression) && node.expression.text === "Date"
  && node.name.text === "now";

export const KERNELS: readonly Kernel[] = Object.freeze([
  { name: "SHA-256", home: "platform/contract/src/digest.ts",
    layers: ["the contract", "a Worker's source", "the runner"], under: "platform/",
    does: hashesSha256 },
  { name: "Date.now()", home: "platform/owner/src/clock.ts", layers: ["a Worker's source"],
    under: "platform/owner/src/", does: readsNow },
]);

/** How many times `source` does what `kernel` does. */
function uses(kernel: Kernel, path: string, source: string): number {
  let count = 0;
  const visit = (node: ts.Node): void => {
    count += kernel.does(node) ? 1 : 0;
    ts.forEachChild(node, visit);
  };
  visit(parse(path, source));
  return count;
}

/**
 * Every source of `files` (scripts by path from the repository root) that does a kernel's work
 * outside its home, in a layer the kernel holds: one violation per source and kernel, in the
 * source's workspace.
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
    const layers = layersOf(path, LAYERS, workers).map((layer) => layer.name);
    const workspace = workspaceOf(path);
    for (const kernel of KERNELS) {
      if (workspace === null || path === kernel.home || !path.startsWith(kernel.under)
          || !kernel.layers.some((name) => layers.includes(name))) {
        continue;
      }
      const count = uses(kernel, path, source);
      if (count > 0) {
        found.push({ workspace, what: kernelBreak(path, kernel.name, count) });
      }
    }
  }
  return found;
}
