/**
 * MCP Events at the MCP door (slice W2; OpenAI's MCP Events page and the MCP Events design
 * sketch it cites): `events/list`, `events/subscribe` and `events/unsubscribe` on the same
 * authenticated endpoint as the tools, and `events` in the capabilities `initialize` and
 * `server/discover` answer. ChatGPT supports webhook delivery and callback verification
 * only, so that is all that is served: no poll, no stream, no `gap` or `terminated`.
 *
 * - `events/list`: each subscribing wake kind's event (`wake-kinds.json`'s `events`), with
 *   its arguments (`command_center_id`, and the `board` and `rooms` it follows) and its
 *   payload (the wake: IDs, cursors and the kinds of change), only to a caller whose grant
 *   reads somewhere (the object's `wakeEvents`).
 * - `events/subscribe`: checked before any byte (a principal, the event, its arguments, a
 *   webhook `https` callback, a `whsec_` secret of 24 to 64 bytes); then the callback is
 *   verified by the gateway's signed single-use challenge (a failure is the record's
 *   `verify_error_code` with a `data.reason` category, never the endpoint's body), sealed,
 *   and stored by the object under the id this door derives from the caller, URL, event and
 *   arguments (never taken from the request). `ttlMs` omitted is the record's default
 *   lifetime, a number at most that, `null` no expiry; the grant is `refreshBefore`. A
 *   repeat with the same identity refreshes it.
 * - `events/unsubscribe`: the same identity, from the name, arguments and URL, ended for the
 *   caller that holds it; `{}` either way.
 *
 * A run's MCP door serves none of this: a run is reached by mail, never by a doorbell.
 */
import { type McpServer, ProtocolError, type StandardSchemaV1 } from "@modelcontextprotocol/server";

import {
  callbackSecret, callbackUrl, eventKind, follows, subscriptionId, type WakeKind,
} from "../../owner/src/wake-kinds";

import type { Bound } from "./binder";
import type { DoorsEnv } from "./config";
import { verifyDoorbell } from "./connect";
import { refusal } from "./tools";

/** The design sketch's codes (`-32023` to `-32026`); the verification's is the record's. */
const NOT_FOUND = -32023;
const FORBIDDEN = -32024;
const EXHAUSTED = -32025;
const UNSUPPORTED = -32026;
const INVALID_PARAMS = -32602;

/** What a subscription may name: its command center, and the board and rooms it follows. */
function inputSchema() {
  return {
  type: "object",
  properties: {
    command_center_id: { type: "string", description: "The command center your agent is in." },
    board: { type: "boolean", description: "Also wake on a change to the owner's board." },
    rooms: { type: "array", items: { type: "string" },
             description: "Also wake on a change to these rooms (room:<app>:<room>)." },
  },
  required: ["command_center_id"],
  additionalProperties: false,
  };
}

/** What each delivered event's `data` is: the wake (`wake-kinds.ts` `WakeContent`). */
function payloadSchema() {
  return {
  type: "object",
  properties: {
    agent_id: { type: "string" },
    command_center_id: { type: "string" },
    changes: { type: "array", items: { type: "string", enum: ["mail", "rooms", "board"] } },
    cursor: { type: "integer", minimum: 0 },
    after: { type: "integer", minimum: 0 },
    read: { type: "array", items: { type: "string", enum: ["inbox_digest", "board_digest"] } },
  },
  required: ["agent_id", "command_center_id", "changes", "cursor", "after", "read"],
  additionalProperties: false,
  };
}

/** A params schema that passes the request's params through: each handler checks its own. */
const PARAMS: StandardSchemaV1<Record<string, unknown>> = Object.freeze({
  "~standard": Object.freeze({
    version: 1 as const, vendor: "tinyassets",
    validate: (value: unknown) => typeof value === "object" && value !== null
      && !Array.isArray(value) ? { value: value as Record<string, unknown> }
      : { issues: [{ message: "params is an object" }] },
  }),
});

function invalid(message: string): ProtocolError {
  return new ProtocolError(INVALID_PARAMS, message);
}

/** The caller's principal, or Forbidden: an outside client's own call, never a run's. */
function principal(bound: Bound): string {
  const binding = bound.binding as { origin?: { client?: unknown } | null; run?: unknown };
  if (binding.run !== undefined || typeof binding.origin?.client !== "string") {
    throw new ProtocolError(FORBIDDEN, "Forbidden", { reason: "an agent's own client only" });
  }
  return binding.origin.client;
}

/** The subscribing kind named, or NotFound. */
function kindOf(name: unknown): WakeKind & { push: NonNullable<WakeKind["push"]> } {
  const kind = typeof name === "string" ? eventKind(name) : null;
  if (kind === null || kind.push === null || kind.push.events === null) {
    throw new ProtocolError(NOT_FOUND, "NotFound", { kind: "event" });
  }
  return kind as WakeKind & { push: NonNullable<WakeKind["push"]> };
}

/** The arguments, checked against the event's input schema; their canonical object. */
function argumentsOf(value: unknown): Record<string, unknown> {
  let follow;
  try {
    follow = follows(value, ["command_center_id"]);
  } catch {
    throw invalid("arguments do not match the event's inputSchema");
  }
  const center = (value as Record<string, unknown>)["command_center_id"];
  if (typeof center !== "string" || !center || center.length > 512) {
    throw invalid("arguments do not match the event's inputSchema");
  }
  return { command_center_id: center, board: follow.board, rooms: follow.rooms };
}

/** The webhook delivery block's URL (and secret, when `withSecret`). */
function deliveryOf(kind: WakeKind & { push: NonNullable<WakeKind["push"]> }, value: unknown,
                    withSecret: boolean): { url: string; secret: string } {
  const delivery = value as Record<string, unknown> | null;
  if (typeof value !== "object" || delivery === null) {
    throw invalid("delivery is an object");
  }
  if (delivery["mode"] !== undefined && delivery["mode"] !== "webhook") {
    throw new ProtocolError(UNSUPPORTED, "Unsupported", { feature: "delivery.mode" });
  }
  try {
    return { url: callbackUrl(kind.push, delivery["url"]),
             secret: withSecret ? callbackSecret(kind.push, delivery["secret"]) : "" };
  } catch (error) {
    throw invalid(error instanceof Error ? error.message : "invalid delivery");
  }
}

/** An object refusal as the design sketch's error; anything else is a bug, rethrown. */
function refused(error: unknown): never {
  const known = refusal(error);
  if (known === null) {
    throw error;
  }
  if (known.message.startsWith("subscription_limit")) {
    throw new ProtocolError(EXHAUSTED, "ResourceExhausted", { limit: "subscriptions" });
  }
  if (known.status === 400) {
    throw invalid(known.message);
  }
  throw new ProtocolError(FORBIDDEN, "Forbidden", { reason: known.message });
}

/** The lifetime granted: `ttlMs` at most, the record's default when omitted, none for null. */
function grantedTtl(kind: WakeKind & { push: NonNullable<WakeKind["push"]> },
                    value: unknown): number | null {
  if (value === null) {
    return null;
  }
  if (value === undefined) {
    return kind.push.events!.default_ttl_ms;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) {
    throw invalid("ttlMs is a positive whole number of milliseconds, or null");
  }
  return value;
}

/** Serve MCP Events on `mcp` for the caller `bound`, its gateway reached through `env`. */
export function serveEvents(mcp: McpServer, bound: Bound, env: DoorsEnv): void {
  mcp.server.registerCapabilities({ events: {} } as never);

  mcp.server.setRequestHandler("events/list", { params: PARAMS }, async (params) => {
    if (params["cursor"] !== undefined && params["cursor"] !== null) {
      throw invalid("events/list has one page");
    }
    let listed;
    try {
      listed = await bound.stub.wakeEvents(bound.binding);
    } catch (error) {
      refused(error);
    }
    return {
      events: listed.events.map((event) => ({
        name: event.name, description: event.description, delivery: ["webhook"],
        inputSchema: inputSchema(), payloadSchema: payloadSchema(),
      })),
    } as never;
  });

  mcp.server.setRequestHandler("events/subscribe", { params: PARAMS }, async (params) => {
    const caller = principal(bound);
    const kind = kindOf(params["name"]);
    const args = argumentsOf(params["arguments"]);
    const { url, secret } = deliveryOf(kind, params["delivery"], true);
    const ttl = grantedTtl(kind, params["ttlMs"]);
    let listed;
    try {
      // Authorized before the callback is sent a byte: this caller's subscribing agent there.
      listed = await bound.stub.wakeEvents(bound.binding, args["command_center_id"] as string);
    } catch (error) {
      refused(error);
    }
    if (!listed.events.some((event) => event.name === kind.push.events!.name)) {
      throw new ProtocolError(FORBIDDEN, "Forbidden", { reason: "no read_graph" });
    }
    const id = subscriptionId(caller, url, kind.push.events!.name, args);
    const verified = await verifyDoorbell(env, bound.binding.owner, id,
                                          { record: kind.id, url, secret });
    if (!verified.verified) {
      throw new ProtocolError(kind.push.events!.verify_error_code, "CallbackEndpointError",
                              { reason: verified.reason });
    }
    const expiresAt = ttl === null ? null : (Date.now() + ttl) / 1000;
    try {
      await bound.stub.wakeSubscribe(bound.binding, {
        subscription_id: id, name: kind.push.events!.name,
        arguments: args as { command_center_id: string },
        blob: verified.sealed.blob, kid: verified.sealed.kid, version: 1,
        expires_at: expiresAt,
      });
    } catch (error) {
      refused(error);
    }
    return { id, refreshBefore: expiresAt === null ? null : new Date(expiresAt * 1000).toISOString(),
             cursor: null, truncated: false } as never;
  });

  mcp.server.setRequestHandler("events/unsubscribe", { params: PARAMS }, async (params) => {
    const caller = principal(bound);
    const kind = kindOf(params["name"]);
    const args = argumentsOf(params["arguments"]);
    const { url } = deliveryOf(kind, params["delivery"], false);
    try {
      await bound.stub.wakeUnsubscribe(bound.binding, {
        subscription_id: subscriptionId(caller, url, kind.push.events!.name, args) });
    } catch (error) {
      refused(error);
    }
    return {} as never;
  });
}
