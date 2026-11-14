/**
 * The agent's definition as the doors serve it (research §4.3): the MCP instructions tell an
 * outside client's agent the brain's rules first, and they and the starter's text name only
 * the door table's tools (`contract/guards/tools.test.ts` holds the table).
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { PLATFORM } from "../../ci/paths";
import { CLIENT_PROFILES, profileNames, toolNames } from "../../contract/src/doors";
import { CLIENT_INSTRUCTIONS, CLIENT_KEY, RUN_INSTRUCTIONS } from "../src/instructions";

describe("the MCP instructions", () => {
  it("tells an outside client's agent the brain's rules in its first 512 characters, a " +
     "run's engine its working files", () => {
    expect(CLIENT_KEY.length).toBeLessThanOrEqual(512);
    expect(CLIENT_INSTRUCTIONS.startsWith(CLIENT_KEY)).toBe(true);
    // Each rule in the key part: goals, look first, save, claim.
    for (const named of ["/workspace/goals/index.md", "search", "fetch", "/workspace/index.md",
                         "file_append", "file_edit", "collection_write"]) {
      expect(CLIENT_KEY).toContain(named);
    }
    for (const profile of CLIENT_PROFILES) {
      expect(profileNames(profile)).toEqual(expect.arrayContaining(
        ["search", "fetch", "file_append", "file_edit", "collection_write", "agents_list"]));
    }
    expect(RUN_INSTRUCTIONS).not.toBe(CLIENT_INSTRUCTIONS);
  });
});

/**
 * The words of `text` that look like one of `tools` and are none: a word whose first segment
 * is a tool family (`file_`, `run_`, ...) that no tool is named, as a renamed tool left in a
 * text would be. A family's wildcard (`file_*`) is one: name the tools, or say it in prose.
 */
function strayTools(text: string, tools: readonly string[]): string[] {
  const families = new Set(tools.filter((name) => name.includes("_"))
    .map((name) => name.split("_")[0]));
  return [...text.matchAll(/\b[a-z]+_[a-z0-9_*]*/g)].map((found) => found[0])
    .filter((word) => families.has(word.split("_")[0]) && !tools.includes(word));
}

/**
 * The agent's one definition, as every engine reads it: the starter's files (the plugins
 * carry its skills byte for byte, as `npm run generate` writes them) but the vendored OKF
 * spec, which is upstream's words, and the MCP instructions.
 */
describe("the agent's definition", () => {
  const starter = JSON.parse(readFileSync(join(PLATFORM, "starter", "starter.json"), "utf8")) as
    { okf: { vendored: string }; files: { path: string; text: string }[] };
  const texts: [string, string][] = [
    ...starter.files.filter((file) => file.path !== starter.okf.vendored)
      .map((file): [string, string] => [file.path, file.text]),
    ["CLIENT_INSTRUCTIONS", CLIENT_INSTRUCTIONS], ["RUN_INSTRUCTIONS", RUN_INSTRUCTIONS]];

  it("names only the door table's tools", () => {
    for (const [where, text] of texts) {
      expect([where, strayTools(text, toolNames())]).toEqual([where, []]);
    }
    // A misspelt tool and a family's wildcard are seen; a field of a tool's answer is not one.
    expect(strayTools("use `file_*` or run_stauts, then agents_list's connect_url", toolNames()))
      .toEqual(["file_*", "run_stauts"]);
  });
});
