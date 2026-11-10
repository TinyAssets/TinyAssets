/**
 * What the ci workspace still owes the platform's guards (`ci/kit/owed.ts`): its own files',
 * and the workflows' inline modules, which its layer guard reads (`ci/kit/layers.ts`'s
 * `INLINE`).
 */
import { imports, type Owed } from "../kit/owed";

export const OWED: readonly Owed[] = Object.freeze([
  // The import window reads the founder's tiers from the release kit: B10 moves them to
  // `deploy/control.json`, read with `jq`.
  ...imports("B10", ".github/workflows/import-window.yml", ["platform/deploy/deploy.ts"]),
]);
