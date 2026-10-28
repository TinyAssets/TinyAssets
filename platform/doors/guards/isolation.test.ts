/**
 * The doors' source is held by the platform's module-state and self-reach guards.
 *
 * Those guards (`owner/guards/module-state.test.ts`, `self-reach.test.ts`) scan every
 * Worker's `src/` in the platform, so they already run over the doors: no module-level
 * mutable state and no write through the global object (the OAuth provider, the resource
 * host and the MCP handler are built per request for it), no `exports`, and nothing from
 * `cloudflare:workers` but the classes a source extends. This guard checks that the
 * doors are in what they scan, so the doors can never fall outside them.
 */
import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PLATFORM, workerSources } from "../../owner/guards/sources";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

describe("doors isolation guards", () => {
  it("are the platform's own, scanning every doors source", () => {
    const scanned = [...workerSources().keys()].filter((path) => path.startsWith("doors/src/"));
    const sources = readdirSync(SRC, { recursive: true, encoding: "utf8" })
      .filter((path) => extname(path) !== "").map((path) => `doors/src/${path}`).sort();
    expect(scanned.sort()).toEqual(sources);
    expect(sources).toContain("doors/src/binder.ts");
    for (const guard of ["module-state.test.ts", "self-reach.test.ts"]) {
      expect(readFileSync(join(PLATFORM, "owner", "guards", guard), "utf8"))
        .toContain("workerSources()");
    }
  });
});
