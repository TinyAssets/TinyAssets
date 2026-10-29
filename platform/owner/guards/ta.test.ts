/**
 * `ta`'s catalog ranks as pi's own `tool_search` does (`ta.ts`): pi-durable keeps its BM25
 * ranking and a tool's search text unexported, so `ta` holds a copy, and this guard runs
 * pi's real `tool_search` beside it over generated catalogs and queries (camelCase,
 * snake_case, plurals, stop words, nested schemas) and requires the same names in the same
 * order. A pi bump that changes the ranking fails here (the pi pin guard holds the
 * version meanwhile).
 *
 * And `AgentSource.trusted`, extensions installed without the package rules, cannot reach
 * production by structure: in the owner's sources only its declaration in `AgentSource`
 * and `OwnerAgent.#install`'s read name it (no key, no literal key, no other use), so
 * production's `gatewayAgent` has no way to pass one.
 */
import { BACKGROUND_CONTEXT } from "@earendil-works/chord/context";
import ts from "typescript";
import { Type } from "@earendil-works/pi-ai";
import type { ToolExecutionApi, ToolRegistration } from "@earendil-works/pi-durable";
import { createToolSearchTool } from "@earendil-works/pi-durable/tool-search";
import { describe, expect, it } from "vitest";

import { rank, searchText, tokenize } from "../src/ta";
import { parse, reporter, workerSources } from "./sources";

const TRUSTED = "trusted";
const AGENT = "owner/src/agent.ts";

/** Each naming of `trusted` in `source` past its declaration and `#install`'s read. */
export function trustedReach(path: string, source: string): string[] {
  const file = parse(path, source);
  const problems: string[] = [];
  const report = reporter(file, problems);
  const inside = (node: ts.Node, test: (up: ts.Node) => boolean): boolean => {
    for (let up = node.parent; up !== undefined; up = up.parent) {
      if (test(up)) {
        return true;
      }
    }
    return false;
  };
  const visit = (node: ts.Node): void => {
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
        && node.text === TRUSTED) {
      report(node, "names trusted by a literal");
    }
    if (ts.isIdentifier(node) && node.text === TRUSTED) {
      const declared = path === AGENT && ts.isPropertySignature(node.parent)
        && node.parent.name === node && ts.isInterfaceDeclaration(node.parent.parent)
        && node.parent.parent.name.text === "AgentSource";
      const installed = path === AGENT && ts.isPropertyAccessExpression(node.parent)
        && node.parent.name === node
        && node.parent.expression.getText(file) === "this.#source"
        && inside(node, (up) => ts.isMethodDeclaration(up) && up.name.getText(file) === "#install"
          && ts.isClassDeclaration(up.parent) && up.parent.name?.text === "OwnerAgent");
      if (!declared && !installed) {
        report(node, "names trusted past AgentSource and #install");
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return problems;
}

const WORDS = Object.freeze([
  "issue", "issues", "search", "searches", "repository", "repositories", "create", "the",
  "a", "of", "listPullRequests", "getHTTPResponse", "weather", "forecast", "city", "cities",
  "box", "boxes", "class", "glass", "mail", "send", "read", "file", "files", "calendar",
  "event", "events", "query", "page", "x2", "and", "with", "to", "MCP", "server", "github",
  "notion", "drive", "upload", "uploads", "user", "users", "status",
]);

/** A small deterministic generator (mulberry32). */
function generator(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function words(next: () => number, most: number, join = " "): string {
  return Array.from({ length: 1 + Math.floor(next() * most) },
                    () => WORDS[Math.floor(next() * WORDS.length)]!).join(join);
}

/** A tool with a generated name, description and schema (nested properties, items, anyOf). */
function tool(next: () => number, index: number): ToolRegistration {
  const name = `${words(next, 3, "_").toLowerCase()}_${index}`;
  const property = () => Type.String({ description: words(next, 4) });
  const parameters = Type.Object({
    [words(next, 1)]: property(),
    nested: Type.Object({ [words(next, 1, "")]: property() }, { description: words(next, 3) }),
    list: Type.Array(Type.Object({ inner: property() })),
    either: Type.Union([property(), Type.Number({ description: words(next, 2) })]),
  });
  return {
    name, description: words(next, 8), parameters,
    execute: async () => ({ output: [] }),
  } as ToolRegistration;
}

/** pi's `tool_search` over `candidates`, as the model would call it: the names it loads. */
async function piRanking(candidates: ToolRegistration[], query: string, limit: number):
    Promise<string[]> {
  const search = createToolSearchTool();
  const api = { agent: async () => ({ deferred: candidates }) } as unknown as ToolExecutionApi;
  const result = await search.execute({ query, limit }, api, BACKGROUND_CONTEXT);
  return (result.details as { loaded: string[] }).loaded;
}

describe("ta guard", () => {
  it("ranks every generated catalog and query exactly as pi's tool_search does", async () => {
    const next = generator(20261029);
    let compared = 0;
    let nonEmpty = 0;
    for (let round = 0; round < 300; round++) {
      const candidates = Array.from({ length: 1 + Math.floor(next() * 30) },
                                    (_, index) => tool(next, index));
      const query = words(next, 4);
      const limit = 1 + Math.floor(next() * 12);
      const ours = rank(query, candidates.map((candidate) => ({
        name: candidate.name, text: searchText(candidate) })), limit);
      expect([query, ours]).toEqual([query, await piRanking(candidates, query, limit)]);
      compared++;
      nonEmpty += ours.length > 0 ? 1 : 0;
    }
    expect(compared).toBe(300);
    expect(nonEmpty).toBeGreaterThan(200);  // the comparison is of real rankings
  });

  it("names trusted only in AgentSource and #install", () => {
    const owner = [...workerSources()].filter(([path]) => path.startsWith("owner/src/"));
    expect(owner.length).toBeGreaterThan(10);
    expect(owner.flatMap(([path, source]) => trustedReach(path, source))).toEqual([]);
    const agent = owner.find(([path]) => path === AGENT)![1];
    expect(agent).toContain("readonly trusted?: readonly Extension[];");
    expect(agent).toContain("this.#source.trusted");
  });

  it.each([
    [AGENT, "export function gatewayAgent(p, k, trusted) { return new OwnerAgent(p, " +
      "{ models, packages: k, trusted }); }"],
    [AGENT, "export function gatewayAgent(p, k) { return new OwnerAgent(p, " +
      "{ models, packages: [], trusted: k }); }"],
    [AGENT, "const s = { [\"trusted\"]: k };"],
    [AGENT, "class OwnerAgent { other() { return this.#source.trusted; } }"],
    ["owner/src/owner-do.ts", "interface AgentSource { readonly trusted?: X; }"],
  ])("sees trusted reached in %s: %s", (path, source) => {
    expect(trustedReach(path, source).length).toBeGreaterThan(0);
  });

  it("tokenizes as pi does: camelCase split, stop words dropped, plurals stemmed", () => {
    expect(tokenize("listPullRequests of the getHTTPResponse issues searches boxes glass"))
      .toEqual(["list", "pull", "request", "get", "http", "response", "issue", "search",
                "box", "glass"]);
  });
});
