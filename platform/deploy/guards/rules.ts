/**
 * What the deploy kit still owes the platform's guards (`ci/kit/owed.ts`), each with the fix
 * that removes it.
 */
import { imports, type Owed } from "../../ci/kit/owed";

export const OWED: readonly Owed[] = Object.freeze([
  // The kit on the test kit and on the sandbox Worker's runtime client: B11 gives it
  // `ci/wrangler.ts` and `sandbox/e2b/`.
  ...imports("B11", "platform/deploy/deploy.ts",
             ["platform/ci/workers.ts", "platform/sandbox/src/e2b.ts"]),
  ...imports("B11", "platform/deploy/facts.ts", ["platform/ci/workers.ts"]),
  ...imports("B11", "platform/deploy/guards/deploy.test.ts", ["platform/sandbox/src/e2b.ts"]),
  // The doors' public identity, read from their source: B13 puts it in the contract.
  ...imports("B13", "platform/deploy/guards/deploy.test.ts",
             ["platform/doors/src/config.ts", "platform/doors/src/oidc.ts"]),
]);
