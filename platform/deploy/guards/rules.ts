/**
 * What the deploy kit still owes the platform's guards (`ci/kit/owed.ts`): the live names its
 * kit test and the founder's checklist spell (B10's literal guard, `literals.test.ts`), until
 * `untangle/x1b` has the test read them from `facts.ts` and the checklist point at the configs
 * and `platform.json` (`o4c.md`).
 */
import { literals, type Owed } from "../../ci/kit/owed";

export const OWED: readonly Owed[] = Object.freeze([
  ...literals("B10", { "platform/deploy/guards/deploy.test.ts": 73,
                       "platform/deploy/checklist.md": 17 }),
]);
