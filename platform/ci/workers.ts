/**
 * The Workers as wrangler sees them, for every test, guard and config that needs one: a
 * Worker's config read (JSONC, as wrangler reads it), the wrangler the platform installs,
 * a Worker bundled as a deploy bundles it and handed to miniflare as an auxiliary Worker,
 * a test pool's own Worker built from its config the same way, the check every test's
 * Workers pass before they run (`pool`: no host secret), and a test fake transpiled into one.
 *
 * Node runs this file as it is (type stripping): `deploy/facts.ts`, `doors/test/app.e2e.mjs` and
 * `owner/memory/baselines.mjs` import it, so its own imports name their `.ts` files.
 */
import { execFile } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative, sep } from "node:path";
import { promisify } from "node:util";

import ts from "typescript";

import { PLATFORM } from "./paths.ts";

/** A Worker's config, as untyped data: each guard reads it field by field. */
export type Config = Record<string, unknown>;

/** A config rule's module type: wrangler's and miniflare's, the same names. */
type RuleType = "ESModule" | "CommonJS" | "Text" | "Data" | "CompiledWasm" | "PythonModule"
  | "PythonRequirement";

/** The fields of a Worker's config the auxiliary Workers below are built from. */
export interface WorkerConfig {
  readonly name: string;
  readonly compatibility_date: string;
  readonly compatibility_flags?: readonly string[];
  readonly r2_buckets?: readonly { readonly binding: string }[];
  readonly durable_objects?: { readonly bindings: readonly { readonly name: string;
                                                              readonly class_name: string;
                                                              readonly script_name?: string }[] };
  readonly migrations?: readonly Readonly<Record<string, unknown>>[];
  readonly rules?: readonly { readonly type: RuleType; readonly globs: readonly string[];
                              readonly fallthrough?: boolean }[];
  readonly vars?: Readonly<Record<string, unknown>>;
  readonly kv_namespaces?: readonly { readonly binding: string; readonly id: string }[];
  readonly services?: readonly { readonly binding: string; readonly service: string;
                                 readonly entrypoint?: string }[];
  readonly queues?: { readonly producers?: readonly { readonly binding: string;
                                                      readonly queue: string }[];
                      readonly consumers?: readonly Readonly<Record<string, unknown>>[] };
  readonly main?: string;
  readonly secrets?: { readonly required?: readonly string[] };
  readonly assets?: Readonly<Record<string, unknown>>;
}

/** `text`, a wrangler config in JSON with comments, parsed; loud when it does not parse. */
export function parseWrangler(text: string, fileName = "wrangler.jsonc"): Config {
  const parsed = ts.parseConfigFileTextToJson(fileName, text);
  if (parsed.error) {
    throw new Error(`${fileName}: ${ts.flattenDiagnosticMessageText(parsed.error.messageText,
                                                                     "\n")}`);
  }
  return parsed.config as Config;
}

/** The config at `where`: a config file, or a Worker's folder (its `wrangler.jsonc`). */
export function readWrangler(where: string): Config {
  const path = /\.(?:jsonc?|toml)$/.test(where) ? where : join(where, "wrangler.jsonc");
  return parseWrangler(readFileSync(path, "utf8"), path);
}

/** A Worker's config, typed for the fields its auxiliary Worker is built from. */
export function workerConfig(dir: string): WorkerConfig {
  return readWrangler(dir) as unknown as WorkerConfig;
}

/** The platform's Workers: each folder of `platform/` that holds a `wrangler.jsonc`, sorted. */
export function workerFolders(): string[] {
  return readdirSync(PLATFORM, { withFileTypes: true })
    .filter((entry) => entry.isDirectory()
                       && existsSync(join(PLATFORM, entry.name, "wrangler.jsonc")))
    .map((entry) => entry.name).sort();
}

/** The wrangler package the platform installs (hoisted to `platform/node_modules`). */
const WRANGLER_PACKAGE = dirname(createRequire(join(PLATFORM, "package.json"))
  .resolve("wrangler/package.json"));
/** Its command line, run with Node. */
export const WRANGLER = join(WRANGLER_PACKAGE, "bin", "wrangler.js");

/** The keys wrangler's own schema offers at the top level, or in an environment. */
export function wranglerKeys(scope: "RawConfig" | "RawEnvironment" = "RawConfig"): string[] {
  const schema = JSON.parse(readFileSync(join(WRANGLER_PACKAGE, "config-schema.json"), "utf8")) as
    { definitions: Record<string, { properties: Record<string, unknown> }> };
  return Object.keys(schema.definitions[scope]!.properties);
}

/** One module of a bundle, as miniflare takes it. */
export interface Module {
  readonly type: "ESModule" | "CompiledWasm" | "Data";
  readonly path: string;
  readonly contents: Uint8Array;
}

/** One file of a bundle written out: its name and how a Worker loads it. */
export interface Built {
  readonly file: string;
  readonly type: Module["type"];
}

/**
 * The Worker at `dir` bundled by wrangler into `out`, as a deploy bundles it (its own build
 * step included), from its own config or `entry` in its place; its files, the entry first
 * (miniflare takes the first module as the main one). A file the config's `rules` import as
 * data (the sandbox's runner, written as `<hash>-runner.mjs`) is data, wasm is compiled, and
 * every other script is an ES module.
 *
 * Never synchronous: a test pool's Workers are built in vitest's main process, which serves
 * every test worker's module loads, so a blocking build there stalls every other project's
 * imports for as long as it runs (five Workers, about 6 s).
 */
export async function bundle(dir: string, out: string, entry?: string): Promise<Built[]> {
  await promisify(execFile)(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", out,
                                               "--config", join(dir, "wrangler.jsonc"),
                                               ...(entry === undefined ? [] : [entry])],
                            { cwd: dir, env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
  const data = (workerConfig(dir).rules ?? []).filter((rule) => rule.type === "Data")
    .flatMap((rule) => rule.globs.map((glob) => glob.split("/").at(-1)!));
  const isData = (file: string) => data.some((name) => file === name || file.endsWith(`-${name}`));
  const main = entry === undefined ? "index.js" : basename(entry).replace(/\.[cm]?[jt]s$/, ".js");
  return readdirSync(out).filter((file) => /\.(?:m?js|wasm)$/.test(file) || isData(file))
    .sort((a, b) => Number(b === main) - Number(a === main) || a.localeCompare(b))
    .map((file) => ({ file, type: isData(file) ? "Data" as const
      : file.endsWith(".wasm") ? "CompiledWasm" as const : "ESModule" as const }));
}

/** The Worker at `dir` bundled (`bundle`), its modules read, named as files beside its config. */
export async function bundled(dir: string, entry?: string): Promise<Module[]> {
  const out = mkdtempSync(join(tmpdir(), `bundle-${basename(dir)}-`));
  try {
    return (await bundle(dir, out, entry)).map(({ file, type }) => ({
      type, path: join(dir, file), contents: readFileSync(join(out, file)) }));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/** One Durable Object binding as miniflare takes it: a class, its script's when another's. */
interface ObjectBinding {
  readonly className: string;
  readonly scriptName?: string;
  readonly useSQLite: true;
}

/**
 * The Durable Object namespaces a Worker's config gives it in miniflare: each of its
 * bindings (another script's class by its `script_name`), and a namespace of each class it
 * defines (`migrations[].new_sqlite_classes`) that no binding of its own names, bound by the
 * class's name. workerd defines a class only through a namespace of the Worker that holds it,
 * so a Worker whose class only other Workers bind (the owner's, which the doors bind by
 * `script_name`) does not start without one; its code never reads that binding. A migration
 * that does more than add SQLite classes fails loudly: these are the only fields read.
 */
export function objectBindings(config: WorkerConfig): Record<string, ObjectBinding> {
  const bindings = config.durable_objects?.bindings ?? [];
  const own = new Set(bindings.filter((binding) => binding.script_name === undefined)
    .map((binding) => binding.class_name));
  const classes = (config.migrations ?? []).flatMap((migration) => {
    const other = Object.keys(migration).filter((key) => !["tag", "new_sqlite_classes"]
      .includes(key));
    if (other.length > 0) {
      throw new Error(`${config.name}: migration ${String(migration["tag"])} has ` +
                      `${other.join(", ")}; the Worker kit reads only new_sqlite_classes`);
    }
    return (migration["new_sqlite_classes"] ?? []) as readonly string[];
  });
  return {
    ...Object.fromEntries(bindings.map((binding) => [binding.name, {
      className: binding.class_name,
      ...(binding.script_name === undefined ? {} : { scriptName: binding.script_name }),
      useSQLite: true as const }])),
    ...Object.fromEntries(classes.filter((name) => !own.has(name))
      .map((name) => [name, { className: name, useSQLite: true as const }])),
  };
}

/** One kind of binding as miniflare takes it: a record by binding name, or a list of names. */
type Named = Readonly<Record<string, unknown>> | readonly string[];

/**
 * The config keys the kit reads, and those it knowingly leaves out. A key in neither fails
 * loudly (`configBindings`), so a binding a config gains never goes missing in tests. Left
 * out: what names or deploys the Worker (`workers_dev`, `preview_urls`, `build`, which
 * `bundle` runs), `limits` (wrangler's own conversion carries none to miniflare either), and
 * `secrets`, which a config names without a value: a test states each (`configured`).
 */
const READ_KEYS: ReadonlySet<string> = new Set([
  "$schema", "name", "main", "compatibility_date", "compatibility_flags", "rules", "vars",
  "kv_namespaces", "services", "queues", "durable_objects", "migrations", "r2_buckets", "assets",
  "workers_dev", "preview_urls", "build", "limits", "secrets",
]);

/** A queue consumer's config fields, as miniflare names them (`max_concurrency`: none). */
const CONSUMER_FIELDS: Readonly<Record<string, string | null>> = Object.freeze({
  queue: null, max_batch_size: "maxBatchSize", max_batch_timeout: "maxBatchTimeout",
  max_retries: "maxRetries", dead_letter_queue: "deadLetterQueue", retry_delay: "retryDelay",
  // miniflare delivers each queue's batches one at a time; wrangler drops it too.
  max_concurrency: null,
});

/**
 * The bindings a Worker's config gives it in miniflare, by miniflare's option, each kind
 * only when the config has one: its vars, KV namespaces, queue producers and consumers and
 * services, as wrangler's own conversion hands a Worker its own; its
 * namespaces (`objectBindings`); and its buckets. Never a secret: a config names its secrets
 * and holds no value, and nothing here reads the environment (wrangler's conversion reads
 * `.dev.vars`, `.env` and `process.env` for them, and for any var the environment names).
 */
export function configBindings(config: WorkerConfig): Record<string, Named> {
  const unread = Object.keys(config).filter((key) => !READ_KEYS.has(key));
  const queues = Object.keys(config.queues ?? {}).filter((key) => key !== "producers"
                                                                  && key !== "consumers");
  if (unread.length + queues.length > 0) {
    throw new Error(`${config.name}: the Worker kit does not carry ` +
                    `${[...unread, ...queues.map((key) => `queues.${key}`)].join(", ")}`);
  }
  const kinds: Record<string, Named> = {
    bindings: { ...config.vars },
    kvNamespaces: Object.fromEntries((config.kv_namespaces ?? [])
      .map((kv) => [kv.binding, { id: kv.id }])),
    queueProducers: Object.fromEntries((config.queues?.producers ?? [])
      .map((producer) => [producer.binding, { queueName: producer.queue }])),
    queueConsumers: Object.fromEntries((config.queues?.consumers ?? [])
      .map((consumer) => [String(consumer["queue"]), consumerOf(config.name, consumer)])),
    serviceBindings: Object.fromEntries((config.services ?? []).map((service) => [
      service.binding, { name: service.service, ...(service.entrypoint === undefined ? {}
        : { entrypoint: service.entrypoint }) }])),
    durableObjects: objectBindings(config),
    r2Buckets: (config.r2_buckets ?? []).map((bucket) => bucket.binding),
  };
  return Object.fromEntries(Object.entries(kinds)
    .filter(([, named]) => Object.keys(named).length > 0));
}

/** One queue consumer's config as miniflare takes it; a field the kit does not know fails. */
function consumerOf(worker: string, consumer: Readonly<Record<string, unknown>>) {
  return Object.fromEntries(Object.entries(consumer).flatMap(([field, value]) => {
    if (!(field in CONSUMER_FIELDS)) {
      throw new Error(`${worker}: the Worker kit does not carry queues.consumers[].${field}`);
    }
    const name = CONSUMER_FIELDS[field];
    return name === null ? [] : [[name, value]];
  }));
}

/** The assets fields the kit reads (wrangler's own `assets` options, from them). */
const ASSET_FIELDS: ReadonlySet<string> = new Set(["directory", "binding", "run_worker_first",
                                                   "html_handling", "not_found_handling"]);

/** A Worker's static assets as miniflare takes them, as wrangler builds them; or none. */
function assetsOf(config: WorkerConfig, dir: string): object {
  const assets = config.assets;
  if (assets === undefined) {
    return {};
  }
  const unread = Object.keys(assets).filter((field) => !ASSET_FIELDS.has(field));
  if (unread.length > 0 || typeof assets["run_worker_first"] !== "boolean") {
    throw new Error(`${config.name}: the Worker kit reads assets' ${[...ASSET_FIELDS]
      .join(", ")}, run_worker_first a boolean; not ${unread.join(", ")}`);
  }
  return { assets: {
    directory: join(dir, String(assets["directory"])),
    binding: assets["binding"],
    run_worker_first: assets["run_worker_first"],
    routerConfig: { has_user_worker: true,
                    invoke_user_worker_ahead_of_assets: assets["run_worker_first"] },
    assetConfig: {
      ...(assets["html_handling"] === undefined ? {} : { html_handling: assets["html_handling"] }),
      ...(assets["not_found_handling"] === undefined ? {}
        : { not_found_handling: assets["not_found_handling"] }),
      compatibility_date: config.compatibility_date,
      compatibility_flags: [...(config.compatibility_flags ?? [])],
    },
  } };
}

/** `named` as a record by binding name, a list of names read as miniflare reads one. */
function byName(named: Named): Readonly<Record<string, unknown>> {
  return Array.isArray(named) ? Object.fromEntries(named.map((name: string) => [name, name]))
    : named as Readonly<Record<string, unknown>>;
}

/**
 * The config's bindings (`configBindings`) with a test's changes: in a kind of binding the
 * config has, the test's binding replaces the config's of its name, and the rest stay.
 */
export function withChanges(config: WorkerConfig, extra: object): Record<string, Named> {
  const changed = extra as Readonly<Record<string, unknown>>;
  return Object.fromEntries(Object.entries(configBindings(config)).map(([kind, named]) =>
    [kind, changed[kind] === undefined ? named
      : { ...byName(named), ...byName(changed[kind] as Named) }]));
}

/**
 * The Worker's options as its config gives them, with a test's `extra`: its runtime and
 * flags, its assets, and every binding (`withChanges`); any option that is no kind of
 * binding the config has is the test's. Every secret its config requires is the test's to
 * state, and one left out fails loudly: none is ever read from the host.
 */
function configured(config: WorkerConfig, dir: string, extra: object) {
  const own = withChanges(config, extra);
  const stated = (own["bindings"] ?? (extra as { bindings?: object }).bindings ?? {}) as object;
  const missing = (config.secrets?.required ?? []).filter((name) => !(name in stated));
  if (missing.length > 0) {
    throw new Error(`${config.name}: a test states each secret its config requires, and ` +
                    `${missing.join(", ")} is not stated`);
  }
  return {
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: [...(config.compatibility_flags ?? [])],
    ...assetsOf(config, dir),
    ...extra,
    ...(own as object),
  };
}

/**
 * The real Worker at `dir`, as an auxiliary Worker beside a test's own: bundled from its own
 * config and entry (or `entry` in its place), as a deploy bundles it, with its own name and
 * everything its config gives it (`configured`), as `main` below is. A test binds its
 * classes by `scriptName`, as other Workers' configs do. Its secrets and only what a test
 * changes are the caller's `extra`.
 */
export async function auxiliary<Extra extends object>(dir: string, extra: Extra,
                                                       entry?: string) {
  const config = workerConfig(dir);
  return {
    name: config.name,
    modulesRoot: dir,
    modules: await bundled(dir, entry),
    ...configured(config, dir, extra),
  };
}

/**
 * A test pool's own Worker (`cloudflareTest`'s `main`) built from the config at `dir`, as
 * `auxiliary` builds one beside it: its entry (`entry`, or the config's own), its module
 * rules, and everything its config gives it, with the test's secrets and changes (`extra`,
 * its auxiliary Workers among them). The pool never reads the config itself (its `wrangler`
 * option), so nothing comes from `.dev.vars`, `.env` or `process.env`.
 */
export function main<Extra extends object>(dir: string, extra: Extra, entry?: string) {
  const config = workerConfig(dir);
  const rules = (config.rules ?? []).map((rule) => ({
    type: rule.type, include: [...rule.globs],
    ...(rule.fallthrough === undefined ? {} : { fallthrough: rule.fallthrough }) }));
  return {
    main: entry ?? join(dir, String(config.main)),
    miniflare: { rootPath: dir, ...configured(config, dir, extra),
                 ...(rules.length === 0 ? {} : { modulesRules: rules }) },
  };
}

/** Every secret a Worker's config requires, by name, across the platform's Workers. */
export function declaredSecrets(): string[] {
  return [...new Set(workerFolders().flatMap((folder) =>
    workerConfig(join(PLATFORM, folder)).secrets?.required ?? []))].sort();
}

/**
 * Where `options` carry the host's value of a declared secret (`declaredSecrets`): each
 * string in them equal to a non-empty `process.env` value of one, by its path and the
 * secret's name. Never the value.
 */
export function hostSecrets(options: unknown): string[] {
  const host = new Map(declaredSecrets().flatMap((name) => {
    const value = process.env[name];
    return value === undefined || value === "" ? [] : [[value, name] as const];
  }));
  const found: string[] = [];
  const walk = (value: unknown, path: string): void => {
    if (typeof value === "string") {
      const name = host.get(value);
      if (name !== undefined) {
        found.push(`${path} is the host's ${name}`);
      }
    } else if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, `${path}[${index}]`));
    } else if (value !== null && typeof value === "object" && !ArrayBuffer.isView(value)) {
      for (const [key, item] of Object.entries(value)) {
        walk(item, path === "" ? key : `${path}.${key}`);
      }
    }
  };
  walk(options, "");
  return found;
}

/**
 * A test's Workers' options, checked before they run
 * (`cloudflareTest(once(async () => pool(...)))`, and the doors' browser test's Miniflare): refused when they read a config through the pool
 * (`wrangler`, or `experimental`'s config file: wrangler's conversion fills either's secrets
 * and vars from the host) or carry any host secret (`hostSecrets`).
 */
export function pool<Options extends object>(options: Options): Options {
  const reads = ["wrangler", "experimental"].filter((option) => option in options);
  if (reads.length > 0) {
    throw new Error(`a test pool reads no config itself (its ${reads.join(" and ")} option ` +
                    "fills secrets and vars from the host): build its Worker with " +
                    "ci/workers.ts main()");
  }
  const found = hostSecrets(options);
  if (found.length > 0) {
    throw new Error(`a test's Workers carry a host secret: ${found.join("; ")}`);
  }
  return options;
}

/**
 * `make`, run once, when first asked: a test pool's options (`cloudflareTest(once(...))`), so
 * its Workers are built when its project runs, not each time its config is loaded (by a
 * project that is not run, or a guard that reads the config), and built once however many
 * pool workers start.
 */
export function once<T>(make: () => T): () => T {
  let made: { value: T } | null = null;
  return () => {
    made ??= { value: make() };
    return made.value;
  };
}

/** `path`'s TypeScript as the JavaScript miniflare runs. */
export function transpiled(path: string): string {
  return ts.transpileModule(readFileSync(path, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
}

/**
 * The fake Worker at `path` (`<workspace>/test/fake-*.ts`) as `fake()` runs it: named as its
 * file, in its workspace, at its workspace's Worker's compatibility date.
 */
export function fakeConfig(path: string): { name: string; workspace: string;
                                           compatibility_date: string } {
  const workspace = join(PLATFORM, relative(PLATFORM, path).split(sep)[0]!);
  return { name: basename(path, ".ts"), workspace,
           compatibility_date: workerConfig(workspace).compatibility_date };
}

/** A test's fake outside world, transpiled into an auxiliary Worker (`fakeConfig`). */
export function fake<Extra extends object = object>(path: string, extra: Extra = {} as Extra) {
  const config = fakeConfig(path);
  return {
    name: config.name,
    modulesRoot: config.workspace,
    modules: [{ type: "ESModule" as const, path: path.replace(/\.ts$/, ".js"),
                contents: transpiled(path) }],
    compatibilityDate: config.compatibility_date,
    ...extra,
  };
}
