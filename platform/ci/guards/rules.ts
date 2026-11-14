/**
 * What the ci workspace still owes the platform's guards (`ci/kit/owed.ts`): its own files',
 * and the workflows' inline modules, which its layer guard reads (`ci/kit/layers.ts`'s
 * `INLINE`). Nothing, now.
 */
import type { Owed } from "../kit/owed";

export const OWED: readonly Owed[] = Object.freeze([]);
