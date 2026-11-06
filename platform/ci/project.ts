/**
 * One vitest project of a workspace, made from one key, `<workspace>/<name>`: its name, the
 * folder its tests are in (`PROJECTS`), its size class's budget and the setup file that
 * holds each test to it (`medium()`), and the large tag. Static projects (every one but
 * `workerd`) run in Node. What a project adds of its own (a pool plugin, an alias, setup
 * files after the class check, a global setup) is `extra`.
 */
import type { TestProjectInlineConfiguration } from "vitest/config";

import { CLASS_CHECK, LARGE, medium, type ProjectName, PROJECTS } from "./classes.ts";

type Inline = TestProjectInlineConfiguration;
type Test = NonNullable<Inline["test"]>;

export interface Extra extends Omit<Inline, "test"> {
  readonly test?: Pick<Test, "setupFiles" | "globalSetup">;
}

export function project(workspace: string, name: ProjectName, extra: Extra = {}): Inline {
  const { test: own = {}, ...rest } = extra;
  const options = medium(`${workspace}/${name}`);
  const setup = own.setupFiles === undefined ? [] : [own.setupFiles].flat();
  return {
    ...rest,
    test: {
      name,
      include: [`${PROJECTS[name]}/**/*.test.ts`],
      tags: [LARGE],
      ...(name === "workerd" ? {} : { environment: "node" }),
      ...(own.globalSetup === undefined ? {} : { globalSetup: own.globalSetup }),
      ...options,
      setupFiles: [CLASS_CHECK, ...setup.filter((file) => file !== CLASS_CHECK)],
    },
  };
}
