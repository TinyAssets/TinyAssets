/**
 * Every large allocation is held under the isolate's memory grant (`owner/src/memory.ts`;
 * memory_research.md §4, "the CI guard" 2). In the owner's, the doors' and the files
 * Worker's sources, each site that can hold a request-sized value — a body read whole
 * (`.arrayBuffer()`, `.text()`, `.json()`, `.blob()`), a byte array of a size not written in
 * the source (`new Uint8Array(n)`), a result made into JSON text (`JSON.stringify` of a
 * tool's answer), a placed body or file read from R2 (`.fetch(` on `#bodies` or `#files`,
 * `getChunks`) — sits in a function that takes a grant (`granted(`, `grant(`, `answered(`),
 * or is listed in `BOUNDED` with the bound that keeps it small or the slice that owes its
 * grant. A new site fails here until it takes a grant or names its bound.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { parse, workerSources } from "./sources";

const ROOTS = ["owner/src/", "doors/src/", "files/src/"];
const GRANTS = /\b(?:granted|grant|answered)\(/;

/** A site's kind, or null when the node is none. */
function siteOf(node: ts.Node, file: ts.SourceFile): string | null {
  if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
    const name = node.expression.name.text;
    const target = node.expression.expression.getText(file);
    if (["arrayBuffer", "text", "json", "blob"].includes(name) && node.arguments.length === 0
        && /(?:request|req|response|res|body|object|got|answer|upstream|r2|stored)\b/i
          .test(target)) {
      return `.${name}()`;
    }
    if (name === "stringify" && target === "JSON"
        && /\b(?:result|answer|value)\b/.test(node.arguments[0]?.getText(file) ?? "")) {
      return "JSON.stringify";
    }
    if (name === "fetch" && /#(?:bodies|files)$/.test(target)) {
      return `${target.replace(/^this\./, "")}.fetch`;
    }
    if (name === "getChunks") {
      return "getChunks";
    }
  }
  if (ts.isNewExpression(node) && node.expression.getText(file) === "Uint8Array"
      && node.arguments?.length === 1) {
    const size = node.arguments[0]!;
    if (!ts.isNumericLiteral(size) && !ts.isIdentifier(size)
        || ts.isIdentifier(size) && !/^[A-Z_0-9]+$/.test(size.text)) {
      return "new Uint8Array(n)";
    }
  }
  return null;
}

/** The names of the functions around `node`, innermost first, and whether one grants. */
function around(node: ts.Node, file: ts.SourceFile): { name: string; granted: boolean } {
  const names: string[] = [];
  let granted = false;
  for (let at: ts.Node | undefined = node.parent; at !== undefined; at = at.parent) {
    if (ts.isFunctionDeclaration(at) || ts.isMethodDeclaration(at) || ts.isArrowFunction(at)
        || ts.isFunctionExpression(at) || ts.isConstructorDeclaration(at)
        || ts.isGetAccessorDeclaration(at)) {
      if (GRANTS.test(at.getText(file))) {
        granted = true;
      }
      const name = ts.isArrowFunction(at) || ts.isFunctionExpression(at)
        ? (ts.isVariableDeclaration(at.parent) ? at.parent.name.getText(file) : null)
        : at.name?.getText(file) ?? "constructor";
      if (name !== null) {
        names.push(name);
      }
    }
  }
  return { name: names.at(0) ?? "<module>", granted };
}

/** Every ungranted site, as `path#function: kind`. */
export function ungranted(sources: ReadonlyMap<string, string>): string[] {
  const found: string[] = [];
  for (const [path, source] of sources) {
    if (!ROOTS.some((root) => path.startsWith(root)) || !path.endsWith(".ts")) {
      continue;
    }
    const file = parse(path, source);
    const visit = (node: ts.Node): void => {
      const kind = siteOf(node, file);
      if (kind !== null) {
        const where = around(node, file);
        if (!where.granted) {
          found.push(`${path}#${where.name}: ${kind}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  return [...new Set(found)].sort();
}

/**
 * The sites outside a grant, each with what bounds it. "Owed" names what a later slice
 * grants (memory_research.md §4's table); every other entry names its bound.
 */
const BOUNDED: Readonly<Record<string, string>> = Object.freeze({
  "doors/src/answers.ts#resultText: JSON.stringify":
    "a result held to RESULT_CHARS, measured first; run inside its request's grant (`answered`)",
  "doors/src/api-keys.ts#secretHash: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "doors/src/authorize.ts#s256: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "doors/src/binder.ts#sha256: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "doors/src/body.ts#boundedBytes: new Uint8Array(n)":
    "a body held to its door's ceiling; the tool doors read it inside their request's grant (`answered`), the inbound door owes one with its slice",
  "doors/src/e2b-hooks.ts#cappedText: new Uint8Array(n)":
    "HOOK_BODY_MAX, a sandbox hook's few KiB",
  "doors/src/egress.ts#decodedChunks: new Uint8Array(n)":
    "owed: the egress door's events and bodies (EGRESS_BODY_MAX, EVENTS_BODY_MAX) under a grant, with the doors' egress paths",
  "doors/src/egress.ts#decodedOps: new Uint8Array(n)":
    "owed: the egress door's events and bodies (EGRESS_BODY_MAX, EVENTS_BODY_MAX) under a grant, with the doors' egress paths",
  "doors/src/egress.ts#serveEgress: .text()":
    "owed: the egress door's events and bodies (EGRESS_BODY_MAX, EVENTS_BODY_MAX) under a grant, with the doors' egress paths",
  "doors/src/egress.ts#spendOf: .text()":
    "owed: the egress door's events and bodies (EGRESS_BODY_MAX, EVENTS_BODY_MAX) under a grant, with the doors' egress paths",
  "doors/src/file-bytes.ts#exactly: new Uint8Array(n)":
    "one upload chunk streamed to the files Worker: CHUNK_BYTES; owed: the upload's grant, with STORE",
  "doors/src/oidc.ts#bounded: new Uint8Array(n)":
    "an identity provider's discovery or token answer, held to its own small bound",
  "doors/src/oidc.ts#challengeOf: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "doors/src/session.ts#flowDigest: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "files/src/cipher.ts#open: new Uint8Array(n)":
    "owed: the files Worker's own grant (memory_research.md §4: shared by every owner), with the files slice",
  "files/src/cipher.ts#seal: new Uint8Array(n)":
    "owed: the files Worker's own grant (memory_research.md §4: shared by every owner), with the files slice",
  "files/src/datakey.ts#chunkName: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "files/src/datakey.ts#rewrap: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "files/src/datakey.ts#wrap: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "files/src/index.ts##plain: .arrayBuffer()":
    "owed: the files Worker's own grant (memory_research.md §4: shared by every owner), with the files slice",
  "files/src/index.ts##plain: new Uint8Array(n)":
    "owed: the files Worker's own grant (memory_research.md §4: shared by every owner), with the files slice",
  "files/src/index.ts#getManifestRoot: .text()":
    "a manifest root: a few KiB of keys",
  "files/src/index.ts#hex: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "files/src/index.ts#rewrapManifestRoot: .text()":
    "a manifest root: a few KiB of keys",
  "owner/src/bodies.ts##window: #files.fetch":
    "a window of at most one page (`PAGE_BYTES`), under its caller's READ_GRANT",
  "owner/src/bodies.ts##window: new Uint8Array(n)":
    "a window of at most one page (`PAGE_BYTES`), under its caller's READ_GRANT",
  "owner/src/export.ts#readPage: JSON.stringify":
    "owed: export pages (BATCH_BYTES a call) under a grant, with the export's own slice",
  "owner/src/files.ts##content: new Uint8Array(n)":
    "an inline file: at most INLINE_BYTES",
  "owner/src/files.ts##known: new Uint8Array(n)":
    "an inline file: at most INLINE_BYTES",
  "owner/src/files.ts#fetch: getChunks":
    "at most two chunks a call (BATCH_BYTES); each caller holds its own grant or names its bound here",
  "owner/src/files.ts#inlineOf: new Uint8Array(n)":
    "an inline file: at most INLINE_BYTES",
  "owner/src/files.ts#scan: new Uint8Array(n)":
    "inline files of one search page: SEARCH_SCAN entries of at most INLINE_BYTES",
  "owner/src/grants.ts#scopes: JSON.stringify":
    "a grant's scopes: at most GRANT_LIMITS.maxScopesBytes",
  "owner/src/ledger.ts#token: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "owner/src/owner-do.ts##mailPrompt: #bodies.fetch":
    "Bodies.fetch takes its own grant (wholeGrant) of the body it reads",
  "owner/src/owner-do.ts##readAgent: #files.fetch":
    "an agent's definition files: owed: their grant, with the agents' slice",
  "owner/src/owner-do.ts##readAgent: new Uint8Array(n)":
    "an agent's definition files: owed: their grant, with the agents' slice",
  "owner/src/owner-do.ts##runsPass: #bodies.fetch":
    "Bodies.fetch takes its own grant (wholeGrant) of the body it reads",
  "owner/src/owner-do.ts##window: #files.fetch":
    "a file_read or fetch window of at most one page (`PAGE_BYTES`), under its caller's READ_GRANT",
  "owner/src/owner-do.ts##window: new Uint8Array(n)":
    "a file_read or fetch window of at most one page (`PAGE_BYTES`), under its caller's READ_GRANT",
  "owner/src/owner-do.ts#exportChunks: #files.fetch":
    "owed: export pages (BATCH_BYTES a call) under a grant, with the export's own slice",
  "owner/src/owner-do.ts#fileAppend: #files.fetch":
    "owed: file writes' grants (file_write, file_append, file_edit: at most WHOLE_BYTES), with STORE",
  "owner/src/owner-do.ts#fileAppend: new Uint8Array(n)":
    "owed: file writes' grants (file_write, file_append, file_edit: at most WHOLE_BYTES), with STORE",
  "owner/src/owner-do.ts#fileEdit: #files.fetch":
    "owed: file writes' grants (file_write, file_append, file_edit: at most WHOLE_BYTES), with STORE",
  "owner/src/owner-do.ts#fileEdit: new Uint8Array(n)":
    "owed: file writes' grants (file_write, file_append, file_edit: at most WHOLE_BYTES), with STORE",
  "owner/src/owner-do.ts#fileReadChunks: #files.fetch":
    "the sync agent's chunk reads: BATCH_BYTES a call; owed: their grant, with STORE",
  "owner/src/policy.ts#canonical: JSON.stringify":
    "the owner's policy: at most its document's own bound",
  "owner/src/registrations.ts#token: new Uint8Array(n)":
    "a digest or key: a fixed few bytes",
  "owner/src/run-events.ts#textOf: JSON.stringify":
    "one events frame: at most FRAME_UNITS_MAX, inside the relay's reservation",
  "owner/src/starter.ts##bytes: new Uint8Array(n)":
    "the starter package's files: the bundle's own few hundred KiB",
  "owner/src/sync-ignore.ts##edge: new Uint8Array(n)":
    "a sync-ignore pattern: at most its file's INLINE_BYTES",
  "owner/src/sync-ignore.ts#utf8: new Uint8Array(n)":
    "a sync-ignore pattern or path: at most MAX_PATH_BYTES",
  "owner/src/wake-kinds.ts#canonicalJson: JSON.stringify":
    "a wake subscription's arguments: its own small bound",
});

describe("the memory scan", () => {
  it("finds every large allocation under a grant or named with its bound", () => {
    const found = ungranted(workerSources());
    const unnamed = found.filter((site) => !(site in BOUNDED));
    const stale = Object.keys(BOUNDED).filter((site) => !found.includes(site));
    expect(unnamed).toEqual([]);
    expect(stale).toEqual([]);
  });

  it("sees a new site, and passes one under a grant", () => {
    const site = "export async function f(request: Request) { return await request.text(); }";
    expect(ungranted(new Map([["doors/src/new.ts", site]])))
      .toEqual(["doors/src/new.ts#f: .text()"]);
    const held = "export async function f(request: Request) {\n" +
      "  return granted(1, 'x', () => request.text());\n}";
    expect(ungranted(new Map([["doors/src/new.ts", held]]))).toEqual([]);
  });
});
