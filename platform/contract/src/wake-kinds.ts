/**
 * Building a wake from its record (slice W, tasks 7): what a subscription follows, its
 * server-derived id, and each wake's headers and body (`doorbellRequest`). The records and
 * the callback checks are `wake-records.ts`.
 */
import { sha256Hex } from "./digest.ts";
import { Refusal } from "./refusal.ts";
import { codePoints, isPrintable } from "./text.ts";
import { ROOM_COLLECTION_PREFIX } from "./ids.ts";
import { ARGUMENTS_CHARS } from "./limits.ts";
import { type RingRequest, ringRequest } from "./gateway.ts";
import type { WakeKind } from "./wake-records.ts";

/** The changes each digest reads, in the order advertised and sent on the wire. */
const DIGEST_CHANGES = {
  inbox_digest: ["mail", "rooms"],
  board_digest: ["board"],
} as const;
export type Change = (typeof DIGEST_CHANGES)[keyof typeof DIGEST_CHANGES][number];
export const CHANGES = Object.freeze(Object.values(DIGEST_CHANGES).flat());

export function wakeReads(changes: readonly Change[]): string[] {
  return Object.entries(DIGEST_CHANGES)
    .filter(([, kinds]) => kinds.some((kind) => changes.includes(kind)))
    .map(([digest]) => digest);
}

/** What a subscription follows besides the mail addressed to its agent. */
export interface Follows {
  readonly board: boolean;
  /** Room collections (`room:<app>:<room>`), each exactly. */
  readonly rooms: readonly string[];
}

/** What a wake says: identifiers, cursors and the kinds of change. Never content. */
export interface WakeContent {
  readonly agent_id: string;
  readonly command_center_id: string;
  readonly changes: readonly Change[];
  /** The agent's own acknowledged mailbox cursor: its unread mail is past it. */
  readonly cursor: number;
  /** The owner-wide change cursor to read rooms and the board after. */
  readonly after: number;
  /** The digests that answer just this change. */
  readonly read: readonly string[];
}

/** JSON with every object's keys sorted, so equal values compare equal. */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/**
 * What a subscription follows, from its arguments: `board` (a boolean, false when left
 * out) and `rooms` (room collections, each exactly; none when left out), checked, besides
 * the keys `allowed` names (an MCP Events subscription's `command_center_id`). The canonical
 * arguments are at most `ARGUMENTS_CHARS`.
 */
export function follows(args: unknown, allowed: readonly string[] = []): Follows {
  if (typeof args !== "object" || args === null || Array.isArray(args)
      || !Object.keys(args).every((key) => ["board", "rooms", ...allowed].includes(key))
      || canonicalJson(args).length > ARGUMENTS_CHARS) {
    throw new Refusal("invalid", "invalid subscription arguments");
  }
  const { board = false, rooms = [] } = args as { board?: unknown; rooms?: unknown };
  if (typeof board !== "boolean" || !Array.isArray(rooms)
      || !rooms.every((room) => typeof room === "string" && room.startsWith(ROOM_COLLECTION_PREFIX)
                      && codePoints(room) <= 256 && isPrintable(room))
      || new Set(rooms).size !== rooms.length) {
    throw new Refusal("invalid",
                      "board is true or false, and rooms are room collections, each once");
  }
  return Object.freeze({ board, rooms: Object.freeze([...rooms as string[]].sort()) });
}

/**
 * A subscription's id, derived by the server, deterministic over who subscribed (`owner`,
 * or an outside client's id), the callback URL, the event or kind, and the canonical
 * arguments; never taken from a request (the MCP Events design sketch).
 */
export function subscriptionId(principal: string, url: string, name: string,
                               args: unknown): string {
  return "sub_" + sha256Hex(canonicalJson([principal, url, name, args])).slice(0, 32);
}

/** The wake's one line, for a kind whose body is text: the change and where to read it. */
export function wakeText(wake: WakeContent): string {
  return `TinyAssets: new ${wake.changes.join(", ")} for agent ${wake.agent_id} in ` +
    `${wake.command_center_id}. Read just the change: ` +
    `${wake.read.map((digest) => `${digest} with after=${wake.after}`).join("; ")}.`;
}

function filled(value: unknown, values: Readonly<Record<string, unknown>>): unknown {
  if (typeof value === "string") {
    return Object.hasOwn(values, value) ? values[value] : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => filled(item, values));
  }
  if (typeof value === "object" && value !== null) {
    return Object.fromEntries(Object.entries(value).map(([k, item]) => [k, filled(item, values)]));
  }
  return value;
}

/**
 * Build one wake's request from its record (`RingRequest`: its headers, never the auth,
 * which the gateway adds from the sealed secret, and its body bytes). `eventId` is the
 * wake's own id, kept across a retry; `at` is the change's own time, in seconds. Refused
 * (`invalid`) before any byte when the kind pushes nothing or the body is past its record's
 * cited size; held to the gateway's own check of a ring (`ringRequest`, `invalid_request`), so
 * the object never builds what the gateway refuses.
 */
export function doorbellRequest(kind: WakeKind, subscriptionIdValue: string, wake: WakeContent,
                                eventId: string, at: number): RingRequest {
  const p = kind.push;
  if (p === null) {
    throw new Refusal("invalid", "this kind has no push");
  }
  const values: Record<string, unknown> = {
    "{wake}": wake, "{text}": wakeText(wake), "{event_id}": eventId,
    "{timestamp}": new Date(Math.floor(at * 1000)).toISOString(),
    "{subscription_id}": subscriptionIdValue,
  };
  const body = JSON.stringify(filled(p.body, values));
  if (p.max_body_bytes !== null && new TextEncoder().encode(body).byteLength > p.max_body_bytes) {
    throw new Refusal("invalid", "the wake is past its kind's body size");
  }
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(p.headers)) {
    headers[name] = String(filled(value, values));
  }
  return ringRequest({ headers, body, event_id: eventId });
}
