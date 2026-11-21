/**
 * The owner Worker's config binds nothing that reaches another owner's object (ADR-032):
 * its allow-list (`rules.ts`), its top level by the platform's config checker and every
 * environment by the same rule checks (`ci/kit/config.ts`), with each Worker it binds held
 * to the allowance its binding gives.
 */
import ts from "typescript";
import { describe, expect, it } from "vitest";

import { keyed, object } from "../../contract/src/checks";
import { classProblems, configProblems as ruleProblems, configTests, exactProblems,
         flagProblems, keyProblems, NO_EXPORTS, patched } from "../../ci/kit/config";
import { type Config, platformConfigs, workerSources } from "../../ci/kit/sources";
import { wranglerKeys } from "../../ci/wrangler";

import { BOUND_KEYS, type Bound, CONFIG, ENV_KEYS, LIMITS, RING_QUEUES, SERVICES } from "./rules";

/** Where a kind's bindings sit when the kind nests them: a namespace's, a queue's producers. */
const NESTED: Readonly<Record<string, string>> = Object.freeze({
  durable_objects: "bindings", queues: "producers" });

/**
 * The bindings `config` holds of `kind`, or null when they are not a list of bindings (a
 * nesting kind holding anything beside its list among them: a queue's consumers).
 */
function bindingsOf(config: Config, kind: string): Config[] | null {
  const value = config[kind];
  const nest = NESTED[kind];
  const list = nest === undefined ? value
    : keyed(value, [nest]) ? value[nest] : null;
  return Array.isArray(list) && list.every(object) ? list : null;
}

/** The migration fields that make a class of the Worker's own. */
const MAKES = Object.freeze(["new_sqlite_classes", "new_classes"]);

/**
 * Each way a bound Worker's own classes could reach another Worker's object: its migrations
 * only make classes, every namespace is of a class they make (no `script_name`), and none of
 * them is `OwnerDO`.
 */
export function ownProblems(config: Config): string[] {
  const steps = config["migrations"];
  if (!Array.isArray(steps) || !steps.every((step) => object(step)
      && Object.entries(step).every(([field, value]) => field === "tag"
                                    || (MAKES.includes(field) && Array.isArray(value))))) {
    return ["its migrations do other than make classes of its own"];
  }
  const made = (steps as Config[]).flatMap((step) =>
    MAKES.flatMap((field) => (step[field] ?? []) as unknown[])).map(String);
  const problems = made.includes("OwnerDO") ? ["its own classes hold OwnerDO"] : [];
  for (const binding of bindingsOf(config, "durable_objects") ?? []) {
    if (Object.hasOwn(binding, "script_name")) {
      problems.push(`its namespace ${String(binding["name"])} names another Worker's class`);
    } else if (!made.includes(String(binding["class_name"]))) {
      problems.push(`its namespace ${String(binding["name"])} binds ` +
                    `${String(binding["class_name"])}, not a class of its own`);
    }
  }
  return problems;
}

/** Each queue a bound Worker produces onto that is not a ring (`RING_QUEUES`). */
export function ringProblems(config: Config): string[] {
  return (bindingsOf(config, "queues") ?? [])
    .filter((producer) => !RING_QUEUES.includes(String(producer["queue"])))
    .map((producer) => `it produces onto ${String(producer["queue"])}, not a ring`);
}

/**
 * Each way a bound Worker's config reaches further than `allowed` lets it, whatever its own
 * rules say: a key that binds anything but the kinds allowed, a count other than the one
 * allowed, a namespace of a class not its own, or a queue that is not a ring.
 */
export function boundProblems(config: Config, allowed: Bound): string[] {
  const kinds = Object.keys(allowed.bindings);
  const classes = kinds.includes("durable_objects");
  const problems = Object.keys(config)
    .filter((key) => !BOUND_KEYS.has(key) && !kinds.includes(key)
                     && !(key === "migrations" && classes))
    .map((key) => `${key} is not a binding the owner's ${allowed.worker} binding allows`);
  for (const [kind, many] of Object.entries(allowed.bindings)) {
    if (bindingsOf(config, kind)?.length !== many) {
      problems.push(`${kind} holds other than ${many}`);
    }
  }
  if (classes) {
    problems.push(...ownProblems(config));
  }
  return [...problems, ...ringProblems(config)];
}

/**
 * Each problem with a scope's service bindings: each must be one `SERVICES` allows, to the
 * Worker it names, whose config (`bound`, by Worker folder) is in the platform, is of that
 * name and binds only what that entry allows.
 */
function serviceProblems(services: unknown, where: string,
                         bound: ReadonlyMap<string, Config>): string[] {
  if (!Array.isArray(services)) {
    return [`${where}services is not a list`];
  }
  return services.flatMap((service: unknown) => {
    if (!keyed(service, ["binding", "service"])) {
      return [`${where}services holds more than a binding and a service name`];
    }
    const { binding, service: name } = service;
    const allowed = typeof binding === "string" && Object.hasOwn(SERVICES, binding)
      ? SERVICES[binding]! : undefined;
    if (allowed === undefined) {
      return [`${where}services binds ${String(binding)}, not allowed`];
    }
    const target = bound.get(allowed.worker);
    if (target === undefined) {
      return [`${where}services binds ${binding}, whose ${allowed.worker} Worker is not in ` +
              "the platform"];
    }
    if (target["name"] !== name) {
      return [`${where}services binds ${binding} to ${String(name)}, not the ` +
              `${allowed.worker} Worker`];
    }
    return boundProblems(target, allowed)
      .map((problem) => `${where}services.${binding}: ${problem}`);
  });
}

/**
 * Each way `config` lets the owner Worker reach past its own object: its top level against
 * its rules (`CONFIG`), each environment against what an environment may set, and every
 * service binding, wherever it is, against the Worker it binds (`bound`, by Worker folder).
 */
export function configProblems(config: Config,
                               bound: ReadonlyMap<string, Config> = new Map()): string[] {
  const problems = ruleProblems(config, CONFIG);
  const services = (scope: Config, where: string) => {
    if (scope["services"] !== undefined) {
      problems.push(...serviceProblems(scope["services"], where, bound));
    }
  };
  services(config, "");
  const envs = config["env"] ?? {};
  if (!object(envs)) {
    problems.push("env is not a table of environments");
    return problems;
  }
  for (const [name, env] of Object.entries(envs)) {
    const where = `env.${name}.`;
    if (!object(env)) {
      problems.push(`env.${name} is not an environment`);
      continue;
    }
    problems.push(...keyProblems(env, [...ENV_KEYS, "services"], where));
    services(env, where);
    if (env["limits"] !== undefined) {
      problems.push(...exactProblems(env, "limits", LIMITS, where));
    }
    if (env["migrations"] !== undefined) {
      problems.push(...classProblems(env["migrations"], CONFIG.classes, where));
    }
    if (env["compatibility_flags"] !== undefined) {
      problems.push(...flagProblems(env["compatibility_flags"], CONFIG.flags, where));
    }
  }
  return problems;
}

/**
 * Every use of the name `CONTROL` in the owner's source, read from its syntax, so no
 * spelling (`env.CONTROL`, `env["CONTROL"]`, a destructuring) hides one: allowed only as
 * `OwnerEnv`'s field and the one `this.env.CONTROL` handed to `new ControlGate`, which only
 * reads it (`storeWrites`).
 */
function controlUses(sources: ReadonlyMap<string, string>): string[] {
  return [...sources].filter(([path]) => path.startsWith("owner/src/"))
    .flatMap(([path, text]) => fileControlUses(path, text)).sort();
}

/**
 * One file's uses of `CONTROL`, parsed once per path and text: each probe changes one file of
 * the owner's source, so the others are not parsed again (parsing every file at each probe
 * took most of these tests' time).
 */
const CONTROL_USES = new Map<string, readonly string[]>();

function fileControlUses(path: string, text: string): readonly string[] {
  const key = `${path}\0${text}`;
  const known = CONTROL_USES.get(key);
  if (known !== undefined) {
    return known;
  }
  const uses: string[] = [];
  {
    const file = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node): void => {
      const named = (ts.isIdentifier(node) || ts.isStringLiteralLike(node))
        && node.text === "CONTROL";
      if (named) {
        const parent = node.parent;
        if (ts.isPropertySignature(parent) && ts.isInterfaceDeclaration(parent.parent)
            && parent.parent.name.text === "OwnerEnv") {
          uses.push(`${path}: OwnerEnv's CONTROL field`);
        } else if (ts.isPropertyAccessExpression(parent) && parent.name === node
                   && parent.expression.getText(file) === "this.env"
                   && ts.isNewExpression(parent.parent)
                   && parent.parent.expression.getText(file) === "ControlGate") {
          uses.push(`${path}: this.env.CONTROL, handed to new ControlGate`);
        } else {
          uses.push(`${path}: ${parent.getText(file).slice(0, 60)}`);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
  }
  CONTROL_USES.set(key, uses);
  return uses;
}

/** Each write a source could make through a KV namespace: put, delete or list, by any spelling. */
function storeWrites(text: string): string[] {
  const file = ts.createSourceFile("gate.ts", text, ts.ScriptTarget.Latest, true);
  const writes: string[] = [];
  const visit = (node: ts.Node): void => {
    if ((ts.isIdentifier(node) || ts.isPrivateIdentifier(node) || ts.isStringLiteralLike(node))
        && ["put", "delete", "list", "getWithMetadata"].includes(node.text)) {
      writes.push(node.parent.getText(file));
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return writes;
}

/**
 * The Workers' sources, read once, and the owner's uses of `CONTROL` in them, parsed once at
 * load (no test's time bound holds a whole parse of the source): each probe below then
 * parses only the one file it changes.
 */
const SOURCES: ReadonlyMap<string, string> = workerSources();
const OWNER_CONTROL = controlUses(SOURCES);

/** The platform's configs, by Worker folder, the Workers the owner binds among them. */
const CONFIGS = platformConfigs();
/** A Worker's name, by its folder, as its config says it. */
const named = (worker: string): string => String(CONFIGS.get(worker)!["name"]);

const config = configTests(CONFIG, {
  refused: ["durable_objects", "r2_buckets", "d1_databases", "vars", "ai", "workflows",
            "browser", "vectorize", "hyperdrive", "analytics_engine_datasets",
            "dispatch_namespaces", "mtls_certificates", "send_email", "tail_consumers",
            "unsafe", "secrets_store_secrets", "worker_loaders", "exports", "define", "routes",
            "route"],
  probes: [
    ["a store namespace", { durable_objects: { bindings: [
      { name: "STORES", class_name: "Store" }] } }],
    ["a self-binding", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["another Worker's class", { durable_objects: { bindings: [
      { name: "X", class_name: "X", script_name: "other" }] } }],
    ["an empty namespace list", { durable_objects: { bindings: [] } }],
    ["a namespace inside an environment", { env: { prod: {
      durable_objects: { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] } } } }],
    ["OwnerDO not SQLite-backed", { migrations: [{ tag: "v1", new_classes: ["OwnerDO"] }] }],
    ["a second class", { migrations: [{ tag: "v1", new_sqlite_classes: ["OwnerDO", "Store"] }] }],
    ["a second migration's class", { migrations: [
      { tag: "v1", new_sqlite_classes: ["OwnerDO"] },
      { tag: "v2", new_sqlite_classes: ["Other"] }] }],
    ["a class deleted", { migrations: [{ tag: "v1", new_sqlite_classes: ["OwnerDO"],
                                         deleted_classes: ["X"] }] }],
    ["an environment's class", { env: { prod: { migrations: [
      { tag: "v1", new_sqlite_classes: ["OwnerDO", "Store"] }] } } }],
    ["another store in CONTROL's place", { kv_namespaces: [
      { binding: "DIRECTORY", id: "not-provisioned-directory" }] }],
    ["a second store", { kv_namespaces: [
      { binding: "CONTROL", id: "not-provisioned-control" },
      { binding: "CACHE", id: "not-provisioned-cache" }] }],
    ["a second queue producer", { queues: { producers: [
      { binding: "ERASURES", queue: "e" }, { binding: "RING", queue: "r" }] } }],
    ["a queue consumer", { queues: { producers: [], consumers: [{ queue: "q" }] } }],
    ["a second secret", { secrets: { required: ["ROUTING_MAC_KEY", "OTHER"] } }],
    ["a services binding", { services: [{ binding: "SELF", service: named("owner") }] }],
    ["a services binding inside an environment", { env: { prod: {
      services: [{ binding: "EDGE", service: "tinyassets-edge" }] } } }],
    ["FILES bound to another Worker", { services: [
      { binding: "FILES", service: named("owner") }] }],
    ["the files Worker under another name", { services: [
      { binding: "BYTES", service: named("files") }] }],
    ["FILES with an entrypoint", { services: [
      { binding: "FILES", service: named("files"), entrypoint: "Other" }] }],
    ["services that are not a list", { services: { FILES: named("files") } }],
    ["a key wrangler adds later", { agent_memory: [] }],
    ["no ctx.exports flag", { compatibility_flags: ["nodejs_compat"] }],
    ["no flags at all", { compatibility_flags: undefined }],
    ["an environment's flags without it", { env: { prod: {
      compatibility_flags: ["nodejs_compat"] } } }],
    ["ctx.exports turned back on", { compatibility_flags: [NO_EXPORTS, "enable_ctx_exports"] }],
    ["an environment that is not a table", { env: { prod: [] } }],
    ["environments that are not a table", { env: [] }],
  ],
  problems: (probe) => configProblems(probe, CONFIGS),
});

/** The owner's own binding of each Worker it binds, by binding name, as its config holds it. */
const BINDING: Readonly<Record<string, Config>> = Object.fromEntries(
  (config["services"] as Config[]).map((service) => [String(service["binding"]), service]));

/** The owner's problems when it binds `binding` alone, to a Worker whose config is `bound`. */
function bindingAlone(binding: string, bound: Config): string[] {
  return configProblems({ ...config, services: [BINDING[binding]!] },
                        new Map([[SERVICES[binding]!.worker, bound]]));
}

describe("owner Worker config", () => {
  it("binds each Worker its rules name", () => {
    expect(Object.keys(BINDING)).toEqual(Object.keys(SERVICES));
  });

  it("binds one store, the control namespace, and only control-gate.ts reads it", () => {
    expect(configProblems({ ...config, kv_namespaces: [{ binding: "DIRECTORY",
                                                          id: "not-provisioned-directory" }] },
                          CONFIGS))
      .toEqual(["kv_namespaces are exactly CONTROL, distinct placeholders"]);
    expect(configProblems({ ...config, env: { prod: { kv_namespaces: [] } } }, CONFIGS))
      .toEqual(["env.prod.kv_namespaces is not an allowed key"]);
    expect(OWNER_CONTROL).toEqual([
      "owner/src/env.ts: OwnerEnv's CONTROL field",
      "owner/src/owner-do.ts: this.env.CONTROL, handed to new ControlGate"]);
    expect(storeWrites(SOURCES.get("owner/src/control-gate.ts")!)).toEqual([]);
  });

  it.each([
    ["a bracket read", "const kv = this.env[\"CONTROL\"];"],
    ["a second dotted read", "const kv = this.env.CONTROL;"],
    ["a destructured read", "const { CONTROL } = this.env;"],
  ])("sees %s of CONTROL outside the gate's one hand-off", (_what, line) => {
    const sources = new Map(SOURCES);
    sources.set("owner/src/extra.ts", `class X { f() { ${line} } }`);
    expect(controlUses(sources)).not.toEqual(OWNER_CONTROL);
  });

  it("sees a write through the gate's store, however it is spelled", () => {
    for (const line of ["this.#store.put('c:control', '{}');",
                        "this.#store[\"delete\"]('c:control');",
                        "const { list } = this.#store;"]) {
      expect(storeWrites(`class G { f() { ${line} } }`), line).not.toEqual([]);
    }
  });

  it("sets the platform's subrequests per invocation explicitly", () => {
    expect(config["limits"]).toEqual({ subrequests: 10_000_000 });
    for (const limits of [undefined, {}, { subrequests: 50 }, { subrequests: 10000 },
                          { subrequests: 1_000_000, cpu_ms: 1 }, { cpu_ms: 30000 }]) {
      expect(configProblems(patched(config, { limits }), CONFIGS)).toEqual(
        [limits === undefined ? "limits are not set" : `limits are not ${JSON.stringify(LIMITS)}`]);
    }
    expect(configProblems({ ...config, env: { prod: { limits: { subrequests: 50 } } } },
                          CONFIGS))
      .toEqual([`env.prod.limits are not ${JSON.stringify(LIMITS)}`]);
  });

  it("refuses each Worker it binds when that Worker's config is not in the platform", () => {
    expect(configProblems(config)).toEqual(Object.entries(SERVICES).map(([binding, { worker }]) =>
      `services binds ${binding}, whose ${worker} Worker is not in the platform`));
  });

  it("refuses FILES bound to another platform Worker, even one that binds nothing", () => {
    const gateway = String(CONFIGS.get("gateway")!["name"]);
    expect(configProblems({ ...config, services: [{ binding: "FILES", service: gateway }] },
                          CONFIGS))
      .toEqual([`services binds FILES to ${gateway}, not the files Worker`]);
  });

  it("lets an environment set only keys wrangler knows there, and no binding but services",
     () => {
    const offered = wranglerKeys("RawEnvironment");
    const allowed = [...ENV_KEYS, "services"];
    expect(allowed.filter((key) => !offered.includes(key))).toEqual([]);
    for (const key of offered.filter((key) => !allowed.includes(key))) {
      expect(configProblems({ ...config, env: { prod: { [key]: {} } } }, CONFIGS), key)
        .toContain(`env.prod.${key} is not an allowed key`);
    }
  });

  it("allows an environment that only renames and re-dates", () => {
    expect(configProblems({ ...config, env: { staging: { name: "tinyassets-owner-staging",
      compatibility_date: "2026-10-23" } } }, CONFIGS)).toEqual([]);
  });

  it("allows FILES at top level and in an environment, to a Worker that reaches nothing", () => {
    const services = [BINDING["FILES"]!];
    const bound = new Map([["files", CONFIGS.get("files")!]]);
    expect(configProblems({ ...config, services, env: { prod: { services } } }, bound))
      .toEqual([]);
  });
});

describe("the owner's own hold on the Workers it binds, whatever their rules say", () => {
  // Each probe grows a bound Worker's config as the platform holds it; its own rules are never
  // read, so only the owner's allowance and invariants can refuse it.
  const namespace = (name: string, class_name: string, script_name?: string) =>
    script_name === undefined ? { name, class_name } : { name, class_name, script_name };
  const ownNamespaces = (worker: string) =>
    (CONFIGS.get(worker)!["durable_objects"] as { bindings: Config[] }).bindings;
  const erasure = String((config["queues"] as { producers: Config[] })
    .producers[0]!["queue"]);
  /** What reaches past every Worker the owner binds, whichever it is. */
  const everywhere: readonly (readonly [string, Config])[] = [
    ["a service", { services: [{ binding: "OWNER", service: "other" }] }],
    ["a namespace of another Worker's class", { durable_objects: { bindings: [
      namespace("OWNER", "OwnerDO", "other")] } }],
    ["a queue consumer", { queues: { producers: [], consumers: [{ queue: "q" }] } }],
    ["a producer onto the erasure queue", { queues: { producers: [
      { binding: "OUT", queue: erasure }] } }],
    ["a workflow", { workflows: [{ binding: "W", name: "w", class_name: "W",
                                   script_name: "other" }] }],
    ["a dispatch namespace", { dispatch_namespaces: [{ binding: "D", namespace: "d" }] }],
    ["a Worker loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["an unsafe Durable Object binding", { unsafe: { bindings: [{ name: "OWNER",
      type: "durable_object_namespace", class_name: "OwnerDO" }] } }],
    ["a tail consumer", { tail_consumers: [{ service: "other" }] }],
    ["a D1 database", { d1_databases: [{ binding: "DB", database_id: "shared" }] }],
    ["a route", { routes: ["example.test/*"] }],
    ["an environment", { env: { prod: { name: "other" } } }],
  ];
  /** What reaches past one Worker's allowance alone. */
  const own: readonly (readonly [string, string, Config])[] = [
    ["FILES", "a second bucket", { r2_buckets: [
      ...(CONFIGS.get("files")!["r2_buckets"] as Config[]), { binding: "B", bucket_name: "b" }] }],
    ["FILES", "no bucket", { r2_buckets: [] }],
    ["FILES", "a KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["FILES", "a class of its own with no namespace", { migrations: [
      { tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["GATEWAY", "a bucket", { r2_buckets: [{ binding: "B", bucket_name: "b" }] }],
    ["GATEWAY", "a KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["CODEMODE_VM", "no namespace", { durable_objects: undefined }],
    ["CODEMODE_VM", "a second namespace of its own class", { durable_objects: { bindings: [
      ...ownNamespaces("codemode"), ...ownNamespaces("codemode")] } }],
    ["CODEMODE_VM", "its namespace as another Worker's class", { durable_objects: { bindings: [
      { ...ownNamespaces("codemode")[0]!, script_name: "other" }] } }],
    ["CODEMODE_VM", "a namespace of a class not its own", { durable_objects: { bindings: [
      namespace("OTHER", "Other")] } }],
    ["CODEMODE_VM", "OwnerDO among its own classes", {
      durable_objects: { bindings: [namespace("OWNER", "OwnerDO")] },
      migrations: [{ tag: "v1", new_sqlite_classes: ["OwnerDO"] }] }],
    ["CODEMODE_VM", "a class taken from another Worker", { migrations: [
      ...(CONFIGS.get("codemode")!["migrations"] as Config[]),
      { tag: "v2", transferred_classes: [{ from: "OwnerDO", from_script: "other",
                                           to: "CodemodeSandbox" }] }] }],
    ["CODEMODE_VM", "a bucket", { r2_buckets: [{ binding: "B", bucket_name: "b" }] }],
    ["CODEMODE_VM", "a KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["SANDBOX", "a second KV namespace", { kv_namespaces: [
      ...(CONFIGS.get("sandbox")!["kv_namespaces"] as Config[]), { binding: "KV", id: "x" }] }],
    ["SANDBOX", "no ring", { queues: undefined }],
    ["SANDBOX", "a producer onto another queue as well", { queues: { producers: [
      ...(CONFIGS.get("sandbox")!["queues"] as { producers: Config[] }).producers,
      { binding: "OUT", queue: "other" }] } }],
    ["SANDBOX", "a third namespace", { durable_objects: { bindings: [
      ...ownNamespaces("sandbox"), namespace("OTHER", "SandboxSlots")] } }],
    ["SANDBOX", "its class as another Worker's", { durable_objects: { bindings: [
      { ...ownNamespaces("sandbox")[0]!, script_name: "other" },
      ...ownNamespaces("sandbox").slice(1)] } }],
    ["SANDBOX", "a bucket", { r2_buckets: [{ binding: "B", bucket_name: "b" }] }],
  ];

  it.each([
    ...Object.keys(SERVICES).flatMap((binding) =>
      everywhere.map(([what, extra]) => [binding, what, extra] as const)),
    ...own,
  ])("refuses %s when its Worker holds %s", (binding, _what, extra) => {
    const bound = CONFIGS.get(SERVICES[binding]!.worker)!;
    expect(boundProblems(bound, SERVICES[binding]!)).toEqual([]);
    expect(bindingAlone(binding, patched(bound, extra))).not.toEqual([]);
  });

  it("allows what binds nothing, which each Worker's own rules hold", () => {
    for (const binding of Object.keys(SERVICES)) {
      const bound = CONFIGS.get(SERVICES[binding]!.worker)!;
      expect(bindingAlone(binding, patched(bound, { vars: { V: "x" }, limits: { cpu_ms: 1 },
                                                    secrets: { required: ["S"] } })))
        .toEqual([]);
    }
  });

  it("holds every Worker it binds today", () => {
    for (const [binding, allowed] of Object.entries(SERVICES)) {
      const bound = CONFIGS.get(allowed.worker);
      expect(bound, binding).toBeDefined();
      expect(boundProblems(bound!, allowed), binding).toEqual([]);
    }
  });
});
