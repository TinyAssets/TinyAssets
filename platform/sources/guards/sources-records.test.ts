/**
 * The catalog finds every record file: `sources/src/records.ts`, which a Worker's bundle
 * reads in place of a folder, is the list `sources/write-records.ts` makes from the folder
 * (one of `npm run generate`'s generators), so a record file needs no hand-added import.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { bundledRecords } from "../src/catalog";
import { recordsText, SOURCES } from "../write-records";

describe("the catalog's record files", () => {
  it("are every record file, in name order, as each holds it", () => {
    const text = recordsText();
    const files = [...text.matchAll(/^import \w+ from "\.\.\/([^"]+)";$/gm)].map((m) => m[1]!);
    expect(files.length).toBeGreaterThan(0);
    expect(bundledRecords()).toEqual(files.map((file) =>
      JSON.parse(readFileSync(join(SOURCES, file), "utf8")) as unknown));
  });

  it("would list a record file added to the folder, and no other file", () => {
    const dir = mkdtempSync(join(tmpdir(), "ta-sources-"));
    try {
      writeFileSync(join(dir, "anthropic.json"), "{}");
      writeFileSync(join(dir, "9-new_source.v2.json"), "{}");
      writeFileSync(join(dir, "notes.md"), "");
      writeFileSync(join(dir, "package.json"), "{}");
      const text = recordsText(dir);
      expect(text).toContain('import _9NewSourceV2 from "../9-new_source.v2.json";');
      expect(text).toContain('import anthropic from "../anthropic.json";');
      expect(text).toMatch(/\[\n {2}_9NewSourceV2,\n {2}anthropic,\n\]\);/);
      expect(text).not.toContain("notes");
      expect(text).not.toContain("package");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
