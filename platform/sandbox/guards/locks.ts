/** The sandbox Worker's migration locks (`ci/kit/locks.ts`): one for each of its two objects. */
import { CONTROL_MIGRATIONS, SLOTS_MIGRATIONS } from "../src/schema.ts";

export const LOCKS = Object.freeze({
  "sandbox/migrations/SandboxControl.json": CONTROL_MIGRATIONS,
  "sandbox/migrations/SandboxSlots.json": SLOTS_MIGRATIONS,
});
