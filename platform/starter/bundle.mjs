// The starter package as one JSON document the owner's object carries (`starter.json`):
// `manifest.json`'s version, OKF pin and files, each file with its text read from the same
// path under this directory. `node platform/starter/bundle.mjs` writes it; `--check` exits
// 1 when the committed one differs from the sources (the owner's starter guard runs it).
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

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const made = bundle();
  if (process.argv.includes("--check")) {
    let committed = "";
    try {
      committed = readFileSync(OUT, "utf8");
    } catch {
      // missing: differs
    }
    if (committed !== made) {
      console.error("platform/starter/starter.json is stale: run node platform/starter/bundle.mjs");
      process.exit(1);
    }
  } else {
    writeFileSync(OUT, made);
  }
}
