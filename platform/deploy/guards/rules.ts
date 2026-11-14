/**
 * What the deploy kit still owes the platform's guards (`ci/kit/owed.ts`), each with the fix
 * that removes it.
 */
import { imports, type Owed } from "../../ci/kit/owed";

export const OWED: readonly Owed[] = Object.freeze([
  // The kit on the test kit: B11's wrangler part (X2) gives it `ci/wrangler.ts`.
  ...imports("B11", "platform/deploy/deploy.ts", ["platform/ci/workers.ts"]),
  ...imports("B11", "platform/deploy/facts.ts", ["platform/ci/workers.ts"]),
  // The doors' public identity, read from their source: B13 puts it in the contract.
  ...imports("B13", "platform/deploy/guards/deploy.test.ts",
             ["platform/doors/src/config.ts", "platform/doors/src/oidc.ts"]),
]);
