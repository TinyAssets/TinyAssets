#!/usr/bin/env node
/**
 * Writes the probe's two deploy configs into `--out`: `bootstrap.wrangler.json`, the committed
 * config without the SELF service binding (deployed first, so the binding's target script
 * exists), and `probe.wrangler.json`, the committed config unchanged. Both point `main` at
 * the committed worker.js by absolute path.
 *
 *   node scripts/probes/rpcretain/config.mjs --out <dir>
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const HERE = dirname(fileURLToPath(import.meta.url));
export const SCRIPT = "rr-rpcretain-probe";

/** The committed config: JSONC whose comments are whole lines. */
export function readCommitted() {
  const text = readFileSync(join(HERE, "wrangler.jsonc"), "utf8");
  return JSON.parse(text.split("\n").filter((line) => !line.trim().startsWith("//")).join("\n"));
}

export function writeConfigs(outDir) {
  const probe = { ...readCommitted(), main: join(HERE, "worker.js") };
  if (probe.name !== SCRIPT) {
    throw new Error(`the committed config names ${probe.name}, not ${SCRIPT}`);
  }
  const { services, ...bootstrap } = probe;
  if (!services?.length) {
    throw new Error("the committed config has no SELF binding");
  }
  mkdirSync(outDir, { recursive: true });
  const paths = { bootstrap: join(outDir, "bootstrap.wrangler.json"), probe: join(outDir, "probe.wrangler.json") };
  writeFileSync(paths.bootstrap, `${JSON.stringify(bootstrap, null, 2)}\n`);
  writeFileSync(paths.probe, `${JSON.stringify(probe, null, 2)}\n`);
  return paths;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  const at = process.argv.indexOf("--out");
  if (at < 0 || !process.argv[at + 1]) {
    throw new Error("usage: config.mjs --out <dir>");
  }
  console.log(JSON.stringify(writeConfigs(process.argv[at + 1])));
}
