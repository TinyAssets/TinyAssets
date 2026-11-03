/**
 * The sandbox Worker reaches nothing but E2B (slice S; the owner config guard's `SERVICES`
 * entry for `SANDBOX` relies on it):
 *
 * - its config binds its own class's namespace, the directory and nothing else (no `OWNER`,
 *   no service, no bucket, no queue, no route), carries `global_fetch_strictly_public` and
 *   `disable_ctx_exports`, and declares its one secret by name;
 * - in its source, `fetch` is named in `e2b.ts` alone, and every URL there is built on
 *   E2B's two origins (`E2B_API`, `E2B_ENVD`), which are E2B's own hosts; no source names
 *   `connect` (a raw socket) or another way out.
 *
 * A tripwire for our own code, read from the syntax tree, not proof against evasion.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, PLATFORM, workerSources } from "../../owner/guards/sources";
import { E2B_API, E2B_ENVD } from "../src/e2b";

const CONFIG = join(PLATFORM, "sandbox", "wrangler.jsonc");
const E2B = "sandbox/src/e2b.ts";

/** Each way a sandbox source reaches out other than through `e2b.ts` to E2B. */
export function reachProblems(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const visit = (node: ts.Node): void => {
    if (ts.isIdentifier(node) && (node.text === "fetch" || node.text === "connect")
        && fileName !== E2B && !(ts.isPropertyAccessExpression(node.parent)
                                 && node.parent.name === node && node.text === "connect")) {
      problems.push(`${fileName}: names ${node.text}`);
    }
    if (fileName === E2B && ts.isCallExpression(node)
        && node.expression.getText(file) === "this.#fetch") {
      const url = node.arguments[0];
      const first = url !== undefined && ts.isTemplateExpression(url) && url.head.text === ""
        ? url.templateSpans[0]!.expression.getText(file) : null;
      if (first !== "E2B_API" && first !== "E2B_ENVD") {
        problems.push(`${fileName}: a fetch not on E2B's origins: ${node.getText(file)}`);
      }
    }
    if (ts.isStringLiteral(node) && /^https?:\/\//.test(node.text) && node.text !== E2B_API
        && node.text !== E2B_ENVD) {
      problems.push(`${fileName}: names ${node.text}`);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

describe("the sandbox Worker", () => {
  it("binds its own class and the directory, and nothing else", () => {
    const config = ts.parseConfigFileTextToJson(CONFIG, readFileSync(CONFIG, "utf8"))
      .config as Record<string, unknown>;
    expect(Object.keys(config).sort()).toEqual([
      "$schema", "compatibility_date", "compatibility_flags", "durable_objects",
      "kv_namespaces", "main", "migrations", "name", "preview_urls", "secrets", "vars",
      "workers_dev"]);
    expect(config["durable_objects"]).toEqual({ bindings: [
      { name: "SANDBOXES", class_name: "SandboxControl" }] });
    expect(config["kv_namespaces"]).toEqual([{ binding: "DIRECTORY",
                                               id: "not-provisioned-directory" }]);
    expect(config["secrets"]).toEqual({ required: ["E2B_API_KEY"] });
    expect(config["compatibility_flags"]).toEqual(expect.arrayContaining([
      "global_fetch_strictly_public", "disable_ctx_exports"]));
    expect([config["workers_dev"], config["preview_urls"]]).toEqual([false, false]);
  });

  it("reaches E2B's own hosts alone", () => {
    expect([new URL(E2B_API).host, new URL(E2B_ENVD).host]).toEqual(["api.e2b.app",
                                                                     "sandbox.e2b.app"]);
    const problems = [...workerSources()].filter(([path]) => path.startsWith("sandbox/src/"))
      .flatMap(([path, source]) => reachProblems(path, source));
    expect(problems).toEqual([]);
  });

  it("finds a fetch outside e2b.ts, and a URL off E2B's origins", () => {
    expect(reachProblems("sandbox/src/control.ts", "await fetch('https://x.test');"))
      .toHaveLength(2);
    expect(reachProblems(E2B, "class A { m() { this.#fetch(`${OTHER}/v2`); } }"))
      .toHaveLength(1);
    expect(reachProblems(E2B, "class A { m() { this.#fetch(`${E2B_API}/v2`); } }"))
      .toEqual([]);
  });
});
