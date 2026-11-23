/**
 * E2B's signed lifecycle webhook (`e2b-hooks.ts`): verified by E2B's own signature
 * scheme, its body read no further than its bound, parsed strictly, routed by the sealed
 * route E2B echoes from the sandbox's metadata to the owner's object, which counts each
 * billed execution once, and only while the event's own signed time is inside its window.
 */
import { createHash } from "node:crypto";

import { env } from "cloudflare:workers";
import { describe, expect, it, vi } from "vitest";

import { E2B_HOOK_PATH } from "../src/config";
import type { DoorsEnv } from "../src/env";
import { lifecycleEvent, signed } from "../src/e2b-hooks";
import { HOOK_BODY_MAX } from "../../contract/src/limits";
import { parseRing, ringText } from "../../contract/src/envelope";
import { openRoute, sealRoute } from "../../contract/src/routing-mac";
import {
  admitOwner, appPost, call, doorsEnv, OPEN, ownDoor, recorded, serveFirstPartyDocument,
  setControl, signInApp, url,
} from "./support";
import type { SandboxSignin } from "../../contract/src/sandbox";

const SECRET = "test-e2b-webhook-signature-secret";

/** E2B's own signature over `body` (docs.e2b.dev, webhook verification). */
function signature(body: string, secret = SECRET): string {
  return createHash("sha256").update(secret + body).digest("base64").replace(/=+$/, "");
}

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** `who`'s sandbox days, as its `sandboxDays` door answers its own binding. */
function daysOf(who: string) {
  return ownDoor(who, "sandboxDays", {});
}

/** An owner the platform admitted, whose object answers its doors. */
async function admitted(): Promise<string> {
  const who = owner();
  await admitOwner(who);
  return who;
}

async function event(who: string, over: Record<string, unknown> = {}) {
  return {
    id: crypto.randomUUID(), version: "v2", type: "sandbox.lifecycle.paused",
    timestamp: new Date().toISOString(), event_category: "lifecycle", event_label: "pause",
    event_data: {
      sandbox_metadata: { ta_route: await sealRoute(env.ROUTING_KEYRING, { owner: who,
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
    const body = JSON.stringify(await event(who));
    const first = await deliver(body);
    expect([first.status, await first.json()]).toEqual([200, { counted: true }]);
    // E2B retries a delivery: the same event is counted once.
    expect(await (await deliver(body)).json()).toEqual({ counted: false });
    // Split across the UTC days it ran: a minute before midnight, a minute after.
    const { days } = await daysOf(who);
    expect(days).toEqual([
      { day: "20261103", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60,
        unverified_executions: 0 },
      { day: "20261102", seconds: 0, billed_seconds: 60, vcpu_seconds: 120, gb_seconds: 60,
        unverified_executions: 0 },
    ]);
  });

  it("counts one execution once, though a pause and a later kill both carry it",
     async () => {
    const who = await admitted();
    const paused = await event(who, { sandbox_execution_id: "exec-1" });
    paused.event_data.execution.execution_time = 60_000;
    const killed = { ...paused, id: crypto.randomUUID(), type: "sandbox.lifecycle.killed",
                     event_label: "kill",
                     event_data: { ...paused.event_data, kill_reason: "request" } };
    expect(await (await deliver(JSON.stringify(paused))).json()).toEqual({ counted: true });
    expect(await (await deliver(JSON.stringify(killed))).json()).toEqual({ counted: false });
    const { days } = await daysOf(who);
    // 60 s ran; 60 s counted.
    expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(60);
  });

  it("hands the owner each event at the time it signs, however old or ahead, and a replay is " +
     "refused by the execution it names", async () => {
    // The window the owner counts an event in is the owner's (`sandbox-time.test.ts`).
    const timed = await admitted();
    const times = [Date.now() - 7 * 86_400_000, Date.now() + 3600_000];
    for (const at of times) {
      await deliver(JSON.stringify(await event(timed, { timestamp: new Date(at).toISOString() })));
    }
    expect((await env.OWNER_GATE.seen(timed)).filter((each) => each.method === "sandboxBilled")
      .map((each) => (each.args as [{ at: number }])[0].at)).toEqual(times);
    // A delivery replayed inside the window is refused by the execution it names, however
    // many others came between; the ids are kept by age, not by count.
    const who = await admitted();
    const kept = JSON.stringify(await event(who));
    expect(await (await deliver(kept)).json()).toEqual({ counted: true });
    for (let i = 0; i < 5; i++) {
      expect(await (await deliver(JSON.stringify(await event(who)))).json())
        .toEqual({ counted: true });
    }
    expect(await (await deliver(kept)).json()).toEqual({ counted: false });
    const { days } = await daysOf(who);
    expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(6 * 120);
    // An event with no time, or no execution id, is out of shape.
    const { timestamp: _t, ...untimed } = await event(who);
    expect(lifecycleEvent(JSON.stringify(untimed))).toBeNull();
    const { sandbox_execution_id: _x, ...unnamed } = await event(who);
    expect(lifecycleEvent(JSON.stringify(unnamed))).toBeNull();
  });

  it("reads a body no further than its bound, with or without a length",
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

  it("refuses a body that is not UTF-8 as out of shape, before its signature", async () => {
    const response = await call(new Request(url(E2B_HOOK_PATH), {
      method: "POST", body: new Uint8Array([0x7b, 0xff, 0x7d]),
      headers: { "e2b-signature": "x" } }), doorsEnv);
    expect([response.status, await response.json()])
      .toEqual([400, { error: "the event is out of shape" }]);
  });

  it("refuses a delivery whose signature does not verify, or with no secret set", async () => {
    const body = JSON.stringify(await event(owner()));
    expect((await deliver(body, null)).status).toBe(401);
    expect((await deliver(body, signature(body, "another-secret-of-sixteen"))).status).toBe(401);
    expect((await deliver(`${body} `, signature(body))).status).toBe(401);
    const unset = { ...doorsEnv, E2B_WEBHOOK_SECRET: undefined } as unknown as DoorsEnv;
    expect((await deliver(body, signature(body), unset)).status).toBe(503);
  });

  it("ignores an event that bills nothing, and one naming no sandbox of ours", async () => {
    const created = JSON.stringify(await event(owner(), { type: "sandbox.lifecycle.created" }));
    expect(await (await deliver(created)).json()).toEqual({ ignored: true });
    const stranger = await event(owner());
    stranger.event_data.sandbox_metadata = { ta_route: "x".repeat(60) };
    const body = JSON.stringify(stranger);
    expect(await (await deliver(body)).json()).toEqual({ unrouted: true });
    expect((await deliver("not json", signature("not json"))).status).toBe(400);
    expect(lifecycleEvent(JSON.stringify({ ...(await event(owner())), event_data: {} })))
      .toBeNull();
  });

  it("answers the owner's object's refusal as JSON, its status and wait, so E2B sends the " +
     "event again", async () => {
    const who = await admitted();
    const busy = Object.assign(new Error("Durable Object is overloaded"), { overloaded: true });
    const stub = vi.spyOn(doorsEnv.OWNER, "getByName").mockImplementation(() => ({
      sandboxBilled: () => Promise.reject(busy) }) as never);
    const answer = await deliver(JSON.stringify(await event(who)))
      .finally(() => stub.mockRestore());
    expect([answer.status, answer.headers.get("Retry-After"), await answer.json()])
      .toEqual([429, "1", { error: "this account is busy" }]);
  });

  it("seals a route only the routing keyring opens, and checks E2B's signature exactly",
     async () => {
    const sealed = await sealRoute(env.ROUTING_KEYRING, { owner: "user_a", authority: "owner" });
    expect(await openRoute(env.ROUTING_KEYRING, sealed)).toEqual({ owner: "user_a",
                                                                   authority: "owner" });
    expect(sealed).not.toContain("user_a");
    const ring = parseRing(env.ROUTING_KEYRING)!;
    const only = (kid: string) => ringText({ active: kid,
                                             keys: new Map([[kid, ring.keys.get(kid)!]]) });
    // Sealed by an owner whose ring still has the older kid active, it opens at these doors,
    // whose ring holds both; a ring without the kid it names opens nothing.
    const older = await sealRoute(only("k1"), { owner: "user_a", authority: "owner" });
    expect(await openRoute(env.ROUTING_KEYRING, older)).toEqual({ owner: "user_a",
                                                                  authority: "owner" });
    expect(await openRoute(only("k2"), older)).toBeNull();
    expect(await openRoute(only("k1"), sealed)).toBeNull();
    expect(signed(SECRET, "{}", signature("{}"))).toBe(true);
    expect(signed(SECRET, "{}", `${signature("{}")}=`)).toBe(true);
    expect(signed(SECRET, "{}", signature("{ }"))).toBe(false);
  });

  it("routes the events of a sandbox a sign-in first made, by the route the sign-in sealed",
     async () => {
    await setControl(OPEN);
    const restore = serveFirstPartyDocument();
    try {
      const who = `user_${crypto.randomUUID().replace(/-/g, "")}`;
      const app = await signInApp({ owner: who });
      expect((await appPost("signin/start", app.access_token, null,
                            { engine: "claude-code" })).status).toBe(200);
      const [asked] = (await recorded(who)).signins as SandboxSignin[];
      expect(await openRoute(env.ROUTING_KEYRING, asked!.route)).toEqual({ owner: who,
                                                                           authority: "owner" });
      const paused = await event(who);
      paused.event_data.sandbox_metadata = { ta_route: asked!.route };
      expect(await (await deliver(JSON.stringify(paused))).json()).toEqual({ counted: true });
      const { days } = await daysOf(who);
      expect(days.reduce((sum, day) => sum + day.billed_seconds, 0)).toBe(120);
    } finally {
      restore();
    }
  });
});
