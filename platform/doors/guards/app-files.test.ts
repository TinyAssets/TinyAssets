/**
 * The owner app's files (`app/`) are exactly what `src/pages.ts` serves, name no other
 * origin, and use no HTML string sink or dynamic evaluation: the pages' CSP refuses those
 * anyway, and this holds the source to it.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
const APP = join(DOORS, "app");

describe("the owner app's files", () => {
  it("are exactly the pages, scripts and style the doors serve", () => {
    const files = readdirSync(APP).sort();
    expect(files).toEqual(["api.js", "app.css", "app.js", "connected.html", "connected.js",
                           "index.html", "signed-in.html", "signed-in.js", "view.js"]);
    const pages = readFileSync(join(DOORS, "src", "pages.ts"), "utf8");
    for (const file of files) {
      expect(pages, file).toContain(`"${file}"`);
    }
  });

  it("name no other origin, parse no HTML string and evaluate no code", () => {
    for (const file of readdirSync(APP)) {
      const text = readFileSync(join(APP, file), "utf8");
      // The app's own origin is `location.origin`: no file names a host or a
      // protocol-relative URL.
      expect(text, file).not.toMatch(/https?:\/\/|["'`(]\/\/[a-z]/i);
      expect(text, file).not.toMatch(
        /innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function|setTimeout\(["'`]/);
    }
  });
});
