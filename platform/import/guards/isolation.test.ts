/**
 * The importer reads the contract and no Worker's `src` (untangle A1): its rules
 * (`rules.ts`), held over its whole import closure by the platform's isolation checker
 * (`ci/kit/imports.ts`). It is a job: no Worker's source guard walks it, and it walks none.
 */
import { describe, expect, it } from "vitest";

import { reach, isolationTests } from "../../ci/kit/imports";
import { workerSources } from "../../ci/kit/sources";

import { IMPORTS } from "./rules";

isolationTests(IMPORTS, {
  sees: [
    ["a Worker's module", "import { storedPath } from '../owner/src/paths';"],
    ["a Worker's module, re-exported", "export { Files } from '../owner/src/files';"],
    ["the doors' module", "import { x } from '../doors/src/tools';"],
    ["a path out and back", "import { x } from './../contract/../owner/src/files';"],
    ["a contract module it does not read", "import { x } from '../contract/src/egress';"],
    ["the network", "export const f = () => fetch('https://example.com');"],
    ["an owner's object", "export const f = (n: { getByName: unknown }) => n.getByName;"],
    ["a Node module it has no use for", "import { request } from 'node:https';"],
    ["child processes outside the job's entry", "import { spawnSync } from 'node:child_process';"],
    ["a package", "import { x } from '@earendil-works/pi-ai';"],
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
    ["the starter's package, to count its seed", "import/probe.ts",
     "import starter from '../starter/starter.json' with { type: 'json' };"],
    ["child processes in its entry alone", "import/cli.ts",
     "import { spawnSync } from 'node:child_process';"],
  ],
});

describe("the importer", () => {
  it("reaches only its own modules and the contract's, and no Worker's source guard walks it", () => {
    const reached = [...reach(IMPORTS).keys()];
    expect(reached).toEqual(expect.arrayContaining(["import/census.ts", "import/bundle.ts",
                                                    "import/cli.ts", "contract/src/content.ts"]));
    expect(reached.filter((path) => !path.startsWith("import/") && !path.startsWith("contract/src/")))
      .toEqual([]);
    expect(reached.filter((path) => path.startsWith("import/guards/") || path.includes("config")))
      .toEqual([]);
    expect([...workerSources().keys()].filter((path) => path.startsWith("import/"))).toEqual([]);
  });
});
