/**
 * The doors Worker's config binds exactly what the doors need, and cannot deploy
 * (research §13): its allow-list (`rules.ts`), held by the platform's config checker
 * (`ci/kit/config.ts`), and what it binds held against the other Workers:
 *
 * - `OWNER`: the owner Worker's `OwnerDO` namespace, by `script_name`, which must be the
 *   owner Worker's own SQLite class in the platform; the owner Worker declares the same
 *   routing key, `ROUTING_MAC_KEY`.
 * - `GATEWAY`: the gateway Worker's `ConnectGateway` entrypoint, whose class in the
 *   gateway's source serves exactly `seal`, `exchange` and `verify` (the connect steps),
 *   so the doors can make no model call through it and hold no keyring. Only
 *   `src/connect.ts` names it (the binder guard).
 * - `EGRESS`: the gateway Worker's `EgressGateway` entrypoint, whose class serves exactly
 *   `forward` (G4's egress door). Only `src/egress.ts` names it.
 * - `FILES`: the files Worker's `FilesUpload` entrypoint, whose class serves exactly
 *   `putChunks`, what the file door's bytes call (slice L), and nothing that reads a chunk,
 *   a generation or a key (review M5-1: the doors carry an owner's wrapped key); only
 *   `src/file-bytes.ts` names it.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { configProblems, configTests } from "../../ci/kit/config";
import { PLATFORM } from "../../ci/paths";
import { type Config, platformConfigs } from "../../ci/kit/sources";
import { readWrangler } from "../../ci/workers";

import { ERASURE_QUEUE } from "../src/config";

import { CONFIG } from "./rules";

const DOORS = join(dirname(fileURLToPath(import.meta.url)), "..");
/** The sandbox tokens' routing key (`routing-mac.ts`), the owner's one secret too. */
const OWNER_SECRETS = Object.freeze({ required: Object.freeze(["ROUTING_MAC_KEY"]) });
/** The files Worker, for the file door's bytes. */
const FILES = Object.freeze({ binding: "FILES", service: "tinyassets-files",
                             entrypoint: "FilesUpload" });
const UPLOAD_METHODS: readonly string[] = Object.freeze(["putChunks"]);
/** The doors' two services: the gateway's connect and egress entrypoints, and what each serves. */
const GATEWAY = Object.freeze({ binding: "GATEWAY", service: "tinyassets-gateway",
                                entrypoint: "ConnectGateway" });
const CONNECT_METHODS: readonly string[] = Object.freeze(["exchange", "seal", "sealDoorbell",
                                                          "verify", "verifyDoorbell"]);
const EGRESS = Object.freeze({ binding: "EGRESS", service: "tinyassets-gateway",
                               entrypoint: "EgressGateway" });
const EGRESS_METHODS: readonly string[] = Object.freeze(["forward"]);
const VARS = Object.fromEntries(Object.entries(CONFIG.vars!).map(([name, values]) =>
  [name, values![0]!]));
const QUEUES = CONFIG.exact!["queues"] as { consumers: object[] };
const ASSETS = CONFIG.exact!["assets"] as Config;
const FLAGS = (CONFIG.flags as { exactly: readonly string[] }).exactly;

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

/** Each way `config` binds more than the doors need, or could deploy. */
export function doorsConfigProblems(config: Config, platform: ReadonlyMap<string, Config>,
                                    gatewaySource: string,
                                    filesSource: string = FILES_SOURCE): string[] {
  const problems = configProblems(config, CONFIG, platform);
  const ownerConfig = platform.get("tinyassets-owner");
  if (JSON.stringify(ownerConfig?.["secrets"]) !== JSON.stringify(OWNER_SECRETS)) {
    problems.push("the owner Worker declares the same ROUTING_MAC_KEY");
  }
  const objects = config["durable_objects"] as { bindings?: unknown } | undefined;
  const bindings = objects?.bindings;
  if (Object.keys(objects ?? {}).join() !== "bindings" || !Array.isArray(bindings)
      || bindings.length !== 1
      || JSON.stringify(bindings[0]) !== JSON.stringify(
        { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" })) {
    problems.push("durable_objects binds exactly OWNER to tinyassets-owner's OwnerDO");
  } else {
    const owner = platform.get("tinyassets-owner");
    const classes = ((owner?.["migrations"] ?? []) as { new_sqlite_classes?: string[] }[])
      .flatMap((m) => m.new_sqlite_classes ?? []);
    if (!classes.includes("OwnerDO")) {
      problems.push("OWNER's script is not the platform's owner Worker with its OwnerDO class");
    }
  }
  const services = config["services"];
  if (!Array.isArray(services) || services.length !== 3
      || JSON.stringify(services[0]) !== JSON.stringify(GATEWAY)
      || JSON.stringify(services[1]) !== JSON.stringify(EGRESS)
      || JSON.stringify(services[2]) !== JSON.stringify(FILES)) {
    problems.push("services binds exactly GATEWAY to tinyassets-gateway's ConnectGateway, " +
                  "EGRESS to its EgressGateway and FILES to tinyassets-files' FilesUpload");
  } else if (platform.get(GATEWAY.service) === undefined
             || platform.get(FILES.service) === undefined) {
    problems.push("GATEWAY's or FILES's service is not the platform's own Worker");
  } else {
    for (const [service, methods] of [[GATEWAY, CONNECT_METHODS], [EGRESS, EGRESS_METHODS]] as const) {
      if (JSON.stringify(exportedMethods(gatewaySource).get(service.entrypoint))
          !== JSON.stringify(methods)) {
        problems.push(`the gateway's ${service.entrypoint} serves exactly ${methods.join(", ")}`);
      }
    }
    if (JSON.stringify(exportedMethods(filesSource).get(FILES.entrypoint))
        !== JSON.stringify(UPLOAD_METHODS)
        || extendsOf(filesSource, FILES.entrypoint) !== "WorkerEntrypoint") {
      problems.push(`the files Worker's ${FILES.entrypoint} extends WorkerEntrypoint and ` +
                    "serves exactly putChunks");
    }
  }
  return problems;
}

const config = readWrangler(DOORS);
const platform = platformConfigs();
const gatewaySource = readFileSync(join(PLATFORM, "gateway", "src", "index.ts"), "utf8");
const FILES_SOURCE = readFileSync(join(PLATFORM, "files", "src", "index.ts"), "utf8");

configTests(CONFIG, {
  refused: ["routes", "route", "env", "migrations", "r2_buckets", "ratelimits", "d1_databases",
            "worker_loaders", "unsafe", "tail_consumers", "dispatch_namespaces"],
  problems: (probe) => doorsConfigProblems(probe, platform, gatewaySource),
  probes: [
    ["a route", { routes: [{ pattern: "tinyassets.io/mcp*", zone_name: "tinyassets.io" }] }],
    ["a custom domain", { route: { pattern: "tinyassets.io", custom_domain: true } }],
    ["workers.dev", { workers_dev: true }],
    ["preview URLs", { preview_urls: true }],
    ["an environment", { env: { prod: { routes: ["tinyassets.io/*"] } } }],
    ["one service", { services: [{ binding: "FILES", service: "tinyassets-files" }] }],
    ["no service", { services: [] }],
    ["no files service", { services: [GATEWAY, EGRESS] }],
    ["no egress entrypoint", { services: [GATEWAY, FILES] }],
    ["the egress door on the gateway's default entrypoint", { services: [GATEWAY,
      { binding: "EGRESS", service: "tinyassets-gateway" }, FILES] }],
    ["the egress door on the connect entrypoint", { services: [GATEWAY,
      { ...EGRESS, entrypoint: "ConnectGateway" }, FILES] }],
    ["the egress door under another name", { services: [GATEWAY, { ...EGRESS, binding: "OUT" },
                                                        FILES] }],
    ["the gateway's whole surface", { services: [{ binding: "GATEWAY",
                                                   service: "tinyassets-gateway" }, EGRESS,
                                                 FILES] }],
    ["the gateway's default entrypoint", { services: [{ ...GATEWAY, entrypoint: "Gateway" },
                                                      EGRESS, FILES] }],
    ["the gateway under another name", { services: [{ ...GATEWAY, binding: "MODELS" }, EGRESS,
                                                    FILES] }],
    ["FILES to another Worker", { services: [GATEWAY, EGRESS, { ...FILES,
                                                                service: "tinyassets-owner" }] }],
    ["the files Worker's whole surface (review M5-1)", { services: [GATEWAY, EGRESS,
      { binding: "FILES", service: "tinyassets-files" }] }],
    ["the files Worker's default entrypoint", { services: [GATEWAY, EGRESS,
      { ...FILES, entrypoint: "FilesService" }] }],
    ["a fourth service", { services: [GATEWAY, EGRESS, FILES, { binding: "HOOKS",
                                                                service: "tinyassets-hooks" }] }],
    ["no app files", { assets: undefined }],
    ["app files served past the Worker", { assets: { ...ASSETS, run_worker_first: false } }],
    ["app files from another directory", { assets: { ...ASSETS, directory: "./src" } }],
    ["the assets' own not-found page", { assets: { ...ASSETS,
                                                   not_found_handling: "single-page-application" } }],
    ["a bucket", { r2_buckets: [{ binding: "BLOBS", bucket_name: "tinyassets-files" }] }],
    ["no ring consumer", { queues: undefined }],
    ["a queue producer", { queues: { ...QUEUES, producers: [{ binding: "OUT",
                                                              queue: "tinyassets-x" }] } }],
    ["another queue's consumer", { queues: { consumers: [{ queue: "tinyassets-x" }] } }],
    ["a migration", { migrations: [{ tag: "v1", new_sqlite_classes: ["X"] }] }],
    ["a rate limit", { ratelimits: [{ name: "KEY_RL", namespace_id: "1",
                                      simple: { limit: 120, period: 60 } }] }],
    ["an upstream before the deploy", { vars: { ...VARS, UPSTREAM: "{}" } }],
    ["no WORKOS_API_KEY", { secrets: { required: ["ROUTING_MAC_KEY"] } }],
    ["no strictly-public fetch", { compatibility_flags: ["nodejs_compat", "disable_ctx_exports"] }],
    ["ctx.exports", { compatibility_flags: [...FLAGS, "enable_ctx_exports"] }],
    ["DCR on", { vars: { ...VARS, DCR_ENABLED: "true" } }],
    ["an owner issuer before AuthKit", { vars: { ...VARS, OWNER_ISSUER: "https://evil.example" } }],
    ["a foreign issuer before its slice", { vars: { ...VARS,
      FOREIGN_ISSUERS: "[\"https://auth.openai.com\"]" } }],
    ["no foreign issuer list", { vars: { DCR_ENABLED: "false", OWNER_ISSUER: "not-configured" } }],
    ["another var", { vars: { ...VARS, OWNER: "alice" } }],
    ["OWNER to another script", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-files" }] } }],
    ["OWNER in this script", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO" }] } }],
    ["a second namespace", { durable_objects: { bindings: [
      { name: "OWNER", class_name: "OwnerDO", script_name: "tinyassets-owner" },
      { name: "OTHER", class_name: "X", script_name: "tinyassets-owner" }] } }],
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
  it("binds OWNER, the gateway's connect and egress entrypoints, OAUTH_KV, DIRECTORY and " +
     "CONTROL, " +
     "and cannot deploy", () => {
    expect(doorsConfigProblems(config, platform, gatewaySource)).toEqual([]);
    expect(exportedMethods(gatewaySource).get("ConnectGateway"))
      .toEqual(["exchange", "seal", "sealDoorbell", "verify", "verifyDoorbell"]);
    expect(exportedMethods(gatewaySource).get("EgressGateway")).toEqual(["forward"]);
    expect(exportedMethods(FILES_SOURCE).get("FilesUpload")).toEqual(["putChunks"]);
  });

  it("is the platform's doors config", () => {
    expect(platform.get("tinyassets-doors")).toEqual(config);
    expect(PLATFORM).toBe(join(DOORS, ".."));
  });

  it("sees OWNER bound to a script whose class is not in the platform", () => {
    const without = new Map(platform);
    without.delete("tinyassets-owner");
    expect(doorsConfigProblems(config, without, gatewaySource)).not.toEqual([]);
  });

  it("sees GATEWAY bound to a Worker that is not in the platform", () => {
    const without = new Map(platform);
    without.delete("tinyassets-gateway");
    expect(doorsConfigProblems(config, without, gatewaySource)).not.toEqual([]);
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
    const head = `export class ${entrypoint} extends WorkerEntrypoint<GatewayEnv> {`;
    const grown = gatewaySource.replace(head, `${head}\n  ${member}`);
    expect(grown).not.toBe(gatewaySource);
    expect(doorsConfigProblems(config, platform, grown)).not.toEqual([]);
  });

  it.each([
    ["a chunk read", "getChunks() { return 1; }"],
    ["a generation's read", "async getManifestPart() { return 1; }"],
    ["a key call", "rewrapKey = () => 1;"],
  ])("sees the upload entrypoint grow %s (review M5-1)", (_what, member) => {
    const head = "export class FilesUpload extends WorkerEntrypoint<FilesEnv> {";
    const grown = FILES_SOURCE.replace(head, `${head}\n  ${member}`);
    expect(grown).not.toBe(FILES_SOURCE);
    expect(doorsConfigProblems(config, platform, gatewaySource, grown)).not.toEqual([]);
  });

  it("sees the upload entrypoint inherit the whole service, its own members unchanged " +
     "(review L6-1)", () => {
    const head = "export class FilesUpload extends WorkerEntrypoint<FilesEnv> {";
    expect(FILES_SOURCE.includes(head)).toBe(true);
    expect(extendsOf(FILES_SOURCE, "FilesUpload")).toBe("WorkerEntrypoint");
    const inherits = FILES_SOURCE.replace(head, "export class FilesUpload extends FilesService {");
    // Its own members are still putChunks alone; RPC would serve every inherited method.
    expect(exportedMethods(inherits).get("FilesUpload")).toEqual(["putChunks"]);
    expect(doorsConfigProblems(config, platform, gatewaySource, inherits)).not.toEqual([]);
  });
});

/** The queue the Worker at `worker` sends to through its producer `binding`. */
function produced(worker: string, binding: string): string | undefined {
  const queues = readWrangler(join(PLATFORM, worker))["queues"] as
    { producers?: { binding: string; queue: string }[] } | undefined;
  return queues?.producers?.find((producer) => producer.binding === binding)?.queue;
}

describe("the doors' two queue consumers", () => {
  it("take erasures by the name the owner sends them under, and every other batch from the " +
     "sandbox's ring", () => {
    // The doors' queue handler takes a batch named ERASURE_QUEUE as erasures and any other as
    // rings (`src/index.ts`): a name that drifted from the owner's producer would hand every
    // erasure to the ring handler, so the name is held to the owner's config here.
    const erasures = produced("owner", "ERASURES");
    const rings = produced("sandbox", "RING");
    expect(erasures).toBeDefined();
    expect(ERASURE_QUEUE).toBe(erasures);
    expect(rings).not.toBe(erasures);
    expect((config["queues"] as { consumers: { queue: string }[] }).consumers
      .map((consumer) => consumer.queue).sort()).toEqual([erasures, rings].sort());
  });
});
