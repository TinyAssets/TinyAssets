/**
 * Every gate in front of the owner's bindings (`vitest.config.ts`) as it started, after each
 * test: a gate's arming, held calls and record are module state, which outlives a test.
 */
import { env } from "cloudflare:workers";
import { afterEach } from "vitest";

afterEach(async () => {
  await env.SANDBOX_GATE.disarm();
});
