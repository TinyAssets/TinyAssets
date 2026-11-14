/**
 * One vitest project of a workspace, made from one key, `<workspace>/<name>`: its name, the
 * folder its tests are in (`PROJECTS`), its size class's budget and the setup file that
 * holds each test to it (`medium()`), and the large tag; every other project's folder that
 * lies in its own is left out (`nestedIn`), so a test is one project's alone. Static projects
 * (every one but `workerd` and those in its folder) run in Node. What a project adds of its own (a pool
 * plugin, an alias, setup files after the class check, a global setup) is `extra`.
 */
import { configDefaults, type TestProjectInlineConfiguration } from "vitest/config";

import {
  CLASS_CHECK, LARGE, medium, nestedIn, type ProjectName, PROJECTS,
} from "./classes.ts";

type Inline = TestProjectInlineConfiguration;
type Test = NonNullable<Inline["test"]>;

export interface Extra extends Omit<Inline, "test"> {
  readonly test?: Pick<Test, "setupFiles" | "globalSetup">;
}

/** What a project leaves out: Vitest's own, and the folders of the projects in its folder. */
export function excluded(name: ProjectName): string[] | undefined {
  const nested = nestedIn(name);
  return nested.length === 0 ? undefined
    : [...configDefaults.exclude, ...nested.map((folder) => `${folder}/**`)];
}

/** Whether `name`'s tests run in workerd: the `workerd` folder's, and any folder's in it. */
function inWorkerd(name: ProjectName): boolean {
  return PROJECTS[name] === PROJECTS.workerd || nestedIn("workerd").includes(PROJECTS[name]);
}

export function project(workspace: string, name: ProjectName, extra: Extra = {}): Inline {
  const { test: own = {}, ...rest } = extra;
  const options = medium(`${workspace}/${name}`);
  const setup = own.setupFiles === undefined ? [] : [own.setupFiles].flat();
  const exclude = excluded(name);
  return {
    ...rest,
    test: {
      name,
      include: [`${PROJECTS[name]}/**/*.test.ts`],
      ...(exclude === undefined ? {} : { exclude }),
      tags: [LARGE],
      ...(inWorkerd(name) ? {} : { environment: "node" }),
      ...(own.globalSetup === undefined ? {} : { globalSetup: own.globalSetup }),
      ...options,
      setupFiles: [CLASS_CHECK, ...setup.filter((file) => file !== CLASS_CHECK)],
    },
  };
}
