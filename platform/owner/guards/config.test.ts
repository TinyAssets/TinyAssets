/**
 * The owner Worker's config binds nothing that reaches another owner's object (ADR-032):
 * its allow-list (`rules.ts`), at top level and in every environment, with each Worker it
 * binds held to the allowance its binding gives, by the platform's rule checks
 * (`ci/kit/config.ts`). It is the only wrangler config in the owner Worker.
 */
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { classProblems, exactProblems, flagProblems, keyProblems, NO_EXPORTS, secretProblems }
  from "../../ci/kit/config";
import { type Config, configFiles, PLATFORM, platformConfigs, workerSources }
  from "../../ci/kit/sources";
import { readWrangler, wranglerKeys } from "../../ci/workers";

import { BOUND_KEYS, type Bound, CLASSES, ENV_KEYS, LIMITS, OWNER_QUEUES, SECRETS, SERVICES,
         TOP_KEYS } from "./rules";

const OWNER = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG = "wrangler.jsonc";

const HOOKS_OWN = SERVICES["HOOKS"]!.own! as {
  durable_objects: { bindings: object[] }; classes: readonly string[] };
const SANDBOX_OWN = SERVICES["SANDBOX"]!.own! as {
  durable_objects: { bindings: object[] }; classes: readonly string[] };
const SANDBOX_QUEUES = SERVICES["SANDBOX"]!.queues as { producers: object[] };

function object(value: unknown): value is Config {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Each way a bound Worker's config holds more than `allowed` lets it: a key that binds
 * anything else, a count other than the allowed one, or an environment (which could
 * bind anything).
 */
export function boundProblems(config: Config, allowed: Bound): string[] {
  const secrets = allowed.secrets ?? [];
  const own = allowed.own;
  const problems = Object.keys(config)
    .filter((key) => !BOUND_KEYS.has(key) && !Object.hasOwn(allowed.bindings, key)
                     && !(key === "secrets" && secrets.length > 0)
                     && !(own !== undefined && (key === "durable_objects"
                                                || key === "migrations"))
                     && !(key === "limits" && allowed.limits !== undefined)
                     && !(key === "vars" && allowed.vars !== undefined)
                     && !(key === "queues" && allowed.queues !== undefined))
    .map((key) => `${key} is not a binding the owner's ${allowed.service} binding allows`);
  if (own !== undefined) {
    if (JSON.stringify(config["durable_objects"]) !== JSON.stringify(own.durable_objects)) {
      problems.push("durable_objects binds other than its own classes");
    }
    problems.push(...classProblems(config["migrations"], own.classes));
  }
  if (allowed.queues !== undefined
      && JSON.stringify(config["queues"]) !== JSON.stringify(allowed.queues)) {
    problems.push(`queues are other than ${JSON.stringify(allowed.queues)}`);
  }
  if (allowed.limits !== undefined && config["limits"] !== undefined) {
    problems.push(...exactProblems(config, "limits", allowed.limits));
  }
  if (allowed.vars !== undefined && config["vars"] !== undefined
      && (!object(config["vars"]) || Object.keys(config["vars"]).sort().join()
          !== [...allowed.vars].sort().join()
          || !Object.values(config["vars"]).every((value) => typeof value === "string"))) {
    problems.push(`vars are other than ${allowed.vars.join(", ")}, as text`);
  }
  if (config["secrets"] !== undefined && secrets.length > 0) {
    problems.push(...secretProblems(config["secrets"], secrets));
  }
  for (const [key, many] of Object.entries(allowed.bindings)) {
    const held = config[key];
    if (!Array.isArray(held) || held.length !== many) {
      problems.push(`${key} holds other than ${many}`);
    }
  }
  return problems;
}

/**
 * Each problem with a scope's service bindings: each must be one `SERVICES` allows, to
 * the Worker it names, whose config (`bound`, by Worker name) is in the platform and
 * binds only what that entry allows.
 */
function serviceProblems(services: unknown, where: string,
                         bound: ReadonlyMap<string, Config>): string[] {
  if (!Array.isArray(services)) {
    return [`${where}services is not a list`];
  }
  return services.flatMap((service: unknown) => {
    if (!object(service) || Object.keys(service).sort().join() !== "binding,service") {
      return [`${where}services holds more than a binding and a service name`];
    }
    const { binding, service: name } = service;
    const allowed = typeof binding === "string" && Object.hasOwn(SERVICES, binding)
      ? SERVICES[binding]! : undefined;
    if (allowed === undefined || allowed.service !== name) {
      return [`${where}services binds ${String(binding)} to ${String(name)}, not allowed`];
    }
    const target = bound.get(name);
    if (target === undefined) {
      return [`${where}services binds ${name}, whose config is not in the platform`];
    }
    return boundProblems(target, allowed)
      .map((problem) => `${where}services.${binding}: ${problem}`);
  });
}

/**
 * Whether `held` is exactly the control record's namespace: `CONTROL`, by the doors'
 * `CONTROL` id, so it is the namespace that holds the record and nothing else.
 */
function controlOnly(held: unknown): boolean {
  const doors = platformConfigs().get("tinyassets-doors")?.["kv_namespaces"];
  const theirs = Array.isArray(doors)
    ? (doors as Config[]).find((namespace) => namespace["binding"] === "CONTROL") : undefined;
  return theirs !== undefined && JSON.stringify(held)
    === JSON.stringify([{ binding: "CONTROL", id: theirs["id"] }]);
}

/** Each way `config` lets the owner Worker reach past its own object. */
export function configProblems(config: Config,
                               bound: ReadonlyMap<string, Config> = new Map()): string[] {
  const problems: string[] = [];
  const check = (scope: Config, where: string, allowed: ReadonlySet<string>, top: boolean) => {
    problems.push(...keyProblems(scope, [...allowed, "services"], where));
    if (scope["services"] !== undefined) {
      problems.push(...serviceProblems(scope["services"], where, bound));
    }
    if (allowed.has("kv_namespaces") && scope["kv_namespaces"] !== undefined
        && !controlOnly(scope["kv_namespaces"])) {
      problems.push(`${where}kv_namespaces is other than the doors' CONTROL namespace`);
    }
    if (top || scope["limits"] !== undefined) {
      problems.push(...exactProblems(scope, "limits", LIMITS, where));
    }
    if (allowed.has("queues") && scope["queues"] !== undefined
        && JSON.stringify(scope["queues"]) !== JSON.stringify(OWNER_QUEUES)) {
      problems.push(`${where}queues are other than the erasure queue's producer`);
    }
    if (allowed.has("secrets") && scope["secrets"] !== undefined) {
      problems.push(...secretProblems(scope["secrets"], SECRETS, where));
    }
    if (scope["migrations"] !== undefined) {
      problems.push(...classProblems(scope["migrations"], CLASSES, where));
    }
    if (scope["compatibility_flags"] !== undefined || top) {
      problems.push(...flagProblems(scope["compatibility_flags"], { carries: [NO_EXPORTS] }, where));
    }
  };
  check(config, "", TOP_KEYS, true);
  const envs = config["env"] ?? {};
  if (!object(envs)) {
    problems.push("env is not a table of environments");
  } else {
    for (const [name, env] of Object.entries(envs)) {
      if (object(env)) {
        check(env, `env.${name}.`, ENV_KEYS, false);
      } else {
        problems.push(`env.${name} is not an environment`);
      }
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

const config = readWrangler(OWNER);
/**
 * The Workers' sources, read once, and the owner's uses of `CONTROL` in them, parsed once at
 * load (no test's time bound holds a whole parse of the source): each probe below then
 * parses only the one file it changes.
 */
const SOURCES: ReadonlyMap<string, string> = workerSources();
const OWNER_CONTROL = controlUses(SOURCES);

describe("owner Worker config", () => {
  it("binds only FILES, to a Worker that binds one bucket, GATEWAY, to one that binds " +
     "nothing, and HOOKS, to one that binds its own class, and turns ctx.exports off", () => {
    expect(configProblems(config, platformConfigs())).toEqual([]);
    expect(config["compatibility_flags"]).toContain(NO_EXPORTS);
    expect(config["services"]).toEqual([{ binding: "FILES", service: "tinyassets-files" },
                                        { binding: "GATEWAY", service: "tinyassets-gateway" },
                                        { binding: "HOOKS", service: "tinyassets-hooks" },
                                        { binding: "SANDBOX", service: "tinyassets-sandbox" }]);
    expect(config["durable_objects"]).toBeUndefined();
  });

  it.each([
    ["a store namespace", { bindings: [{ name: "STORES", class_name: "Store" }] }],
    ["OWNER", { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] }],
    ["another Worker's class", { bindings: [{ name: "X", class_name: "X",
                                             script_name: "tinyassets-owner" }] }],
    ["no binding", { bindings: [] }],
    ["an environment's", undefined],
  ])("refuses a Durable Object namespace that is %s", (_what, objects) => {
    const probe = objects === undefined
      ? { ...config, env: { prod: { durable_objects: { bindings: [] } } } }
      : { ...config, durable_objects: objects };
    expect(configProblems(probe as Config, platformConfigs())).not.toEqual([]);
  });

  it("makes OwnerDO alone, SQLite-backed, and no other class", () => {
    for (const migrations of [[{ tag: "v1", new_classes: ["OwnerDO"] }],
                              [{ tag: "v1", new_sqlite_classes: ["OwnerDO", "Store"] }],
                              [{ tag: "v1", new_sqlite_classes: ["OwnerDO"] },
                               { tag: "v2", new_sqlite_classes: ["Other"] }],
                              [{ tag: "v1", new_sqlite_classes: ["OwnerDO"],
                                 deleted_classes: ["X"] }]]) {
      expect(configProblems({ ...config, migrations }, platformConfigs())).not.toEqual([]);
    }
  });

  const gateway = { name: "tinyassets-gateway", main: "src/index.ts",
                    compatibility_flags: ["nodejs_compat"], workers_dev: false,
                    secrets: { required: ["GATEWAY_KEYRING"] } };
  const gatewayBinding = [{ binding: "GATEWAY", service: "tinyassets-gateway" }];

  it("allows GATEWAY to the gateway Worker, which binds nothing and declares its keyring",
     () => {
    expect(configProblems({ ...config, services: gatewayBinding },
                          new Map([["tinyassets-gateway", gateway as Config]]))).toEqual([]);
  });

  it.each([
    ["a Durable Object namespace", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a service", { services: [{ binding: "FILES", service: "tinyassets-files" }] }],
    ["a bucket", { r2_buckets: [{ binding: "B", bucket_name: "b" }] }],
    ["a KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["a var", { vars: { OWNER: "alice" } }],
    ["a second secret", { secrets: { required: ["GATEWAY_KEYRING", "OTHER"] } }],
    ["a secret of another name", { secrets: { required: ["OTHER"] } }],
    ["secrets of another shape", { secrets: ["GATEWAY_KEYRING"] }],
    ["a route", { routes: ["tinyassets.io/gateway/*"] }],
    ["an environment", { env: { prod: { name: "tinyassets-gateway-prod" } } }],
  ])("refuses GATEWAY when the gateway Worker holds %s", (_what, extra) => {
    expect(configProblems({ ...config, services: gatewayBinding },
                          new Map([["tinyassets-gateway", { ...gateway, ...extra } as Config]])))
      .not.toEqual([]);
  });

  it("refuses a secret on a bound Worker other than the one it is allowed", () => {
    const files = { name: "tinyassets-files", main: "src/index.ts",
                    r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }],
                    secrets: { required: ["X"] } };
    expect(configProblems({ ...config, services: [{ binding: "FILES",
                                                    service: "tinyassets-files" }] },
                          new Map([["tinyassets-files", files as Config]])))
      .toEqual(["services.FILES: secrets declares other than FILES_KEYRING"]);
  });

  it("binds one store, the doors' control namespace, and only control-gate.ts reads it",
     () => {
    expect(config["kv_namespaces"]).toEqual([{ binding: "CONTROL",
                                               id: "not-provisioned-control" }]);
    for (const kv_namespaces of [[{ binding: "DIRECTORY", id: "not-provisioned-directory" }],
                                 [{ binding: "CONTROL", id: "not-provisioned-other" }],
                                 [{ binding: "CONTROL", id: "not-provisioned-control" },
                                  { binding: "CACHE", id: "not-provisioned-cache" }]]) {
      expect(configProblems({ ...config, kv_namespaces }, platformConfigs()))
        .toEqual(["kv_namespaces is other than the doors' CONTROL namespace"]);
    }
    expect(configProblems({ ...config, env: { prod: { kv_namespaces: [] } } },
                          platformConfigs())).toEqual(["env.prod.kv_namespaces is not an allowed key"]);
    expect(OWNER_CONTROL).toEqual([
      "owner/src/owner-do.ts: OwnerEnv's CONTROL field",
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
      const { limits: _drop, ...rest } = config;
      const probe = limits === undefined ? rest : { ...rest, limits };
      expect(configProblems(probe, platformConfigs())).toEqual(
        [limits === undefined ? "limits are not set" : `limits are not ${JSON.stringify(LIMITS)}`]);
    }
    expect(configProblems({ ...config, env: { prod: { limits: { subrequests: 50 } } } },
                          platformConfigs()))
      .toEqual([`env.prod.limits are not ${JSON.stringify(LIMITS)}`]);
  });

  it("refuses FILES, GATEWAY, HOOKS and SANDBOX when their Workers' configs are not in the " +
     "platform", () => {
    expect(configProblems(config)).toEqual(
      ["services binds tinyassets-files, whose config is not in the platform",
       "services binds tinyassets-gateway, whose config is not in the platform",
       "services binds tinyassets-hooks, whose config is not in the platform",
       "services binds tinyassets-sandbox, whose config is not in the platform"]);
  });

  const sandbox = { name: "tinyassets-sandbox", main: "src/index.ts",
                    compatibility_flags: ["disable_ctx_exports", "global_fetch_strictly_public"],
                    workers_dev: false, vars: { E2B_TEMPLATE: "tinyassets-sandbox:s0" },
                    secrets: { required: ["E2B_API_KEY"] },
                    kv_namespaces: [{ binding: "CONTROL", id: "control" }],
                    durable_objects: SANDBOX_OWN.durable_objects,
                    migrations: [{ tag: "v1", new_sqlite_classes: SANDBOX_OWN.classes }],
                    queues: SANDBOX_QUEUES };
  const sandboxBinding = [{ binding: "SANDBOX", service: "tinyassets-sandbox" }];

  it("allows SANDBOX to the sandbox Worker: its own classes, the control record, the ring's " +
     "producer, its key", () => {
    expect(configProblems({ ...config, services: sandboxBinding },
                          new Map([["tinyassets-sandbox", sandbox as Config]]))).toEqual([]);
  });

  it.each([
    ["the owner's class", { durable_objects: { bindings: [
      ...SANDBOX_OWN.durable_objects.bindings,
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["its class as another Worker's", { durable_objects: { bindings: [
      { name: "SANDBOXES", class_name: "SandboxControl", script_name: "tinyassets-owner" }] } }],
    ["a second KV namespace", { kv_namespaces: [{ binding: "CONTROL", id: "control" },
                                                { binding: "OAUTH_KV", id: "oauth" }] }],
    ["another queue's producer", { queues: { producers: [
      { binding: "RING", queue: "tinyassets-other" }] } }],
    ["a queue consumer", { queues: { ...SANDBOX_QUEUES,
                                     consumers: [{ queue: "tinyassets-sandbox-ring" }] } }],
    ["no ring", { queues: undefined }],
    ["a service", { services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["a bucket", { r2_buckets: [{ binding: "B", bucket_name: "tinyassets-files" }] }],
    ["another var", { vars: { E2B_TEMPLATE: "t", OWNER: "alice" } }],
    ["another secret", { secrets: { required: ["E2B_API_KEY", "ROUTING_MAC_KEY"] } }],
    ["a Worker Loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["a route", { routes: ["tinyassets.io/sandbox/*"] }],
  ])("refuses SANDBOX when the sandbox Worker holds %s", (_what, extra) => {
    expect(configProblems({ ...config, services: sandboxBinding },
                          new Map([["tinyassets-sandbox", { ...sandbox, ...extra } as Config]])))
      .not.toEqual([]);
  });

  const hooks = { name: "tinyassets-hooks", main: "src/index.ts",
                  compatibility_flags: ["disable_ctx_exports"], workers_dev: false,
                  limits: { cpu_ms: 300_000 },
                  durable_objects: HOOKS_OWN.durable_objects,
                  migrations: [{ tag: "v1", new_sqlite_classes: HOOKS_OWN.classes }] };
  const hooksBinding = [{ binding: "HOOKS", service: "tinyassets-hooks" }];

  it("allows HOOKS to the hooks Worker, which binds its own class", () => {
    expect(configProblems({ ...config, services: hooksBinding },
                          new Map([["tinyassets-hooks", hooks as Config]]))).toEqual([]);
  });

  it.each([
    ["the owner's class", { durable_objects: { bindings: [
      ...HOOKS_OWN.durable_objects.bindings,
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["its sandbox as another Worker's class", { durable_objects: { bindings: [
      { name: "CODEMODE", class_name: "CodemodeSandbox", script_name: "tinyassets-owner" }] } }],
    ["no classes", { durable_objects: undefined }],
    ["a second class made", { migrations: [{ tag: "v1", new_sqlite_classes: [
      ...HOOKS_OWN.classes, "Other"] }] }],
    ["classes made without SQLite", { migrations: [{ tag: "v1", new_classes: HOOKS_OWN.classes }] }],
    ["a Worker Loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["a service", { services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["a bucket", { r2_buckets: [{ binding: "B", bucket_name: "tinyassets-files" }] }],
    ["a KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["a var", { vars: { OWNER: "alice" } }],
    ["a secret", { secrets: { required: ["GATEWAY_KEYRING"] } }],
    ["other limits", { limits: { cpu_ms: 30_000, subrequests: 10 } }],
    ["a route", { routes: ["tinyassets.io/hooks/*"] }],
    ["an environment", { env: { prod: { name: "tinyassets-hooks-prod" } } }],
  ])("refuses HOOKS when the hooks Worker holds %s", (_what, extra) => {
    const probe: Config = { ...hooks, ...extra };
    for (const [key, value] of Object.entries(extra)) {
      if (value === undefined) {
        delete probe[key];
      }
    }
    expect(configProblems({ ...config, services: hooksBinding },
                          new Map([["tinyassets-hooks", probe]]))).not.toEqual([]);
  });

  it("refuses FILES bound to another platform Worker, even one that binds nothing", () => {
    const other = { name: "tinyassets-other", main: "src/index.ts" };
    const services = [{ binding: "FILES", service: "tinyassets-other" }];
    expect(configProblems({ ...config, services },
                          new Map([["tinyassets-other", other as Config]])))
      .toEqual(["services binds FILES to tinyassets-other, not allowed"]);
  });

  const files = { name: "tinyassets-files", main: "src/index.ts",
                  r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }] };
  it.each([
    ["a Durable Object namespace", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" }] } }],
    ["a service", { services: [{ binding: "OWNER", service: "tinyassets-owner" }] }],
    ["a queue producer", { queues: { producers: [{ binding: "Q", queue: "q" }] } }],
    ["a workflow", { workflows: [{ binding: "W", name: "w", class_name: "W",
                                   script_name: "tinyassets-owner" }] }],
    ["a dispatch namespace", { dispatch_namespaces: [{ binding: "D", namespace: "d" }] }],
    ["a Worker loader", { worker_loaders: [{ binding: "LOADER" }] }],
    ["an unsafe Durable Object binding", { unsafe: { bindings: [{ name: "OWNER",
      type: "durable_object_namespace", class_name: "OwnerDO" }] } }],
    ["a tail consumer", { tail_consumers: [{ service: "tinyassets-owner" }] }],
    ["a shared KV namespace", { kv_namespaces: [{ binding: "KV", id: "shared" }] }],
    ["a shared D1 database", { d1_databases: [{ binding: "DB", database_id: "shared" }] }],
    ["a second bucket", { r2_buckets: [...files.r2_buckets,
                                       { binding: "OTHER", bucket_name: "other" }] }],
    ["no bucket", { r2_buckets: [] }],
    ["a var", { vars: { NS: "x" } }],
    ["a migration", { migrations: [{ tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["an environment", { env: { prod: { name: "tinyassets-files-prod" } } }],
  ])("refuses FILES when the files Worker holds %s", (_what, extra) => {
    expect(configProblems(config, new Map([["tinyassets-files",
                                            { ...files, ...extra } as Config]])))
      .not.toEqual([]);
  });


  it("is the only wrangler config in the owner Worker", () => {
    expect(configFiles(OWNER)).toEqual([CONFIG]);
    expect(relative(PLATFORM, OWNER)).toBe("owner");
  });

  it("declares OwnerDO a SQLite-backed class", () => {
    const migrations = (config["migrations"] ?? []) as
      { new_sqlite_classes?: string[]; new_classes?: string[] }[];
    expect(migrations.flatMap((m) => m.new_sqlite_classes ?? [])).toContain("OwnerDO");
    expect(migrations.flatMap((m) => m.new_classes ?? [])).not.toContain("OwnerDO");
  });

  it("allows only keys wrangler knows, and none of the other binding keys", () => {
    const top = wranglerKeys("RawConfig");
    const env = wranglerKeys("RawEnvironment");
    expect([...TOP_KEYS, "services"].filter((key) => !top.includes(key))).toEqual([]);
    // The one store the top level may hold is the doors' control namespace (controlOnly).
    expect(TOP_KEYS.has("kv_namespaces") && !ENV_KEYS.has("kv_namespaces")).toBe(true);
    expect([...ENV_KEYS, "services"].filter((key) => !env.includes(key))).toEqual([]);
    // Every key wrangler offers beyond the allow-list is refused, bindings among them.
    for (const key of ["durable_objects", "r2_buckets",
                       "d1_databases", "vars", "ai", "workflows", "browser",
                       "vectorize", "hyperdrive", "analytics_engine_datasets",
                       "dispatch_namespaces", "mtls_certificates", "send_email",
                       "tail_consumers", "unsafe", "secrets_store_secrets", "worker_loaders",
                       "exports", "define"]) {
      expect(top).toContain(key);
      expect(env).toContain(key);
      expect(TOP_KEYS.has(key) || ENV_KEYS.has(key)).toBe(false);
    }
  });

  const base = { name: "tinyassets-owner", main: "src/index.ts",
                 compatibility_flags: ["nodejs_compat", NO_EXPORTS], limits: LIMITS };
  const bound = new Map([["tinyassets-files", files as Config]]);

  it.each([
    ["a self-binding", { ...base, durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a binding inside an environment", { ...base, env: { staging: {
      durable_objects: { bindings: [{ name: "OWNER", class_name: "OwnerDO" }] } } } }],
    ["a services binding", { ...base, services: [{ binding: "SELF", service: "tinyassets-owner" }] }],
    ["a services binding inside an environment", { ...base, env: { prod: {
      services: [{ binding: "EDGE", service: "tinyassets-edge" }] } } }],
    ["FILES bound to another Worker", { ...base, services: [
      { binding: "FILES", service: "tinyassets-owner" }] }],
    ["the files Worker under another name", { ...base, services: [
      { binding: "BYTES", service: "tinyassets-files" }] }],
    ["FILES with an entrypoint", { ...base, services: [
      { binding: "FILES", service: "tinyassets-files", entrypoint: "Other" }] }],
    ["services that are not a list", { ...base, services: { FILES: "tinyassets-files" } }],
    ["the bucket bound directly", { ...base, r2_buckets: [{ binding: "BLOBS",
      bucket_name: "tinyassets-files" }] }],
    ["a var", { ...base, vars: { OWNER: "alice" } }],
    ["an unsafe binding", { ...base, unsafe: { bindings: [] } }],
    ["a key wrangler adds later", { ...base, agent_memory: [] }],
    ["no ctx.exports flag", { ...base, compatibility_flags: ["nodejs_compat"] }],
    ["no flags at all", { name: "tinyassets-owner", limits: LIMITS }],
    ["an environment's flags without it", { ...base, env: { prod: {
      compatibility_flags: ["nodejs_compat"] } } }],
    ["ctx.exports turned back on", { ...base, compatibility_flags: [NO_EXPORTS,
      "enable_ctx_exports"] }],
    ["an environment that is not a table", { ...base, env: { prod: [] } }],
  ])("sees %s", (_what, probe) => {
    expect(configProblems(probe as Config, bound)).not.toEqual([]);
  });

  it("allows an environment that only renames and re-dates", () => {
    expect(configProblems({ ...base, env: { staging: { name: "tinyassets-owner-staging",
      compatibility_date: "2026-10-23" } } })).toEqual([]);
  });

  it("allows FILES at top level and in an environment, to a Worker that reaches nothing", () => {
    const services = [{ binding: "FILES", service: "tinyassets-files" }];
    expect(configProblems({ ...base, services, env: { prod: { services } } }, bound))
      .toEqual([]);
  });
});
