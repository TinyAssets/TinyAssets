/**
 * The binder is the only binder (research checklist 1, 6, 17).
 *
 * - `getByName` appears only in `doors/src/binder.ts`, across every Worker's source, but
 *   for the owner's object naming its own thread stores (`env.PISTORE.getByName`, in
 *   `owner-do.ts` alone, by the random ids its rows hold), and no source names another
 *   way to an object id (`idFromName`, `idFromString`, `newUniqueId`).
 * - A `Binding` is built only there: no other doors source writes an object literal with
 *   a `caller`.
 * - Only `session.ts` reads the `Cookie` header, and only `directory.ts` touches the
 *   directory, whose keys hold no cookie or session hash.
 * - Only `connect.ts` reaches the gateway (`GATEWAY`), so a pasted key or an OAuth code
 *   has one path, to the gateway's connect entrypoint.
 * - Consent is never remembered: no source names `remember` or `isConsentRemembered`.
 *
 * A tripwire for our own code, read from the syntax tree, not proof against deliberate
 * evasion.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, reporter, workerSources } from "../../owner/guards/sources";

const BINDER = "doors/src/binder.ts";
/** The owner's object, which names its own thread stores and nothing else. */
const OWNER_DO = "owner/src/owner-do.ts";
const SESSION = "doors/src/session.ts";
const DIRECTORY = "doors/src/directory.ts";
const CONNECT = "doors/src/connect.ts";
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
    if (name === "getByName" && fileName !== BINDER && !(fileName === OWNER_DO
        && ts.isPropertyAccessExpression(node.parent) && node.parent.name === node
        && node.parent.expression.getText(file) === "env.PISTORE")) {
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
    ["owner/src/agent.ts", "export const f = (env: any) => env.PISTORE.getByName('x');"],
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
  ])("sees, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).not.toEqual([]);
  });

  it.each([
    ["doors/src/binder.ts", "export const f = (ns: any) => ns.getByName('alice');"],
    [OWNER_DO, "export const f = (env: any, name: string) => env.PISTORE.getByName(name);"],
    ["doors/src/binder.ts", "export const b = { owner: 'a', caller: 'a', origin: null };"],
    ["doors/src/session.ts", "export const f = (r: Request) => r.headers.get('Cookie');"],
    ["doors/src/app.ts", "export const h = { 'Set-Cookie': 'x' };"],
    ["doors/src/directory.ts", "export const f = (env: any) => env.DIRECTORY.get('c:control');"],
    ["doors/src/connect.ts", "export const f = (env: any) => env.GATEWAY.verify('a', 'c', 1, 'b');"],
  ])("allows, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).toEqual([]);
  });
});
