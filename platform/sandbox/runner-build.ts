/**
 * The runner the Worker ships (`src/runner-bytes.ts` imports it as data), built as the
 * Worker's own build makes it (`build.command` in wrangler.jsonc), before the workerd tests
 * and the guards run: tests run what deploys, and the template guard holds `dist/` to it. A
 * global setup, so loading the config builds nothing.
 */
import { execFileSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SANDBOX = dirname(fileURLToPath(import.meta.url));

export function setup(): void {
  execFileSync(process.execPath, [join(SANDBOX, "template", "bundle.mjs"), join(SANDBOX, "dist")],
               { stdio: "ignore" });
}
