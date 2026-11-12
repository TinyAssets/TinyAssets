/**
 * The gate: a test's way to reach the point between two awaits of a real call, or to make a
 * real binding refuse, with no hook in product code (untangle B23, `b23_research.md`
 * questions 2 and 4). A gate is a Worker (`gate-worker.ts`) that a test's config binds in
 * place of one binding, in front of the real Worker or object that binding names. It
 * forwards every call unchanged unless the test arms it, through its `Control` entrypoint, to
 * hold a call before it is sent or after its answer, change its bytes in flight, or refuse it
 * with a refusal that crosses as a real one does. It records what crossed.
 *
 * - `gate(name, methods, to)`: the gate Worker, for a pool's `workers`. `to` is the real
 *   Worker's folder, and the entrypoint it is bound at or the class whose object it fronts.
 * - `methodsOf(file, name)`: a contract interface's method names, the methods a gate has.
 * - `control(name)`: the binding of a gate's `Control` entrypoint, for the test Worker alone.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";

import type { Arm, Crossed, GateSetup, Held } from "./gate-worker.ts";
import { PLATFORM } from "./paths.ts";
import { transpiled, workerConfig } from "./workers.ts";

/** The gate Worker's source. */
const GATE_WORKER = join(PLATFORM, "ci", "gate-worker.ts");

/** What a gate stands in front of: a Worker's service (at an entrypoint), or its objects. */
type GateTo =
  | { readonly worker: string; readonly entrypoint?: string }
  | { readonly worker: string; readonly className: string };

/**
 * The gate Worker `name`, with `methods`, in front of `to`: the Worker at the folder
 * `to.worker`, as its config names it, at its runtime and flags. In front of a service its
 * default entrypoint is the gate; in front of objects it exports the class `to.className`,
 * bound by `scriptName` as the real class is, each object forwarding to the real object of
 * its name. What a test changes is `extra`.
 */
export function gate<Extra extends object = object>(name: string, methods: readonly string[],
                                                    to: GateTo, extra: Extra = {} as Extra) {
  if (methods.length === 0 || new Set(methods).size !== methods.length) {
    throw new Error(`gate ${name}: its methods are one or more names, each once`);
  }
  const config = workerConfig(to.worker);
  const className = "className" in to ? to.className : null;
  if (className !== null && !/^[A-Za-z_$][\w$]*$/.test(className)) {
    throw new Error(`gate ${name}: ${className} is no class name`);
  }
  const setup: GateSetup = { methods, object: className !== null };
  const source = transpiled(GATE_WORKER) +
    (className === null ? "" : `\nexport { GatedObject as ${className} };\n`);
  return {
    name,
    modulesRoot: join(PLATFORM, "ci"),
    modules: [{ type: "ESModule" as const, path: join(PLATFORM, "ci", `${name}.js`),
                contents: source }],
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: [...(config.compatibility_flags ?? [])],
    bindings: { GATE: JSON.stringify(setup) },
    ...(className === null
      ? { serviceBindings: { REAL: "entrypoint" in to && to.entrypoint !== undefined
                                     ? { name: config.name, entrypoint: to.entrypoint }
                                     : config.name } }
      : { durableObjects: {
            REAL: { className, scriptName: config.name, useSQLite: true },
            // workerd defines a class only through a namespace of its own Worker.
            [className]: { className, useSQLite: true } } }),
    ...extra,
  };
}

/** The binding of gate `name`'s `Control` entrypoint, for the test Worker alone. */
export function control(name: string): { name: string; entrypoint: "Control" } {
  return { name, entrypoint: "Control" };
}

/** A gate's `Control`, as a test calls it through `control(name)`'s binding. */
export interface GateControl {
  /** The next matching call (or `times` of them) acted on as `arm` says. */
  arm(arm: Arm): Promise<void>;
  /** The calls held now, after waiting up to `waitMs` for one to be. */
  held(waitMs?: number): Promise<Held[]>;
  /** The held call `id` let go, or every held call. */
  release(id?: number): Promise<void>;
  /** Every arming dropped and every held call let go. */
  disarm(): Promise<void>;
  /** What crossed (for the object `name`, or all), oldest first, taken from the record. */
  seen(name?: string): Promise<Crossed[]>;
}

/**
 * The method names of the interface `name` in the TypeScript file `file`, in order: the
 * methods a gate in front of that interface's binding has. Loud when there is no such
 * interface, or it has no method.
 */
export function methodsOf(file: string, name: string): string[] {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest);
  const found = source.statements.find((statement): statement is ts.InterfaceDeclaration =>
    ts.isInterfaceDeclaration(statement) && statement.name.text === name);
  if (found === undefined) {
    throw new Error(`${file} declares no interface ${name}`);
  }
  const methods = found.members.filter(ts.isMethodSignature)
    .map((member) => member.name.getText(source));
  if (methods.length === 0) {
    throw new Error(`${file}: interface ${name} has no method`);
  }
  return methods;
}
