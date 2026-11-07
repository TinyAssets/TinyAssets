// The catalog's list of record files (`src/records.ts`), written from this folder: every
// `*.json` beside this script is a record (`build.py` and the template guard read the same
// files), imported in name order. A Worker's bundle has no filesystem, so the list is code;
// the sources guard (`contract/guards/sources-records.test.ts`) fails while it is not the
// folder's, so a record added without it is never left out.
//
//   node platform/sources/write-records.ts
import { readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/** This folder: the records. */
export const SOURCES = dirname(fileURLToPath(import.meta.url));
/** The list the catalog reads. */
export const RECORDS_FILE = join(SOURCES, "src", "records.ts");

/** A record file's binding: its name in camel case (`claude-code.json` is `claudeCode`). */
function binding(file: string): string {
  const name = file.slice(0, -".json".length)
    .replace(/[-_.]+([a-z0-9])/g, (_match, next: string) => next.toUpperCase());
  return /^[0-9]/.test(name) ? `_${name}` : name;
}

/** `src/records.ts` as the record files in `dir` make it. */
export function recordsText(dir: string = SOURCES): string {
  const files = readdirSync(dir).filter((name) => name.endsWith(".json")).sort();
  return [
    "// Written by `node platform/sources/write-records.ts` from the record files beside",
    "// `src/`, and held to them by `contract/guards/sources-records.test.ts`: never by hand.",
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
