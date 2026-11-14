/**
 * Every gate in front of the owner's bindings (`vitest.config.ts`) as it started, after each
 * test: a gate's arming, held calls and record are module state, which outlives a test.
 */
import { env } from "cloudflare:workers";
import { afterEach } from "vitest";

afterEach(async () => {
  await Promise.all([env.SANDBOX_GATE.disarm(), env.FILES_GATE.disarm(),
                     env.GATEWAY_GATE.disarm()]);
});
