/**
 * MCP Events at the MCP door (slice W2): `events` advertised, `events/list`,
 * `events/subscribe` with the gateway's signed callback challenge (a failure a category
 * under the record's code), the server-derived id, `ttlMs`, and `events/unsubscribe`; and
 * the owner surface's doorbell and wake-limits routes, whose token never comes back.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { SUBSCRIPTIONS_PER_AGENT } from "../../contract/src/limits";
import { subscriptionId } from "../../contract/src/wake-kinds";
import { mcpOrigin } from "../src/binder";
import {
  appPost, call, connect, crossed, legacyPost, OPEN, ownDoor, registerAgent, registerClient,
  rpcBody, serveFirstPartyDocument, setControl, signInApp, type Tokens, url,
} from "./support";

const KEY = new Uint8Array(32).map((_, n) => n + 1);
const WHSEC = "whsec_" + btoa(String.fromCharCode(...KEY));
const EVENT = "tinyassets.changed";

function owner(): string {
  return `user_${crypto.randomUUID().replace(/-/g, "")}`;
}

/** One stateless JSON-RPC request, as a 2025-11-25 client sends it. */
async function rpc(tokens: Tokens, method: string, params: unknown) {
  return rpcBody(await legacyPost(tokens.access_token, { jsonrpc: "2.0", id: 1, method, params }));
}

/**
 * A connected client with a ringing agent registered for it in `alpha`, as a client registers
 * one: the client files its card (`agent_register`) and the owner applies it in its session.
 */
async function ringingClient(alice = owner(), delivery_method = "mcp_event") {
  const registered = await registerClient();
  const tokens = await connect(registered, { owner: alice });
  const agent = await registerAgent(tokens, alice, delivery_method);
  const origin = await mcpOrigin(registered.clientId, registered.redirectUri);
  return { alice, tokens, origin, agent };
}

function subscribe(tokens: Tokens, path: string, extra: Record<string, unknown> = {}) {
  return rpc(tokens, "events/subscribe", {
    name: EVENT, arguments: { command_center_id: "alpha", board: true },
    delivery: { mode: "webhook", url: `https://callback.test/${path}`, secret: WHSEC },
    cursor: null, ...extra,
  });
}

let restore: () => void = () => undefined;

beforeEach(async () => {
  await setControl(OPEN);
  restore();
  restore = serveFirstPartyDocument();
});

describe("MCP Events at the MCP door", () => {
  it("advertises events in initialize and in server/discover", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const init = await rpc(tokens, "initialize", {
      protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "t", version: "1" } });
    expect((init["result"] as { capabilities: Record<string, unknown> }).capabilities)
      .toMatchObject({ tools: {}, events: {} });
    // The client's own schema drops a capability it does not know, so the wire is read.
    const discovered: unknown[] = [];
    const client = new Client({ name: "t", version: "1" },
                              { versionNegotiation: { mode: { pin: "2026-07-28" } } });
    await client.connect(new StreamableHTTPClientTransport(new URL(url("/mcp")), {
      fetch: async (input, init) => {
        const request = new Request(input, init);
        const asked = request.method === "POST" ? await request.clone().text() : "";
        const answer = await call(request);
        if (asked.includes('"server/discover"')) {
          const text = await answer.clone().text();
          const data = text.trimStart().startsWith("{") ? text
            : text.split("\n").find((line) => line.startsWith("data: "))!.slice(6);
          discovered.push(JSON.parse(data));
        }
        return answer;
      },
      requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } } }));
    await client.close();
    expect(discovered).toHaveLength(1);
    expect((discovered[0] as { result: { capabilities: unknown } }).result.capabilities)
      .toMatchObject({ tools: {}, events: {} });
  });

  it("lists the one event, with its arguments and its payload", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const listed = (await rpc(tokens, "events/list", {}))["result"] as
      { events: { name: string; delivery: string[]; inputSchema: { required: string[] };
                  payloadSchema: { required: string[] } }[] };
    expect(listed.events.map((event) => [event.name, event.delivery])).toEqual(
      [[EVENT, ["webhook"]]]);
    expect(listed.events[0]!.inputSchema.required).toEqual(["command_center_id"]);
    expect(listed.events[0]!.payloadSchema).toMatchObject({
      required: ["agent_id", "command_center_id", "changes", "cursor", "after", "read"],
      properties: {
        changes: { items: { enum: ["mail", "rooms", "board"] } },
        read: { items: { enum: ["inbox_digest", "board_digest"] } },
      },
    });
  });

  it("verifies the callback, then stores it under the id the server derives", async () => {
    const { alice, tokens, origin } = await ringingClient();
    const before = Date.now();
    const first = (await subscribe(tokens, "echo/dot"))["result"] as Record<string, unknown>;
    const after = Date.now();
    const id = subscriptionId(origin.client, "https://callback.test/echo/dot", EVENT,
                              { command_center_id: "alpha", board: true, rooms: [] });
    expect(first).toMatchObject({ id, cursor: null, truncated: false });
    // The record's default: a day on from the server's clock, read between the two reads.
    const refresh = Date.parse(first["refreshBefore"] as string);
    expect(refresh).toBeGreaterThanOrEqual(before + 86_400_000 - 1_000);
    expect(refresh).toBeLessThanOrEqual(after + 86_400_000 + 1_000);
    // The same identity refreshes it; ttlMs null asks for no expiry, and is granted.
    const again = (await subscribe(tokens, "echo/dot", { ttlMs: null }))["result"];
    expect(again).toMatchObject({ id, refreshBefore: null });
    // A Work chat beside the dot is its own subscription.
    await subscribe(tokens, "echo/work-chat");
    const work = subscriptionId(origin.client, "https://callback.test/echo/work-chat", EVENT,
                                { command_center_id: "alpha", board: true, rooms: [] });
    // The object is handed each under its derived id, and only the sealed callback: never
    // the secret, never the URL.
    const subscribed = (await crossed(alice, "wakeSubscribe")).map((call) => call.request);
    expect(subscribed.map((request) => request["subscription_id"])).toEqual([id, id, work]);
    expect(JSON.stringify(subscribed)).not.toContain(WHSEC);
    expect(JSON.stringify(subscribed)).not.toContain("callback.test");
    // Unsubscribe by name, arguments and URL: the dot's id, derived again.
    const ended = await rpc(tokens, "events/unsubscribe", {
      name: EVENT, arguments: { command_center_id: "alpha", board: true },
      delivery: { mode: "webhook", url: "https://callback.test/echo/dot" } });
    expect(ended["result"]).toMatchObject({});
    expect((await crossed(alice, "wakeUnsubscribe")).map((call) => call.request)).toEqual([{ subscription_id: id }]);
  });

  it("answers each refusal with the design sketch's code, and a callback's as a category",
     async () => {
    const { alice, tokens } = await ringingClient();
    const code = async (result: Record<string, unknown>) =>
      (result["error"] as { code: number; data?: unknown }) ?? result;
    expect(await code(await subscribe(tokens, "wrong/x")))
      .toMatchObject({ code: -32015, data: { reason: "challenge_failed" } });
    expect(await code(await subscribe(tokens, "missing/x")))
      .toMatchObject({ code: -32015, data: { reason: "http_4xx" } });
    expect(await code(await subscribe(tokens, "echo/x", { name: "nope" })))
      .toMatchObject({ code: -32023, data: { kind: "event" } });
    expect(await code(await subscribe(tokens, "echo/x", {
      delivery: { mode: "poll", url: "https://callback.test/echo/x", secret: WHSEC } })))
      .toMatchObject({ code: -32026 });
    for (const delivery of [{ url: "https://callback.test/echo/x", secret: "whsec_short" },
                            { url: "http://callback.test/echo/x", secret: WHSEC },
                            { url: "https://localhost/echo/x", secret: WHSEC },
                            { url: "https://mcp.tinyassets.io/echo/x", secret: WHSEC }]) {
      expect(await code(await subscribe(tokens, "echo/x", { delivery })))
        .toMatchObject({ code: -32602 });
    }
    expect(await code(await subscribe(tokens, "echo/x", {
      arguments: { command_center_id: "alpha", extra: 1 } }))).toMatchObject({ code: -32602 });
    // A lifetime no date can write is refused before anything is stored.
    expect(await code(await subscribe(tokens, "echo/x", { ttlMs: 9_000_000_000_000_000 })))
      .toMatchObject({ code: -32602 });
    expect((await crossed(alice, "wakeSubscribe")).map((call) => call.request)).toEqual([]);
    // A client with no ringing agent there may verify nothing into place.
    const stranger = await connect(await registerClient(), { owner: alice });
    expect(await code(await subscribe(stranger, "echo/x")))
      .toMatchObject({ code: -32024 });
  });
});

describe("an agent's own subscriptions", () => {
  it("are held to the agent's own bound, answered ResourceExhausted with its max", async () => {
    const { alice, tokens } = await ringingClient();
    for (let n = 0; n < SUBSCRIPTIONS_PER_AGENT; n++) {
      expect((await subscribe(tokens, `echo/chat-${n}`))["result"]).toBeDefined();
    }
    expect((await subscribe(tokens, "echo/one-more"))["error"]).toMatchObject(
      { code: -32025, data: { limit: "subscriptions", max: SUBSCRIPTIONS_PER_AGENT } });
    // A refresh of one it holds is no new place.
    expect((await subscribe(tokens, "echo/chat-0"))["result"]).toBeDefined();
  });
});

describe("the owner surface's doorbell", () => {
  it("seals the pasted token through the gateway and never answers it back", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const { agent } = await ringingClient(who, "routine_fire");
    const token = "sk-ant-oat01-" + "z".repeat(40);
    const fire = "https://api.anthropic.com/v1/claude_code/routines/trig_01ABC/fire";
    await env.OWNER_GATE.seen(who);
    const answered = await appPost("agent/doorbell", app.access_token, app.session, {
      agent_id: agent, kind: "claude_routine_fire", url: fire, secret: token, board: true });
    const text = await answered.text();
    expect(answered.status).toBe(200);
    expect(text).not.toContain(token);
    // The object is handed the doorbell sealed: its kind and board, never the token.
    const rung = (await crossed(who, "agentDoorbell")).map((call) => call.request);
    expect(rung).toMatchObject([{ agent_id: agent, kind: "claude_routine_fire", board: true }]);
    expect(JSON.stringify(rung)).not.toContain(token);
    const refused = await appPost("agent/doorbell", app.access_token, app.session, {
      agent_id: agent, kind: "claude_routine_fire",
      url: "https://evil.example/v1/claude_code/routines/t/fire", secret: token });
    expect(refused.status).toBe(400);
    const { subscription_id } = JSON.parse(text) as { subscription_id: string };
    const ended = await appPost("agent/doorbell/end", app.access_token, app.session,
                                { subscription_id });
    expect(await ended.json()).toEqual({ unsubscribed: true });
    // The owner's own wake settings, off by default, set here.
    const policy = await ownDoor(who, "policyRead");
    const limits = await appPost("agent/wake-limits", app.access_token, app.session, {
      agent_id: agent, max_wakes_per_hour: 2, batch_window_s: 0,
      expected_generation: policy.generation });
    expect((await limits.json<{ agent: { max_wakes_per_hour: number } }>()).agent
      .max_wakes_per_hour).toBe(2);
  });
});
