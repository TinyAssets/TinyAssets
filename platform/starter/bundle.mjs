// The starter package as one JSON document the owner's object carries (`starter.json`):
// `manifest.json`'s version, OKF pin, files and retired paths, each file with its text read
// from the same path under this directory. `node platform/starter/bundle.mjs` writes it;
// `--check` exits 1 when the committed one differs from the sources (the owner's starter
// guard runs it). Writing it, a path the committed bundle named that the new one names
// nowhere, neither as a file nor as `retired`, fails: a dropped path must be retired, so an
// owner who deleted it keeps it deleted if a later version ships it again.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "starter.json");

export function bundle() {
  const manifest = JSON.parse(readFileSync(join(HERE, "manifest.json"), "utf8"));
  const files = manifest.files.map((file) => {
    if (!file.path.startsWith("/workspace/")) {
      throw new Error(`${file.path} is not under /workspace`);
    }
    return { ...file, text: readFileSync(join(HERE, file.path.slice(1)), "utf8") };
  });
  return `${JSON.stringify({ version: manifest.version, okf: manifest.okf, files,
                            retired: manifest.retired ?? [] }, null, 1)}\n`;
}

/** The paths `previous` (a bundle's JSON) names that `next` names nowhere. */
export function dropped(previous, next) {
  const named = (pkg) => [...pkg.files.map((file) => file.path), ...(pkg.retired ?? [])];
  const kept = new Set(named(next));
  return named(previous).filter((path) => !kept.has(path));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const made = bundle();
  let committed = "";
  try {
    committed = readFileSync(OUT, "utf8");
  } catch {
    // none yet: nothing was dropped, and a check differs
  }
  if (process.argv.includes("--check")) {
    if (committed !== made) {
      console.error("platform/starter/starter.json is stale: run node platform/starter/bundle.mjs");
      process.exit(1);
    }
  } else {
    const lost = committed === "" ? [] : dropped(JSON.parse(committed), JSON.parse(made));
    if (lost.length > 0) {
      console.error(`these paths left the package without being retired: ${lost.join(", ")}; ` +
                    "list each in manifest.json's retired");
      process.exit(1);
    }
    writeFileSync(OUT, made);
  }
}
