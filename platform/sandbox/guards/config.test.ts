/**
 * The sandbox Worker reaches nothing but E2B (slice S; the owner config guard's `SERVICES`
 * entry for `SANDBOX` relies on it):
 *
 * - its config binds its own two classes' namespaces, the control record's namespace and the
 *   ring queue's producer, and nothing else (no `OWNER`, no service, no bucket, no consumer,
 *   no route), carries `global_fetch_strictly_public` and `disable_ctx_exports`, and
 *   declares its one secret by name;
 * - the runner's copies of the object's numbers (its bundle takes nothing of the object's
 *   storage modules) are the object's own: the lease's TTL and the sync bounds;
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
import { HEARTBEAT_MS, LEASE_TTL_MS } from "../runner/runner";
import { KILL_WITHIN_MS } from "../runner/users";
import { CHUNK_BYTES, COMMIT_OPS, FEED_LIMIT, INLINE_BYTES, INTENT_ID_CHARS, SYNC_BODY }
  from "../runner/sync";
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
  it("binds its own classes, the control record and the ring's producer, and nothing else",
     () => {
    const config = ts.parseConfigFileTextToJson(CONFIG, readFileSync(CONFIG, "utf8"))
      .config as Record<string, unknown>;
    expect(Object.keys(config).sort()).toEqual([
      "$schema", "build", "compatibility_date", "compatibility_flags", "durable_objects",
      "kv_namespaces", "main", "migrations", "name", "preview_urls", "queues", "rules",
      "secrets", "vars", "workers_dev"]);
    // Its build makes the runner it ships, and the runner alone is imported as data.
    expect(config["build"]).toEqual({ command: "node template/bundle.mjs dist" });
    expect(config["rules"]).toEqual([{ type: "Data", globs: ["**/dist/runner.mjs"],
                                       fallthrough: false }]);
    expect(config["durable_objects"]).toEqual({ bindings: [
      { name: "SANDBOXES", class_name: "SandboxControl" },
      { name: "SLOTS", class_name: "SandboxSlots" }] });
    expect(config["kv_namespaces"]).toEqual([{ binding: "CONTROL",
                                               id: "not-provisioned-control" }]);
    expect(config["queues"]).toEqual({ producers: [{ binding: "RING",
                                                     queue: "tinyassets-sandbox-ring" }] });
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

  it("holds the runner's copies of the object's numbers to the object's", () => {
    // Read from the object's source: its modules load only in workerd.
    const files = readFileSync(join(PLATFORM, "owner", "src", "files.ts"), "utf8");
    const manifest = readFileSync(join(PLATFORM, "owner", "src", "manifest.ts"), "utf8");
    const number = (source: string, name: string) => {
      const found = new RegExp(`export const ${name} = ([0-9_ *]+);`).exec(source);
      expect(found, name).not.toBeNull();
      return found![1]!.replaceAll("_", "").split("*")
        .reduce((product, factor) => product * Number(factor.trim()), 1);
    };
    expect(LEASE_TTL_MS).toBe(number(files, "LEASE_TTL_S") * 1000);
    expect(KILL_WITHIN_MS).toBe(HEARTBEAT_MS);
    const egress = readFileSync(join(PLATFORM, "doors", "src", "egress.ts"), "utf8");
    expect([CHUNK_BYTES, INLINE_BYTES, COMMIT_OPS, FEED_LIMIT, SYNC_BODY]).toEqual([
      number(manifest, "CHUNK_BYTES"), number(files, "INLINE_BYTES"),
      number(files, "MAX_SYNC_OPS"), number(files, "MAX_LIST"),
      number(egress, "SYNC_BODY_MAX")]);
    expect(INTENT_ID_CHARS).toBe(2 * number(files, "INTENT_ID_BYTES"));
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
