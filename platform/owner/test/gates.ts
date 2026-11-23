/**
 * Every gate in front of the owner's bindings (`vitest.config.ts`) as it started, after each
 * test: a gate's arming, held calls and record are module state, which outlives a test.
 *
 * Before each test, a control record with the founder's tiers, the maintenance gate open and
 * outside access off (`SHUT_OUTSIDE`). The object keeps its one copy of the record from each
 * read of it, its own `runs` lane's too, and reads its tier numbers from that copy
 * (`admission.ts`), so a namespace with no record would drop the copy a test's binding handed
 * at the lane's next pass. Outside access is off as an empty namespace has it: the doorbell's
 * kill switch is closed, so the object's own alarm rings no wake a test drives by hand
 * (`ledger-support.ts`, `tick`). A test that needs another record writes it (`OPEN`: a test
 * that rings, or runs an outside client's code-mode calls).
 */
import { env } from "cloudflare:workers";
import { afterEach, beforeEach } from "vitest";

import { OPEN, writeControl } from "../../ci/fixtures";

const SHUT_OUTSIDE = Object.freeze({ ...OPEN, outside: "off" });

beforeEach(async () => {
  await writeControl(env.CONTROL, SHUT_OUTSIDE);
});

afterEach(async () => {
  await Promise.all([env.SANDBOX_GATE.disarm(), env.FILES_GATE.disarm(),
                     env.GATEWAY_GATE.disarm()]);
});
