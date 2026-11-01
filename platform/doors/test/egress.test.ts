/**
 * The egress door (tasks 4 G4), in workerd, through the real owner object and the real
 * gateway Worker (its `EgressGateway` entrypoint, as the config binds it), against the fake
 * providers' Groq.
 *
 * Every test holds the door's rule: a sandbox model call reaches its provider only with a
 * live egress token whose routing MAC the door verified before any object, admitted and
 * charged at the owner's object under the current generations and the owner's spend caps,
 * to a metered, sealed endpoint of the owner's own connection (a read of one is free), with the sealed key in place of every credential it carried and its output cap
 * clamped to its reservation; every refusal is a fixed code that forwards nothing; and every
 * admitted call is settled once, however it ends, at the cost pi-ai read, or kept whole.
 */
import { createExecutionContext, env, runInDurableObject } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PLATFORM_CALLER } from "../../owner/src/admission";
import { EGRESS_DEADLINE_S, GENERATION_HEADER, SANDBOX_HEADER } from "../../owner/src/network";
import type { Binding, OpenedLease } from "../../owner/src/owner-do";
import type { Bound } from "../src/binder";
import { type DoorsEnv, EGRESS_ORIGIN } from "../src/config";
import { EGRESS_DEADLINE_MS, serveEgress } from "../src/egress";
import { mintToken, verifyToken } from "../src/routing-mac";
import worker, { type OwnerDO } from "./entry";
import { EGRESS_CHUNKS, EGRESS_USAGE } from "./fake-providers";
import {
  appPost, call, doorsEnv, OPEN, reached, serveFirstPartyDocument, setControl, signInApp, sqlIn,
  url,
} from "./support";

const COMPLETIONS = "/openai/v1/chat/completions";

interface Sandbox {
  readonly owner: string;
  readonly session: string;
  readonly token: string;
  readonly app: string;
  readonly connection: string;
  readonly model: string;
  readonly bound: Bound;
  readonly opened: OpenedLease;
  readonly key: string;
}

/**
 * One egress call through the doors, its answer handed back at once: the settle it
 * registers with `waitUntil` resolves only once its stream ended, which the test reads.
 */
function send(request: Request, environment: DoorsEnv = doorsEnv): Promise<Response> {
  return worker.fetch(request, environment, createExecutionContext());
}

function ownerBound(owner: string): Bound {
  return { binding: { owner, caller: owner, origin: null }, stub: env.OWNER.getByName(owner) };
}

function sessionOf(sandbox: Sandbox): Binding {
  return { owner: sandbox.owner, caller: sandbox.owner, origin: null, session: sandbox.session };
}

/** The owner object's own sandbox lease API, as 8b's `exec` reaches it in the object. */
function leases<R>(owner: string, act: (leases: OwnerDO["sandbox"]) => Promise<R>): Promise<R> {
  return runInDurableObject(env.OWNER.getByName(owner), (instance: unknown) =>
    act((instance as OwnerDO).sandbox));
}

/**
 * A signed-in owner with a Groq key connected, its sandbox's lease open and its network
 * applied; the owner's caps roomy unless `models` sets them, its spend table as `spend`.
 */
async function sandbox(apply = true, models: Record<string, unknown> = {
  turn_requests: 10_000, turn_tokens: 1_000_000_000, turn_cost: 1_000_000_000_000 },
                       spend: Record<string, unknown> = {}): Promise<Sandbox> {
  const owner = `user_${crypto.randomUUID().replace(/-/g, "")}`;
  const app = await signInApp({ owner });
  const key = `gsk-good-${crypto.randomUUID()}`;
  const connected = await appPost("connections/key", app.access_token, app.session,
                                  { source: "groq", key });
  expect(connected.status).toBe(200);
  const { connection, models: ids } = await connected.json<{ connection: string;
                                                             models: string[] }>();
  const bound = ownerBound(owner);
  const { generation, policy } = await bound.stub.policyRead(bound.binding);
  await bound.stub.policyWrite({ ...bound.binding, session: app.session }, {
    expected_generation: generation,
    policy: { ...policy, models, spend: { ...policy.spend, ...spend } } });
  const opened = await leases(owner, (sandbox) => sandbox.open({ holder: "sandbox-1" }));
  if (apply) {
    await leases(owner, (sandbox) => sandbox.networkApplied({
      lease_id: opened.lease.lease_id, epoch: opened.lease.epoch,
      generation: opened.network.generation }));
  }
  return { owner, session: app.session, token: opened.egress_token, app: app.access_token,
           connection, model: ids[0]!, bound, opened, key };
}

function bodyOf(box: Pick<Sandbox, "model">, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ model: box.model, stream: true, max_completion_tokens: 900_000_000,
                          messages: [{ role: "user", content: "hi" }], ...extra });
}

function egressRequest(box: Pick<Sandbox, "token" | "connection" | "model">,
                       path: string = COMPLETIONS,
                       init: { method?: string; headers?: Record<string, string>;
                               body?: string | null; generation?: string | null } = {}):
    Request {
  const generation = init.generation === undefined ? "1" : init.generation;
  const body = init.body === undefined ? bodyOf(box) : init.body;
  return new Request(`${EGRESS_ORIGIN}/c/${box.connection}${path}`, {
    method: init.method ?? "POST",
    headers: {
      "content-type": "application/json",
      ...(body === null ? {} : { "content-length": String(new TextEncoder().encode(body)
        .byteLength) }),
      // What a model client in the sandbox sends: a placeholder in every credential place.
      authorization: `Bearer ta_ph_${box.connection}_0123456789abcdef`,
      "x-api-key": "ta_ph_placeholder", cookie: "session=s",
      ...(box.token === "" ? {} : { [SANDBOX_HEADER]: box.token }),
      ...(generation === null ? {} : { [GENERATION_HEADER]: generation }),
      ...init.headers,
    },
    body,
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
  return sqlIn(owner, "SELECT state, code, ended, outcome, status, bytes, bytes_hint, path, " +
                        "method FROM egress_reservations ORDER BY opened_at, rowid");
}

/** Each call's charge: its state (or the row's, uncharged), its rule, what it held and used. */
async function spends(owner: string) {
  return sqlIn(owner, "SELECT coalesce(charge, state) AS state, rule, tokens, cost, est_cost " +
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

/** A policy write in the owner's session that changes the egress table. */
async function writeEgress(box: Sandbox): Promise<number> {
  const { generation, policy } = await box.bound.stub.policyRead(box.bound.binding);
  await box.bound.stub.policyWrite(sessionOf(box), {
    expected_generation: generation,
    policy: { ...policy, egress: { mode: "allowlist", destinations: [] } } });
  return (await leases(box.owner, (sandbox) => sandbox.config())).generation;
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

  it("forwards a live token's call with the sealed key in place of every credential, its " +
     "output cap clamped, streams the answer back and settles it at pi-ai's cost", async () => {
    const box = await sandbox();
    const body = bodyOf(box);
    const response = await send(egressRequest(
      box, `${COMPLETIONS}?key=ta_ph&KEY=ta_ph&Access_Token=ta_ph&stream=true`));
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
    const reserved = (await spends(box.owner))[0]!;
    const asked = JSON.parse(sent!.body) as Record<string, unknown>;
    // The output cap the reservation leaves, in place of the 900M the client asked.
    expect(asked["max_completion_tokens"]).toBeLessThan(900_000_000);
    expect(asked["max_completion_tokens"]).toBeGreaterThan(0);
    // The rest as the client sent it, but for the usage report the gateway asks for.
    expect({ ...asked, max_completion_tokens: 0 }).toEqual({
      ...JSON.parse(body), max_completion_tokens: 0, stream_options: { include_usage: true } });
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toEqual([{
      state: "settled", code: null, ended: null, outcome: "complete", status: 200,
      bytes: new TextEncoder().encode(text).byteLength,
      bytes_hint: new TextEncoder().encode(body).byteLength,
      path: `${COMPLETIONS}?key=ta_ph&KEY=ta_ph&Access_Token=ta_ph&stream=true`, method: "POST" }]);
    // pi-ai's own reading of the stream, priced by the catalog's record of the model.
    const [{ models }] = await sqlIn(box.owner, "SELECT models FROM connections") as
      [{ models: string }];
    const record = (JSON.parse(models) as { id: string; cost: { input: number;
                                                                output: number } }[])
      .find((model) => model.id === box.model)!;
    const cost = Math.ceil((EGRESS_USAGE.prompt_tokens * record.cost.input
                            + EGRESS_USAGE.completion_tokens * record.cost.output));
    expect(await spends(box.owner)).toEqual([{ state: "succeeded", rule: null,
                                               tokens: EGRESS_USAGE.total_tokens, cost,
                                               est_cost: reserved["est_cost"] }]);
  });

  it("streams an SSE answer as the provider writes it, unbuffered, its type kept", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-stream" } }));
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("text/event-stream");
    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let seen = "";
    // The first event and the pings after it arrive while the provider is still writing.
    while (!seen.includes(": ping")) {
      const { value, done } = await reader.read();
      expect(done).toBe(false);
      seen += decoder.decode(value, { stream: true });
    }
    expect(seen.startsWith(EGRESS_CHUNKS[0]!.slice(0, 20))).toBe(true);
    expect(await upstreamCancelled("stream")).toBe(false);
    await reader.cancel();
    await settledOf(box.owner);
    await until(() => upstreamCancelled("stream"), "the upstream cancel");
  });

  it("forwards a read of a sealed endpoint free: no charge, no metering", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, "/openai/v1/models",
                                              { method: "GET", body: null }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ data: [] });
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled", method: "GET",
                                                           outcome: "complete", status: 200 }]);
    expect(await spends(box.owner)).toEqual([{ state: "settled", rule: null, tokens: null,
                                               cost: null, est_cost: null }]);
  });

  it("settles a stream that reported no usage unknown, keeping its whole charge", async () => {
    const box = await sandbox();
    for (let i = 0; i < 3; i++) {
      const response = await send(egressRequest(box, COMPLETIONS, {
        headers: { "x-fake-egress": "nousage" } }));
      expect(response.status).toBe(200);
      expect(await response.text()).toContain("one ");
      await until(async () => (await reservations(box.owner))
        .filter((row) => row["state"] === "settled").length === i + 1, "the settle");
    }
    const charged = await spends(box.owner);
    for (const row of charged) {
      expect(row).toMatchObject({ state: "unknown", cost: null, tokens: null });
    }
    const [spent] = await sqlIn(box.owner, "SELECT COALESCE(SUM(cost), 0) AS cost " +
                                            "FROM model_spend WHERE period GLOB 'd:*'");
    expect(spent!["cost"]).toBe(charged.reduce((sum, row) => sum + (row["est_cost"] as number), 0));
  }, 60_000);

  it("prices a dated snapshot as the model the call named, and keeps its id; a call that " +
     "listed fallbacks stays unknown", async () => {
    const box = await sandbox();
    const named = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "snapshot" } }));
    await named.text();
    await settledOf(box.owner);
    const [settled] = await sqlIn(box.owner, "SELECT charge, cost, served FROM " +
                                              "egress_reservations");
    expect(settled).toMatchObject({ charge: "succeeded", served: `${box.model}-2026-01-01` });
    expect(settled!["cost"] as number).toBeGreaterThan(0);
    const other = await sandbox();
    const listed = await send(egressRequest(other, COMPLETIONS, {
      headers: { "x-fake-egress": "snapshot" },
      body: bodyOf(other, { models: [other.model, "someone/else"] }) }));
    await listed.text();
    await settledOf(other.owner);
    expect(await spends(other.owner)).toMatchObject([{ state: "unknown", cost: null }]);
  });

  it("holds a call to the model policy's caps alone: a purchase threshold refuses nothing",
     async () => {
    const box = await sandbox(true, { turn_requests: 10_000, turn_tokens: 1_000_000_000,
                                      turn_cost: 1_000_000_000_000 }, { ask_above: 100 });
    const response = await send(egressRequest(box));
    expect(response.status).toBe(200);
    await response.text();
    await settledOf(box.owner);
  });

  it("refuses what the charge cannot bound before any forward: several choices, a paid " +
     "server-side tool", async () => {
    const box = await sandbox();
    for (const [extra, status, code] of [
      [{ n: 3 }, 400, "egress_multiple_choices"],
      [{ web_search_options: {} }, 403, "egress_unmetered_tool"],
      [{ tools: [{ type: "browser_search" }] }, 403, "egress_unmetered_tool"]] as const) {
      const response = await send(egressRequest(box, COMPLETIONS, { body: bodyOf(box, extra) }));
      expect([response.status, await codeOf(response)]).toEqual([status, code]);
    }
    expect(await forwarded()).toEqual([]);
  });

  it("passes the provider's own refusal through, and settles it at no cost", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "busy" } }));
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("7");
    expect(await response.json()).toEqual({ error: { message: "rate limited" } });
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "complete", status: 429 }]);
    expect(await spends(box.owner)).toMatchObject([{ state: "failed", cost: 0 }]);
  });

  it("keeps the gateway's own header from the sandbox, and settles despite a bad one",
     async () => {
    const box = await sandbox();
    const resealing: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: async () => ({
        response: new Response("ok", { headers: { "x-ta-resealed": "{\"blob\":1}",
                                                  "x-upstream": "fake" } }),
        outcome: new Response("{\"state\":\"unknown\"}\n").body! }) } };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await send(egressRequest(box), resealing);
    expect(response.headers.get("x-ta-resealed")).toBeNull();
    expect(response.headers.get("x-upstream")).toBe("fake");
    expect(await response.text()).toBe("ok");
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled",
                                                           outcome: "complete", bytes: 2 }]);
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown", cost: null }]);
  });

  it("hands the sandbox slice two tokens, its network and its clients' environment once",
     async () => {
    const box = await sandbox(false);
    expect(verifyToken(env.ROUTING_MAC_KEY, "egress", box.opened.egress_token))
      .toMatchObject({ owner: box.owner });
    expect(verifyToken(env.ROUTING_MAC_KEY, "sync", box.opened.sync_token))
      .toMatchObject({ owner: box.owner });
    const { network } = box.opened;
    expect(network.network.rules["egress.tinyassets.io"]).toEqual([{ transform: { headers: {
      [SANDBOX_HEADER]: box.token, [GENERATION_HEADER]: "1" } } }]);
    expect(JSON.stringify(network)).not.toContain(box.opened.sync_token);
    expect(network.network.denyOut).toEqual(["0.0.0.0/0"]);
    expect(network.network.allowOut).toEqual(["*", "egress.tinyassets.io"]);
    expect(box.opened.env["GROQ_BASE_URL"])
      .toBe(`https://egress.tinyassets.io/c/${box.connection}/openai/v1`);
    expect(box.opened.env["GROQ_API_KEY"]).toMatch(
      new RegExp(`^ta_ph_${box.connection}_[0-9a-f]{32}$`));
    expect(JSON.stringify(box.opened.env)).not.toContain(box.key);
    // No directory entry is written for either token.
    expect((await env.DIRECTORY.list({ prefix: "l:" })).keys).toEqual([]);
    const stored = JSON.stringify(await sqlIn(box.owner, "SELECT * FROM file_space"));
    expect(stored).not.toContain(box.token.split(".")[2]);
    expect(stored).toContain(verifyToken(env.ROUTING_MAC_KEY, "egress", box.token)!.hash);
  });
});

describe("a refused call", () => {
  it.each([
    ["no token", "", 401, "egress_token_missing"],
    ["a malformed token", "not-a-token", 401, "egress_token_unknown"],
    ["a token naming no owner", `${"!".repeat(4)}.2000000000.${"A".repeat(43)}.${"A".repeat(43)}`, 401,
     "egress_token_unknown"],
    ["the old two-part form", `${btoa("user_x")}.${"A".repeat(43)}`, 401, "egress_token_unknown"],
    ["the round-1 three-part form", `${btoa("user_x")}.${"A".repeat(43)}.${"A".repeat(43)}`, 401,
     "egress_token_unknown"],
  ])("with %s is refused before any object, and forwards nothing", async (_what, token, status,
                                                                         code) => {
    const box = await sandbox();
    const spy = vi.spyOn(doorsEnv.OWNER, "getByName");
    const response = await send(egressRequest({ ...box, token }));
    expect([response.status, await codeOf(response)]).toEqual([status, code]);
    expect(spy).not.toHaveBeenCalled();
    expect(await forwarded()).toEqual([]);
  });

  it("with an empty token header is a missing token", async () => {
    const box = await sandbox();
    const response = await send(egressRequest({ ...box, token: "" }, COMPLETIONS, {
      headers: { [SANDBOX_HEADER]: "" } }));
    expect([response.status, await codeOf(response)]).toEqual([401, "egress_token_missing"]);
  });

  it("with a forged or altered token, or the sync token, never reaches the object", async () => {
    const box = await sandbox();
    const victim = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const otherKey = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const [routing, expires, secret, mac] = box.token.split(".") as
      [string, string, string, string];
    const flip = (part: string) => `${part.slice(0, 5)}${part[5] === "A" ? "B" : "A"}${
      part.slice(6)}`;
    const victimRouting = btoa(victim).replace(/=+$/, "");
    const later = String(Number(expires) + 86_400);
    const spy = vi.spyOn(doorsEnv.OWNER, "getByName");
    for (const token of [
      mintToken(otherKey, "egress", victim, Number(expires)).token,  // another key's MAC
      `${victimRouting}.${expires}.${secret}.${mac}`,     // a real MAC moved to another owner
      `${routing}.${later}.${secret}.${mac}`,             // the expiry pushed out
      `${routing}.${expires}.${flip(secret)}.${mac}`,     // the secret altered
      `${routing}.${expires}.${secret}.${flip(mac)}`,     // the MAC altered
      box.opened.sync_token,                              // the other kind's MAC
      mintToken(env.ROUTING_MAC_KEY, "sync", victim, Number(expires)).token]) {
      const response = await send(egressRequest({ ...box, token }));
      expect([response.status, await codeOf(response)]).toEqual([401, "egress_token_unknown"]);
    }
    expect(spy).not.toHaveBeenCalled();
    expect(await reached(victim)).toBe(false);
    expect(await forwarded()).toEqual([]);
  });

  it("with a token past its run's deadline is refused at the door, no object reached",
     async () => {
    const box = await sandbox();
    const spy = vi.spyOn(doorsEnv.OWNER, "getByName");
    const dead = mintToken(env.ROUTING_MAC_KEY, "egress", box.owner,
                           Math.floor(Date.now() / 1000) - 1).token;
    const response = await send(egressRequest({ ...box, token: dead }));
    expect([response.status, await codeOf(response)]).toEqual([401, "egress_token_expired"]);
    expect(spy).not.toHaveBeenCalled();
    expect(await reservations(box.owner)).toEqual([]);
  });

  it("with an unknown secret under a true MAC, or a lease that lapsed or closed, is refused " +
     "by the owner's object; a never-seen owner's gets no rows", async () => {
    const box = await sandbox();
    const deadline = Math.floor(Date.now() / 1000) + 3600;
    const unknown = mintToken(env.ROUTING_MAC_KEY, "egress", box.owner, deadline).token;
    const response = await send(egressRequest({ ...box, token: unknown }));
    expect([response.status, await codeOf(response)]).toEqual([401, "egress_lease_closed"]);
    const stranger = `user_${crypto.randomUUID().replace(/-/g, "")}`;
    const theirs = await send(egressRequest({ ...box,
      token: mintToken(env.ROUTING_MAC_KEY, "egress", stranger, deadline).token }));
    expect(theirs.status).toBe(403);
    expect(await reached(stranger)).toBe(false);
    expect(await reservations(stranger)).toEqual([]);
    await sqlIn(box.owner, "UPDATE file_space SET lease_expires = 1");
    const lapsed = await send(egressRequest(box));
    expect([lapsed.status, await codeOf(lapsed)]).toEqual([401, "egress_lease_closed"]);
    const other = await sandbox();
    await leases(other.owner, (sandbox) => sandbox.close(other.opened.lease));
    const closed = await send(egressRequest(other));
    expect([closed.status, await codeOf(closed)]).toEqual([401, "egress_lease_closed"]);
    expect(await forwarded()).toEqual([]);
  });

  it("names a connection that is not the owner's, an endpoint not sealed (a write of a read), " +
     "or no body length", async () => {
    const box = await sandbox();
    const other = await sandbox();
    for (const [request, status, code] of [
      [egressRequest({ ...box, connection: other.connection }), 403, "egress_connection_refused"],
      [egressRequest({ ...box, connection: `c${"0".repeat(24)}` }), 403,
       "egress_connection_refused"],
      [egressRequest(box, "/openai/v1/files"), 403, "egress_endpoint_refused"],
      [egressRequest(box, "/openai/v1/chat/completions%2F..%2Fadmin"), 403,
       "egress_endpoint_refused"],
      [egressRequest(box, "/openai/v1/models", { body: "{}" }), 403, "egress_endpoint_refused"],
      [egressRequest(box, COMPLETIONS, { headers: { "content-length": "" } }), 411,
       "egress_length_required"],
    ] as const) {
      const response = await call(request);
      expect([response.status, await codeOf(response)]).toEqual([status, code]);
    }
    expect(await forwarded()).toEqual([]);
  });

  it("past the owner's spend caps is refused before any forward, its receipt kept",
     async () => {
    const box = await sandbox(true, { turn_cost: 1 });
    const response = await send(egressRequest(box));
    expect([response.status, await codeOf(response)]).toEqual([402, "egress_spend_refused"]);
    expect(await spends(box.owner)).toMatchObject([{ state: "refused", rule: "run_cost" }]);
    expect(await forwarded()).toEqual([]);
  });

  it("of a suspended owner (billing refuses work) or an erased one is refused", async () => {
    const box = await sandbox();
    await box.bound.stub.admissionSet({ owner: box.owner, caller: PLATFORM_CALLER, origin: null },
                                      { reason: "billing", suspended: true });
    const suspended = await send(egressRequest(box));
    expect([suspended.status, await codeOf(suspended)]).toEqual([403, "owner_suspended"]);
    await sqlIn(box.owner, "UPDATE owner_admission SET state = 'erased', reasons = '[]'");
    const { evictDurableObject } = await import("cloudflare:test");
    await evictDurableObject(box.bound.stub);
    const erased = await send(egressRequest(box));
    expect([erased.status, await codeOf(erased)]).toEqual([403, "owner_erased"]);
    expect(await forwarded()).toEqual([]);
  });

  it("under a stale egress generation, or before its network is applied", async () => {
    const box = await sandbox(false);
    const unapplied = await send(egressRequest(box));
    expect([unapplied.status, await codeOf(unapplied)]).toEqual([409, "egress_network_stale"]);
    for (const generation of [null, "0", "2", "x"]) {
      const stale = await send(egressRequest(box, COMPLETIONS, { generation }));
      expect([stale.status, await codeOf(stale)]).toEqual([409, "egress_policy_stale"]);
    }
    expect(await forwarded()).toEqual([]);
  });

  it("is refused in maintenance before any object", async () => {
    const box = await sandbox();
    await setControl({ outside: "on", ordinary: "canary", canary: [], inbound: false });
    const response = await send(egressRequest(box));
    expect(response.status).toBe(503);
    expect(await reservations(box.owner)).toEqual([]);
  });
});

describe("a sandbox token", () => {
  it("reaches no other door: /api, /mcp and /app refuse it, and the egress origin serves " +
     "nothing else", async () => {
    const box = await sandbox();
    for (const token of [box.token, box.opened.sync_token]) {
      const headers = { [SANDBOX_HEADER]: token, [GENERATION_HEADER]: "1",
                        Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
      for (const path of ["/api/tools/read_graph", "/mcp", "/app/api/agent/status"]) {
        const response = await call(new Request(url(path), { method: "POST", headers,
                                                             body: "{}" }));
        expect(response.status).toBe(401);
      }
      for (const path of ["/api/tools/read_graph", "/mcp", "/app/api/agent/status", "/c",
                          `/c/${box.connection}`]) {
        const response = await call(new Request(`${EGRESS_ORIGIN}${path}`, {
          method: "POST", headers, body: "{}" }));
        expect(response.status).toBe(404);
      }
    }
    expect(await forwarded()).toEqual([]);
  });
});

describe("a policy write mid-stream", () => {
  it("ends the stream and settles it; the next call waits for the network only when the " +
     "write changed egress", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-policy" } }));
    expect(response.status).toBe(200);
    const reader = response.body!.getReader();
    // The gateway holds back a secret's length less one, in case a secret spans chunks.
    const first = (await reader.read()).value!.byteLength;
    expect(first).toBeGreaterThan(0);
    const generation = await writeEgress(box);
    expect(generation).toBe(2);
    await settledOf(box.owner);
    expect(await drained(reader)).toBe("egress_ended");
    expect(await reservations(box.owner)).toMatchObject([{
      state: "settled", ended: "policy", outcome: "ended", status: 200, bytes: first }]);
    // A cut stream's usage cannot be read: its whole reservation is kept.
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown", cost: null }]);
    await until(() => upstreamCancelled("policy"), "the upstream cancel");
    const old = await send(egressRequest(box));
    expect([old.status, await codeOf(old)]).toEqual([409, "egress_policy_stale"]);
    const waiting = await send(egressRequest(box, COMPLETIONS, { generation: "2" }));
    expect([waiting.status, await codeOf(waiting)]).toEqual([409, "egress_network_stale"]);
    await leases(box.owner, (sandbox) => sandbox.networkApplied({
      lease_id: box.opened.lease.lease_id, epoch: box.opened.lease.epoch, generation: 2 }));
    const applied = await send(egressRequest(box, COMPLETIONS, { generation: "2" }));
    expect(applied.status).toBe(200);
    expect(await applied.text()).toBe(EGRESS_CHUNKS.join(""));
  });
});

describe("settlement", () => {
  it("records a client's abort, the provider's request cancelled", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-abort" } }));
    const reader = response.body!.getReader();
    await reader.read();
    await reader.cancel();
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "aborted", ended: null,
                                                           status: 200 }]);
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown" }]);
    await until(() => upstreamCancelled("abort"), "the upstream cancel");
  });

  it("trusts the meter only for an answer that completed: a cut one keeps its whole charge",
     async () => {
    const box = await sandbox();
    const claiming: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: async () => ({
        response: new Response(new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("data: one\n\n"));
          },
        })),
        // A meter that claims a cost however the stream ended.
        outcome: new Response("{\"state\":\"succeeded\",\"tokens\":1,\"cost\":0.000001}\n")
          .body! }) } };
    const response = await send(egressRequest(box), claiming);
    const reader = response.body!.getReader();
    await reader.read();
    await reader.cancel();
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "aborted" }]);
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown", cost: null }]);
  });

  it("is registered with waitUntil at open, so a client that leaves before the answer " +
     "still settles", async () => {
    const box = await sandbox();
    let release: (answer: { response: Response; outcome: ReadableStream<Uint8Array> }) => void =
      () => undefined;
    const slow: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: () => new Promise((resolve) => { release = resolve; }) } };
    const waits: Promise<unknown>[] = [];
    const ctx = { waitUntil: (promise: Promise<unknown>) => { waits.push(promise); },
                  passThroughOnException: () => undefined, props: {} } as unknown as
      ExecutionContext;
    const pending = serveEgress(egressRequest(box), slow, ctx);
    await until(async () => (await reservations(box.owner)).length === 1, "the open");
    expect(waits.length).toBe(1);
    expect((await reservations(box.owner))[0]!["state"]).toBe("open");
    release({ response: new Response("ok"),
              outcome: new Response("{\"state\":\"failed\"}\n").body! });
    const response = await pending;
    expect(await response.text()).toBe("ok");
    await Promise.all(waits);
    expect((await reservations(box.owner))[0]!["outcome"]).toBe("complete");
  });

  it("goes through a fresh stub when the owner's object reset mid-stream", async () => {
    const box = await sandbox();
    const response = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-reset" } }));
    expect(response.status).toBe(200);
    const reader = response.body!.getReader();
    await reader.read();
    await runInDurableObject(box.bound.stub, (_instance: unknown, state: DurableObjectState) => {
      state.abort("reset");
    }).catch(() => undefined);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await drained(reader)).toBe("egress_ended");
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled",
                                                           outcome: "ended" }]);
  }, 30_000);

  it("closes the gateway's stop stream when the stream ends before any answer", async () => {
    const box = await sandbox();
    let stopped = false;
    const holding: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: (_owner, _connection, _version, _blob, _request, _metering, stop) => {
        stop.getReader().read().then(() => { stopped = true; }, () => { stopped = true; });
        return new Promise(() => undefined);  // the provider never answers
      } } };
    const pending = send(egressRequest(box), holding);
    await until(async () => (await reservations(box.owner)).length === 1, "the open");
    expect(stopped).toBe(false);
    await leases(box.owner, (sandbox) => sandbox.close(box.opened.lease));
    const response = await pending;
    expect([response.status, await codeOf(response)]).toEqual([503, "egress_ended"]);
    await until(async () => stopped, "the stop");
    await settledOf(box.owner);
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown" }]);
  });

  it("settles a call whose client left before any answer at once (its request's signal)",
     async () => {
    const box = await sandbox();
    let stopped = false;
    const holding: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: (_owner, _connection, _version, _blob, _request, _metering, stop) => {
        stop.getReader().read().then(() => { stopped = true; }, () => { stopped = true; });
        return new Promise(() => undefined);  // the provider never answers
      } } };
    const leaving = new AbortController();
    const request = new Request(egressRequest(box), { signal: leaving.signal });
    const pending = send(request, holding);
    await until(async () => (await reservations(box.owner)).length === 1, "the open");
    leaving.abort();
    const response = await pending;
    expect([response.status, await codeOf(response)]).toEqual([503, "egress_aborted"]);
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ outcome: "aborted" }]);
    expect(stopped).toBe(true);
  });

  it("records the gateway's failure", async () => {
    const box = await sandbox();
    const failing: DoorsEnv = { ...doorsEnv, EGRESS: {
      forward: () => Promise.reject(new Error("the gateway is gone")) } };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = await send(egressRequest(box), failing);
    expect([response.status, await codeOf(response)]).toEqual([502, "egress_gateway_failed"]);
    await settledOf(box.owner);
    expect(await reservations(box.owner)).toMatchObject([{ state: "settled", outcome: "failed",
                                                           status: null, bytes: 0 }]);
    expect(await spends(box.owner)).toMatchObject([{ state: "unknown" }]);
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
    const first = await send(egressRequest(box, COMPLETIONS, {
      headers: { "x-fake-egress": "slow-close" } }));
    const reader = first.body!.getReader();
    await reader.read();
    await leases(box.owner, (sandbox) => sandbox.close(box.opened.lease));
    await settledOf(box.owner);
    expect(await drained(reader)).toBe("egress_ended");
    expect(await reservations(box.owner)).toMatchObject([{ ended: "lease", outcome: "ended" }]);

    const again = await sandbox();
    const second = await send(egressRequest(again, COMPLETIONS, {
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
    const after = await send(egressRequest(again));
    expect([after.status, await codeOf(after)]).toEqual([403, "egress_connection_refused"]);
  });
});
