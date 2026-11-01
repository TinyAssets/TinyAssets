/**
 * The owner's app sockets (tasks 5, A6; pi audit F2, F10): hibernating WebSockets on the
 * object's one Lifecycle, each watching one of the owner's agent threads through pi's own
 * `Conversation.watch()`.
 *
 * The transport is the Agents SDK's `WebSockets` capability (`agents/websockets`), installed
 * on the object's own `new Lifecycle(this)` beside the schedule and the thread host, still
 * without Lifecycle's `installHandlers`: the object forwards `fetch` and the three
 * `webSocket*` reserved methods to Lifecycle's methods of the same names (`owner-do.ts`),
 * and a Workers RPC cannot carry a socket (a `Response` with `webSocket` returned over RPC
 * fails `DataCloneError`, workerd), so the upgrade is the stub's `fetch`, the object's one
 * non-RPC entry. The capability speaks no Agents protocol here (`protocol: false`) and
 * serves no callables: every frame is ours. This class is a Lifecycle capability of its
 * own too, for one job: closing sockets as their tokens expire.
 *
 * The credential is a socket ticket. A browser's `WebSocket` sends no `Authorization`, and
 * the session cookie is only for widenings, so the app first asks
 * `POST /app/api/agent/socket-ticket` behind every first-party token check, and the object
 * mints (`socketTicket`) a random, single-use ticket good for `SOCKET_TICKET_S`, bound to
 * one of the owner's threads, to the first-party grant the token was issued under and to
 * that token's expiry, keeping only its SHA-256 (`socket_tickets`, at most
 * `SOCKET_TICKETS`). The upgrade carries the ticket in its query (a browser's `WebSocket`
 * takes a URL and subprotocols, never headers); the doors read only which owner it names,
 * and the object consumes it in the attach's unit, refusing it alike (`TICKET_REFUSED`)
 * reused, expired, for another thread, or for a grant whose sign-out was told.
 *
 * The binder's shape. Only the doors Worker holds `OWNER`, and it forwards an upgrade as a
 * fresh request it builds itself (`doors/src/binder.ts`, `socketRequest`): a `GET` of exactly
 * `SOCKET_URL`, with no query (so neither the SDK's connection id `_pk` nor its Cap'n Web
 * transport can be chosen), and exactly four headers: `Upgrade: websocket`, the bound
 * identity (`BINDING_HEADER`), the thread (`THREAD_HEADER`) and the ticket
 * (`TICKET_HEADER`). No header a client sent survives that hop. The object accepts nothing
 * else (`NOT_BINDER_SHAPE`, `400`), so a request carrying the SDK's
 * `x-agents-lifecycle-props` never reaches Lifecycle either.
 *
 * Attaching. In one unit (`SocketParts.admit`): the binding's account, as a `read` door's
 * (a billing suspension leaves reads open), the owner's own (an outside client has no
 * socket), no session, the owner's own ready thread, and the ticket consumed. Then the caps,
 * counted from the runtime's own sockets by tag with the attaches in flight, refused at
 * once with a fixed code: `OWNER_SOCKETS` an owner (`SOCKETS_FULL`) and `THREAD_SOCKETS` a
 * thread (`THREAD_SOCKETS_FULL`); then the thread's Session pinned (`ThreadHost.pin`),
 * counted against `OPEN_MAX`, past which the upgrade is refused (`SOCKET_SESSIONS_FULL`),
 * never waited on. Connection state lives in the socket's attachment only (`SocketState`:
 * the owner, the thread, the grant, the token's expiry, and the binding a frame is checked
 * by): no SQL row per socket. A failure inside the accept answers a fixed code
 * (`SOCKET_FAILED`, or a `watch_lost` close), never an error's text.
 *
 * Frames are JSON, each with `v: SOCKET_VERSION`. Server to client:
 * - `{v, type: "view", seq, value}`: pi's `ConversationView`, whole: the first frame of an
 *   attach, and a slow watch's catch-up (pi keeps at most 100 undelivered frames, then
 *   replaces them by one whole-view frame, `["r", view]`, sent as a view);
 * - `{v, type: "ops", seq, ops}`: one commit's exact Chord ops, applied to the last view;
 * - `{v, type: "live", base, ops}`: the live tee between commits, in the view's own
 *   vocabulary: appends to `docs["pi.live"].generation.message.content[i]`'s `text` or
 *   `thinking` (and a splice for a block no commit holds yet), laid over commit `base` on a
 *   display copy the client resets at each commit; the next commit's frame is followed by the
 *   tail it does not hold yet (`liveOps`). So the app has one reducer, Chord's, the one pi's
 *   watch needs. Until pi's next minor the tee is the delta source between commits; on the
 *   upgrade its frames are deleted, and the view alone moves (tasks, the pi upgrade);
 * - `{v, type: "graph", seq, value}` and `{v, type: "graph_ops", seq, ops}`: the thread's
 *   task graph (`watchTaskGraph`), once the client asked for it.
 * Client to server: `{v, type: "ack", seq}`, the newest frame it applied, and
 * `{v, type: "task_graph"}`. The view, ops and graph frames are flow-controlled: at most
 * `SOCKET_WINDOW` unacknowledged, past which the watch's listener waits, so pi's own rule
 * applies to a slow client; a live frame is dropped instead (the next commit carries it).
 * Every frame a client sends is checked against its attachment's authority, as a `read`
 * door's, in a unit (`SocketParts.check`), before it is acted on; one refused closes the
 * socket with its code, and one malformed closes it `frame_refused`.
 *
 * Lifetime is the credential's and the thread's, each close a fixed code (`CLOSE`): the
 * token's expiry (`token_expired`, by this capability's job at the earliest expiry, and at
 * every frame), the sign-out of the grant it was opened under (`signed_out`: the doors tell
 * the object before they revoke the grant, cookie or not, and only that grant's sockets
 * close) and every session's close (account revocation), the thread's drop or wipe
 * (`thread_dropped`), an admission that refuses reads, an abuse suspension
 * (`owner_suspended`), and the owner's erasure (`owner_erased`). A watch pi ends on its own
 * (its Session failed) closes `watch_lost`; the app reconnects from a fresh view.
 *
 * Hibernation: a socket outlives the object's eviction, its watch does not. Pi replays
 * nothing, so a socket the instance holds no watch for reattaches from a fresh view: at its
 * next frame, or as its thread's Session opens again (`opened`, called by the thread host),
 * since nothing commits in a closed Session. Its authority is checked again first.
 */
import type { NonEmptyPath, Op, Path } from "@earendil-works/chord/delta";
import type {
  ConversationView, ConversationWatch, TaskGraphWatch, WatchEnd,
} from "@earendil-works/pi-durable";
import {
  LifecycleCapability, type LifecycleJobContext, type LifecycleJobOutcome,
} from "agents/lifecycle";
import { type WebSocketHandlers, WebSockets } from "agents/websockets";

import { Invalid, Refused } from "./errors";
import type { LiveBlock, LiveEvent, LiveTee } from "./live-tee";
import {
  BINDING_HEADER, SHAPE_HEADERS, SOCKET_FAILED, SOCKET_URL, SOCKET_VERSION, THREAD_HEADER,
  TICKET_HEADER, TICKET_REFUSED,
} from "./socket-shape";

export {
  BINDING_HEADER, SOCKET_FAILED, SOCKET_URL, SOCKET_VERSION, THREAD_HEADER, TICKET_HEADER,
  TICKET_REFUSED,
};

/** A connection, as the capability hands it to its handlers. */
type Connection = Parameters<NonNullable<WebSocketHandlers["onConnect"]>>[0];

/** This capability's Lifecycle id, its one job and the job's function. */
export const SOCKETS = "ta-sockets";
export const EXPIRY_JOB = "socket-expiry";
const EXPIRE_FN = "expire";

/** App sockets an owner holds open at once (every thread's). */
export const OWNER_SOCKETS = 16;
/** App sockets one thread holds open at once. */
export const THREAD_SOCKETS = 4;
/** Frames a socket may hold unacknowledged before its watch waits (pi then catches up). */
export const SOCKET_WINDOW = 16;
/** The longest frame a client may send, in UTF-16 code units. */
export const CLIENT_FRAME_MAX = 1024;
/** How long a socket ticket is good for, in seconds; it is used once. */
export const SOCKET_TICKET_S = 30;
/** How long a told sign-out is held on the instance, for the attaches it raced, in ms. */
const ENDED_HELD_MS = (SOCKET_TICKET_S + 30) * 1000;
/** Told sign-outs held at once; past it the oldest goes (its tickets are gone already). */
export const SIGNED_OUT_KEPT = 64;

export const NOT_BINDER_SHAPE = "only the binder's upgrade reaches the object's socket door";
export const SOCKETS_FULL = "sockets_full: the owner has as many app sockets open as it may";
export const THREAD_SOCKETS_FULL = "thread_sockets_full: the thread has as many app sockets " +
  "open as it may";
export const SOCKET_SESSIONS_FULL = "sessions_full: too many of the agent's threads are open " +
  "at once to watch another";
/** A socket's owner only: no outside client has one. */
export const OWNERS_SOCKET = "an app socket is the owner's own";

/** Why a socket closed, as its code and reason: each fixed. */
export type CloseKind = "expired" | "signedOut" | "dropped" | "suspended" | "erased"
  | "frame" | "lost";
export const CLOSE: Readonly<Record<CloseKind, readonly [number, string]>> = Object.freeze({
  expired: Object.freeze([4402, "token_expired"] as const),
  suspended: Object.freeze([4403, "owner_suspended"] as const),
  dropped: Object.freeze([4404, "thread_dropped"] as const),
  erased: Object.freeze([4410, "owner_erased"] as const),
  signedOut: Object.freeze([4440, "signed_out"] as const),
  frame: Object.freeze([4400, "frame_refused"] as const),
  lost: Object.freeze([4500, "watch_lost"] as const),
});

/** Where a streaming answer's blocks sit in a conversation's view. */
export const LIVE_CONTENT: readonly (string | number)[] = Object.freeze([
  "docs", "pi.live", "generation", "message", "content"]);

/** A socket's attachment: whose, on what, under which grant, until when. */
export interface SocketState {
  readonly v: number;
  readonly owner: string;
  readonly thread: string;
  /** The first-party grant the ticket's token was issued under. */
  readonly grant: string;
  /** When that token expires, in whole seconds: the socket closes then. */
  readonly expires_at: number;
  /** The binding each frame is checked by again, as the binder bound it. */
  readonly binding: Readonly<Record<string, unknown>>;
}

/** What the sockets run on: the object's checks, its thread host and its live tee. */
export interface SocketParts {
  /**
   * In a unit: the binding's account, as a `read` door's, the owner's own thread, and the
   * ticket consumed; the attachment, else a refusal thrown.
   */
  admit(binding: unknown, thread: string, ticket: string): Promise<SocketState>;
  /** In a unit: why the attachment no longer holds, as its close; null if it does. */
  check(state: SocketState): Promise<CloseKind | null>;
  /** Pin the thread's Session open (counted against `OPEN_MAX`), or the host's refusal. */
  pin(thread: string): Promise<void>;
  unpin(thread: string): void;
  watch(thread: string): Promise<ConversationWatch>;
  watchTaskGraph(thread: string): Promise<TaskGraphWatch>;
  readonly tee: LiveTee;
  /** The object's clock, in whole seconds (a test's moves ahead). */
  now(): number;
}

/** One socket's watch, on the instance: gone with an eviction, made again at reattach. */
interface Live {
  readonly thread: string;
  pinned: boolean;
  closing: boolean;
  watch: ConversationWatch | undefined;
  graph: TaskGraphWatch | undefined;
  /** The last view the client applied a commit to, as the watch delivered it. */
  value: ConversationView | undefined;
  /** The flow-controlled frames sent, and the newest the client acknowledged. */
  seq: number;
  acked: number;
  waiters: (() => void)[];
  /** The commit the live frames lie over. */
  base: number;
  /**
   * Per block, how much of the tee's text the client displays past the commit (-1: a block
   * the tee's text does not extend, the commits' alone until the next commit).
   */
  readonly shown: Map<number, number>;
  /** The tee subscription, and the conversation it is of. */
  tee: { off: () => void; session: string } | undefined;
}

function threadTag(thread: string): string {
  return `t:${thread}`;
}

/** The binder's upgrade's binding, thread and ticket; anything else refused. */
export function socketShape(request: Request):
    { binding: unknown; thread: string; ticket: string } {
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
  return { binding, thread: request.headers.get(THREAD_HEADER)!,
           ticket: request.headers.get(TICKET_HEADER)! };
}

/** A conversation view's streaming answer's blocks, as the last commit holds them. */
function committedBlocks(view: ConversationView | undefined): unknown[] | undefined {
  const live = view?.docs["pi.live"] as { generation?: { message?: { content?: unknown } } }
    | undefined;
  const content = live?.generation?.message?.content;
  return Array.isArray(content) ? content : undefined;
}

/**
 * The live tee as Chord ops over `view` (the last commit the client applied): for each block
 * the tee holds whose text extends the committed block's (asked once a block a commit, then
 * kept in `shown`), the tail the client does not show yet, as an append to that block's
 * `text` or `thinking`; a block no commit holds yet, the next after the committed ones,
 * spliced in whole. A block whose committed text the tee's does not extend (another attempt,
 * a stream heard in part) is left to the commits.
 */
export function liveOps(view: ConversationView | undefined,
                        blocks: ReadonlyMap<number, LiveBlock> | undefined,
                        shown: Map<number, number>): Op[] {
  const content = committedBlocks(view);
  if (content === undefined || blocks === undefined) {
    return [];
  }
  const ops: Op[] = [];
  let length = content.length;
  for (const [index, block] of [...blocks].sort(([a], [b]) => a - b)) {
    const field = block.type;
    const path = [...LIVE_CONTENT, index, field] as unknown as NonEmptyPath;
    let at = shown.get(index);
    if (at === undefined && index < content.length) {
      const committed = content[index] as Record<string, unknown> | null;
      const text = committed?.[field];
      at = committed?.["type"] === block.type && typeof text === "string"
        && block.text.startsWith(text) ? text.length : -1;
      shown.set(index, at);
    }
    if (at === undefined) {
      if (index === length) {
        ops.push(["p", [...LIVE_CONTENT] as Path, index, 0,
                  [block.type === "text" ? { type: "text", text: block.text.from(0) }
                    : { type: "thinking", thinking: block.text.from(0) }]]);
        shown.set(index, block.text.length);
        length++;
      }
      continue;
    }
    if (index >= content.length) {
      length = Math.max(length, index + 1);
    }
    if (at >= 0 && block.text.length > at) {
      ops.push(["a", path, block.text.from(at)]);
      shown.set(index, block.text.length);
    }
  }
  return ops;
}

/** A refusal as the upgrade's answer: a `Refused` is `403`, an `Invalid` `400`. */
function refusedUpgrade(error: unknown): Response {
  if (error instanceof Refused || error instanceof Invalid) {
    return Response.json({ error: error.message },
                         { status: error instanceof Refused ? 403 : 400 });
  }
  console.error("an app socket's upgrade failed");  // no ids, no error text
  return Response.json({ error: SOCKET_FAILED }, { status: 500 });
}

export class AppSockets extends LifecycleCapability {
  /** The Lifecycle capability the object installs beside this one: the sockets' transport. */
  readonly capability: WebSockets;
  readonly #parts: SocketParts;
  /** Each socket's watch, by connection id, on the instance. */
  readonly #live = new Map<string, Live>();
  /** The attachments of upgrades being accepted, by their request. */
  readonly #accepting = new WeakMap<Request, SocketState>();
  /** Attaches in flight, by thread: counted against the caps until accepted. */
  readonly #pending = new Map<string, number>();
  /** Grants whose sign-out was told, and when, held a little for the attaches it raced. */
  readonly #signedOut = new Map<string, number>();

  constructor(parts: SocketParts) {
    super(SOCKETS);
    this.#parts = parts;
    this.capability = new WebSockets({
      protocol: false,
      // Never a throw: Lifecycle would answer one with its stack. A socket the door did
      // not attach is closed at once by `#connected`.
      getConnectionTags: (_connection, { request }) => {
        const state = this.#accepting.get(request);
        return state === undefined ? [] : [threadTag(state.thread)];
      },
      handlers: {
        onConnect: (connection, { request }) => this.#connected(connection, request),
        onMessage: (connection, message) => this.#message(connection, message),
        onClose: (connection) => this.#detach(connection.id),
        onError: (connection) => this.#detach(connection.id),
      },
    });
  }

  // -- Lifecycle: the expiry job -----------------------------------------------------

  /** The earliest token expiry is due: close what expired, then wait for the next. */
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

  /** The earliest expiry of the sockets open now, in seconds, or null. */
  #nextExpiry(): number | null {
    const times = this.#select(() => true).map((connection) =>
      (connection.state as SocketState).expires_at);
    return times.length === 0 ? null : Math.min(...times);
  }

  /** The expiry job set at the earliest expiry of the sockets open now. */
  async #armExpiry(): Promise<void> {
    const next = this.#nextExpiry();
    if (next !== null) {
      await this.lifecycle.jobs.push({ id: EXPIRY_JOB, fn: EXPIRE_FN, time: next * 1000 });
    }
  }

  // -- The door ------------------------------------------------------------------------

  /**
   * The object's `fetch`: the binder's upgrade, attached (its account, thread, ticket, caps
   * and pinned Session), then handed to Lifecycle (`accept`), whose `WebSockets` accepts it;
   * any other request, and any refusal, answered here with its fixed code.
   */
  async serve(request: Request, accept: (upgrade: Request) => Promise<Response>):
      Promise<Response> {
    let state: SocketState;
    try {
      const shape = socketShape(request);
      state = await this.#parts.admit(shape.binding, shape.thread, shape.ticket);
      this.#room(state.thread);
    } catch (error) {
      return refusedUpgrade(error);
    }
    this.#pending.set(state.thread, (this.#pending.get(state.thread) ?? 0) + 1);
    try {
      try {
        await this.#parts.pin(state.thread);
      } catch (error) {
        return refusedUpgrade(error instanceof Refused ? new Refused(SOCKET_SESSIONS_FULL)
          : error);
      }
      this.#accepting.set(request, state);
      try {
        return await accept(request);
      } catch (error) {
        return refusedUpgrade(error);
      } finally {
        if (this.#accepting.delete(request)) {
          this.#parts.unpin(state.thread);  // never connected: its pin goes
        }
      }
    } finally {
      const left = (this.#pending.get(state.thread) ?? 1) - 1;
      if (left === 0) {
        this.#pending.delete(state.thread);
      } else {
        this.#pending.set(state.thread, left);
      }
    }
  }

  /** The caps, with the attaches in flight: refused at once, never waited on. */
  #room(thread: string): void {
    const pending = [...this.#pending.values()].reduce((sum, n) => sum + n, 0);
    if ([...this.capability.getConnections()].length + pending >= OWNER_SOCKETS) {
      throw new Refused(SOCKETS_FULL);
    }
    if ([...this.capability.getConnections(threadTag(thread))].length
        + (this.#pending.get(thread) ?? 0) >= THREAD_SOCKETS) {
      throw new Refused(THREAD_SOCKETS_FULL);
    }
  }

  /**
   * A socket the door attached, now accepted: its attachment kept, the expiry job set, its
   * watch attached. One its grant's sign-out raced (told after its ticket was consumed) is
   * closed `signed_out` at once. Never a throw: Lifecycle would answer it with its stack.
   */
  async #connected(connection: Connection, request: Request): Promise<void> {
    const state = this.#accepting.get(request);
    this.#accepting.delete(request);
    try {
      if (state === undefined) {
        throw new Error("an app socket is accepted only as the object's door attached it");
      }
      connection.setState(state);
      if (this.#signedOut.has(state.grant)) {
        this.#parts.unpin(state.thread);
        this.#close(connection, "signedOut");
        return;
      }
      await this.#armExpiry();
      await this.#attach(connection, state, true);
    } catch {
      console.error("an app socket could not be attached");  // no ids, no error text
      if (state !== undefined && !this.#live.has(connection.id)) {
        this.#parts.unpin(state.thread);
      }
      this.#close(connection, "lost");
    }
  }

  /**
   * Attach `connection` to its thread's watch from a fresh view: its Session pinned (unless
   * the door pinned it), the view sent, then each commit's ops, the tee's appends between. A
   * socket closed while the pin or the watch was awaited lets both go.
   */
  async #attach(connection: Connection, state: SocketState, pinned: boolean): Promise<void> {
    const live: Live = {
      thread: state.thread, pinned, closing: false, watch: undefined, graph: undefined,
      value: undefined, seq: 0, acked: 0, waiters: [], base: 0, shown: new Map(),
      tee: undefined,
    };
    this.#live.set(connection.id, live);
    const gone = () => live.closing || this.#live.get(connection.id) !== live;
    try {
      if (!live.pinned) {
        await this.#parts.pin(state.thread);
        if (gone()) {
          this.#parts.unpin(state.thread);  // closed while it was pinned: the pin goes
          return;
        }
        live.pinned = true;
      }
      const watch = await this.#parts.watch(state.thread);
      if (gone()) {
        void watch.stop();
        return;
      }
      live.watch = watch;
      live.value = watch.value;
      live.base = this.#send(connection, live, { type: "view", value: watch.value });
      this.#follow(connection, live);
      watch.start(async (value, ops) => {
        await this.#window(live);
        if (live.closing) {
          return;
        }
        live.value = value;
        live.shown.clear();
        const whole = ops.length === 1 && ops[0]![0] === "r";
        live.base = this.#send(connection, live, whole ? { type: "view", value }
          : { type: "ops", ops });
        this.#follow(connection, live);
      });
      void watch.closed.then((end) => this.#ended(connection, live, end));
    } catch {
      this.#close(connection, "lost");
    }
  }

  /**
   * After a view or a commit: the tee followed for the conversation the view names (again,
   * when a grant or policy change closed it), and the tail of its blocks the commit does not
   * hold yet sent as one live frame.
   */
  #follow(connection: Connection, live: Live): void {
    const provider = live.value?.docs["pi.provider"] as { sessionId?: unknown } | undefined;
    const session = typeof provider?.sessionId === "string" ? provider.sessionId : undefined;
    if (session !== undefined && live.tee?.session !== session) {
      live.tee?.off();
      live.tee = undefined;
      try {
        const off = this.#parts.tee.subscribe(session, (event) =>
          this.#teed(connection, live, event));
        live.tee = { off, session };
      } catch {
        return;  // the tee is full: this socket moves by its commits alone
      }
    }
    if (live.tee !== undefined) {
      this.#sendLive(connection, live);
    }
  }

  /** A tee event for the socket's conversation: its appends, unless the window is full. */
  #teed(connection: Connection, live: Live, event: LiveEvent): void {
    if (event.type === "end") {
      live.tee = undefined;  // the tee closed it (a grant or policy change); followed again
      return;
    }
    if (event.type === "text_delta" || event.type === "thinking_delta"
        || event.type === "text_start" || event.type === "thinking_start") {
      this.#sendLive(connection, live);
    }
  }

  #sendLive(connection: Connection, live: Live): void {
    if (live.closing || live.tee === undefined || live.seq - live.acked >= SOCKET_WINDOW) {
      return;
    }
    const ops = liveOps(live.value, this.#parts.tee.blocks(live.tee.session), live.shown);
    if (ops.length > 0) {
      this.#frame(connection, { type: "live", base: live.base, ops });
    }
  }

  /** Wait while the socket holds `SOCKET_WINDOW` frames unacknowledged. */
  async #window(live: Live): Promise<void> {
    while (!live.closing && live.seq - live.acked >= SOCKET_WINDOW) {
      await new Promise<void>((resolve) => live.waiters.push(resolve));
    }
  }

  /** Send one flow-controlled frame, numbered; its number. */
  #send(connection: Connection, live: Live, frame: Record<string, unknown>): number {
    live.seq++;
    this.#frame(connection, { ...frame, seq: live.seq });
    return live.seq;
  }

  #frame(connection: Connection, frame: Record<string, unknown>): void {
    try {
      connection.send(JSON.stringify({ v: SOCKET_VERSION, ...frame }));
    } catch {
      this.#detach(connection.id);  // the peer went: its close follows
    }
  }

  /** A watch pi ended on its own, its Session gone: the socket closes `watch_lost`. */
  #ended(connection: Connection, live: Live, end: WatchEnd): void {
    if (live.closing || this.#live.get(connection.id) !== live) {
      return;
    }
    if (end.reason !== "stopped" && end.reason !== "cancelled") {
      this.#close(connection, "lost");
    }
  }

  /** A frame the client sent: checked against its authority first, then acted on. */
  async #message(connection: Connection, message: string | ArrayBuffer | ArrayBufferView):
      Promise<void> {
    let frame: { v?: unknown; type?: unknown; seq?: unknown };
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
    let live = this.#live.get(connection.id);
    if (live === undefined) {
      await this.#attach(connection, state, false);  // a hibernation cut its watch off
      live = this.#live.get(connection.id);
      if (live === undefined) {
        return;
      }
    }
    if (frame.type === "ack" && typeof frame.seq === "number" && Number.isSafeInteger(frame.seq)) {
      live.acked = Math.max(live.acked, Math.min(frame.seq, live.seq));
      for (const resolve of live.waiters.splice(0)) {
        resolve();
      }
      return;
    }
    if (frame.type === "task_graph") {
      await this.#graph(connection, live);
      return;
    }
    this.#close(connection, "frame");
  }

  /** The thread's task graph, watched over the socket: its value, then each commit's ops. */
  async #graph(connection: Connection, live: Live): Promise<void> {
    if (live.graph !== undefined) {
      return;
    }
    let graph: TaskGraphWatch;
    try {
      graph = await this.#parts.watchTaskGraph(live.thread);
    } catch {
      this.#close(connection, "lost");
      return;
    }
    if (live.closing || live.graph !== undefined) {
      void graph.stop();
      return;
    }
    live.graph = graph;
    this.#send(connection, live, { type: "graph", value: graph.value });
    graph.start(async (value, ops) => {
      await this.#window(live);
      if (live.closing) {
        return;
      }
      const whole = ops.length === 1 && ops[0]![0] === "r";
      this.#send(connection, live, whole ? { type: "graph", value } : { type: "graph_ops", ops });
    });
  }

  /** Close a socket with its fixed code, its watch detached at once. */
  #close(connection: Connection, kind: CloseKind): void {
    this.#detach(connection.id);
    const [code, reason] = CLOSE[kind];
    try {
      connection.close(code, reason);
    } catch {
      // closed already
    }
  }

  /** A socket's watch, tee and task graph ended, and its Session's pin let go. */
  #detach(id: string): void {
    const live = this.#live.get(id);
    if (live === undefined) {
      return;
    }
    this.#live.delete(id);
    live.closing = true;
    for (const resolve of live.waiters.splice(0)) {
      resolve();
    }
    live.tee?.off();
    live.tee = undefined;
    void live.watch?.stop();
    void live.graph?.stop();
    if (live.pinned) {
      live.pinned = false;
      this.#parts.unpin(live.thread);
    }
  }

  /** The open sockets whose attachment `which` selects. */
  #select(which: (state: SocketState) => boolean, tag?: string): Connection[] {
    return [...this.capability.getConnections(tag)].filter((connection) => {
      const state = connection.state as SocketState | null;
      return state !== null && typeof state === "object" && which(state);
    });
  }

  /** `thread`'s Session opened: each of its sockets with no watch here reattaches. */
  opened(thread: string): void {
    for (const connection of this.#select(() => true, threadTag(thread))) {
      if (this.#live.has(connection.id)) {
        continue;
      }
      const state = connection.state as SocketState;
      void (async () => {
        const refused = this.#signedOut.has(state.grant) ? "signedOut"
          : await this.#parts.check(state);
        if (refused !== null) {
          this.#close(connection, refused);
        } else if (!this.#live.has(connection.id)) {
          await this.#attach(connection, state, false);
        }
      })().catch(() => this.#close(connection, "lost"));
    }
  }

  /**
   * A grant signed out: its sockets closed `signed_out`, and the grant held a little on the
   * instance, so an attach whose ticket was consumed before is closed as it connects.
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

  /** Close `thread`'s sockets: its drop or wipe (`thread_dropped`), or another end. */
  closeThread(thread: string, kind: CloseKind = "dropped"): void {
    for (const connection of this.#select(() => true, threadTag(thread))) {
      this.#close(connection, kind);
    }
  }

  /** Close every socket: every session's close, an admission refusing reads, the erasure. */
  closeAll(kind: CloseKind): void {
    for (const connection of this.#select(() => true)) {
      this.#close(connection, kind);
    }
  }

  /** The sockets open now, and those with a watch on this instance. */
  counts(): { open: number; watching: number } {
    return { open: [...this.capability.getConnections()].length, watching: this.#live.size };
  }
}
