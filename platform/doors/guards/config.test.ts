/**
 * The doors Worker's config binds exactly what the doors need, and cannot deploy
 * (research §13): its allow-list (`rules.ts`), held by the platform's config checker
 * (`ci/kit/config.ts`), and what it binds held against the other Workers' configs and
 * sources, each named by its folder and read from its own config:
 *
 * - `OWNER` (`OBJECTS`): the owner Worker's `OwnerDO` namespace, by the owner Worker's name,
 *   which must be its own SQLite class; the owner Worker declares the same routing key
 *   (`SHARED_SECRETS`).
 * - `GATEWAY`, `EGRESS` and `FILES` (`SERVICES`): each one entrypoint of its Worker, whose
 *   class in that Worker's entry extends `WorkerEntrypoint` and serves exactly what the rules
 *   list. Only `src/connect.ts`, `src/egress.ts` and `src/file-bytes.ts` name them (the
 *   binder guard).
 * - Its queues: it consumes exactly the queues the platform's Workers produce onto, so the
 *   entry's dispatch by `QUEUES` (generated from those producers) takes every one; and no
 *   other Worker binds its buckets or produces onto its queues (the operator channel's,
 *   `OPERATOR_CHANNEL`, and the import window's, `IMPORT_WINDOW`, deleted at the cutover).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { configProblems, configTests, patched } from "../../ci/kit/config";
import { PLATFORM } from "../../ci/paths";
import { type Config, platformConfigs } from "../../ci/kit/sources";
import { readWrangler } from "../../ci/wrangler";

import {
  CONFIG, IMPORT_WINDOW, OBJECTS, OPERATOR_CHANNEL, SERVICES, SHARED_SECRETS,
} from "./rules";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/**
 * The members each class a source exports serves, by class name: its own public methods
 * and properties, read from the syntax tree.
 */
export function exportedMethods(source: string): Map<string, string[]> {
  const file = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true);
  const found = new Map<string, string[]>();
  file.forEachChild((node) => {
    if (ts.isClassDeclaration(node) && node.name !== undefined
        && node.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) {
      found.set(node.name.text, node.members.filter((member) =>
        (ts.isMethodDeclaration(member) || ts.isPropertyDeclaration(member)
         || ts.isGetAccessorDeclaration(member))
        && !ts.isPrivateIdentifier(member.name)
        && !ts.getModifiers(member)?.some((m) => m.kind === ts.SyntaxKind.PrivateKeyword
                                                 || m.kind === ts.SyntaxKind.ProtectedKeyword))
        .map((member) => member.name!.getText(file)).sort());
    }
  });
  return found;
}

/**
 * What the exported class `name` of `source` extends, its type arguments left out, or null:
 * an entrypoint must extend `WorkerEntrypoint` itself, since RPC serves inherited methods
 * too (review L6-1).
 */
export function extendsOf(source: string, name: string): string | null {
  const file = ts.createSourceFile("index.ts", source, ts.ScriptTarget.Latest, true);
  let found: string | null = null;
  file.forEachChild((node) => {
    if (ts.isClassDeclaration(node) && node.name?.text === name) {
      const clause = node.heritageClauses?.find((each) =>
        each.token === ts.SyntaxKind.ExtendsKeyword);
      found = clause === undefined || clause.types.length !== 1 ? null
        : clause.types[0]!.expression.getText(file);
    }
  });
  return found;
}

/** The queues `config` produces onto. */
function produced(config: Config): string[] {
  const producers = (config["queues"] as { producers?: unknown } | undefined)?.producers;
  return Array.isArray(producers) ? producers.map((each: Config) => String(each["queue"])) : [];
}

/** The buckets `config` binds, by bucket name. */
function bucketNames(config: Config): string[] {
  const buckets = config["r2_buckets"];
  return Array.isArray(buckets) ? buckets.map((each: Config) => String(each["bucket_name"])) : [];
}

/**
 * Each way `config` binds more than the doors need, or could deploy, against the platform's
 * other configs (`platform`, by Worker folder) and their entries' sources (`sources`, by
 * Worker folder).
 */
export function doorsConfigProblems(config: Config, platform: ReadonlyMap<string, Config>,
                                    sources: ReadonlyMap<string, string>): string[] {
  const problems = configProblems(config, CONFIG);
  const nameOf = (worker: string) => platform.get(worker)?.["name"];
  for (const [worker, names] of Object.entries(SHARED_SECRETS)) {
    const declared = (platform.get(worker)?.["secrets"] as { required?: unknown } | undefined)
      ?.required;
    if (!Array.isArray(declared) || !names.every((name) => declared.includes(name))) {
      problems.push(`the ${worker} Worker declares ${names.join(", ")} too`);
    }
  }
  const objects = Object.entries(OBJECTS).map(([name, { worker, class_name }]) =>
    ({ name, class_name, script_name: nameOf(worker) }));
  if (!same(config["durable_objects"], { bindings: objects })) {
    problems.push(`durable_objects binds exactly ${Object.entries(OBJECTS).map(([name, each]) =>
      `${name} to the ${each.worker} Worker's ${each.class_name}`).join(", ")}`);
  } else {
    for (const { worker, class_name } of Object.values(OBJECTS)) {
      const made = ((platform.get(worker)?.["migrations"] ?? []) as
                    { new_sqlite_classes?: string[] }[]).flatMap((m) => m.new_sqlite_classes ?? []);
      if (!made.includes(class_name)) {
        problems.push(`the ${worker} Worker makes no SQLite ${class_name}`);
      }
    }
  }
  const services = Object.entries(SERVICES).map(([binding, { worker, entrypoint }]) =>
    ({ binding, service: nameOf(worker), entrypoint }));
  if (!same(config["services"], services)) {
    problems.push(`services binds exactly ${Object.entries(SERVICES).map(([binding, each]) =>
      `${binding} to the ${each.worker} Worker's ${each.entrypoint}`).join(", ")}`);
  } else {
    for (const { worker, entrypoint, serves } of Object.values(SERVICES)) {
      const source = sources.get(worker) ?? "";
      if (!same(exportedMethods(source).get(entrypoint), serves)
          || extendsOf(source, entrypoint) !== "WorkerEntrypoint") {
        problems.push(`the ${worker} Worker's ${entrypoint} extends WorkerEntrypoint and ` +
                      `serves exactly ${serves.join(", ")}`);
      }
    }
  }
  const others = [...platform].filter(([worker]) => worker !== CONFIG.worker);
  const consumers = (config["queues"] as { consumers?: { queue: string }[] } | undefined)
    ?.consumers ?? [];
  const everyProduced = new Set([config, ...others.map(([, other]) => other)].flatMap(produced));
  if (!same(consumers.map((each) => each.queue).sort(), [...everyProduced].sort())) {
    problems.push("the doors consume exactly the queues the platform's Workers produce onto");
  }
  for (const [worker, other] of others) {
    for (const bucket of bucketNames(other).filter((name) => bucketNames(config).includes(name))) {
      problems.push(`the ${worker} Worker binds the doors' bucket ${bucket}`);
    }
    for (const queue of produced(other).filter((name) => produced(config).includes(name))) {
      problems.push(`the ${worker} Worker produces onto the doors' queue ${queue}`);
    }
  }
  return problems;
}

const config = readWrangler(DOORS);
const platform = platformConfigs();
/** The entry of each Worker the doors bind a service of, by folder. */
const SOURCES: ReadonlyMap<string, string> = new Map(
  [...new Set(Object.values(SERVICES).map((each) => each.worker))].map((worker) =>
    [worker, readFileSync(join(PLATFORM, worker, String(platform.get(worker)!["main"])),
                          "utf8")]));
/** The doors' own bindings, as the config holds them, for the probes. */
const SERVICE = Object.fromEntries((config["services"] as Config[]).map((each) =>
  [String(each["binding"]), each])) as Record<keyof typeof SERVICES, Config>;
const { GATEWAY, EGRESS, FILES } = SERVICE;
const QUEUES = config["queues"] as { producers: Config[]; consumers: Config[] };
const IMPORTS = QUEUES.producers.find((each) => each["binding"] === IMPORT_WINDOW.producer)!;
const OPERATOR = QUEUES.producers.find((each) => each["binding"] === OPERATOR_CHANNEL.producer)!;
const IMPORT_CONSUMER = QUEUES.consumers.find((each) => each["queue"] === IMPORTS["queue"])!;
const OPERATOR_CONSUMER = QUEUES.consumers.find((each) => each["queue"] === OPERATOR["queue"])!;
const bucketBound = (binding: string) => (config["r2_buckets"] as Config[])
  .find((each) => each["binding"] === binding)!;
const STAGING = bucketBound(IMPORT_WINDOW.bucket);
const OPERATOR_LOG = bucketBound(OPERATOR_CHANNEL.bucket);
const SECRETS = (config["secrets"] as { required: string[] }).required;
const OWNER_NAMESPACE = (config["durable_objects"] as { bindings: Config[] }).bindings[0]!;
const FILES_BUCKET = String((platform.get("files")!["r2_buckets"] as Config[])[0]!["bucket_name"]);
const ASSETS = CONFIG.exact!["assets"] as Config;
const FLAGS = config["compatibility_flags"] as string[];
const VARS = Object.fromEntries(Object.entries(CONFIG.vars!).map(([name, values]) =>
  [name, values![0]!]));
const scriptOf = (worker: string) => String(platform.get(worker)!["name"]);
/** The entrypoints' heads, whatever contract each implements. */
const head = (source: string, entrypoint: string, env: string) => new RegExp(
  `export class ${entrypoint} extends WorkerEntrypoint<${env}>[^{]*\\{`).exec(source)![0];
const GATEWAY_SOURCE = SOURCES.get("gateway")!;
const FILES_SOURCE = SOURCES.get("files")!;
const UPLOAD_HEAD = head(FILES_SOURCE, "FilesUpload", "FilesEnv");

configTests(CONFIG, {
  refused: ["routes", "route", "env", "migrations", "ratelimits", "d1_databases",
            "worker_loaders", "unsafe", "tail_consumers", "dispatch_namespaces"],
  problems: (probe) => doorsConfigProblems(probe, platform, SOURCES),
  probes: [
    ["a route", { routes: [{ pattern: "example.test/mcp*", zone_name: "example.test" }] }],
    ["a custom domain", { route: { pattern: "example.test", custom_domain: true } }],
    ["workers.dev", { workers_dev: true }],
    ["preview URLs", { preview_urls: true }],
    ["an environment", { env: { prod: { routes: ["example.test/*"] } } }],
    ["one service", { services: [{ binding: "FILES", service: FILES["service"] }] }],
    ["no service", { services: [] }],
    ["no files service", { services: [GATEWAY, EGRESS] }],
    ["no egress entrypoint", { services: [GATEWAY, FILES] }],
    ["the egress door on the gateway's default entrypoint", { services: [GATEWAY,
      { binding: "EGRESS", service: EGRESS["service"] }, FILES] }],
    ["the egress door on the connect entrypoint", { services: [GATEWAY,
      { ...EGRESS, entrypoint: GATEWAY["entrypoint"] }, FILES] }],
    ["the egress door under another name", { services: [GATEWAY, { ...EGRESS, binding: "OUT" },
                                                        FILES] }],
    ["the gateway's whole surface", { services: [{ binding: "GATEWAY",
                                                   service: GATEWAY["service"] }, EGRESS,
                                                 FILES] }],
    ["the gateway's default entrypoint", { services: [{ ...GATEWAY, entrypoint: "Gateway" },
                                                      EGRESS, FILES] }],
    ["the gateway under another name", { services: [{ ...GATEWAY, binding: "MODELS" }, EGRESS,
                                                    FILES] }],
    ["FILES to another Worker", { services: [GATEWAY, EGRESS, { ...FILES,
                                                                service: scriptOf("owner") }] }],
    ["the files Worker's whole surface (review M5-1)", { services: [GATEWAY, EGRESS,
      { binding: "FILES", service: FILES["service"] }] }],
    ["the files Worker's default entrypoint", { services: [GATEWAY, EGRESS,
      { ...FILES, entrypoint: "FilesService" }] }],
    ["a fourth service", { services: [GATEWAY, EGRESS, FILES,
      { binding: "CODEMODE_VM", service: scriptOf("codemode") }] }],
    ["no app files", { assets: undefined }],
    ["app files served past the Worker", { assets: { ...ASSETS, run_worker_first: false } }],
    ["app files from another directory", { assets: { ...ASSETS, directory: "./src" } }],
    ["the assets' own not-found page", { assets: { ...ASSETS,
                                                   not_found_handling: "single-page-application" } }],
    ["a bucket", { r2_buckets: [{ binding: "BLOBS", bucket_name: FILES_BUCKET }] }],
    ["the files Worker's bucket as the staging bucket", { r2_buckets: [
      { ...STAGING, bucket_name: FILES_BUCKET }, OPERATOR_LOG] }],
    ["the files Worker's bucket as the operator bucket", { r2_buckets: [
      STAGING, { ...OPERATOR_LOG, bucket_name: FILES_BUCKET }] }],
    ["a third bucket", { r2_buckets: [STAGING, OPERATOR_LOG,
                                      { binding: "BLOBS", bucket_name: "b" }] }],
    ["no staging bucket", { r2_buckets: [OPERATOR_LOG] }],
    ["no operator bucket", { r2_buckets: [STAGING] }],
    ["no queues", { queues: undefined }],
    ["a queue producer", { queues: { ...QUEUES, producers: [...QUEUES.producers,
                                                            { binding: "OUT", queue: "q" }] } }],
    ["the import producer onto another Worker's queue", { queues: { ...QUEUES, producers: [
      { ...IMPORTS, queue: produced(platform.get("owner")!)[0] }, OPERATOR] } }],
    ["the operator producer onto another Worker's queue", { queues: { ...QUEUES, producers: [
      IMPORTS, { ...OPERATOR, queue: produced(platform.get("owner")!)[0] }] } }],
    ["no import producer", { queues: { ...QUEUES, producers: [OPERATOR] } }],
    ["no operator producer", { queues: { ...QUEUES, producers: [IMPORTS] } }],
    ["no import consumer", { queues: { ...QUEUES, consumers: QUEUES.consumers.filter((each) =>
      each !== IMPORT_CONSUMER) } }],
    ["no operator consumer", { queues: { ...QUEUES, consumers: QUEUES.consumers.filter((each) =>
      each !== OPERATOR_CONSUMER) } }],
    ["no ring consumer", { queues: { ...QUEUES, consumers: QUEUES.consumers.filter((each) =>
      each["queue"] !== produced(platform.get("sandbox")!)[0]) } }],
    ["another queue's consumer", { queues: { ...QUEUES, consumers: [...QUEUES.consumers,
                                                                    { queue: "q" }] } }],
    ["a migration", { migrations: [{ tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["a rate limit", { ratelimits: [{ name: "KEY_RL", namespace_id: "1",
                                      simple: { limit: 120, period: 60 } }] }],
    ["an upstream before the deploy", { vars: { ...VARS, UPSTREAM: "{}" } }],
    ["no WORKOS_API_KEY", { secrets: { required: SECRETS.filter((name) =>
      name !== "WORKOS_API_KEY") } }],
    ["no OPERATOR_KEYRING", { secrets: { required: SECRETS.filter((name) =>
      name !== "OPERATOR_KEYRING") } }],
    ["the import window's key required, so every deploy would set it", { secrets: {
      required: [...SECRETS, "IMPORT_KEY"] } }],
    ["no strictly-public fetch", { compatibility_flags: FLAGS.filter((flag) =>
      flag !== "global_fetch_strictly_public") }],
    ["ctx.exports", { compatibility_flags: [...FLAGS, "enable_ctx_exports"] }],
    ["DCR on", { vars: { ...VARS, DCR_ENABLED: "true" } }],
    ["an owner issuer before AuthKit", { vars: { ...VARS, OWNER_ISSUER: "https://evil.example" } }],
    ["a foreign issuer before its slice", { vars: { ...VARS,
      FOREIGN_ISSUERS: "[\"https://auth.openai.com\"]" } }],
    ["no foreign issuer list", { vars: { DCR_ENABLED: "false", OWNER_ISSUER: "not-configured" } }],
    ["another var", { vars: { ...VARS, OWNER: "alice" } }],
    ["OWNER to another script", { durable_objects: { bindings: [
      { ...OWNER_NAMESPACE, script_name: scriptOf("files") }] } }],
    ["OWNER in this script", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a second namespace", { durable_objects: { bindings: [
      OWNER_NAMESPACE, { ...OWNER_NAMESPACE, name: "OTHER", class_name: "X" }] } }],
    ["a real KV id", { kv_namespaces: [{ binding: "OAUTH_KV", id: "0f2ac74b498b48028cb68387c421e279" },
                                       { binding: "DIRECTORY", id: "not-provisioned-directory" },
                                       { binding: "CONTROL", id: "not-provisioned-control" }] }],
    ["one shared KV namespace", { kv_namespaces: [{ binding: "OAUTH_KV", id: "not-provisioned-x" },
                                                  { binding: "DIRECTORY", id: "not-provisioned-x" },
                                                  { binding: "CONTROL", id: "not-provisioned-c" }] }],
    ["no control namespace", { kv_namespaces: [
      { binding: "OAUTH_KV", id: "not-provisioned-oauth" },
      { binding: "DIRECTORY", id: "not-provisioned-directory" }] }],
    ["a fourth KV namespace", { kv_namespaces: [
      { binding: "OAUTH_KV", id: "not-provisioned-oauth" },
      { binding: "DIRECTORY", id: "not-provisioned-directory" },
      { binding: "CONTROL", id: "not-provisioned-control" },
      { binding: "CACHE", id: "not-provisioned-cache" }] }],
  ],
});

describe("doors Worker config", () => {
  it("binds what its rules allow of the Workers they name, and cannot deploy", () => {
    expect(doorsConfigProblems(config, platform, SOURCES)).toEqual([]);
  });

  it.each([["owner", "OWNER's script"], ["gateway", "GATEWAY's service"],
           ["files", "FILES' service"]])("sees the %s Worker missing from the platform (%s)",
                                         (worker) => {
    const without = new Map(platform);
    without.delete(worker);
    expect(doorsConfigProblems(config, without, SOURCES)).not.toEqual([]);
  });

  it("sees the owner Worker without the routing key the doors share with it", () => {
    const owner = platform.get("owner")!;
    expect(doorsConfigProblems(config, new Map(platform).set("owner", patched(owner,
      { secrets: undefined })), SOURCES))
      .toEqual(["the owner Worker declares ROUTING_MAC_KEY too"]);
  });

  it.each([
    ["the connect entrypoint", "ConnectGateway", "a model call", "fetch(r: Request) { return r; }"],
    ["the connect entrypoint", "ConnectGateway", "egress forwarding", "forward() { return 1; }"],
    ["the connect entrypoint", "ConnectGateway", "a refresh", "async refresh() { return 1; }"],
    ["the connect entrypoint", "ConnectGateway", "a provider revoke", "revokeAtProvider = () => 1;"],
    ["the egress entrypoint", "EgressGateway", "a model call", "fetch(r: Request) { return r; }"],
    ["the egress entrypoint", "EgressGateway", "a seal", "seal() { return 1; }"],
    ["the egress entrypoint", "EgressGateway", "a refresh", "async refresh() { return 1; }"],
  ])("sees %s grow %s", (_where, entrypoint, _what, member) => {
    const at = head(GATEWAY_SOURCE, entrypoint, "GatewayEnv");
    const grown = GATEWAY_SOURCE.replace(at, `${at}\n  ${member}`);
    expect(grown).not.toBe(GATEWAY_SOURCE);
    expect(doorsConfigProblems(config, platform, new Map(SOURCES).set("gateway", grown)))
      .not.toEqual([]);
  });

  it.each([
    ["a chunk read", "getChunks() { return 1; }"],
    ["a generation's read", "async getManifestPart() { return 1; }"],
    ["a key call", "rewrapKey = () => 1;"],
  ])("sees the upload entrypoint grow %s (review M5-1)", (_what, member) => {
    const grown = FILES_SOURCE.replace(UPLOAD_HEAD, `${UPLOAD_HEAD}\n  ${member}`);
    expect(grown).not.toBe(FILES_SOURCE);
    expect(doorsConfigProblems(config, platform, new Map(SOURCES).set("files", grown)))
      .not.toEqual([]);
  });

  it("sees the upload entrypoint inherit the whole service, its own members unchanged " +
     "(review L6-1)", () => {
    const inherits = FILES_SOURCE.replace(UPLOAD_HEAD,
                                          "export class FilesUpload extends FilesService {");
    // Its own members are still putChunks alone; RPC would serve every inherited method.
    expect(exportedMethods(inherits).get("FilesUpload")).toEqual(SERVICES.FILES.serves);
    expect(doorsConfigProblems(config, platform, new Map(SOURCES).set("files", inherits)))
      .not.toEqual([]);
  });
});

describe("the doors' queues and bucket, against the other Workers", () => {
  it.each([
    ["the files Worker bind the doors' staging bucket", "files", (files: Config) => ({
      r2_buckets: [...(files["r2_buckets"] as Config[]), STAGING] })],
    ["the owner Worker produce onto the doors' import queue", "owner", (owner: Config) => ({
      queues: { producers: [...(owner["queues"] as { producers: Config[] }).producers,
                            { binding: "X", queue: IMPORTS["queue"] }] } })],
    ["the owner Worker produce onto the doors' operator queue", "owner", (owner: Config) => ({
      queues: { producers: [...(owner["queues"] as { producers: Config[] }).producers,
                            { binding: "X", queue: OPERATOR["queue"] }] } })],
    ["the files Worker bind the doors' operator bucket", "files", (files: Config) => ({
      r2_buckets: [...(files["r2_buckets"] as Config[]), OPERATOR_LOG] })],
    ["the sandbox Worker produce onto a queue the doors do not consume", "sandbox",
     (sandbox: Config) => ({ queues: { producers: [
       ...(sandbox["queues"] as { producers: Config[] }).producers,
       { binding: "X", queue: "q" }] } })],
  ])("sees %s", (_what, worker, grow) => {
    const other = platform.get(worker)!;
    expect(doorsConfigProblems(config, new Map(platform).set(worker, patched(other, grow(other))),
                               SOURCES)).not.toEqual([]);
  });
});
