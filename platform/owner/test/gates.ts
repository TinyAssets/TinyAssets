/**
 * Every gate in front of the owner's bindings (`vitest.config.ts`) as it started, after each
 * test: a gate's arming, held calls and record are module state, which outlives a test.
 *
 * Before each test, the control record as a deployed platform holds it (`OPEN`: every gate
 * open, the founder's tiers). The object keeps its one copy of the record from each read of
 * it, its own `runs` lane's too, and reads its tier numbers from that copy (`admission.ts`),
 * so a namespace with no record would drop the copy a test's binding handed at the lane's next
 * pass. A test that needs another record writes it.
 */
import { env } from "cloudflare:workers";
import { afterEach, beforeEach } from "vitest";

import { OPEN, writeControl } from "../../ci/fixtures";

beforeEach(async () => {
  await writeControl(env.CONTROL, OPEN);
});

afterEach(async () => {
  await Promise.all([env.SANDBOX_GATE.disarm(), env.FILES_GATE.disarm(),
                     env.GATEWAY_GATE.disarm()]);
});
