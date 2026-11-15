/**
 * The runner the Worker ships (`src/runner-bytes.ts` imports it as data), built as the
 * Worker's own build makes it (`build.command` in wrangler.jsonc), before the workerd tests
 * and the guards run: tests run what deploys, and the template guard holds `dist/` to it. A
 * global setup, so loading the config builds nothing.
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { bundles } from "./template/pair";

const SANDBOX = dirname(fileURLToPath(import.meta.url));

export function setup(): void {
  bundles(join(SANDBOX, "dist"));
}
