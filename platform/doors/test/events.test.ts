/**
 * MCP Events at the MCP door (slice W2): `events` advertised, `events/list`,
 * `events/subscribe` with the gateway's signed callback challenge (a failure a category
 * under the record's code), the server-derived id, `ttlMs`, and `events/unsubscribe`; and
 * the owner surface's doorbell and wake-limits routes, whose token never comes back.
 */
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { runInDurableObject } from "cloudflare:test";
import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import type { OwnerDO } from "../../owner/src/owner-do";
import { subscriptionId } from "../../owner/src/wake-kinds";
import { mcpOrigin } from "../src/binder";
import {
  appPost, call, connect, legacyPost, OPEN, rpcBody, registerClient, setControl, signInApp,
  sqlIn, type Tokens, url,
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

/** A connected client with a ringing agent registered for it in `alpha`. */
async function ringingClient(alice = owner()) {
  const registered = await registerClient();
  const tokens = await connect(registered, { owner: alice });
  await rpc(tokens, "events/list", {});  // its grant recorded on its first call
  const origin = await mcpOrigin(registered.clientId, registered.redirectUri);
  await runInDurableObject(env.OWNER.getByName(alice), (_i: OwnerDO, state) => {
    state.storage.sql.exec(
      "INSERT INTO outside_agents (agent_id, command_center_id, outside_client, outside_family, " +
        "source, source_ref, display_name, delivery_method, recipe_id, revision, created_at) " +
        "VALUES ('agent_00000000000000aa', 'alpha', ?, ?, 'mcp', ?, 'Dot', 'mcp_event', NULL, " +
        "1, 0)", origin.client, origin.family, registered.clientId);
  });
  return { alice, tokens, origin };
}

function subscribe(tokens: Tokens, path: string, extra: Record<string, unknown> = {}) {
  return rpc(tokens, "events/subscribe", {
    name: EVENT, arguments: { command_center_id: "alpha", board: true },
    delivery: { mode: "webhook", url: `https://callback.test/${path}`, secret: WHSEC },
    cursor: null, ...extra,
  });
}

beforeEach(async () => {
  await setControl(OPEN);
});

describe("MCP Events at the MCP door", () => {
  it("advertises events in initialize and in server/discover", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const init = await rpc(tokens, "initialize", {
      protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "t", version: "1" } });
    expect((init["result"] as { capabilities: Record<string, unknown> }).capabilities)
      .toMatchObject({ tools: {}, events: {} });
    const client = new Client({ name: "t", version: "1" },
                              { versionNegotiation: { mode: { pin: "2026-07-28" } } });
    await client.connect(new StreamableHTTPClientTransport(new URL(url("/mcp")), {
      fetch: (input, init) => call(new Request(input, init)),
      requestInit: { headers: { Authorization: `Bearer ${tokens.access_token}` } } }));
    expect(client.getServerCapabilities()).toMatchObject({ events: {} });
    await client.close();
  });

  it("lists the one event, with its arguments and its payload", async () => {
    const tokens = await connect(await registerClient(), { owner: owner() });
    const listed = (await rpc(tokens, "events/list", {}))["result"] as
      { events: { name: string; delivery: string[]; inputSchema: { required: string[] };
                  payloadSchema: { required: string[] } }[] };
    expect(listed.events.map((event) => [event.name, event.delivery])).toEqual(
      [[EVENT, ["webhook"]]]);
    expect(listed.events[0]!.inputSchema.required).toEqual(["command_center_id"]);
    expect(listed.events[0]!.payloadSchema.required).toEqual(
      ["agent_id", "command_center_id", "changes", "cursor", "after", "read"]);
  });

  it("verifies the callback, then stores it under the id the server derives", async () => {
    const { alice, tokens, origin } = await ringingClient();
    const first = (await subscribe(tokens, "echo/dot"))["result"] as Record<string, unknown>;
    const id = subscriptionId(origin.client, "https://callback.test/echo/dot", EVENT,
                              { command_center_id: "alpha", board: true, rooms: [] });
    expect(first).toMatchObject({ id, cursor: null, truncated: false });
    const granted = Date.parse(first["refreshBefore"] as string) - Date.now();
    expect(granted).toBeGreaterThan(86_400_000 - 60_000);  // the record's default: a day
    // The same identity refreshes it; ttlMs null asks for no expiry, and is granted.
    const again = (await subscribe(tokens, "echo/dot", { ttlMs: null }))["result"];
    expect(again).toMatchObject({ id, refreshBefore: null });
    // A Work chat beside the dot is its own subscription.
    await subscribe(tokens, "echo/work-chat");
    const rows = await sqlIn(alice, "SELECT subscription_id, blob FROM wake_callbacks " +
                                    "ORDER BY subscription_id");
    expect(rows).toHaveLength(2);
    expect(JSON.stringify(rows)).not.toContain(WHSEC);
    expect(JSON.stringify(rows)).not.toContain("callback.test");
    // Unsubscribe by name, arguments and URL.
    const ended = await rpc(tokens, "events/unsubscribe", {
      name: EVENT, arguments: { command_center_id: "alpha", board: true },
      delivery: { mode: "webhook", url: "https://callback.test/echo/dot" } });
    expect(ended["result"]).toMatchObject({});
    expect(await sqlIn(alice, "SELECT 1 FROM wake_callbacks")).toHaveLength(1);
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
    expect(await sqlIn(alice, "SELECT 1 FROM wake_callbacks")).toEqual([]);
    // A client with no ringing agent there may verify nothing into place.
    const stranger = await connect(await registerClient(), { owner: alice });
    expect(await code(await subscribe(stranger, "echo/x")))
      .toMatchObject({ code: -32024 });
  });
});

describe("the owner surface's doorbell", () => {
  it("seals the pasted token through the gateway and never answers it back", async () => {
    const who = owner();
    const app = await signInApp({ owner: who });
    const { origin } = await ringingClient(who);
    await runInDurableObject(env.OWNER.getByName(who), (_i: OwnerDO, state) => {
      state.storage.sql.exec("UPDATE outside_agents SET delivery_method = 'routine_fire'");
    });
    void origin;
    const token = "sk-ant-oat01-" + "z".repeat(40);
    const fire = "https://api.anthropic.com/v1/claude_code/routines/trig_01ABC/fire";
    const answered = await appPost("agent/doorbell", app.access_token, app.session, {
      agent_id: "agent_00000000000000aa", kind: "claude_routine_fire", url: fire, secret: token,
      board: true });
    const text = await answered.text();
    expect(answered.status).toBe(200);
    expect(text).not.toContain(token);
    const rows = await sqlIn(who, "SELECT principal, kind, board FROM wake_callbacks");
    expect(rows).toEqual([{ principal: "owner", kind: "claude_routine_fire", board: 1 }]);
    expect(JSON.stringify(await sqlIn(who, "SELECT * FROM wake_callbacks"))).not.toContain(token);
    const refused = await appPost("agent/doorbell", app.access_token, app.session, {
      agent_id: "agent_00000000000000aa", kind: "claude_routine_fire",
      url: "https://evil.example/v1/claude_code/routines/t/fire", secret: token });
    expect(refused.status).toBe(400);
    const { subscription_id } = JSON.parse(text) as { subscription_id: string };
    const ended = await appPost("agent/doorbell/end", app.access_token, app.session,
                                { subscription_id });
    expect(await ended.json()).toEqual({ unsubscribed: true });
    // The owner's own wake settings, off by default, set here.
    const policy = await env.OWNER.getByName(who).policyRead({ owner: who, caller: who,
                                                               origin: null } as never);
    const limits = await appPost("agent/wake-limits", app.access_token, app.session, {
      agent_id: "agent_00000000000000aa", max_wakes_per_hour: 2, batch_window_s: 0,
      expected_generation: policy.generation });
    expect((await limits.json<{ agent: { max_wakes_per_hour: number } }>()).agent
      .max_wakes_per_hour).toBe(2);
  });
});
