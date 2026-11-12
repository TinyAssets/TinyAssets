/**
 * Each Worker's binding types, as `wrangler types` makes them from its config: committed as
 * `<worker>/worker-configuration.d.ts`. Each declares wrangler's globals (`Env`,
 * `Cloudflare.GlobalProps`), which collide across Workers in the platform's one compilation,
 * so `tsconfig.json` leaves them out; the Worker's own env interfaces are held to the
 * bindings they name instead (`envProblems`), so a binding is declared once, in its config.
 *
 * Run as a script, this writes every Worker's (one of `npm run generate`'s generators, which
 * `ci/guards/generated.test.ts` holds current). Node runs this file as it is (type stripping).
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

import { PLATFORM } from "../paths.ts";
import { WRANGLER, workerFolders } from "../workers.ts";

/** The generated file, in each Worker's folder. */
const TYPES_FILE = "worker-configuration.d.ts";

/**
 * Writes `worker`'s binding types as wrangler makes them from its own config. The configs of
 * the Workers it binds are not given: wrangler would run their custom builds in this Worker's
 * folder (the sandbox's), and the guard reads the bindings' names, which a service's type
 * does not change.
 */
function generate(worker: string): void {
  execFileSync(process.execPath, [WRANGLER, "types", "--include-runtime=false"], {
    cwd: join(PLATFORM, worker), stdio: "pipe",
    env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
}

/** The members of the interface `name` in `source`, or null when it declares none. */
function members(source: ts.SourceFile, name: (found: string) => boolean): string[] | null {
  const declared = source.statements.filter((node): node is ts.InterfaceDeclaration =>
    ts.isInterfaceDeclaration(node) && name(node.name.text));
  return declared.length === 0 ? null : declared.flatMap((node) => node.members.flatMap(
    (member) => member.name === undefined ? [] : [member.name.getText(source)]));
}

function parsed(path: string): ts.SourceFile {
  return ts.createSourceFile(path, readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true);
}

/** The bindings `worker`'s committed binding types name, sorted. */
function bindingNames(worker: string): string[] {
  const path = join(PLATFORM, worker, TYPES_FILE);
  return [...members(parsed(path), (found) => found.startsWith("__BaseEnv_")) ?? []].sort();
}

/**
 * How the env interfaces `env` (`<file from the Worker's folder>#<interface>`, their own
 * members together) name other than exactly the bindings `worker`'s config makes.
 */
export function envProblems(worker: string, env: readonly string[]): string[] {
  const named = new Set<string>();
  const problems: string[] = [];
  for (const ref of env) {
    const [file, name] = ref.split("#") as [string, string];
    const found = members(parsed(join(PLATFORM, worker, file)), (each) => each === name);
    if (found === null) {
      problems.push(`${worker}/${file} declares no interface ${name}`);
    }
    for (const member of found ?? []) {
      named.add(member);
    }
  }
  const bindings = bindingNames(worker);
  problems.push(...bindings.filter((binding) => !named.has(binding))
    .map((binding) => `${env.join(", ")} name no ${binding}, which the config binds`));
  problems.push(...[...named].filter((member) => !bindings.includes(member)).sort()
    .map((member) => `${env.join(", ")} name ${member}, which the config does not bind`));
  return problems;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const worker of workerFolders()) {
    generate(worker);
    console.log(`platform/${worker}/${TYPES_FILE}`);
  }
}
