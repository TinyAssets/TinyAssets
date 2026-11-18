#!/usr/bin/env node
/**
 * Compare the installed Wrangler's local Worker binding conversion to the test kit.
 * Run this same file on each revision's installed platform directory and diff stdout.
 * Wrangler only sees disposable config copies. No Worker starts.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";

const kinds = ["bindings", "kvNamespaces", "queueProducers", "queueConsumers",
               "serviceBindings", "durableObjects", "r2Buckets", "assets"];

function ordered(value) {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === "object") return Object.fromEntries(
    Object.keys(value).filter((k) => value[k] !== undefined).sort().map((name) => [name, ordered(value[name])]));
  return value;
}

function digest(value) {
  return createHash("sha256").update(JSON.stringify(ordered(value))).digest("hex");
}

export async function compare(platform) {
  const req = createRequire(join(platform, "package.json"));
  const { unstable_getMiniflareWorkerOptions: convert, unstable_readConfig: read } = req("wrangler");
  const { main, workerConfig } = await import(pathToFileURL(join(platform, "ci/workers.ts")));
  const { workerFolders } = await import(pathToFileURL(join(platform, "ci/wrangler.ts")));
  const configs = workerFolders().map((folder) => [folder, workerConfig(join(platform, folder))]);
  if (configs.length === 0) throw new Error("the platform contains no Worker configs");
  const sqlite = new Map(configs.map(([, cfg]) => [cfg.name,
    new Set((cfg.migrations ?? []).flatMap((m) => m.new_sqlite_classes ?? []))]));
  for (const [folder, cfg] of configs) {
    const dir = join(process.cwd(), folder);
    mkdirSync(dir);
    const path = join(dir, "wrangler.jsonc");
    copyFileSync(join(platform, folder, "wrangler.jsonc"), path);
    const secrets = cfg.secrets?.required ?? [];
    const declared = read({ config: path }).secrets?.required ?? [];
    if (!isDeepStrictEqual([...declared].sort(), [...secrets].sort())) {
      throw new Error(`${folder}: test kit and Wrangler differ in declared secret names`);
    }
    const stated = Object.fromEntries(secrets.map((key) => [key, `replay-fixture-${key}`]));
    writeFileSync(join(dir, ".dev.vars"), secrets.map((s) => `${s}=${stated[s]}\n`).join(""));
    const real = convert(path).workerOptions;
    const kit = main(dir, { bindings: { ...stated } }).miniflare;
    for (const options of [real, kit]) {
      for (const name of secrets) {
        if (options.bindings?.[name] !== stated[name]) {
          throw new Error(`${folder}: missing synthetic secret ${name}`);
        }
        delete options.bindings[name];
      }
      if (options.assets) options.assets.directory = relative(dir, options.assets.directory);
    }

    // The kit binds bucket names locally and the hosted SQL class by its class name (#5510).
    // Check the two actual representations before comparing their common binding inventory.
    const buckets = Object.fromEntries((cfg.r2_buckets ?? []).map((b) =>
      [b.binding, { id: b.bucket_name }]));
    if (!isDeepStrictEqual(ordered(real.r2Buckets ?? {}), ordered(buckets))) {
      throw new Error(`${folder}: Wrangler changed its R2 bucket representation`);
    }
    if (Object.keys(buckets).length) console.log(`${folder} vendor bucket ids ${digest(buckets)}`);
    real.r2Buckets = Object.keys(buckets).sort();
    const hosted = real.additionalUnboundDurableObjects ?? [];
    if (Object.keys(real.durableObjects ?? {}).length || hosted.length) {
      console.log(`${folder} vendor object bindings ${digest([real.durableObjects ?? {}, hosted])}`);
    }
    for (const object of hosted) {
      if (Object.hasOwn(real.durableObjects, object.className)) {
        throw new Error(`${folder}: ambiguous unbound class ${object.className}`);
      }
      real.durableObjects[object.className] = object;
    }
    for (const object of Object.values(real.durableObjects ?? {})) {
      if (object.scriptName && object.useSQLite === undefined
          && sqlite.get(object.scriptName)?.has(object.className)) object.useSQLite = true;
    }
    console.log(`${folder} declared secrets ${secrets.slice().sort().join(",") || "(none)"}`);
    for (const kind of kinds) {
      const actual = kit[kind] ?? (kind === "r2Buckets" ? [] : {});
      const expected = real[kind] ?? (kind === "r2Buckets" ? [] : {});
      if (kind === "r2Buckets" && Array.isArray(actual)) actual.sort();
      if (!isDeepStrictEqual(ordered(actual), ordered(expected))) {
        throw new Error(`${folder}: test kit and Wrangler differ in ${kind}`);
      }
      if (Object.keys(expected).length) console.log(`${folder} ${kind} ${digest(expected)}`);
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 3) throw new Error("usage: node wrangler-bindings.mjs <platform>");
  const platform = resolve(process.argv[2]);
  const dir = mkdtempSync(join(tmpdir(), "wrangler-binding-replay-"));
  try {
    // The converter reads .env/.dev.vars and the invoking process's environment.
    execFileSync(process.execPath, ["--input-type=module", "--eval",
      `import {compare} from ${JSON.stringify(import.meta.url)}; await compare(process.argv[1]);`,
      platform], { cwd: dir, env: { HOME: dir, WRANGLER_SEND_METRICS: "false",
                                    WRANGLER_LOG: "error" }, stdio: "inherit" });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
