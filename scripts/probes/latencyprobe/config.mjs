/**
 * The latency probe's three Worker configs (latency-probe.md), written to `--out`:
 *   files.wrangler.json  ta-latencyprobe-files: the bucket, no URL;
 *   owner.wrangler.json  ta-latencyprobe: the owner Worker's runtime exactly (date, flags,
 *                        `disable_ctx_exports`, subrequests, no URL), its `Owner` and `Shard`
 *                        SQLite classes, `SHARD` the one same-script binding, `FILES` the
 *                        service, `BUCKET` the same bucket direct;
 *   door.wrangler.json   ta-latencyprobe-door: the one workers.dev URL, binding `Owner` by
 *                        script_name as the doors bind OwnerDO.
 * `--bucket NAME` binds the throwaway bucket; without it (the token could not make one) the
 * owner and files Workers carry no bucket, and option B is not run.
 *
 *   node config.mjs --out DIR [--bucket NAME]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OWNER_CONFIG = join(HERE, "..", "..", "..", "platform", "owner", "wrangler.jsonc");
export const SCRIPTS = Object.freeze(["ta-latencyprobe-door", "ta-latencyprobe",
                                      "ta-latencyprobe-files"]);

/** The owner Worker's runtime, read from its own config (JSONC: comments dropped). */
export function ownerRuntime() {
  const text = readFileSync(OWNER_CONFIG, "utf8").replace(/^\s*\/\/.*$/gm, "");
  const config = JSON.parse(text);
  return { compatibility_date: config.compatibility_date,
           compatibility_flags: config.compatibility_flags, limits: config.limits };
}

export function configs(bucket) {
  const runtime = ownerRuntime();
  const r2 = bucket ? [{ binding: "BUCKET", bucket_name: bucket }] : [];
  return {
    files: { name: "ta-latencyprobe-files", main: join(HERE, "files.js"),
             compatibility_date: runtime.compatibility_date,
             compatibility_flags: runtime.compatibility_flags, workers_dev: false,
             preview_urls: false, r2_buckets: r2 },
    owner: { name: "ta-latencyprobe", main: join(HERE, "owner.js"), ...runtime,
             workers_dev: false, preview_urls: false,
             durable_objects: { bindings: [{ name: "SHARD", class_name: "Shard" }] },
             migrations: [{ tag: "v1", new_sqlite_classes: ["Owner", "Shard"] }],
             services: bucket ? [{ binding: "FILES", service: "ta-latencyprobe-files" }] : [],
             r2_buckets: r2 },
    door: { name: "ta-latencyprobe-door", main: join(HERE, "door.js"),
            compatibility_date: runtime.compatibility_date,
            compatibility_flags: ["nodejs_compat", "global_fetch_strictly_public"],
            workers_dev: true, preview_urls: false,
            durable_objects: { bindings: [{ name: "OWNER", class_name: "Owner",
                                            script_name: "ta-latencyprobe" }] } },
  };
}

function main(argv) {
  const at = (flag) => {
    const i = argv.indexOf(flag);
    return i < 0 ? null : argv[i + 1] ?? null;
  };
  const out = at("--out");
  if (!out) {
    throw new Error("usage: node config.mjs --out DIR [--bucket NAME]");
  }
  const bucket = at("--bucket");
  if (bucket !== null && !/^ta-latencyprobe-[a-z0-9-]{1,40}$/.test(bucket)) {
    throw new Error("the bucket must be named ta-latencyprobe-*");
  }
  mkdirSync(resolve(out), { recursive: true });
  for (const [name, config] of Object.entries(configs(bucket))) {
    writeFileSync(join(resolve(out), `${name}.wrangler.json`), JSON.stringify(config, null, 2));
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2));
}
