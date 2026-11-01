/**
 * The egress door (tasks 4 G4), in workerd, through the real owner object and the real
 * gateway Worker (its `EgressGateway` entrypoint, as the config binds it), against the fake
 * providers' Groq.
 *
 * Every test holds the door's rule: a sandbox request reaches its provider only with a
 * live lease token, admitted at the owner's object under the current policy and network
 * generations, to a sealed endpoint of the owner's own connection, with the sealed key in
 * place of every credential it carried; every refusal is a fixed code that forwards
 * nothing; and every admitted request is settled once, however it ends.
 */
import { createExecutionContext, env, evictDurableObject } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PLATFORM_CALLER } from "../../owner/src/admission";
import { EGRESS_DEADLINE_S, GENERATION_HEADER, SANDBOX_HEADER } from "../../owner/src/network";
import type { Binding } from "../../owner/src/owner-do";
import type { Bound } from "../src/binder";
import { type DoorsEnv, EGRESS_ORIGIN, now } from "../src/config";
import { putLease } from "../src/directory";
import { EGRESS_DEADLINE_MS, serveEgress } from "../src/egress";
import { leaseTokenHash, type OpenedLease, sandboxLeaseClose, sandboxLeaseOpen,
         sandboxLeaseRenew } from "../src/sandbox";
import { EGRESS_CHUNKS } from "./fake-providers";
import {
  appPost, call, doorsEnv, OPEN, serveFirstPartyDocument, setControl, signInApp, sqlIn, url,
} from "./support";

const COMPLETIONS = "/openai/v1/chat/completions";
const BODY = JSON.stringify({ model: "llama", stream: true,
                              messages: [{ role: "user", content: "hi" }] });

interface Sandbox {
  readonly owner: string;
  readonly session: string;
  readonly token: string;
  readonly app: string;
  readonly connection: string;
  readonly bound: Bound;
  readonly opened: OpenedLease;
  readonly key: string;
}

function ownerBound(owner: string): Bound {
  return { binding: { owner, caller: owner, origin: null }, stub: env.OWNER.getByName(owner) };
}

function sessionOf(sandbox: Sandbox): Binding {
  return { owner: sandbox.owner, caller: sandbox.owner, origin: null, session: sandbox.session };
}

/** A signed-in owner with a Groq key connected, its sandbox's lease open and its network applied. */
async function sandbox(apply = true): Promise<Sandbox> {
  const owner = `user_${crypto.randomUUID().replace(/-/g, "")}`;
  const app = await signInApp({ owner });
  const key = `gsk-good-${crypto.randomUUID()}`;
  const connected = await appPost("connections/key", app.access_token, app.session,
                                  { source: "groq", key });
  expect(connected.status).toBe(200);
  const { connection } = await connected.json<{ connection: string }>();
  const bound = ownerBound(owner);
  const opened = await sandboxLeaseOpen(doorsEnv, bound, { holder: "sandbox-1" });
  if (apply) {
    await bound.stub.sandboxNetworkApplied(bound.binding, {
      lease_id: opened.lease.lease_id, epoch: opened.lease.epoch,
      generation: opened.network.generation });
  }
  return { owner, session: app.session, token: opened.token, app: app.access_token,
           connection, bound, opened, key };
}

function egressRequest(box: Pick<Sandbox, "token" | "connection">, path: string = COMPLETIONS,
                       init: { method?: string; headers?: Record<string, string>;
                               body?: string | null; generation?: string | null } = {}):
    Request {
  const generation = init.generation === undefined ? "1" : init.generation;
  return new Request(`${EGRESS_ORIGIN}/c/${box.connection}${path}`, {
    method: init.method ?? "POST",
    headers: {
      "content-type": "application/json",
      // What a model client in the sandbox sends: a placeholder in every credential place.
      authorization: `Bearer ta_ph_${box.connection}_0123456789abcdef`,
      "x-api-key": "ta_ph_placeholder", cookie: "session=s",
      ...(box.token === "" ? {} : { [SANDBOX_HEADER]: box.token }),
      ...(generation === null ? {} : { [GENERATION_HEADER]: generation }),
      ...init.headers,
    },
    body: init.body === undefined ? BODY : init.body,
  });
}

/** What the fake providers received at Groq's chat completions. */
async function forwarded(): Promise<{ headers: Record<string, string>; url: string;
                                      body: string }[]> {
  const log = await (await env.PROVIDERS.fetch("https://status.test/log")).json<
    { headers: Record<string, string>; url: string; body: string }[]>();
  return log.filter((entry) => new URL(entry.url).pathname === COMPLETIONS);
}

async function reservations(owner: string) {
  return sqlIn(owner, "SELECT state, code, ended, outcome, status, bytes, path, method " +
                        "FROM egress_reservations ORDER BY opened_at, rowid");
}

/** The answer's refusal code. */
async function codeOf(response: Response): Promise<string> {
  return (await response.json<{ error: { code: string } }>()).error.code;
}

async function upstreamCancelled(id: string): Promise<boolean> {
  return (await (await env.PROVIDERS.fetch(`https://status.test/cancelled?id=${id}`))
    .json<{ cancelled: boolean }>()).cancelled;
}

async function until(check: () => Promise<boolean>, what: string): Promise<void> {
  for (let tries = 0; tries < 200; tries++) {
    if (await check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error(`timed out waiting for ${what}`);
}

/** Read until the stream ends: "done", or the message it was errored with. */
async function drained(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<string> {
  for (;;) {
    try {
      if ((await reader.read()).done) {
        return "done";
      }
    } catch (error) {
      return (error as Error).message;
    }
  }
}

async function settledOf(owner: string): Promise<void> {
  await until(async () => (await reservations(owner))[0]!["state"] === "settled", "the settle");
}

/** A policy write in the owner's session: the open-mode document, at the next generation. */
async function writePolicy(box: Sandbox): Promise<number> {
  const { generation, policy } = await box.bound.stub.policyRead(box.bound.binding);
  const written = await box.bound.stub.policyWrite(sessionOf(box), {
    expected_generation: generation,
    policy: { ...policy, egress: { mode: "allowlist", destinations: [] } } });
  return written.generation;
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  await env.PROVIDERS.fetch("https://status.test/log", { method: "DELETE" });
  restore = serveFirstPartyDocument();
});

afterEach(() => {
  restore();
  vi.restoreAllMocks();
});

describe("the egress door", () => {
  it("keeps the gateway's own deadline (the gateway's tests hold the two equal)", () => {
    expect(EGRESS_DEADLINE_MS).toBe(EGRESS_DEADLINE_S * 1000);
  });

  it("forwards a live token's request with the sealed key in place of every credential, " +
     "streams the answer back and settles its bytes", async () => {
    const box = await sandbox();
    const response = await call(egressRequest(box, `${COMPLETIONS}?key=ta_ph&stream=true`));
    expect(response.status).toBe(200);
    expect(response.headers.get("x-upstream")).toBe("fake");
    expect(response.headers.get("x-ta-resealed")).toBeNull();
    const text = await response.text();
    expect(text).toBe(EGRESS_CHUNKS.join(""));
    const [sent, ...more] = await forwarded();
    expect(more).toEqual([]);
    expect(sent!.url).toBe(`https://api.groq.com${COMPLETIONS}?stream=true`);
    expect(sent!.headers["authorization"]).toBe(`Bearer ${box.key}`);
    for (const stripped of ["x-api-key", "cookie", SANDBOX_HEADER, GENERATION_HEADER]) {
      expect(sent!.headers[stripped]).toBeUndefined();
    }
    expect(JSON.stringify(sent)).not.toContain("ta_ph");
    expect(JSON.stringify(sent)).not.toContain(box.token);
    expect(sent!.body).toBe(BODY);
    expect(await reservations(box.owner)).toEqual([{
      state: "settled", code: null, ended: null, outcome: "complete", status: 200,
      bytes: new TextEncoder().encode(text).byteLength, path: `${COMPLETIONS}?key=ta_ph&stream=true`,
      method: "POST" }]);
  });

  it("passes the provider's own refusal through, and settles its status", async () => {
    const box = await sandbox();
    const response = await call(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "busy" } }));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("7");
    expect(await response.json()).toEqual({ error: { message: "rate limited" } });
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled",
                                                           outcome: "complete", status: 429 }]);
  });

  it("keeps the gateway's own header from the sandbox, and settles despite a bad one",
     async () => {
    const box = await sandbox();
    const resealing: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: async () => new Response("ok", { headers: { "x-ta-resealed": "{\"blob\":1}",
                                                           "x-upstream": "fake" } }) } };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await call(egressRequest(box), resealing);
    expect(response.headers.get("x-ta-resealed")).toBeNull();
    expect(response.headers.get("x-upstream")).toBe("fake");
    expect(await response.text()).toBe("ok");
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled",
                                                           outcome: "complete", bytes: 2 }]);
  });

  it("writes no directory entry for a lease the object refused", async () => {
    const box = await sandbox();
    const entries = async () => (await env.DIRECTORY.list({ prefix: "l:" })).keys.length;
    const before = await entries();
    await expect(sandboxLeaseOpen(doorsEnv, box.bound, { holder: "sandbox-2" }))
      .rejects.toThrow("the sync lease is held");
    expect(await entries()).toBe(before);
  });

  it("hands the sandbox slice a token, its network and its clients' environment once", async () => {
    const box = await sandbox(false);
    expect(box.token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    const { network } = box.opened;
    expect(network.network.rules["egress.tinyassets.io"]).toEqual([{ transform: { headers: {
      [SANDBOX_HEADER]: box.token, [GENERATION_HEADER]: "1" } } }]);
    expect(network.network.denyOut).toEqual(["0.0.0.0/0"]);
    expect(network.network.allowOut).toEqual(["*", "egress.tinyassets.io"]);
    expect(box.opened.env["GROQ_BASE_URL"])
      .toBe(`https://egress.tinyassets.io/c/${box.connection}/openai/v1`);
    expect(box.opened.env["GROQ_API_KEY"]).toMatch(
      new RegExp(`^ta_ph_${box.connection}_[0-9a-f]{32}$`));
    expect(JSON.stringify(box.opened.env)).not.toContain(box.key);
    // Only the token's hash is kept: in the object and as the directory's key.
    const hash = await leaseTokenHash(box.token);
    expect(await sqlIn(box.owner, "SELECT lease_token FROM file_space"))
      .toEqual([{ lease_token: hash }]);
    expect(await env.DIRECTORY.get(`l:${hash}`, { type: "json" })).toEqual({
      o: box.owner, l: box.opened.lease.lease_id, e: Math.floor(box.opened.lease.expires_at) });
    const stored = JSON.stringify(await sqlIn(box.owner, "SELECT * FROM file_space"));
    expect(stored).not.toContain(box.token);
  });

  it("renews the lease's directory entry after the object, and closes the object first", async () => {
    const box = await sandbox();
    const hash = await leaseTokenHash(box.token);
    await sqlIn(box.owner, "UPDATE file_space SET lease_expires = lease_expires - 30");
    await putLease(doorsEnv, hash, { o: box.owner, l: box.opened.lease.lease_id, e: now() + 5 },
                   now());
    const renewed = await sandboxLeaseRenew(doorsEnv, box.bound, box.opened.lease);
    expect(renewed.expires_at).toBeGreaterThanOrEqual(now() + 59);
    expect(await env.DIRECTORY.get(`l:${hash}`, { type: "json" })).toEqual({
      o: box.owner, l: box.opened.lease.lease_id, e: Math.floor(renewed.expires_at) });
    // The object refuses the token before the entry goes: here the entry is put back.
    expect(await sandboxLeaseClose(doorsEnv, box.bound, box.opened.lease))
      .toEqual({ released: true });
    expect(await env.DIRECTORY.get(`l:${hash}`)).toBeNull();
    const gone = await call(egressRequest(box));
    expect([gone.status, await codeOf(gone)]).toEqual([401, "egress_token_unknown"]);
    await putLease(doorsEnv, hash, { o: box.owner, l: box.opened.lease.lease_id,
                                     e: now() + 60 }, now());
    const closed = await call(egressRequest(box));
    expect([closed.status, await codeOf(closed)]).toEqual([401, "egress_lease_closed"]);
    expect(await forwarded()).toEqual([]);
  });
});

describe("a refused request", () => {
  it.each([
    ["no token", "", 401, "egress_token_missing"],
    ["a malformed token", "not-a-token", 401, "egress_token_unknown"],
    ["an unknown token", "A".repeat(43), 401, "egress_token_unknown"],
  ])("with %s is refused before any object, and forwards nothing", async (_what, token, status,
                                                                         code) => {
    const box = await sandbox();
    const spy = vi.spyOn(doorsEnv.OWNER, "getByName");
    const response = await call(egressRequest({ ...box, token }));
    expect([response.status, await codeOf(response)]).toEqual([status, code]);
    expect(spy).not.toHaveBeenCalled();
    expect(await forwarded()).toEqual([]);
  });

  it("with an empty or malformed token is refused before the directory is read", async () => {
    const box = await sandbox();
    const read = vi.spyOn(doorsEnv.DIRECTORY, "get");
    for (const [token, code] of [["", "egress_token_missing"], [" ", "egress_token_missing"],
                                 ["A".repeat(42), "egress_token_unknown"],
                                 [`${"A".repeat(42)}=`, "egress_token_unknown"]] as const) {
      const request = egressRequest(box);
      request.headers.set(SANDBOX_HEADER, token);
      const response = await call(request);
      expect([response.status, await codeOf(response)]).toEqual([401, code]);
    }
    expect(read.mock.calls.filter(([key]) => String(key).startsWith("l:"))).toEqual([]);
    expect(await forwarded()).toEqual([]);
  });

  it("with an expired token is refused before any object", async () => {
    const box = await sandbox();
    const hash = await leaseTokenHash(box.token);
    await env.DIRECTORY.put(`l:${hash}`, JSON.stringify({
      o: box.owner, l: box.opened.lease.lease_id, e: now() - 1 }));
    const spy = vi.spyOn(doorsEnv.OWNER, "getByName");
    const response = await call(egressRequest(box));
    expect([response.status, await codeOf(response)]).toEqual([401, "egress_token_expired"]);
    expect(spy).not.toHaveBeenCalled();
    expect(await forwarded()).toEqual([]);
  });

  it("whose lease the object holds no longer is refused there, with a receipt", async () => {
    const box = await sandbox();
    await sqlIn(box.owner, "UPDATE file_space SET lease_expires = 1");
    const response = await call(egressRequest(box));
    expect(response.status).toBe(401);
    const answer = await response.json<{ error: { code: string }; receipt: string }>();
    expect(answer.error.code).toBe("egress_lease_closed");
    expect(answer.receipt).toMatch(/^[0-9a-f]{32}$/);
    expect(await reservations(box.owner)).toMatchObject([{ state: "refused",
                                                           code: "egress_lease_closed" }]);
    expect(await forwarded()).toEqual([]);
  });

  it("names a connection that is not the owner's, or an endpoint not sealed", async () => {
    const box = await sandbox();
    const other = await sandbox();
    for (const [request, code] of [
      [egressRequest({ ...box, connection: other.connection }), "egress_connection_refused"],
      [egressRequest({ ...box, connection: `c${"0".repeat(24)}` }), "egress_connection_refused"],
      [egressRequest(box, "/openai/v1/files"), "egress_endpoint_refused"],
      [egressRequest(box, COMPLETIONS, { method: "DELETE", body: null }),
       "egress_endpoint_refused"],
      [egressRequest(box, "/openai/v1/chat/completions%2F..%2Fadmin"), "egress_endpoint_refused"],
    ] as const) {
      const response = await call(request);
      expect([response.status, await codeOf(response)]).toEqual([403, code]);
    }
    expect(await forwarded()).toEqual([]);
  });

  it("of a suspended owner (billing refuses work) or an erased one is refused", async () => {
    const box = await sandbox();
    await box.bound.stub.admissionSet({ owner: box.owner, caller: PLATFORM_CALLER, origin: null },
                                      { reason: "billing", suspended: true });
    const suspended = await call(egressRequest(box));
    expect([suspended.status, await codeOf(suspended)]).toEqual([403, "owner_suspended"]);
    await sqlIn(box.owner, "UPDATE owner_admission SET state = 'erased', reasons = '[]'");
    await evictDurableObject(box.bound.stub);
    const erased = await call(egressRequest(box));
    expect([erased.status, await codeOf(erased)]).toEqual([403, "owner_erased"]);
    expect(await forwarded()).toEqual([]);
  });

  it("under a stale policy generation, or before its network is applied", async () => {
    const box = await sandbox(false);
    const unapplied = await call(egressRequest(box));
    expect([unapplied.status, await codeOf(unapplied)]).toEqual([409, "egress_network_stale"]);
    for (const generation of [null, "0", "2", "x"]) {
      const stale = await call(egressRequest(box, COMPLETIONS, { generation }));
      expect([stale.status, await codeOf(stale)]).toEqual([409, "egress_policy_stale"]);
    }
    expect(await forwarded()).toEqual([]);
  });

  it("past the owner's rate", async () => {
    const box = await sandbox();
    await sqlIn(box.owner, "INSERT INTO egress_reservations (id, lease_id, connection_id, " +
                  "version, method, path, generation, opened_at, state, outcome, settled_at) " +
                  "SELECT lower(hex(randomblob(16))), ?, ?, 1, 'POST', '/x', 1, ?, 'settled', " +
                  "'complete', ? FROM (WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 " +
                  "FROM n WHERE i < 600) SELECT i FROM n)",
                box.opened.lease.lease_id, box.connection, Date.now() / 1000,
                Date.now() / 1000);
    const response = await call(egressRequest(box));
    expect([response.status, await codeOf(response)]).toEqual([429, "egress_rate"]);
    expect(response.headers.get("Retry-After")).toBe("1");
    expect(await forwarded()).toEqual([]);
  });
});

describe("a lease token", () => {
  it("is refused at another owner's object", async () => {
    const box = await sandbox();
    const other = await sandbox();
    const hash = await leaseTokenHash(box.token);
    await env.DIRECTORY.put(`l:${hash}`, JSON.stringify({
      o: other.owner, l: other.opened.lease.lease_id, e: now() + 60 }));
    const response = await call(egressRequest({ ...box, connection: other.connection }));
    expect([response.status, await codeOf(response)]).toEqual([401, "egress_lease_closed"]);
    expect(await forwarded()).toEqual([]);
  });

  it("reaches no other door: /api, /mcp and /app refuse it, and the egress origin serves " +
     "nothing else", async () => {
    const box = await sandbox();
    const headers = { [SANDBOX_HEADER]: box.token, [GENERATION_HEADER]: "1",
                      Authorization: `Bearer ${box.token}`, "Content-Type": "application/json" };
    for (const path of ["/api/tools/read_graph", "/mcp", "/app/api/agent/status"]) {
      const response = await call(new Request(url(path), { method: "POST", headers,
                                                           body: "{}" }));
      expect(response.status).toBe(401);
    }
    for (const path of ["/api/tools/read_graph", "/mcp", "/app/api/agent/status", "/c",
                        `/c/${box.connection}`]) {
      const response = await call(new Request(`${EGRESS_ORIGIN}${path}`, { method: "POST",
                                                                           headers, body: "{}" }));
      expect(response.status).toBe(404);
    }
    expect(await forwarded()).toEqual([]);
  });
});

describe("a policy write mid-stream", () => {
  it("ends the stream and settles it, and the next open waits for the network", async () => {
    const box = await sandbox();
    const response = await call(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-policy" } }));
    expect(response.status).toBe(200);
    const reader = response.body!.getReader();
    // The gateway holds back a secret's length less one, in case a secret spans chunks.
    const first = (await reader.read()).value!.byteLength;
    expect(first).toBeGreaterThan(0);
    const generation = await writePolicy(box);
    expect(generation).toBe(2);
    await settledOf(box.owner);
    expect(await drained(reader)).toBe("egress_ended");
    expect(await reservations(box.owner)).toMatchObject([{
      state: "settled", ended: "policy", outcome: "ended", status: 200, bytes: first }]);
    await until(() => upstreamCancelled("policy"), "the upstream cancel");
    // The old network's transform carries the old generation; the new one waits to be applied.
    const old = await call(egressRequest(box));
    expect([old.status, await codeOf(old)]).toEqual([409, "egress_policy_stale"]);
    const waiting = await call(egressRequest(box, COMPLETIONS, { generation: "2" }));
    expect([waiting.status, await codeOf(waiting)]).toEqual([409, "egress_network_stale"]);
    await box.bound.stub.sandboxNetworkApplied(box.bound.binding, {
      lease_id: box.opened.lease.lease_id, epoch: box.opened.lease.epoch, generation: 2 });
    const applied = await call(egressRequest(box, COMPLETIONS, { generation: "2" }));
    expect(applied.status).toBe(200);
    expect(await applied.text()).toBe(EGRESS_CHUNKS.join(""));
  });
});

describe("settlement", () => {
  it("records a client's abort", async () => {
    const box = await sandbox();
    const response = await call(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-abort" } }));
    const reader = response.body!.getReader();
    await reader.read();
    await reader.cancel();
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "aborted", ended: null,
                                                           status: 200 }]);
    await until(() => upstreamCancelled("abort"), "the upstream cancel");
  });

  it("records the gateway's failure", async () => {
    const box = await sandbox();
    const failing: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: () => Promise.reject(new Error("the gateway is gone")) } };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await call(egressRequest(box), failing);
    expect([response.status, await codeOf(response)]).toEqual([502, "egress_gateway_failed"]);
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled", outcome: "failed",
                                                           status: null, bytes: 0 }]);
  });

  it("records the door's deadline", async () => {
    const box = await sandbox();
    const ctx = createExecutionContext();
    const response = await serveEgress(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-deadline" } }), doorsEnv, ctx, 300);
    const reader = response.body!.getReader();
    expect(await drained(reader)).toBe("egress_deadline");
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "deadline", status: 200 }]);
    await until(() => upstreamCancelled("deadline"), "the upstream cancel");
  });

  it("ends a stream when its lease closes, or its connection is revoked", async () => {
    const box = await sandbox();
    const first = await call(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-close" } }));
    const reader = first.body!.getReader();
    await reader.read();
    await sandboxLeaseClose(doorsEnv, box.bound, box.opened.lease);
    await settledOf(box.owner);
    expect(await drained(reader)).toBe("egress_ended");
    expect(await reservations(box.owner)).toMatchObject([{ ended: "lease", outcome: "ended" }]);

    const again = await sandbox();
    const second = await call(egressRequest(again, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-revoke" } }));
    const held = second.body!.getReader();
    await held.read();
    const revoked = await appPost("connections/revoke", again.app, again.session,
                                  { connection: again.connection });
    expect(revoked.status).toBe(200);
    await settledOf(again.owner);
    expect(await drained(held)).toBe("egress_ended");
    expect(await reservations(again.owner)).toMatchObject([{ ended: "connection",
                                                             outcome: "ended" }]);
    const after = await call(egressRequest(again));
    expect([after.status, await codeOf(after)]).toEqual([403, "egress_connection_refused"]);
  });
});
