/**
 * One checker for every Worker's config allow-list, and one test template for it.
 *
 * Each Worker states its allow-list as data (`<workspace>/guards/rules.ts`): the keys its
 * config may set, its compatibility flags, its secrets (declared by name, which binds
 * nothing), the classes its migrations make, and its namespaces, vars, buckets, KV
 * namespaces and queue producers by binding name, and each other key whose value is itself
 * a security statement. A value the config is the one home of (a queue's, a bucket's or a
 * namespace's name, an id, a build command) is not restated. `configProblems` holds a config
 * to it; what a Worker checks across configs or sources (the doors' entrypoints, the owner's
 * bound Workers) is its own, built on the same rule checks.
 */
import { join } from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { readWrangler, wranglerKeys } from "../wrangler.ts";
import { PLATFORM } from "../paths.ts";
import { type Config, configFiles, parse } from "./sources.ts";

/** The flag that keeps a Worker's own namespaces out of its `ctx.exports`. */
export const NO_EXPORTS = "disable_ctx_exports";

export type Flags = { readonly exactly: readonly string[] } | { readonly carries: readonly string[] };

export interface ConfigRules {
  /** The Worker's workspace folder under `platform/`. */
  readonly worker: string;
  /** Its name and entry. */
  readonly name: string;
  readonly main: string;
  /** Every key its config may set. */
  readonly keys: readonly string[];
  /**
   * Its compatibility flags: exactly these, or carrying these (and, when one of them turns
   * `ctx.exports` off, no other flag of that kind).
   */
  readonly flags: Flags;
  /** Its secrets, declared by name: exactly `{ required: secrets }`. */
  readonly secrets?: readonly string[];
  /** The classes its migrations make, all SQLite-backed, in one migration's list. */
  readonly classes?: readonly string[];
  /**
   * Its own classes' namespaces, by binding, in order, each a binding and one of its
   * `classes` alone (no `script_name`, so no other Worker's class).
   */
  readonly objects?: readonly string[];
  /** Its vars, by name exactly, each one of the values listed, or any text (`null`). */
  readonly vars?: Readonly<Record<string, readonly string[] | null>>;
  /** Its R2 buckets, by binding, in order, each a binding and a bucket name alone. */
  readonly buckets?: readonly string[];
  /** Its KV namespaces, by binding, each a binding and a distinct placeholder id alone. */
  readonly kv?: readonly string[];
  /**
   * Its queues: its producers, by binding, in order, each a binding and a queue name alone,
   * and whether it consumes (which queues is its own guard's to hold, against the producers').
   */
  readonly queues?: { readonly producers: readonly string[]; readonly consumes: boolean };
  /** Each other key whose value is a security statement, exactly. */
  readonly exact?: Readonly<Record<string, unknown>>;
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function object(value: unknown): value is Config {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Each key of `scope` (at `where`, e.g. `env.prod.`) not in `allowed`. */
export function keyProblems(scope: Config, allowed: Iterable<string>, where = ""): string[] {
  const keys = new Set(allowed);
  return Object.keys(scope).filter((key) => !keys.has(key))
    .map((key) => `${where}${key} is not an allowed key`);
}

/** How `flags` breaks `rule`. */
export function flagProblems(flags: unknown, rule: Flags, where = ""): string[] {
  const held = Array.isArray(flags) ? flags.map(String) : null;
  if ("exactly" in rule) {
    return held !== null && held.length === rule.exactly.length
        && rule.exactly.every((flag) => held.includes(flag))
      ? [] : [`${where}compatibility_flags are exactly ${rule.exactly.join(", ")}`];
  }
  const lacking = rule.carries.filter((flag) => held === null || !held.includes(flag));
  if (lacking.length > 0) {
    return [`${where}compatibility_flags lacks ${lacking.join(", ")}`];
  }
  return rule.carries.includes(NO_EXPORTS)
      && held!.some((flag) => flag !== NO_EXPORTS && /ctx_exports/.test(flag))
    ? [`${where}compatibility_flags turns ctx.exports back on`] : [];
}

/** How `scope[key]` differs from `value`, exactly. */
export function exactProblems(scope: Config, key: string, value: unknown, where = ""): string[] {
  if (scope[key] === undefined) {
    return [`${where}${key} are not set`];
  }
  return same(scope[key], value) ? [] : [`${where}${key} are not ${JSON.stringify(value)}`];
}

/** How `migrations` make other than `classes`, SQLite-backed, in one list. */
export function classProblems(migrations: unknown, classes: readonly string[],
                              where = ""): string[] {
  const made = (Array.isArray(migrations) ? migrations as Config[] : [])
    .flatMap((migration) => Object.entries(migration)).filter(([field]) => field !== "tag");
  return same(made, [["new_sqlite_classes", classes]])
    ? [] : [`${where}migrations make other than ${classes.join(" and ")}, SQLite`];
}

/** How `secrets` declares other than exactly `names`. */
export function secretProblems(secrets: unknown, names: readonly string[], where = ""): string[] {
  return same(secrets, { required: names })
    ? [] : [`${where}secrets declares other than ${names.join(", ")}`];
}

/** How `vars` holds other than exactly `allowed`, each as text. */
function varProblems(vars: unknown,
                     allowed: Readonly<Record<string, readonly string[] | null>>): string[] {
  const names = Object.keys(allowed).sort();
  const fits = object(vars) && same(Object.keys(vars).sort(), names)
    && names.every((name) => typeof vars[name] === "string"
                             && (allowed[name] === null || allowed[name]!.includes(vars[name])));
  return fits ? [] : [`vars are exactly ${Object.entries(allowed).map(([name, values]) =>
    values === null ? name : `${name} ${values.join(" or ")}`).join(", ")}`];
}

/** How `buckets` holds other than exactly `bindings`, each a binding and a bucket name. */
function bucketProblems(buckets: unknown, bindings: readonly string[]): string[] {
  const fits = Array.isArray(buckets) && buckets.length === bindings.length
    && buckets.every((bucket, i) => object(bucket)
      && same(Object.keys(bucket).sort(), ["binding", "bucket_name"])
      && bucket["binding"] === bindings[i] && typeof bucket["bucket_name"] === "string");
  return fits ? [] : [`r2_buckets is exactly ${bindings.join(", ")}`];
}

/**
 * How `namespaces` holds other than exactly `bindings`, each a binding and a placeholder id
 * (`not-provisioned-…`, so a deploy fails until the cutover makes it), the ids distinct.
 */
function kvProblems(namespaces: unknown, bindings: readonly string[]): string[] {
  const fits = Array.isArray(namespaces) && namespaces.length === bindings.length
    && same(namespaces.map((n: Config) => n["binding"]).sort(), [...bindings].sort())
    && namespaces.every((n: unknown) => object(n) && same(Object.keys(n).sort(), ["binding", "id"])
                                        && /^not-provisioned-[a-z]+$/.test(String(n["id"])))
    && new Set(namespaces.map((n: Config) => n["id"])).size === namespaces.length;
  return fits ? [] : [`kv_namespaces are exactly ${bindings.join(", ")}, distinct placeholders`];
}

/** Whether `list` holds exactly `names`, in order, by `field`, each with exactly `keys`. */
function named(list: unknown, names: readonly string[], keys: readonly string[],
               field: string): list is Config[] {
  return Array.isArray(list) && list.length === names.length
    && list.every((each, i) => object(each) && same(Object.keys(each).sort(), keys)
                               && each[field] === names[i]);
}

/** How `objects` binds other than exactly `bindings`, each to one of `classes`. */
function objectProblems(objects: unknown, bindings: readonly string[],
                        classes: readonly string[]): string[] {
  const fits = object(objects) && same(Object.keys(objects), ["bindings"])
    && named(objects["bindings"], bindings, ["class_name", "name"], "name")
    && objects["bindings"].every((binding) => classes.includes(String(binding["class_name"])));
  return fits ? [] : [`durable_objects binds exactly ${bindings.join(", ")}, each a class of ` +
                      "its own"];
}

/** How `queues` holds other than `rule`'s producers, and consumers only where it consumes. */
function queueProblems(queues: unknown, rule: NonNullable<ConfigRules["queues"]>): string[] {
  const parts = rule.consumes ? ["consumers", "producers"] : ["producers"];
  const fits = object(queues) && same(Object.keys(queues).sort(), parts)
    && named(queues["producers"], rule.producers, ["binding", "queue"], "binding")
    && queues["producers"].every((producer) => typeof producer["queue"] === "string")
    && (!rule.consumes || (Array.isArray(queues["consumers"]) && queues["consumers"]
      .every((consumer: unknown) => object(consumer) && typeof consumer["queue"] === "string")));
  return fits ? [] : [`queues produce exactly by ${rule.producers.join(", ")}` +
                      (rule.consumes ? ", and consume" : ", and consume none")];
}

/**
 * How the platform's Worker configs (`configs`, by Worker folder) disagree on their
 * compatibility date: every Worker runs at one date, so the runtime's behaviour one Worker
 * relies on (a refusal's own fields across RPC, `contract/guards/refusal.test.ts`) is every
 * other's too, and a move of the date is one change to all of them.
 */
export function dateProblems(configs: ReadonlyMap<string, Config>): string[] {
  const dates = new Set([...configs.values()].map((config) => config["compatibility_date"]));
  return dates.size <= 1 ? [] : [`the Workers' compatibility dates differ: ${[...configs]
    .map(([name, config]) => `${name} ${String(config["compatibility_date"])}`).join(", ")}`];
}

/** An R2 type's name: a bucket, an object, a listing. */
const R2_TYPE = /^R2[A-Z]/;

/**
 * Each Worker source in `sources` (by path from `platform/`) that names an R2 type though its
 * Worker's rules allow it no bucket (`buckets`, in `allowed`'s rules, by Worker): a Worker
 * reaches R2 only through a bucket its config binds and its rules allow.
 */
export function bucketTypeProblems(sources: ReadonlyMap<string, string>,
                                   allowed: ReadonlyMap<string, ConfigRules>): string[] {
  return [...sources].flatMap(([path, source]) => {
    if (allowed.get(path.split("/")[0]!)?.buckets !== undefined) {
      return [];
    }
    const named = new Set<string>();
    const visit = (node: ts.Node): void => {
      if (ts.isIdentifier(node) && R2_TYPE.test(node.text)) {
        named.add(node.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(parse(path, source));
    return [...named].map((name) => `${path} names ${name}, and its Worker's rules allow no bucket`);
  });
}

/** Each way `config` holds more, or other, than `rules` allow. */
export function configProblems(config: Config, rules: ConfigRules): string[] {
  const problems = keyProblems(config, rules.keys);
  if (config["name"] !== rules.name || config["main"] !== rules.main) {
    problems.push(`the Worker is ${rules.name} at ${rules.main}`);
  }
  if (typeof config["compatibility_date"] !== "string") {
    problems.push("the compatibility date is set");
  }
  if (typeof config["$schema"] !== "string") {
    problems.push("the config names wrangler's schema ($schema)");
  }
  if (config["workers_dev"] !== false || config["preview_urls"] !== false) {
    problems.push("workers_dev and preview_urls are false");
  }
  problems.push(...flagProblems(config["compatibility_flags"], rules.flags));
  if (rules.secrets !== undefined) {
    problems.push(...secretProblems(config["secrets"], rules.secrets));
  }
  if (rules.classes !== undefined) {
    problems.push(...classProblems(config["migrations"], rules.classes));
  }
  if (rules.vars !== undefined) {
    problems.push(...varProblems(config["vars"], rules.vars));
  }
  if (rules.buckets !== undefined) {
    problems.push(...bucketProblems(config["r2_buckets"], rules.buckets));
  }
  if (rules.objects !== undefined) {
    problems.push(...objectProblems(config["durable_objects"], rules.objects, rules.classes ?? []));
  }
  if (rules.kv !== undefined) {
    problems.push(...kvProblems(config["kv_namespaces"], rules.kv));
  }
  if (rules.queues !== undefined) {
    problems.push(...queueProblems(config["queues"], rules.queues));
  }
  for (const [key, value] of Object.entries(rules.exact ?? {})) {
    problems.push(...exactProblems(config, key, value));
  }
  return problems;
}

/** `config` with `patch` laid over it, a key the patch sets `undefined` removed. */
export function patched(config: Config, patch: Config): Config {
  const probe: Config = { ...config, ...patch };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) {
      delete probe[key];
    }
  }
  return probe;
}

export interface ConfigTests {
  /** The keys wrangler offers that the Worker must refuse, named so a rename is seen. */
  readonly refused: readonly string[];
  /** Changes to the Worker's config, each of which the checker must refuse. */
  readonly probes: readonly (readonly [string, Config])[];
  /** The checker, when the Worker's is more than `configProblems` over its rules. */
  readonly problems?: (config: Config) => string[];
}

/**
 * The tests every Worker's config is held to, from its rules: its config passes; every key
 * the rules allow is one wrangler knows; every other key wrangler offers is refused; each
 * probe is refused; and it is its Worker's only wrangler config.
 */
export function configTests(rules: ConfigRules, tests: ConfigTests): Config {
  const dir = join(PLATFORM, rules.worker);
  const config = readWrangler(dir);
  const problems = tests.problems ?? ((probe: Config) => configProblems(probe, rules));
  const offered = new Set(wranglerKeys());
  describe(`${rules.worker} Worker config, by its rules`, () => {
    it("holds to its rules", () => {
      expect(problems(config)).toEqual([]);
    });

    it("allows only keys wrangler knows", () => {
      expect(rules.keys.filter((key) => !offered.has(key))).toEqual([]);
    });

    it("refuses every other key wrangler offers, the named ones among them", () => {
      const others = [...offered].filter((key) => !rules.keys.includes(key));
      expect(tests.refused.filter((key) => !others.includes(key))).toEqual([]);
      for (const key of others) {
        expect(problems({ ...config, [key]: {} }), key).toContain(`${key} is not an allowed key`);
      }
    });

    it.each(tests.probes)("sees %s", (_what, patch) => {
      expect(problems(patched(config, patch))).not.toEqual([]);
    });

    it(`is the only wrangler config in the ${rules.worker} Worker`, () => {
      expect(configFiles(dir)).toEqual(["wrangler.jsonc"]);
    });

  });
  return config;
}
