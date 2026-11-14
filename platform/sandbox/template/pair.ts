/**
 * The pair the sandbox runs on: the template (the image, `image.ts`), the runner the sandbox
 * Worker ships, and the probes that checked them together (the Worker's build,
 * `build/bundle.mjs`). A pair that passed its standing checks (`checks.ts`) is marked at E2B
 * with its pair tag, which the deploy kit looks for before a deploy (`deploy/deploy.ts`), one
 * lookup. The kit and the checks name the pair by this one module, so the pair the kit looks
 * for names exactly the bytes that were checked. It is the one home of the pinned template
 * (`pinned`) and of the bundles' file names (`BUNDLES`).
 *
 * Its imports name their `.ts` files, so the deploy kit runs it by Node's type stripping. The
 * template's other tools (`image.ts`, `checks.ts`) import the platform's modules, which
 * resolve as its bundler does (`resolve.mjs`), so they run by `tsTool`.
 */
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { readWrangler } from "../../ci/workers.ts";

const TEMPLATE = dirname(fileURLToPath(import.meta.url));
const SANDBOX = join(TEMPLATE, "..");

/** The Worker's build, which makes both bundles (`wrangler.jsonc`'s `build.command`). */
export const BUILD = join(SANDBOX, "build", "bundle.mjs");

/** Each bundle's file name, where a build writes it (`bundles`). */
export const BUNDLES = Object.freeze({ runner: "runner.mjs", probes: "probes.mjs" });

/** The image the sandbox Worker starts, `name:tag` (`wrangler.jsonc`'s `E2B_TEMPLATE`). */
export function pinned(): string {
  return String((readWrangler(SANDBOX) as { vars: { E2B_TEMPLATE: string } }).vars.E2B_TEMPLATE);
}

/** One bundle as built: its bytes and their SHA-256. */
export interface Bundle {
  readonly bytes: Buffer;
  readonly sha: string;
}

/**
 * Node's argv for one of the template's TypeScript tools (`image.ts`, `checks.ts`): Node's
 * own type stripping, the platform's module resolution (`resolve.mjs`), then the tool.
 */
export function tsTool(tool: "image.ts" | "checks.ts"): string[] {
  return ["--experimental-strip-types", "--no-warnings", "--import",
          join(TEMPLATE, "resolve.mjs"), join(TEMPLATE, tool)];
}

/** Node's argv for the checks of a pair, `pair` or `build` (`checks.ts`). */
export function checksArgv(mode: "pair" | "build"): string[] {
  return [...tsTool("checks.ts"), mode];
}

/**
 * The runner the Worker ships and the probes beside it, as the Worker's build makes them
 * (`BUILD`), into `out` by their names (`BUNDLES`) if given (else a temporary directory,
 * deleted), by `exec`.
 */
export function bundles(exec: typeof execFileSync = execFileSync, out?: string):
    { runner: Bundle; probes: Bundle } {
  const dir = out ?? mkdtempSync(join(tmpdir(), "ta-bundles-"));
  try {
    exec(process.execPath, [BUILD, join(dir, BUNDLES.runner), join(dir, BUNDLES.probes)],
         { stdio: "ignore" });
    const read = (name: string): Bundle => {
      const bytes = readFileSync(join(dir, name));
      return { bytes, sha: createHash("sha256").update(bytes).digest("hex") };
    };
    return { runner: read(BUNDLES.runner), probes: read(BUNDLES.probes) };
  } finally {
    if (out === undefined) {
      rmSync(dir, { recursive: true, force: true });
    }
  }
}

/**
 * The E2B tag a pair is marked with once it passed its standing checks: the template's
 * `name:tag`, then the runner's first 16 hex, then the probes'
 * (`<name>:<tag>-r<runner>-p<probes>`). A probes change moves it as a runner change does:
 * the probes carry what the checks judge by.
 */
export function pairTag(template: string, runner: string, probes: string): string {
  if (!/^[0-9a-f]{64}$/.test(runner) || !/^[0-9a-f]{64}$/.test(probes)) {
    throw new Error("a runner and its probes are each named by their SHA-256");
  }
  return `${template}-r${runner.slice(0, 16)}-p${probes.slice(0, 16)}`;
}

/** This tree's image, `name:tag`, as `image.ts` computes it, by `exec`. */
export function treeImage(exec: typeof execFileSync = execFileSync): string {
  return String(exec(process.execPath, tsTool("image.ts"), { encoding: "utf8" })).trim();
}
