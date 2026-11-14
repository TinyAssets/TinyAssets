/**
 * The binder is the only binder (research checklist 1, 6, 17).
 *
 * - `getByName` appears only in `doors/src/binder.ts`, across every Worker's source, but
 *   where a Worker names its own classes' objects, each place stated in its own rules
 *   (`OWN_OBJECTS`, its `guards/rules.ts`: a source and the namespaces it names), and no
 *   source names another way to an object id (`idFromName`, `idFromString`, `newUniqueId`).
 *   The owner's object names no object at all.
 * - A `Binding` is built only there: no other doors source writes an object literal with
 *   a `caller`.
 * - Only `session.ts` reads the `Cookie` header, and only `directory.ts` touches the
 *   directory, whose keys hold no cookie or session hash.
 * - Only `connect.ts` reaches the gateway (`GATEWAY`), so a pasted key or an OAuth code
 *   has one path, to the gateway's connect entrypoint; only `egress.ts` reaches its egress
 *   entrypoint (`EGRESS`), so a blob the owner's object handed out is forwarded on one path;
 *   only `file-bytes.ts` reaches the files Worker (`FILES`), the file door's bytes (an
 *   upload's, and the cutover import's); and only `import/` reaches the import window's
 *   bucket and queue (`IMPORT_STAGING`, `IMPORTS`).
 * - Consent is never remembered: no source names `remember` or `isConsentRemembered`.
 *
 * A tripwire for our own code, read from the syntax tree, not proof against deliberate
 * evasion.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { ruleLists } from "../../ci/kit/owed";
import { envFile, parse, reporter, workerSources } from "../../ci/kit/sources";

const BINDER = "doors/src/binder.ts";
/** The doors' generated env, which declares every binding and reaches none. */
const ENV = envFile("doors");
/** The owner's object, which names no object. */
const OWNER_DO = "owner/src/owner-do.ts";
/**
 * Where each Worker names its own classes' objects, by source, as its rules state it, and
 * nothing else: what a source names `getByName` on there.
 */
const OWN_OBJECTS: ReadonlyMap<string, readonly string[]> = new Map([
  ...(await ruleLists<Readonly<Record<string, readonly string[]>>>("OWN_OBJECTS")).entries()]
  .flatMap(([workspace, own]) => Object.entries(own)
    .filter(([path]) => path.startsWith(`${workspace}/src/`))));
/** The codemode Worker's entry, which names its own class's objects and nothing else. */
const CODEMODE_ENTRY = "codemode/src/index.ts";
const SESSION = "doors/src/session.ts";
const DIRECTORY = "doors/src/directory.ts";
const CONNECT = "doors/src/connect.ts";
const EGRESS = "doors/src/egress.ts";
const FILE_BYTES = "doors/src/file-bytes.ts";
const IMPORT = "doors/src/import/";
const IMPORT_WINDOW = new Set(["IMPORT_STAGING", "IMPORTS"]);
/**
 * The generated queue names (`contract/src/queue-names.ts`), keyed by each queue's producer
 * binding: a name read there (`QUEUES.IMPORTS`, the doors' queue dispatch) reaches no binding.
 */
const QUEUE_NAMES = "QUEUES";
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
        && !(on !== null && (OWN_OBJECTS.get(fileName) ?? []).includes(on))) {
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
        && fileName !== ENV) {
      report(node, "touches the directory outside directory.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "GATEWAY" && fileName !== CONNECT
        && fileName !== ENV) {
      report(node, "reaches the gateway outside connect.ts");
    }
    if (doors && ts.isIdentifier(node) && node.text === "FILES" && fileName !== FILE_BYTES
        && fileName !== ENV) {
      report(node, "reaches the files Worker outside file-bytes.ts");
    }
    if (doors && ts.isIdentifier(node) && IMPORT_WINDOW.has(node.text)
        && !fileName.startsWith(IMPORT) && fileName !== ENV
        && !(ts.isPropertyAccessExpression(node.parent) && node.parent.name === node
             && node.parent.expression.getText(file) === QUEUE_NAMES)) {
      report(node, `reaches the import window's ${node.text} outside import/`);
    }
    if (doors && ts.isIdentifier(node) && node.text === "EGRESS" && fileName !== EGRESS
        && fileName !== ENV) {
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

  it("takes each Worker's own objects from its own rules, for its own sources alone", () => {
    expect([...OWN_OBJECTS]).toEqual([
      [CODEMODE_ENTRY, ["this.env.CODEMODE"]],
      ["sandbox/src/index.ts", ["this.env.SANDBOXES", "this.env.SLOTS"]],
      ["sandbox/src/control.ts", ["this.env.SANDBOXES", "this.env.SLOTS"]]]);
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
    ["doors/src/ring.ts", "export const f = (env: any) => env.IMPORT_STAGING.get('k');"],
    ["doors/src/erase.ts", "export const f = (env: any) => env.IMPORTS.send({});"],
    ["doors/src/index.ts", "export const f = (env: any) => env.QUEUES.IMPORTS.send({});"],
    ["doors/src/import/index.ts", "export const f = (env: any) => env.FILES.putChunks('n', 'k', []);"],
    [CODEMODE_ENTRY, "export const f = (env: any) => env.OWNER.getByName('bob');"],
    [CODEMODE_ENTRY, "export const f = (env: any) => env.RUNNER.getByName('bob');"],
    [CODEMODE_ENTRY, "class H { env: any; f(n: string) { return this.env.RUNNER.getByName(n); } }"],
    ["codemode/src/codemode.ts", "export const f = (o: any) => o.env.CODEMODE.getByName('bob');"],
    // Another Worker's own objects, or a Worker's own from a source its rules do not name.
    [CODEMODE_ENTRY, "class H { env: any; f(n: string) { return this.env.SLOTS.getByName(n); } }"],
    ["sandbox/src/slots.ts",
     "class H { env: any; f(n: string) { return this.env.SLOTS.getByName(n); } }"],
  ])("sees, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).not.toEqual([]);
  });

  it.each([
    ["doors/src/binder.ts", "export const f = (ns: any) => ns.getByName('alice');"],
    [CODEMODE_ENTRY,
     "class H { env: any; f(n: string) { return this.env.CODEMODE.getByName(n); } }"],
    ["doors/src/binder.ts", "export const b = { owner: 'a', caller: 'a', origin: null };"],
    ["doors/src/session.ts", "export const f = (r: Request) => r.headers.get('Cookie');"],
    ["doors/src/app.ts", "export const h = { 'Set-Cookie': 'x' };"],
    ["doors/src/directory.ts", "export const f = (env: any) => env.DIRECTORY.get('c:control');"],
    ["doors/src/connect.ts", "export const f = (env: any) => env.GATEWAY.verify('a', 'c', 1, 'b');"],
    ["doors/src/egress.ts", "export const f = (env: any) => env.EGRESS.forward('a', 'c', 1, 'b', r);"],
    ["doors/src/import/index.ts", "export const f = (env: any) => env.IMPORT_STAGING.get('k');"],
    ["doors/src/index.ts", "export const f = (b: any) => b.queue === QUEUES.IMPORTS;"],
  ])("allows, in %s, %s", (fileName, source) => {
    expect(binderProblems(fileName, source)).toEqual([]);
  });
});
