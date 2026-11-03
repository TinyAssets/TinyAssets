/**
 * E2B's signed lifecycle webhook (slice P, `e2b-hooks.ts`): verified by E2B's own signature
 * scheme, parsed strictly, routed by the sealed route E2B echoes from the sandbox's metadata
 * to the owner's object, which counts each billed execution once.
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import type { DoorsEnv } from "../src/config";
import { E2B_HOOK_PATH, lifecycleEvent, signed } from "../src/e2b-hooks";
import { openRoute, sealRoute } from "../src/routing-mac";
import { call, doorsEnv, url } from "./support";

const SECRET = "test-e2b-webhook-signature-secret";

/** E2B's own signature over `body` (docs.e2b.dev, webhook verification). */
function signature(body: string, secret = SECRET): string {
  return createHash("sha256").update(secret + body).digest("base64").replace(/=+$/, "");
}

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

function event(who: string, over: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(), version: "v2", type: "sandbox.lifecycle.paused",
    timestamp: "2026-11-03T10:00:00Z", event_category: "lifecycle", event_label: "pause",
    event_data: {
      sandbox_metadata: { ta_route: sealRoute(env.ROUTING_MAC_KEY, { owner: who,
                                                                    authority: "owner" }) },
      execution: { started_at: "2026-11-02T23:59:00.000Z", vcpu_count: 2, memory_mb: 1024,
                   execution_time: 120_000 },
      pause_mode: "full",
    },
    sandbox_id: "sbx", sandbox_execution_id: "x", sandbox_template_id: "t",
    sandbox_build_id: "b", sandbox_team_id: "team", events_ttl_days: 30, ...over,
  };
}

function deliver(body: string, sig: string | null = signature(body),
                 environment: DoorsEnv = doorsEnv): Promise<Response> {
  return call(new Request(url(E2B_HOOK_PATH), {
    method: "POST", body,
    headers: { "Content-Type": "application/json", ...(sig === null ? {}
      : { "e2b-signature": sig }) },
  }), environment);
}

describe("E2B's lifecycle webhook", () => {
  it("counts a verified billed execution once, in the owner's object, by its sealed route",
     async () => {
    const who = owner();
    const body = JSON.stringify(event(who));
    const first = await deliver(body);
    expect([first.status, await first.json()]).toEqual([200, { counted: true }]);
    // E2B retries a delivery: the same event is counted once.
    expect(await (await deliver(body)).json()).toEqual({ counted: false });
    // Split across the UTC days it ran: a minute before midnight, a minute after.
    const { days } = await env.OWNER.getByName(who).sandboxDays(
      { owner: who, caller: who, origin: null }, {});
    expect(days).toEqual([
      { day: "20261103", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60 },
      { day: "20261102", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60 },
    ]);
  });

  it("refuses a delivery whose signature does not verify, or with no secret set", async () => {
    const body = JSON.stringify(event(owner()));
    expect((await deliver(body, null)).status).toBe(401);
    expect((await deliver(body, signature(body, "another-secret-of-sixteen"))).status).toBe(401);
    expect((await deliver(`${body} `, signature(body))).status).toBe(401);
    const unset = { ...doorsEnv, E2B_WEBHOOK_SECRET: undefined } as unknown as DoorsEnv;
    expect((await deliver(body, signature(body), unset)).status).toBe(503);
  });

  it("ignores an event that bills nothing, and one naming no sandbox of ours", async () => {
    const created = JSON.stringify(event(owner(), { type: "sandbox.lifecycle.created" }));
    expect(await (await deliver(created)).json()).toEqual({ ignored: true });
    const stranger = event(owner());
    stranger.event_data.sandbox_metadata = { ta_route: "x".repeat(60) };
    const body = JSON.stringify(stranger);
    expect(await (await deliver(body)).json()).toEqual({ unrouted: true });
    expect((await deliver("not json", signature("not json"))).status).toBe(400);
    expect(lifecycleEvent(JSON.stringify({ ...event(owner()), event_data: {} }))).toBeNull();
  });

  it("seals a route only the routing key opens, and checks E2B's signature exactly", () => {
    const sealed = sealRoute(env.ROUTING_MAC_KEY, { owner: "user_a", authority: "owner" });
    expect(openRoute(env.ROUTING_MAC_KEY, sealed)).toEqual({ owner: "user_a",
                                                             authority: "owner" });
    expect(sealed).not.toContain("user_a");
    expect(openRoute("A".repeat(43), sealed)).toBeNull();
    expect(openRoute(env.ROUTING_MAC_KEY, `${sealed.slice(0, -2)}AA`)).toBeNull();
    expect(signed(SECRET, "{}", signature("{}"))).toBe(true);
    expect(signed(SECRET, "{}", `${signature("{}")}=`)).toBe(true);
    expect(signed(SECRET, "{}", signature("{ }"))).toBe(false);
  });
});
