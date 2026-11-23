/**
 * One vitest project of a workspace, made from one key, `<workspace>/<name>`: its name, the
 * folder its tests are in (`PROJECTS`), its size class's budget and the setup file that
 * holds each test to it (`medium()`), and the large tag. Static projects (every one but
 * `workerd`) run in Node. What a project adds of its own (a pool plugin, an alias, setup
 * files after the class check, a global setup) is `extra`. Each refuses an install that is not
 * the lockfile's first (`deps.ts`).
 */
import type { TestProjectInlineConfiguration, ViteUserConfig } from "vitest/config";

import { CLASS_CHECK, LARGE, medium, type ProjectName, PROJECTS } from "./classes.ts";
import { installed } from "./deps.ts";

type Inline = TestProjectInlineConfiguration;
type Test = NonNullable<Inline["test"]>;

export interface Extra extends Omit<Inline, "test"> {
  readonly test?: Pick<Test, "setupFiles" | "globalSetup">;
}

export function project(workspace: string, name: ProjectName, extra: Extra = {}): Inline {
  installed();
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

/**
 * A workspace's recorders' folder (`record.ts`): its `*.record.ts`, which no
 * project's `include` names, and the config that runs them, `record.config.ts`.
 */
export const RECORDS = "test/record";

/**
 * The config that runs a workspace's recorders: its own config's workerd project, its pool,
 * class and setup files as they are, running its recorders (`RECORDS`) instead of its tests.
 */
export function recording(config: ViteUserConfig): ViteUserConfig {
  const workerd = config.test?.projects?.find((each): each is Inline =>
    typeof each === "object" && "test" in each && each.test?.name === "workerd");
  if (workerd === undefined) {
    throw new Error("a workspace's recorders run on its workerd project, and it has none");
  }
  const include = [`${RECORDS}/*.record.ts`];
  return { test: { projects: [{ ...workerd, test: { ...workerd.test, include } }] } };
}
