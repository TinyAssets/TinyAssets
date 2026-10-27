import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

/** The platform workspace root (`platform/`). */
export const PLATFORM = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Every extension a Worker's bundler would take as script, and how to parse it. */
const KINDS: Readonly<Record<string, ts.ScriptKind>> = Object.freeze({
  ".ts": ts.ScriptKind.TS, ".mts": ts.ScriptKind.TS, ".cts": ts.ScriptKind.TS,
  ".tsx": ts.ScriptKind.TSX, ".js": ts.ScriptKind.JS, ".mjs": ts.ScriptKind.JS,
  ".cjs": ts.ScriptKind.JS, ".jsx": ts.ScriptKind.JSX,
});

/** Every script under each Worker project's `src/`, keyed by path from `platform/`. */
export function workerSources(root: string = PLATFORM): Map<string, string> {
  const found = new Map<string, string>();
  for (const project of readdirSync(root, { withFileTypes: true })) {
    if (!project.isDirectory() || project.name === "node_modules" || project.name.startsWith(".")) {
      continue;
    }
    const src = join(root, project.name, "src");
    let entries: string[];
    try {
      entries = readdirSync(src, { recursive: true, encoding: "utf8" });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (extname(entry) in KINDS) {
        const path = join(src, entry);
        found.set(relative(root, path).split("\\").join("/"), readFileSync(path, "utf8"));
      }
    }
  }
  return found;
}

/** `source` parsed as its extension says, with parent links. */
export function parse(fileName: string, source: string): ts.SourceFile {
  return ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true,
                             KINDS[extname(fileName)] ?? ts.ScriptKind.TS);
}

/** Each problem as `file:line: what`. */
export function reporter(file: ts.SourceFile, problems: string[]) {
  return (node: ts.Node, what: string) => {
    const line = file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
    problems.push(`${file.fileName}:${line}: ${what}`);
  };
}
