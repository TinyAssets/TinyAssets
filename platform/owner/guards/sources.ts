import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

/** The platform workspace root (`platform/`). */
export const PLATFORM = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Every TypeScript source under each Worker project's `src/`, keyed by path from `platform/`. */
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
      if (entry.endsWith(".ts")) {
        const path = join(src, entry);
        found.set(relative(root, path).split("\\").join("/"), readFileSync(path, "utf8"));
      }
    }
  }
  return found;
}
