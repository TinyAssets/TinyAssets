/**
 * The Workers as wrangler sees them, for every test, guard and config that needs one: a
 * Worker's config read (JSONC, as wrangler reads it), the wrangler the platform installs,
 * a Worker bundled as a deploy bundles it and handed to miniflare as an auxiliary Worker,
 * and a test fake transpiled into one.
 *
 * Node runs this file as it is (type stripping): `budgets.mjs`, `doors/test/app.e2e.mjs` and
 * `owner/memory/baselines.mjs` import it, so its own imports name their `.ts` files.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { basename, dirname, join, relative, sep } from "node:path";

import ts from "typescript";

import { PLATFORM } from "./paths.ts";

/** A Worker's config, as untyped data: each guard reads it field by field. */
export type Config = Record<string, unknown>;

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
  readonly rules?: readonly { readonly type: string; readonly globs: readonly string[] }[];
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
export const WRANGLER_PACKAGE = dirname(createRequire(join(PLATFORM, "package.json"))
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
 */
export function bundle(dir: string, out: string, entry?: string): Built[] {
  execFileSync(process.execPath, [WRANGLER, "deploy", "--dry-run", "--outdir", out,
                                  "--config", join(dir, "wrangler.jsonc"),
                                  ...(entry === undefined ? [] : [entry])],
               { cwd: dir, stdio: "pipe", env: { ...process.env, WRANGLER_SEND_METRICS: "false" } });
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
export function bundled(dir: string, entry?: string): Module[] {
  const out = mkdtempSync(join(tmpdir(), `bundle-${basename(dir)}-`));
  try {
    return bundle(dir, out, entry).map(({ file, type }) => ({
      type, path: join(dir, file), contents: readFileSync(join(out, file)) }));
  } finally {
    rmSync(out, { recursive: true, force: true });
  }
}

/** One Durable Object binding as miniflare takes it: a class, its script's when another's. */
export interface ObjectBinding {
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

/**
 * The real Worker at `dir`, as an auxiliary Worker beside a test's own: bundled from its own
 * config and entry, as a deploy bundles it, with its own name, runtime and flags, its
 * namespaces (`objectBindings`: so a test binds its classes by `scriptName`, as other
 * Workers' configs do) and its own buckets. Its secrets and anything a test changes are the
 * caller's `extra`.
 */
export function auxiliary<Extra extends object>(dir: string, extra: Extra) {
  const config = workerConfig(dir);
  const objects = objectBindings(config);
  const buckets = config.r2_buckets ?? [];
  return {
    name: config.name,
    modulesRoot: dir,
    modules: bundled(dir),
    compatibilityDate: config.compatibility_date,
    compatibilityFlags: [...(config.compatibility_flags ?? [])],
    ...(Object.keys(objects).length === 0 ? {} : { durableObjects: objects }),
    ...(buckets.length === 0 ? {} : { r2Buckets: buckets.map((bucket) => bucket.binding) }),
    ...extra,
  };
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
 * A test's fake outside world (`<workspace>/test/fake-*.ts`), transpiled into an auxiliary
 * Worker named as its file, at its workspace's Worker's compatibility date.
 */
export function fake<Extra extends object = object>(path: string, extra: Extra = {} as Extra) {
  const workspace = join(PLATFORM, relative(PLATFORM, path).split(sep)[0]!);
  return {
    name: basename(path, ".ts"),
    modulesRoot: workspace,
    modules: [{ type: "ESModule" as const, path: path.replace(/\.ts$/, ".js"),
                contents: transpiled(path) }],
    compatibilityDate: workerConfig(workspace).compatibility_date,
    ...extra,
  };
}
