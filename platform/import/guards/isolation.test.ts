/**
 * The importer reaches no Worker, starts no process and opens no socket of its own: its banned
 * names (`rules.ts`), held over its whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`). That it reads the contract and no Worker's `src` is the layer
 * table's (`ci/kit/layers.ts`). It is a job: no Worker's source guard walks it, and it walks
 * none.
 */
import { describe, expect, it } from "vitest";

import { reach, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["the network", "export const f = () => fetch('https://example.com');"],
    ["an owner's object", "export const f = (n: { getByName: unknown }) => n.getByName;"],
    ["a Node module it has no use for", "import { request } from 'node:https';"],
    ["child processes", "import { spawnSync } from 'node:child_process';"],
    ["a dynamic import", "export const f = () => import('./census');"],
    ["a type import by path", "export type T = import('../owner/src/files').Files;"],
  ],
  allows: [
    ["its own modules, the contract it reads, and Node's file system and SQLite",
     "import/probe.ts", [
       "import { readFileSync } from 'node:fs';",
       "import { DatabaseSync } from 'node:sqlite';",
       "import { inlineId } from '../contract/src/content';",
       "import { survey } from './survey';",
     ].join("\n")],
    ["the network in its staging step alone", "import/stage.ts",
     "export const f = (r: Request) => fetch(r);"],
  ],
});

describe("the importer", () => {
  it("is walked from its own scripts, not its guards or config, and by no Worker's source guard",
     () => {
    const reached = [...reach(IMPORTS).keys()];
    expect(reached.filter((path) => path.startsWith("import/guards/") || path.includes("config")))
      .toEqual([]);
    expect([...workerSources().keys()].filter((path) => path.startsWith("import/"))).toEqual([]);
  });
});
