/**
 * The gate in front of the owner Worker's objects (`vitest.config.ts`) as it started, after
 * each test: its arming and record are module state, which outlives a test.
 */
import { env } from "cloudflare:workers";
import { afterEach } from "vitest";

afterEach(async () => {
  await env.OWNER_GATE.disarm();
});
