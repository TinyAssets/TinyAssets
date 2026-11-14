/**
 * Each Worker's env, `<Worker>Env` in `<worker>/src/env.ts`, written from its config (d3 §R):
 * every binding as `wrangler types` types it, and every binding to another Worker (a service,
 * a Durable Object namespace by `script_name`) or to a queue as the contract types it
 * (`contract/services.json`), never by the bound Worker's own source. Wrangler's file itself
 * is not kept: it declares globals (`Cloudflare.Env`, `NodeJS.ProcessEnv`) that collide across
 * the platform's one compilation. A binding the table does not type fails the run.
 *
 * Each queue's name too, by its producer's binding, into the contract (`QUEUES_FILE`): the
 * configs are its one home, and a consumer dispatches by it (d3 §A3).
 *
 * Run as a script, this writes every Worker's and the queues' names (one of
 * `npm run generate`'s generators, which `ci/guards/generated.test.ts` holds current). Node
 * runs this file as it is (type stripping).
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

import { PLATFORM } from "../paths.ts";
import { readWrangler, WRANGLER, workerFolders } from "../workers.ts";
import { envFile } from "./sources.ts";

/** The widest line the platform's sources keep. */
const WIDTH = 100;

interface ServiceClass {
  readonly source: string;
  readonly contract: string;
  readonly args?: readonly string[];
}

export interface Services {
  readonly workers: Readonly<Record<string, {
    readonly default?: string;
    readonly classes: Readonly<Record<string, ServiceClass>>;
  }>>;
  readonly queues: Readonly<Record<string, string>>;
}

/** The contract's table of the Workers' entrypoints and the queues' messages. */
export function services(): Services {
  return JSON.parse(readFileSync(join(PLATFORM, "contract", "services.json"), "utf8")) as
    Services;
}

interface Bound {
  readonly binding?: string;
  readonly name?: string;
  readonly service?: string;
  readonly entrypoint?: string;
  readonly class_name?: string;
  readonly script_name?: string;
  readonly queue?: string;
}

/** The types one generated file imports, by module. */
class Imports {
  readonly #by = new Map<string, Set<string>>();

  /** `name` from `module`, as the type's text. */
  add(module: string, name: string): string {
    this.#by.set(module, (this.#by.get(module) ?? new Set()).add(name));
    return name;
  }

  /** `<module>#<name>`: a package's type, or a contract module's (under `contract`). */
  ref(spec: string, contract: string): string {
    const [module, name] = spec.split("#") as [string, string];
    return this.add(module.startsWith("@") ? module : `${contract}/${module}`, name);
  }

  lines(): string[] {
    return [...this.#by].sort(([a], [b]) => a.localeCompare(b)).map(([module, names]) => {
      const list = [...names].sort();
      const one = `import type { ${list.join(", ")} } from "${module}";`;
      return one.length <= WIDTH ? one
        : `import type {\n${list.map((name) => `  ${name},`).join("\n")}\n} from "${module}";`;
    });
  }
}

/** Every binding of `worker` as `wrangler types` types it, by name, in its order. */
function wranglerTypes(worker: string): [string, string][] {
  const dir = mkdtempSync(join(tmpdir(), `types-${worker}-`));
  try {
    const out = join(dir, "env.d.ts");
    execFileSync(process.execPath, [WRANGLER, "types", out, "--include-runtime=false",
                                    "--strict-vars=false"], {
      cwd: join(PLATFORM, worker), stdio: "pipe",
      env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
    const source = ts.createSourceFile(out, readFileSync(out, "utf8"), ts.ScriptTarget.Latest,
                                       true);
    const base = source.statements.find((node): node is ts.InterfaceDeclaration =>
      ts.isInterfaceDeclaration(node) && node.name.text === "__BaseEnv_Env");
    if (base === undefined) {
      throw new Error(`wrangler types wrote no bindings for platform/${worker}`);
    }
    return base.members.map((member) => {
      if (!ts.isPropertySignature(member) || member.type === undefined) {
        throw new Error(`platform/${worker}: a binding typed ${member.getText(source)}`);
      }
      return [member.name.getText(source), member.type.getText(source)];
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** The contract type of `folder`'s class `name`, as `table` lists it; `what` binds it. */
function contractType(table: Services, folder: string, name: string, imports: Imports,
                      contract: string, what: string): string {
  const entry = table.workers[folder]?.classes[name];
  if (entry === undefined) {
    throw new Error(`${what} reaches ${folder}'s ${name}, which contract/services.json does ` +
                    "not type");
  }
  const type = imports.add(`${contract}/${folder}`, entry.contract);
  const args = (entry.args ?? []).map((arg) => imports.ref(arg, contract));
  return args.length === 0 ? type : `${type}<${args.join(", ")}>`;
}

/** The source of `worker`'s `src/env.ts`, from its config and `table`. */
function envSource(worker: string, table: Services): string {
  const config = readWrangler(join(PLATFORM, worker)) as Record<string, unknown>;
  const folders = new Map(workerFolders().map((folder) =>
    [readWrangler(join(PLATFORM, folder))["name"] as string, folder]));
  const target = (script: string, what: string): string => {
    const folder = folders.get(script);
    if (folder === undefined) {
      throw new Error(`${what} reaches ${script}, which is no platform Worker`);
    }
    return folder;
  };
  const here = dirname(join(PLATFORM, envFile(worker)));
  const contract = relative(here, join(PLATFORM, "contract", "src"));
  const main = `./${relative(here, join(PLATFORM, worker, config["main"] as string))
    .replace(/\.ts$/, "")}`;
  const imports = new Imports();
  const typed = new Map<string, string>();
  for (const bound of (config["services"] ?? []) as Bound[]) {
    const what = `platform/${worker}'s ${bound.binding}`;
    const folder = target(bound.service!, what);
    const name = bound.entrypoint ?? table.workers[folder]?.default ?? "default";
    typed.set(bound.binding!, contractType(table, folder, name, imports, contract, what));
  }
  const objects = (config["durable_objects"] as { bindings?: Bound[] } | undefined)?.bindings
    ?? [];
  for (const bound of objects) {
    const what = `platform/${worker}'s ${bound.name}`;
    const type = bound.script_name === undefined ? imports.add(main, bound.class_name!)
      : contractType(table, target(bound.script_name, what), bound.class_name!, imports,
                     contract, what);
    typed.set(bound.name!, `DurableObjectNamespace<${type}>`);
  }
  const producers = (config["queues"] as { producers?: Bound[] } | undefined)?.producers ?? [];
  for (const bound of producers) {
    const message = table.queues[bound.queue!];
    if (message === undefined) {
      throw new Error(`platform/${worker}'s ${bound.binding} sends to ${bound.queue}, whose ` +
                      "message contract/services.json does not type");
    }
    typed.set(bound.binding!, `Queue<${imports.ref(message, contract)}>`);
  }
  const members = wranglerTypes(worker).map(([name, type]) =>
    `  readonly ${name}: ${typed.get(name) ?? type};`);
  const name = `${worker[0]!.toUpperCase()}${worker.slice(1)}Env`;
  return [
    "/**",
    ` * The ${worker} Worker's bindings: written by \`npm run generate\` (\`ci/kit/types.ts\`)`,
    " * from its `wrangler.jsonc`, each binding to another Worker or to a queue typed by its",
    " * contract (`contract/services.json`). Edit the config, never this file.",
    " */",
    ...imports.lines(),
    "",
    `export interface ${name} {`,
    ...members,
    "}",
    "",
  ].join("\n");
}

/** The queues `table` types that no Worker's config sends to. */
function unsentQueues(table: Services): string[] {
  const sent = new Set(workerFolders().flatMap((folder) =>
    ((readWrangler(join(PLATFORM, folder))["queues"] as { producers?: Bound[] } | undefined)
      ?.producers ?? []).map((bound) => bound.queue)));
  return Object.keys(table.queues).filter((queue) => !sent.has(queue));
}

/** The contract module the queues' names are written to, from `platform/`. */
const QUEUES_FILE = "contract/src/queue-names.ts";

/**
 * The source of `QUEUES_FILE`: each queue a Worker's config produces to, by its binding,
 * sorted. One binding naming two queues fails the run.
 */
function queueNamesSource(): string {
  const names = new Map<string, string>();
  for (const folder of workerFolders()) {
    const producers = (readWrangler(join(PLATFORM, folder))["queues"] as
      { producers?: Bound[] } | undefined)?.producers ?? [];
    for (const { binding, queue } of producers) {
      if (names.has(binding!) && names.get(binding!) !== queue) {
        throw new Error(`the queue binding ${binding} names ${names.get(binding!)} and ${queue}`);
      }
      names.set(binding!, queue!);
    }
  }
  return [
    "/**",
    " * Each queue's name, by its producer's binding: written by `npm run generate`",
    " * (`ci/kit/types.ts`) from the Workers' `wrangler.jsonc`, the names' one home. Edit the",
    " * configs, never this file.",
    " */",
    "export const QUEUES = Object.freeze({",
    ...[...names].sort(([a], [b]) => a.localeCompare(b))
      .map(([binding, queue]) => `  ${binding}: ${JSON.stringify(queue)},`),
    "});",
    "",
  ].join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const table = services();
  const unsent = unsentQueues(table);
  if (unsent.length > 0) {
    throw new Error(`contract/services.json types queues no Worker sends to: ${unsent.join(", ")}`);
  }
  for (const worker of workerFolders()) {
    writeFileSync(join(PLATFORM, envFile(worker)), envSource(worker, table));
    console.log(`platform/${envFile(worker)}`);
  }
  writeFileSync(join(PLATFORM, QUEUES_FILE), queueNamesSource());
  console.log(`platform/${QUEUES_FILE}`);
}
