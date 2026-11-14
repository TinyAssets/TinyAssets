// The catalog's list of record files (`src/records.ts`), written from this folder: every
// `*.json` beside this script but the workspace's own manifest (`package.json`) is a record,
// imported in name order. A Worker's bundle has no
// filesystem, so the list is code. One of `npm run generate`'s generators, which
// `ci/guards/generated.test.ts` holds current, so a record added is never left out.
import { readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** This folder: the records. */
export const SOURCES = dirname(fileURLToPath(import.meta.url));
/** The list the catalog reads. */
const RECORDS_FILE = join(SOURCES, "src", "records.ts");

/** A record file's binding: its name in camel case (`claude-code.json` is `claudeCode`). */
function binding(file: string): string {
  const name = file.slice(0, -".json".length)
    .replace(/[-_.]+([a-z0-9])/g, (_match, next: string) => next.toUpperCase());
  return /^[0-9]/.test(name) ? `_${name}` : name;
}

/** The record files in `dir`, in name order: its JSON files, the workspace's manifest not one. */
export function recordFiles(dir: string = SOURCES): string[] {
  return readdirSync(dir).filter((name) => name.endsWith(".json") && name !== "package.json")
    .sort();
}

/** `src/records.ts` as the record files in `dir` make it. */
export function recordsText(dir: string = SOURCES): string {
  const files = recordFiles(dir);
  return [
    "// Written by `npm run generate` (`write-records.ts`) from the record files beside",
    "// `src/`: never by hand.",
    ...files.map((file) => `import ${binding(file)} from "../${file}";`),
    "",
    "/**",
    " * Every record file in `platform/sources/`, as it holds it. Never written: each read is",
    " * a copy (`sourceRecord`).",
    " */",
    "export const RECORDS: readonly unknown[] = Object.freeze([",
    ...files.map((file) => `  ${binding(file)},`),
    "]);",
    "",
  ].join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  writeFileSync(RECORDS_FILE, recordsText());
}
