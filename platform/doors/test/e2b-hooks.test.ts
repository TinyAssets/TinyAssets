/**
 * E2B's signed lifecycle webhook (slice P, `e2b-hooks.ts`): verified by E2B's own signature
 * scheme, its body read no further than its bound, parsed strictly, routed by the sealed
 * route E2B echoes from the sandbox's metadata to the owner's object, which counts each
 * billed execution once, and only while the event's own signed time is inside its window.
 */
import { createHash } from "node:crypto";

import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import type { SandboxSignin } from "../../owner/src/sandbox";
import type { DoorsEnv } from "../src/config";
import { BILLED_WINDOW_S } from "../../owner/src/sandbox-time";
import { E2B_HOOK_PATH, HOOK_BODY_MAX, lifecycleEvent, signed } from "../src/e2b-hooks";
import { openRoute, sealRoute } from "../src/routing-mac";
import type { OwnerDO } from "./entry";
import {
  admitOwner, appPost, call, doorsEnv, OPEN, serveFirstPartyDocument, setControl, signInApp, url,
} from "./support";

const SECRET = "test-e2b-webhook-signature-secret";

/** E2B's own signature over `body` (docs.e2b.dev, webhook verification). */
function signature(body: string, secret = SECRET): string {
  return createHash("sha256").update(secret + body).digest("base64").replace(/=+$/, "");
}

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** An owner the platform admitted, whose object answers its doors. */
async function admitted(): Promise<string> {
  const who = owner();
  await admitOwner(who);
  return who;
}

function event(who: string, over: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(), version: "v2", type: "sandbox.lifecycle.paused",
    timestamp: new Date().toISOString(), event_category: "lifecycle", event_label: "pause",
    event_data: {
      sandbox_metadata: { ta_route: sealRoute(env.ROUTING_MAC_KEY, { owner: who,
                                                                    authority: "owner" }) },
      execution: { started_at: "2026-11-02T23:59:00.000Z", vcpu_count: 2, memory_mb: 1024,
                   execution_time: 120_000 },
      pause_mode: "full",
    },
    sandbox_id: "sbx", sandbox_execution_id: crypto.randomUUID(), sandbox_template_id: "t",
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
    const who = await admitted();
    const body = JSON.stringify(event(who));
    const first = await deliver(body);
    expect([first.status, await first.json()]).toEqual([200, { counted: true }]);
    // E2B retries a delivery: the same event is counted once.
    expect(await (await deliver(body)).json()).toEqual({ counted: false });
    // Split across the UTC days it ran: a minute before midnight, a minute after.
    const { days } = await env.OWNER.getByName(who).sandboxDays(
      { owner: who, caller: who, origin: null }, {});
    expect(days).toEqual([
      { day: "20261103", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60,
        unverified_executions: 0 },
      { day: "20261102", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60,
        unverified_executions: 0 },
    ]);
  });

  it("counts one execution once, though a pause and a later kill both carry it (review L1)",
     async () => {
    const who = await admitted();
    const paused = event(who, { sandbox_execution_id: "exec-1" });
    paused.event_data.execution.execution_time = 60_000;
    const killed = { ...paused, id: crypto.randomUUID(), type: "sandbox.lifecycle.killed",
                     event_label: "kill",
                     event_data: { ...paused.event_data, kill_reason: "request" } };
    expect(await (await deliver(JSON.stringify(paused))).json()).toEqual({ counted: true });
    expect(await (await deliver(JSON.stringify(killed))).json()).toEqual({ counted: false });
    const { days } = await env.OWNER.getByName(who).sandboxDays(
      { owner: who, caller: who, origin: null }, {});
    // 60 s ran; 60 s counted.
    expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(60);
  });

  it("refuses an event older than its window, or ahead of the clock, by its signed time " +
     "(review L2)", async () => {
    const who = await admitted();
    const old = event(who, { timestamp: new Date(Date.now() - BILLED_WINDOW_S * 1000 - 60_000)
      .toISOString() });
    expect(await (await deliver(JSON.stringify(old))).json()).toEqual({ counted: false });
    const ahead = event(who, { timestamp: new Date(Date.now() + 3600_000).toISOString() });
    expect(await (await deliver(JSON.stringify(ahead))).json()).toEqual({ counted: false });
    // A delivery replayed inside the window is refused by the execution it names, however
    // many others came between; the ids are kept by age, not by count.
    const kept = JSON.stringify(event(who));
    expect(await (await deliver(kept)).json()).toEqual({ counted: true });
    for (let i = 0; i < 5; i++) {
      expect(await (await deliver(JSON.stringify(event(who)))).json())
        .toEqual({ counted: true });
    }
    expect(await (await deliver(kept)).json()).toEqual({ counted: false });
    const { days } = await env.OWNER.getByName(who).sandboxDays(
      { owner: who, caller: who, origin: null }, {});
    expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(6 * 120);
    // An event with no time, or no execution id, is out of shape.
    const { timestamp: _t, ...untimed } = event(who);
    expect(lifecycleEvent(JSON.stringify(untimed))).toBeNull();
    const { sandbox_execution_id: _x, ...unnamed } = event(who);
    expect(lifecycleEvent(JSON.stringify(unnamed))).toBeNull();
  });

  it("reads a body no further than its bound, with or without a length (review L3)",
     async () => {
    let pulled = 0;
    const endless = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulled++;
        controller.enqueue(new Uint8Array(16 * 1024).fill(0x20));
      },
    });
    const response = await call(new Request(url(E2B_HOOK_PATH), {
      method: "POST", body: endless, headers: { "e2b-signature": "x" } }), doorsEnv);
    expect(response.status).toBe(413);
    // It stopped a chunk past its bound: never the whole of an endless body.
    expect(pulled).toBeLessThanOrEqual(HOOK_BODY_MAX / (16 * 1024) + 3);
    // Bytes, not UTF-16 units: a body of multi-byte characters past the bound is refused.
    const wide = "\u00e9".repeat(HOOK_BODY_MAX / 2 + 1);
    expect((await deliver(wide, signature(wide))).status).toBe(413);
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
    // A bit of each byte flipped, not a text suffix swapped: the last characters can carry
    // padding bits, so a changed suffix may decode to the same bytes (1 run in 256 did).
    const bytes = Buffer.from(sealed, "base64url");
    for (let at = 0; at < bytes.byteLength; at++) {
      const changed = Buffer.from(bytes);
      changed[at]! ^= 1;
      expect(openRoute(env.ROUTING_MAC_KEY, changed.toString("base64url"))).toBeNull();
    }
    expect(signed(SECRET, "{}", signature("{}"))).toBe(true);
    expect(signed(SECRET, "{}", `${signature("{}")}=`)).toBe(true);
    expect(signed(SECRET, "{}", signature("{ }"))).toBe(false);
  });

  it("routes the events of a sandbox a sign-in first made, by the route the sign-in sealed " +
     "(S14)", async () => {
    await setControl(OPEN);
    const restore = serveFirstPartyDocument();
    try {
      const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
      const app = await signInApp({ owner: who });
      expect((await appPost("signin/start", app.access_token, null,
                            { engine: "claude-code" })).status).toBe(200);
      const [asked] = await runInDurableObject(env.OWNER.getByName(who), (instance: unknown) =>
        (instance as OwnerDO).recorder.signins) as SandboxSignin[];
      expect(openRoute(env.ROUTING_MAC_KEY, asked!.route)).toEqual({ owner: who,
                                                                     authority: "owner" });
      const paused = event(who);
      paused.event_data.sandbox_metadata = { ta_route: asked!.route };
      expect(await (await deliver(JSON.stringify(paused))).json()).toEqual({ counted: true });
      const { days } = await env.OWNER.getByName(who).sandboxDays(
        { owner: who, caller: who, origin: null }, {});
      expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(120);
    } finally {
      restore();
    }
  });
});
