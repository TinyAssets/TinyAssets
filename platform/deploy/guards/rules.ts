/**
 * What the deploy kit still owes the platform's guards (`ci/kit/owed.ts`), each with the fix
 * that removes it.
 */
import { imports, type Owed } from "../../ci/kit/owed";

export const OWED: readonly Owed[] = Object.freeze([
  // The doors' public identity, read from their source: B13 puts it in the contract.
  ...imports("B13", "platform/deploy/guards/deploy.test.ts",
             ["platform/doors/src/config.ts", "platform/doors/src/oidc.ts"]),
]);
