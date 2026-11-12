/**
 * One checker for every Worker's config allow-list, and one test template for it.
 *
 * Each Worker states its allow-list as data (`<workspace>/guards/rules.ts`): the keys its
 * config may set, its compatibility flags, its secrets (declared by name, which binds
 * nothing), the classes its migrations make, its vars, buckets and KV namespaces, and every
 * other key whose value is fixed. `configProblems` holds a config to it; what a Worker
 * checks across configs or sources (the doors' entrypoints, the owner's bound Workers) is
 * its own, built on the same rule checks.
 */
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { readWrangler, wranglerKeys } from "../workers.ts";
import { PLATFORM } from "../paths.ts";
import { type Config, configFiles, platformConfigs } from "./sources.ts";
import { envProblems, staleTypes } from "./types.ts";

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
  /** Its vars, by name exactly, each one of the values listed, or any text (`null`). */
  readonly vars?: Readonly<Record<string, readonly string[] | null>>;
  /** Its R2 buckets, by binding, in order, each a binding and a bucket name alone. */
  readonly buckets?: readonly string[];
  /** Its KV namespaces, by binding, each a binding and a distinct placeholder id alone. */
  readonly kv?: readonly string[];
  /** Every other key whose value is fixed, exactly. */
  readonly exact?: Readonly<Record<string, unknown>>;
  /** What the Worker checks against the platform's other configs. */
  readonly across?: (config: Config, platform: ReadonlyMap<string, Config>) => string[];
  /**
   * Its env interfaces, `<file from its folder>#<interface>`: together their own members
   * name exactly the bindings its config makes (`types.ts`).
   */
  readonly env: readonly string[];
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

/** Each way `config` holds more, or other, than `rules` allow. */
export function configProblems(config: Config, rules: ConfigRules,
                               platform: ReadonlyMap<string, Config> = platformConfigs()): string[] {
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
  if (rules.kv !== undefined) {
    problems.push(...kvProblems(config["kv_namespaces"], rules.kv));
  }
  for (const [key, value] of Object.entries(rules.exact ?? {})) {
    problems.push(...exactProblems(config, key, value));
  }
  problems.push(...(rules.across?.(config, platform) ?? []));
  return problems;
}

/**
 * The test that `worker`'s committed binding types are what `wrangler types` makes from its
 * config now, and that its env interfaces (`env`) name exactly those bindings.
 */
export function bindingTypesTest(worker: string, env: readonly string[]): void {
  it("has its binding types as wrangler types makes them now, its env naming each binding",
     () => {
    expect(staleTypes(worker)).toBeNull();
    expect(envProblems(worker, env)).toEqual([]);
  });
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

    bindingTypesTest(rules.worker, rules.env);
  });
  return config;
}
