/**
 * A run's live output, relayed to the owner's app sockets (ADR-034 decision 10;
 * `engines-replan.md` §2.2). Salvaged from A6 (`platform-a6`'s `sockets.ts`): the socket
 * ticket, the binder's upgrade path, the ticket's grant and expiry binding and sign-out
 * ending sockets; not its pi `watch()` or live tee, which went with pi.
 *
 * The transport is the Agents SDK's `WebSockets` capability (`agents/websockets`), on the
 * object's own `new Lifecycle(this)` beside the schedule, still without Lifecycle's
 * `installHandlers`: the object forwards `fetch` and the three `webSocket*` reserved
 * methods to Lifecycle's methods of the same names, and a Workers RPC cannot carry a socket,
 * so the upgrade is the stub's `fetch`, the object's one non-RPC entry. The capability
 * speaks no Agents protocol here (`protocol: false`): every frame is ours. This class is a
 * Lifecycle capability of its own too, for one job: closing sockets as their tokens expire.
 *
 * **The credential** is a socket ticket: `POST /app/api/agent/socket-ticket` behind every
 * first-party token check, which the object mints for one of the owner's conversations,
 * bound to the first-party grant the token was issued under and to that token's expiry,
 * single-use, good for `SOCKET_TICKET_S`, keeping only its SHA-256 (`tickets.ts`). The
 * ticket is a routing-MAC token (`socket` kind), so the doors refuse a forged one before any
 * object; the object consumes it in the attach's unit, refusing it alike
 * (`TICKET_REFUSED`) reused, expired, for another conversation, or for a signed-out grant.
 *
 * **The binder's shape** (`socket-shape.ts`): a `GET` of exactly `SOCKET_URL`, no query,
 * exactly the four headers; anything else `400` (`NOT_BINDER_SHAPE`) before Lifecycle.
 *
 * **Frames.** Server to client, each a run's frame as the object read it (`run-events.ts`):
 * `{v, run, seq, at, kind, event}`, for every run in the socket's conversation. On attach,
 * the conversation's live runs' tails first, then each frame as it comes. Client to server:
 * `{v, type: "ping"}` alone, answered `{v, type: "pong"}`; each is checked against the
 * socket's authority first, and anything else closes it `frame_refused`.
 *
 * **The tail.** Frames are never committed: the object keeps, in memory only, a tail of each
 * live run's frames for late joiners, at most `TAIL_BYTES` in all (the oldest frame of the
 * longest tail goes first, so every run keeps its newest), and drops a run's at its settle,
 * when its result is the receipt's. A frame longer than `TAIL_FRAME_MAX` is relayed and
 * never kept. The memory guard counts both.
 *
 * **Lifetime** is the credential's, each close a fixed code (`CLOSE`): the token's expiry,
 * the sign-out of its grant (the doors tell the object before they revoke the grant), every
 * session's close, an admission that refuses reads, an abuse suspension, the erasure.
 *
 * `OWNER_SOCKETS` is a technical ceiling: the attachments and states the isolate holds per
 * socket, which the memory guard counts at it.
 */
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";
import { type WebSocketHandlers, WebSockets } from "agents/websockets";

import { Invalid, Refused } from "./errors";
import type { Frame } from "./run-events";
import {
  BINDING_HEADER, CONVERSATION_HEADER, SHAPE_HEADERS, SOCKET_FAILED, SOCKET_URL,
  SOCKET_VERSION, TICKET_HEADER,
} from "./socket-shape";

type Connection = Parameters<NonNullable<WebSocketHandlers["onConnect"]>>[0];

export const RELAY = "ta-relay";
export const EXPIRY_JOB = "socket-expiry";
const EXPIRE_FN = "expire";

/** App sockets an owner holds open at once: a technical ceiling of the isolate's memory. */
export const OWNER_SOCKETS = 1024;
/** The frames' tail kept for late joiners, in UTF-16 code units, every run's together. */
export const TAIL_BYTES = 4_194_304;
/** A frame longer than this is relayed but never kept in the tail. */
export const TAIL_FRAME_MAX = 262_144;
/** The longest frame a client may send, in UTF-16 code units. */
export const CLIENT_FRAME_MAX = 1024;
/** Told sign-outs held at once; past it the oldest goes (its tickets are gone already). */
export const SIGNED_OUT_KEPT = 64;
/** How long a told sign-out is held, for the attaches it raced, in ms. */
const ENDED_HELD_MS = 60_000;

export const NOT_BINDER_SHAPE = "only the binder's upgrade reaches the object's socket door";
export const SOCKETS_FULL = "sockets_full: the owner has as many app sockets open as it may";
export const OWNERS_SOCKET = "an app socket is the owner's own";

export type CloseKind = "expired" | "signedOut" | "suspended" | "erased" | "frame" | "lost";
export const CLOSE: Readonly<Record<CloseKind, readonly [number, string]>> = Object.freeze({
  expired: Object.freeze([4402, "token_expired"] as const),
  suspended: Object.freeze([4403, "owner_suspended"] as const),
  erased: Object.freeze([4410, "owner_erased"] as const),
  signedOut: Object.freeze([4440, "signed_out"] as const),
  frame: Object.freeze([4400, "frame_refused"] as const),
  lost: Object.freeze([4500, "socket_lost"] as const),
});

/** A socket's attachment: whose, on what conversation, under which grant, until when. */
export interface SocketState {
  readonly v: number;
  readonly owner: string;
  readonly conversation: string;
  readonly grant: string;
  readonly expires_at: number;
  /** The binding each frame is checked by again, as the binder bound it. */
  readonly binding: Readonly<Record<string, unknown>>;
}

export interface RelayParts {
  /** In a unit: the attach's account, the owner's conversation, the ticket consumed. */
  admit(binding: unknown, conversation: string, ticketHash: string): Promise<SocketState>;
  /** In a unit: why the attachment no longer holds, as its close; null if it does. */
  check(state: SocketState): Promise<CloseKind | null>;
  /** The conversation's live runs, oldest first, whose tails a joiner is sent. */
  live(conversation: string): Promise<readonly string[]>;
  /** The object's clock, in whole seconds. */
  now(): number;
}

function tag(conversation: string): string {
  return `c:${conversation}`;
}

/** The binder's upgrade's binding, conversation and ticket hash; anything else refused. */
export function socketShape(request: Request):
    { binding: unknown; conversation: string; ticket: string } {
  const upgrade = request.headers.get("upgrade");
  if (request.method !== "GET" || request.url !== SOCKET_URL
      || upgrade === null || upgrade.toLowerCase() !== "websocket"
      || [...request.headers.keys()].sort().join() !== SHAPE_HEADERS.join()) {
    throw new Invalid(NOT_BINDER_SHAPE);
  }
  let binding: unknown;
  try {
    binding = JSON.parse(request.headers.get(BINDING_HEADER)!);
  } catch {
    throw new Invalid(NOT_BINDER_SHAPE);
  }
  return { binding, conversation: request.headers.get(CONVERSATION_HEADER)!,
           ticket: request.headers.get(TICKET_HEADER)! };
}

function refusedUpgrade(error: unknown): Response {
  if (error instanceof Refused || error instanceof Invalid) {
    return Response.json({ error: error.message },
                         { status: error instanceof Refused ? 403 : 400 });
  }
  console.error("an app socket's upgrade failed");  // no ids, no error text
  return Response.json({ error: SOCKET_FAILED }, { status: 500 });
}

interface Tail {
  readonly conversation: string;
  readonly frames: string[];
  bytes: number;
}

export class Relay extends LifecycleCapability {
  /** The Lifecycle capability the object installs beside this one: the transport. */
  readonly capability: WebSockets;
  readonly #parts: RelayParts;
  /** Each live run's tail, on the instance only. */
  readonly #tails = new Map<string, Tail>();
  #tailBytes = 0;
  /** The attachments of upgrades being accepted, by their request. */
  readonly #accepting = new WeakMap<Request, SocketState>();
  #pending = 0;
  /** Grants whose sign-out was told, and when. */
  readonly #signedOut = new Map<string, number>();

  constructor(parts: RelayParts) {
    super(RELAY);
    this.#parts = parts;
    this.capability = new WebSockets({
      protocol: false,
      getConnectionTags: (_connection, { request }) => {
        const state = this.#accepting.get(request);
        return state === undefined ? [] : [tag(state.conversation)];
      },
      handlers: {
        onConnect: (connection, { request }) => this.#connected(connection, request),
        onMessage: (connection, message) => this.#message(connection, message),
      },
    });
  }

  // -- the expiry job ------------------------------------------------------------------

  async onJob({ job }: LifecycleJobContext): Promise<LifecycleJobOutcome> {
    if (job.id !== EXPIRY_JOB) {
      return undefined;
    }
    const now = this.#parts.now();
    for (const connection of this.#select((state) => state.expires_at <= now)) {
      this.#close(connection, "expired");
    }
    const next = this.#nextExpiry();
    return next === null ? undefined : { rescheduleAt: next * 1000 };
  }

  #nextExpiry(): number | null {
    const times = this.#select(() => true).map((connection) =>
      (connection.state as SocketState).expires_at);
    return times.length === 0 ? null : Math.min(...times);
  }

  // -- the door ------------------------------------------------------------------------

  /**
   * The object's `fetch`: the binder's upgrade attached (its account, conversation and
   * ticket), then handed to Lifecycle (`accept`); anything else refused with its fixed code.
   */
  async serve(request: Request, accept: (upgrade: Request) => Promise<Response>):
      Promise<Response> {
    let state: SocketState;
    try {
      const shape = socketShape(request);
      state = await this.#parts.admit(shape.binding, shape.conversation, shape.ticket);
      if ([...this.capability.getConnections()].length + this.#pending >= OWNER_SOCKETS) {
        throw new Refused(SOCKETS_FULL);
      }
    } catch (error) {
      return refusedUpgrade(error);
    }
    this.#pending++;
    this.#accepting.set(request, state);
    try {
      return await accept(request);
    } catch (error) {
      return refusedUpgrade(error);
    } finally {
      this.#accepting.delete(request);
      this.#pending--;
    }
  }

  /** An accepted socket: its attachment kept, the expiry job set, the live tails sent. */
  async #connected(connection: Connection, request: Request): Promise<void> {
    const state = this.#accepting.get(request);
    try {
      if (state === undefined) {
        throw new Error("an app socket is accepted only as the object's door attached it");
      }
      connection.setState(state);
      if (this.#signedOut.has(state.grant)) {
        this.#close(connection, "signedOut");
        return;
      }
      const next = this.#nextExpiry();
      if (next !== null) {
        await this.lifecycle.jobs.push({ id: EXPIRY_JOB, fn: EXPIRE_FN, time: next * 1000 });
      }
      for (const run of await this.#parts.live(state.conversation)) {
        for (const frame of this.#tails.get(run)?.frames ?? []) {
          this.#send(connection, frame);
        }
      }
    } catch {
      console.error("an app socket could not be attached");  // no ids, no error text
      this.#close(connection, "lost");
    }
  }

  /** A client frame: its authority checked, then a ping answered; anything else closes it. */
  async #message(connection: Connection, message: string | ArrayBuffer | ArrayBufferView):
      Promise<void> {
    let frame: { v?: unknown; type?: unknown };
    try {
      if (typeof message !== "string" || message.length > CLIENT_FRAME_MAX) {
        throw new Error("a client frame is short JSON text");
      }
      const parsed = JSON.parse(message) as unknown;
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("a client frame is an object");
      }
      frame = parsed;
    } catch {
      this.#close(connection, "frame");
      return;
    }
    const state = connection.state as SocketState | null;
    if (frame.v !== SOCKET_VERSION || state === null || typeof state !== "object") {
      this.#close(connection, "frame");
      return;
    }
    const refused = this.#signedOut.has(state.grant) ? "signedOut"
      : await this.#parts.check(state);
    if (refused !== null) {
      this.#close(connection, refused);
      return;
    }
    if (frame.type === "ping" && Object.keys(frame).length === 2) {
      this.#send(connection, JSON.stringify({ v: SOCKET_VERSION, type: "pong" }));
      return;
    }
    this.#close(connection, "frame");
  }

  // -- the relay -----------------------------------------------------------------------

  /** A run's new frames: sent to every socket on its conversation, and kept in its tail. */
  relay(conversation: string, frames: readonly Frame[]): void {
    const sockets = this.#select(() => true, tag(conversation));
    for (const frame of frames) {
      const text = JSON.stringify(frame);
      for (const connection of sockets) {
        this.#send(connection, text);
      }
      if (text.length <= TAIL_FRAME_MAX) {
        this.#keep(frame.run, conversation, text);
      }
    }
  }

  #keep(run: string, conversation: string, text: string): void {
    let tail = this.#tails.get(run);
    if (tail === undefined) {
      tail = { conversation, frames: [], bytes: 0 };
      this.#tails.set(run, tail);
    }
    tail.frames.push(text);
    tail.bytes += text.length;
    this.#tailBytes += text.length;
    while (this.#tailBytes > TAIL_BYTES) {
      let longest: Tail | undefined;
      for (const each of this.#tails.values()) {
        if (longest === undefined || each.bytes > longest.bytes) {
          longest = each;
        }
      }
      const dropped = longest!.frames.shift()!;
      longest!.bytes -= dropped.length;
      this.#tailBytes -= dropped.length;
    }
  }

  /** A run settled: its tail goes (its result is its receipt's). */
  settled(run: string): void {
    const tail = this.#tails.get(run);
    if (tail !== undefined) {
      this.#tailBytes -= tail.bytes;
      this.#tails.delete(run);
    }
  }

  /** The tails' size, in UTF-16 code units, and how many runs hold one. */
  tailSize(): { runs: number; bytes: number } {
    return { runs: this.#tails.size, bytes: this.#tailBytes };
  }

  #send(connection: Connection, text: string): void {
    try {
      connection.send(text);
    } catch {
      // the peer went: its close follows
    }
  }

  #close(connection: Connection, kind: CloseKind): void {
    const [code, reason] = CLOSE[kind];
    try {
      connection.close(code, reason);
    } catch {
      // closed already
    }
  }

  #select(which: (state: SocketState) => boolean, tagged?: string): Connection[] {
    return [...this.capability.getConnections(tagged)].filter((connection) => {
      const state = connection.state as SocketState | null;
      return state !== null && typeof state === "object" && which(state);
    });
  }

  /**
   * A grant signed out: its sockets closed `signed_out`, and the grant held a little, so an
   * attach whose ticket was consumed before is closed as it connects.
   */
  endGrant(grant: string): void {
    const now = Date.now();
    for (const [held, at] of [...this.#signedOut]) {
      if (now - at > ENDED_HELD_MS) {
        this.#signedOut.delete(held);
      }
    }
    this.#signedOut.set(grant, now);
    while (this.#signedOut.size > SIGNED_OUT_KEPT) {
      this.#signedOut.delete(this.#signedOut.keys().next().value!);
    }
    for (const connection of this.#select((state) => state.grant === grant)) {
      this.#close(connection, "signedOut");
    }
  }

  /** Close every socket: every session's close, an admission refusing reads, the erasure. */
  closeAll(kind: CloseKind): void {
    for (const connection of this.#select(() => true)) {
      this.#close(connection, kind);
    }
  }

  /** The sockets open now. */
  open(): number {
    return [...this.capability.getConnections()].length;
  }
}
