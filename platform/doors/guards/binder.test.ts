/**
 * The binder is the only binder (research checklist 1, 6, 17).
 *
 * - `getByName` appears only in `doors/src/binder.ts`, across every Worker's source, but
 *   for the hooks Worker naming its own class's objects (`this.env.CODEMODE`, in
 *   `hooks/src/index.ts` alone: a code mode execution's sandbox, which binds nothing of an
 *   owner) and the sandbox Worker naming its own classes' objects (`this.env.SANDBOXES` and
 *   `this.env.SLOTS`, in `sandbox/src/index.ts` and `control.ts` alone: one owner's
 *   sandboxes' E2B calls, and the platform's line for one more sandbox, neither of which
 *   binds anything of an owner's object), and no source names another way to an object id (`idFromName`,
 *   `idFromString`, `newUniqueId`). The owner's object names no object at all.
 * - A `Binding` is built only there: no other doors source writes an object literal with
 *   a `caller`.
 * - Only `session.ts` reads the `Cookie` header, and only `directory.ts` touches the
 *   directory, whose keys hold no cookie or session hash.
 * - Only `connect.ts` reaches the gateway (`GATEWAY`), so a pasted key or an OAuth code
 *   has one path, to the gateway's connect entrypoint; only `egress.ts` reaches its egress
 *   entrypoint (`EGRESS`), so a blob the owner's object handed out is forwarded on one path;
 *   only `file-bytes.ts` reaches the files Worker (`FILES`), the file door's bytes.
 * - Consent is never remembered: no source names `remember` or `isConsentRemembered`.
 *
 * A tripwire for our own code, read from the syntax tree, not proof against deliberate
 * evasion.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "../../owner/guards/sources";

const BINDER = "doors/src/binder.ts";
/** The owner's object, which names no object. */
const OWNER_DO = "owner/src/owner-do.ts";
/** The hooks Worker's entry, which names its own class's objects and nothing else. */
const HOOKS_ENTRY = "hooks/src/index.ts";
const HOOKS_OWN = new Set(["this.env.CODEMODE"]);
/** The sandbox Worker's sources that name its own classes' objects, and nothing else. */
const SANDBOX_SOURCES = new Set(["sandbox/src/index.ts", "sandbox/src/control.ts"]);
const SANDBOX_OWN = new Set(["this.env.SANDBOXES", "this.env.SLOTS"]);
const SESSION = "doors/src/session.ts";
const DIRECTORY = "doors/src/directory.ts";
const CONNECT = "doors/src/connect.ts";
const EGRESS = "doors/src/egress.ts";
const FILE_BYTES = "doors/src/file-bytes.ts";
const OBJECT_IDS = new Set(["idFromName", "idFromString", "newUniqueId"]);
const REMEMBERING = new Set(["remember", "isConsentRemembered"]);

function nameOf(node: ts.Node): string | null {
  if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node)) {
    return node.text;
  }
  return null;
}

/** Each way `source` breaks the binder's rules, as `file:line: what`. */
export function binderProblems(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const doors = fileName.startsWith("doors/src/");
  const visit = (node: ts.Node): void => {
    const name = nameOf(node);
    const on = name === "getByName" && ts.isPropertyAccessExpression(node.parent)
      && node.parent.name === node ? node.parent.expression.getText(file) : null;
    if (name === "getByName" && fileName !== BINDER
        && !(fileName === HOOKS_ENTRY && on !== null && HOOKS_OWN.has(on))
        && !(SANDBOX_SOURCES.has(fileName) && on !== null && SANDBOX_OWN.has(on))) {
      report(node, "names getByName outside the binder");
    }
    if (name !== null && OBJECT_IDS.has(name)) {
      report(node, `names ${name}, another way to an object`);
    }
    if (doors && name !== null && REMEMBERING.has(name)) {
      report(node, `names ${name}: consent is never remembered`);
    }
    if (doors && ts.isStringLiteralLike(node) && /^cookie$/i.test(node.text)
        && fileName !== SESSION) {
      report(node, "reads the Cookie header outside session.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "DIRECTORY" && fileName !== DIRECTORY
        && fileName !== "doors/src/config.ts") {
      report(node, "touches the directory outside directory.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "GATEWAY" && fileName !== CONNECT
        && fileName !== "doors/src/config.ts") {
      report(node, "reaches the gateway outside connect.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "FILES" && fileName !== FILE_BYTES
        && fileName !== "doors/src/config.ts") {
      report(node, "reaches the files Worker outside file-bytes.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "EGRESS" && fileName !== EGRESS
        && fileName !== "doors/src/config.ts") {
      report(node, "forwards egress outside egress.ts");
    }
    if (doors && fileName !== BINDER && ts.isObjectLiteralExpression(node)
        && node.properties.some((p) => p.name !== undefined && nameOf(p.name) === "caller")) {
      report(node, "builds a binding outside the binder");
    }
    if (doors && ts.isStringLiteralLike(node) && /session|cookie/i.test(node.text)
        && /^[a-z]:/.test(node.text)) {
      report(node, "a directory key for a session or cookie");
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("binder guard", () => {
  it("finds the binder alone binding, in every Worker's source", () => {
    const sources = workerSources();
    expect([...sources.keys()]).toContain(BINDER);
    expect([...sources].flatMap(([path, source]) => binderProblems(path, source))).toEqual([]);
  });

  it("finds getByName in the binder", () => {
    expect(workerSources().get(BINDER)).toContain("getByName");
  });

  it.each([
    ["doors/src/mcp.ts", "export const f = (env: any) => env.OWNER.getByName('alice');"],
    ["doors/src/mcp.ts", "export const f = (env: any) => env.OWNER['getByName']('alice');"],
    ["owner/src/owner-do.ts", "export const f = (ns: any) => ns.getByName('bob');"],
    [OWNER_DO, "export const f = (env: any) => env.STORES.getByName('x');"],
    [OWNER_DO, "export const f = (env: any) => env.OWNER.getByName('bob');"],
    ["doors/src/binder.ts", "export const f = (ns: any) => ns.get(ns.idFromName('bob'));"],
    ["doors/src/app.ts", "export const b = { owner: 'a', caller: 'a', origin: null };"],
    ["doors/src/app.ts", "export const f = (r: Request) => r.headers.get('Cookie');"],
    ["doors/src/app.ts", "export const f = (r: Request) => r.headers.get('cookie');"],
    ["doors/src/authorize.ts", "export const f = (o: any) => o.approveConsent(r, h, { remember });"],
    ["doors/src/authorize.ts", "export const f = (o: any) => o.isConsentRemembered(r, a, x);"],
    ["doors/src/app.ts", "export const f = (env: any) => env.DIRECTORY.put('k:x', 'y');"],
    ["doors/src/directory.ts", "export const k = 's:session';"],
    ["doors/src/app.ts", "export const f = (env: any) => env.GATEWAY.seal('a', 'c', 1, 'k', {});"],
    ["doors/src/binder.ts", "export const f = ({ GATEWAY }: any) => GATEWAY;"],
    ["doors/src/connect.ts", "export const f = (env: any) => env.EGRESS.forward('a', 'c', 1, 'b', r);"],
    ["doors/src/sandbox.ts", "export const f = ({ EGRESS }: any) => EGRESS;"],
    [HOOKS_ENTRY, "export const f = (env: any) => env.OWNER.getByName('bob');"],
    [HOOKS_ENTRY, "export const f = (env: any) => env.RUNNER.getByName('bob');"],
    [HOOKS_ENTRY, "class H { env: any; f(n: string) { return this.env.RUNNER.getByName(n); } }"],
    ["hooks/src/codemode.ts", "export const f = (o: any) => o.env.CODEMODE.getByName('bob');"],
  ])("sees, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).not.toEqual([]);
  });

  it.each([
    ["doors/src/binder.ts", "export const f = (ns: any) => ns.getByName('alice');"],
    [HOOKS_ENTRY, "class H { env: any; f(n: string) { return this.env.CODEMODE.getByName(n); } }"],
    ["doors/src/binder.ts", "export const b = { owner: 'a', caller: 'a', origin: null };"],
    ["doors/src/session.ts", "export const f = (r: Request) => r.headers.get('Cookie');"],
    ["doors/src/app.ts", "export const h = { 'Set-Cookie': 'x' };"],
    ["doors/src/directory.ts", "export const f = (env: any) => env.DIRECTORY.get('c:control');"],
    ["doors/src/connect.ts", "export const f = (env: any) => env.GATEWAY.verify('a', 'c', 1, 'b');"],
    ["doors/src/egress.ts", "export const f = (env: any) => env.EGRESS.forward('a', 'c', 1, 'b', r);"],
  ])("allows, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).toEqual([]);
  });
});
