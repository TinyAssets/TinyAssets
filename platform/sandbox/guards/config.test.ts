/**
 * The sandbox Worker reaches nothing but E2B (slice S; the owner config guard's `SERVICES`
 * entry for `SANDBOX` relies on it):
 *
 * - its config holds to its allow-list (`rules.ts`), by the platform's config checker;
 * - in its source and the E2B client beside it (`e2b/`, whose transport the Worker ships),
 *   `fetch` is named in `e2b/transport.ts` alone, and every URL there is built on E2B's two
 *   origins (`E2B_API`, `E2B_ENVD`), which are E2B's own hosts; no source names `connect`
 *   (a raw socket) or another way out.
 *
 * A tripwire for our own code, read from the syntax tree, not proof against evasion.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { configTests } from "../../ci/kit/config";
import { parse, sourcesOf } from "../../ci/kit/sources";
import { PLATFORM } from "../../ci/paths";
import { E2B_API, E2B_ENVD } from "../e2b/transport";

import { CONFIG } from "./rules";

const E2B = "sandbox/e2b/transport.ts";

/** The Worker's source and the E2B client's, by path from `platform/`. */
function sources(): Map<string, string> {
  const client = readdirSync(join(PLATFORM, "sandbox", "e2b"))
    .map((name) => [`sandbox/e2b/${name}`, readFileSync(join(PLATFORM, "sandbox", "e2b", name),
                                                         "utf8")] as const);
  return new Map([...sourcesOf("sandbox"), ...client]);
}

/** Each way a sandbox source reaches out other than through the transport to E2B. */
export function reachProblems(fileName: string, source: string): string[] {
  const file = parse(fileName, source);
  const problems: string[] = [];
  const visit = (node: ts.Node): void => {
    // A method named `connect` (E2B's resume), or a call of one, is no socket.
    if (ts.isIdentifier(node) && (node.text === "fetch" || node.text === "connect")
        && fileName !== E2B && !((ts.isPropertyAccessExpression(node.parent)
                                  || ts.isMethodDeclaration(node.parent))
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

configTests(CONFIG, {
  refused: ["routes", "route", "services", "r2_buckets", "d1_databases", "env", "unsafe",
            "worker_loaders", "tail_consumers", "dispatch_namespaces"],
  probes: [
    ["the owner's class", { durable_objects: { bindings: [
      { name: "SANDBOXES", class_name: "SandboxControl" },
      { name: "SLOTS", class_name: "SandboxSlots" },
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a second KV namespace", { kv_namespaces: [{ binding: "CONTROL", id: "not-provisioned-control" },
                                                { binding: "OAUTH_KV", id: "not-provisioned-oauth" }] }],
    ["a queue consumer", { queues: { producers: [{ binding: "RING",
                                                   queue: "tinyassets-sandbox-ring" }],
                                     consumers: [{ queue: "tinyassets-sandbox-ring" }] } }],
    ["no ring", { queues: undefined }],
    ["another var", { vars: { E2B_TEMPLATE: "t", OWNER: "alice" } }],
    ["another secret", { secrets: { required: ["E2B_API_KEY", "ROUTING_MAC_KEY"] } }],
    ["another build", { build: { command: "node other.mjs" } }],
    ["the runner not as data", { rules: undefined }],
    ["no strictly-public fetch", { compatibility_flags: ["nodejs_compat", "disable_ctx_exports"] }],
    ["a workers.dev URL", { workers_dev: true }],
  ],
});

describe("the sandbox Worker", () => {
  it("reaches E2B's own hosts alone", () => {
    expect([new URL(E2B_API).host, new URL(E2B_ENVD).host]).toEqual(["api.e2b.app",
                                                                     "sandbox.e2b.app"]);
    expect([...sources().keys()]).toContain(E2B);
    const problems = [...sources()]
      .flatMap(([path, source]) => reachProblems(path, source));
    expect(problems).toEqual([]);
  });

  it("finds a fetch outside the transport, and a URL off E2B's origins", () => {
    expect(reachProblems("sandbox/src/control.ts", "await fetch('https://x.test');"))
      .toHaveLength(2);
    expect(reachProblems("sandbox/src/e2b.ts", "class A { connect() {} }\nconnect(1);"))
      .toEqual(["sandbox/src/e2b.ts: names connect"]);
    expect(reachProblems(E2B, "class A { m() { this.#fetch(`${OTHER}/v2`); } }"))
      .toHaveLength(1);
    expect(reachProblems(E2B, "class A { m() { this.#fetch(`${E2B_API}/v2`); } }"))
      .toEqual([]);
  });
});
