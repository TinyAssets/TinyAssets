/**
 * The owner's app sockets (tasks 5, A6; pi audit F2, F10): hibernating WebSockets on the
 * object's one Lifecycle, each watching one of the caller's agent threads through pi's own
 * `Conversation.watch()`.
 *
 * The transport is the Agents SDK's `WebSockets` capability (`agents/websockets`), installed
 * on the object's own `new Lifecycle(this)` beside the schedule and the thread host, still
 * without Lifecycle's `installHandlers`: the object forwards `fetch` and the three
 * `webSocket*` reserved methods to Lifecycle's methods of the same names (`owner-do.ts`),
 * and a Workers RPC cannot carry a socket (a `Response` with `webSocket` returned over RPC
 * fails `DataCloneError`, workerd), so the upgrade is the stub's `fetch`, the object's one
 * non-RPC entry. The capability speaks no Agents protocol here (`protocol: false`) and
 * serves no callables: every frame is ours.
 *
 * The binder's shape. Only the doors Worker holds `OWNER`, and it forwards an upgrade as a
 * fresh request it builds itself (`doors/src/binder.ts`, `socketRequest`): a `GET` of exactly
 * `SOCKET_URL`, with no query (so neither the SDK's connection id `_pk` nor its Cap'n Web
 * transport can be chosen), and exactly three headers: `Upgrade: websocket`, the bound
 * identity (`BINDING_HEADER`, the `Binding` as JSON, set from the verified credential) and
 * the thread (`THREAD_HEADER`). No header a client sent survives that hop. The object
 * accepts nothing else (`NOT_BINDER_SHAPE`, `400`), so a request carrying the SDK's
 * `x-agents-lifecycle-props` never reaches Lifecycle either.
 *
 * Attaching. In one unit (`SocketParts.admit`): the binding's account, as a `read` door's
 * (a billing suspension leaves reads open), no session (the socket widens nothing), the
 * thread the caller's own (`Threads.mine`), and a client's stamp standing at its generation.
 * Then the caps, counted from the runtime's own sockets by tag with the attaches in flight,
 * refused at once with a fixed code: `OWNER_SOCKETS` an owner (`SOCKETS_FULL`) and
 * `THREAD_SOCKETS` a thread (`THREAD_SOCKETS_FULL`); then the thread's Session pinned
 * (`ThreadHost.pin`), counted against `OPEN_MAX`, past which the upgrade is refused
 * (`SOCKET_SESSIONS_FULL`), never waited on. Connection state lives in the socket's
 * attachment only (`SocketState`: the owner, the client, the thread, the authority
 * generation at attach, and the binding a frame is checked by): no SQL row per socket.
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
 * Lifetime is authority's: a thread's sockets close with a fixed code (`CLOSE`) when its
 * authority ends: a grant revoke or an authority-generation move (`authority_ended`), the
 * thread's drop or wipe (`thread_dropped`), an admission that refuses reads, an abuse
 * suspension (`owner_suspended`), the owner's erasure (`owner_erased`) and the owner's
 * session's close, which ends the owner's own sockets (`session_closed`). A watch pi ends on
 * its own (its Session failed) closes `watch_lost`; the app reconnects from a fresh view.
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
import { type WebSocketHandlers, WebSockets } from "agents/websockets";

import { Invalid, Refused } from "./errors";
import type { LiveBlock, LiveEvent, LiveTee } from "./live-tee";
import {
  BINDING_HEADER, SHAPE_HEADERS, SOCKET_URL, SOCKET_VERSION, THREAD_HEADER,
} from "./socket-shape";

export { BINDING_HEADER, SOCKET_URL, SOCKET_VERSION, THREAD_HEADER };

/** A connection, as the capability hands it to its handlers. */
type Connection = Parameters<NonNullable<WebSocketHandlers["onConnect"]>>[0];

/** App sockets an owner holds open at once (every thread's). */
export const OWNER_SOCKETS = 16;
/** App sockets one thread holds open at once. */
export const THREAD_SOCKETS = 4;
/** Frames a socket may hold unacknowledged before its watch waits (pi then catches up). */
export const SOCKET_WINDOW = 16;
/** The longest frame a client may send, in UTF-16 code units. */
export const CLIENT_FRAME_MAX = 1024;

export const NOT_BINDER_SHAPE = "only the binder's upgrade reaches the object's socket door";
export const SOCKETS_FULL = "sockets_full: the owner has as many app sockets open as it may";
export const THREAD_SOCKETS_FULL = "thread_sockets_full: the thread has as many app sockets " +
  "open as it may";
export const SOCKET_SESSIONS_FULL = "sessions_full: too many of the agent's threads are open " +
  "at once to watch another";

/** Why a socket closed, as its code and reason: each fixed. */
export type CloseKind = "authority" | "dropped" | "suspended" | "erased" | "session" | "frame"
  | "lost";
export const CLOSE: Readonly<Record<CloseKind, readonly [number, string]>> = Object.freeze({
  authority: Object.freeze([4401, "authority_ended"] as const),
  suspended: Object.freeze([4403, "owner_suspended"] as const),
  dropped: Object.freeze([4404, "thread_dropped"] as const),
  erased: Object.freeze([4410, "owner_erased"] as const),
  session: Object.freeze([4440, "session_closed"] as const),
  frame: Object.freeze([4400, "frame_refused"] as const),
  lost: Object.freeze([4500, "watch_lost"] as const),
});

/** Where a streaming answer's blocks sit in a conversation's view. */
export const LIVE_CONTENT: readonly (string | number)[] = Object.freeze([
  "docs", "pi.live", "generation", "message", "content"]);

/** A socket's attachment: who attached it, to what, under which authority generation. */
export interface SocketState {
  readonly v: number;
  readonly owner: string;
  /** The outside client the binding came through; null for the owner's own. */
  readonly client: string | null;
  readonly thread: string;
  /** The thread's stamp generation at attach (a client's); null for the owner's. */
  readonly generation: number | null;
  /** The binding each frame is checked by again, as the binder bound it. */
  readonly binding: Readonly<Record<string, unknown>>;
}

/** What the sockets run on: the object's checks, its thread host and its live tee. */
export interface SocketParts {
  /**
   * In a unit: the binding's account, as a `read` door's, the thread the caller's own and
   * its stamp standing; the attachment, else a refusal thrown.
   */
  admit(binding: unknown, thread: string): Promise<SocketState>;
  /** In a unit: why the attachment's authority no longer holds, as its close; null if it does. */
  check(state: SocketState): Promise<CloseKind | null>;
  /** Pin the thread's Session open (counted against `OPEN_MAX`), or the host's refusal. */
  pin(thread: string): Promise<void>;
  unpin(thread: string): void;
  watch(thread: string): Promise<ConversationWatch>;
  watchTaskGraph(thread: string): Promise<TaskGraphWatch>;
  readonly tee: LiveTee;
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
  /** Per block, how much of the tee's text the client displays past the commit. */
  readonly shown: Map<number, number>;
  /** The tee subscription, and the conversation it is of. */
  tee: { off: () => void; session: string } | undefined;
}

function threadTag(thread: string): string {
  return `t:${thread}`;
}

/** The binder's upgrade's binding and thread; anything else refused (`NOT_BINDER_SHAPE`). */
export function socketShape(request: Request): { binding: unknown; thread: string } {
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
  return { binding, thread: request.headers.get(THREAD_HEADER)! };
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
 * the tee holds whose text extends the committed block's, the tail the client does not show
 * yet (`shown`, updated), as an append to that block's `text` or `thinking`; a block no
 * commit holds yet, the next after the committed ones, spliced in whole. A block whose
 * committed text the tee's does not extend (another attempt, a stream seen in part) is left
 * to the commits.
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
    if (index < content.length) {
      const committed = content[index] as Record<string, unknown> | null;
      const text = committed?.[field];
      if (committed?.["type"] !== block.type || typeof text !== "string"
          || !block.text.startsWith(text)) {
        continue;
      }
      const at = shown.get(index) ?? text.length;
      if (block.text.length > at) {
        ops.push(["a", path, block.text.slice(at)]);
        shown.set(index, block.text.length);
      }
    } else if (shown.has(index)) {
      const at = shown.get(index)!;
      if (block.text.length > at) {
        ops.push(["a", path, block.text.slice(at)]);
        shown.set(index, block.text.length);
      }
      length = Math.max(length, index + 1);
    } else if (index === length) {
      ops.push(["p", [...LIVE_CONTENT] as Path, index, 0,
                [block.type === "text" ? { type: "text", text: block.text }
                  : { type: "thinking", thinking: block.text }]]);
      shown.set(index, block.text.length);
      length++;
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
  throw error;
}

export class AppSockets {
  /** The Lifecycle capability the object installs: the sockets' transport. */
  readonly capability: WebSockets;
  readonly #parts: SocketParts;
  /** Each socket's watch, by connection id, on the instance. */
  readonly #live = new Map<string, Live>();
  /** The attachments of upgrades being accepted, by their request. */
  readonly #accepting = new WeakMap<Request, SocketState>();
  /** Attaches in flight, by thread: counted against the caps until accepted. */
  readonly #pending = new Map<string, number>();

  constructor(parts: SocketParts) {
    this.#parts = parts;
    this.capability = new WebSockets({
      protocol: false,
      getConnectionTags: (_connection, { request }) => {
        const state = this.#accepting.get(request);
        if (state === undefined) {
          throw new Error("an app socket is accepted only as the object's door attached it");
        }
        return [threadTag(state.thread)];
      },
      handlers: {
        onConnect: (connection, { request }) => this.#connected(connection, request),
        onMessage: (connection, message) => this.#message(connection, message),
        onClose: (connection) => this.#detach(connection.id),
        onError: (connection) => this.#detach(connection.id),
      },
    });
  }

  /**
   * The object's `fetch`: the binder's upgrade, attached (its account, thread, caps and
   * pinned Session), then handed to Lifecycle (`accept`), whose `WebSockets` accepts it; any
   * other request, and any refusal, answered here with its fixed code.
   */
  async serve(request: Request, accept: (upgrade: Request) => Promise<Response>):
      Promise<Response> {
    let state: SocketState;
    try {
      const shape = socketShape(request);
      state = await this.#parts.admit(shape.binding, shape.thread);
      this.#room(state.thread);
    } catch (error) {
      return refusedUpgrade(error);
    }
    this.#pending.set(state.thread, (this.#pending.get(state.thread) ?? 0) + 1);
    try {
      try {
        await this.#parts.pin(state.thread);
      } catch (error) {
        if (error instanceof Refused) {
          return refusedUpgrade(new Refused(SOCKET_SESSIONS_FULL));
        }
        throw error;
      }
      this.#accepting.set(request, state);
      try {
        return await accept(request);
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

  /** A socket the door attached, now accepted: its attachment kept, its watch attached. */
  async #connected(connection: Connection, request: Request): Promise<void> {
    const state = this.#accepting.get(request);
    this.#accepting.delete(request);
    if (state === undefined) {
      throw new Error("an app socket is accepted only as the object's door attached it");
    }
    connection.setState(state);
    await this.#attach(connection, state, true);
  }

  /**
   * Attach `connection` to its thread's watch from a fresh view: its Session pinned (unless
   * the door pinned it), the view sent, then each commit's ops, the tee's appends between.
   */
  async #attach(connection: Connection, state: SocketState, pinned: boolean): Promise<void> {
    const live: Live = {
      thread: state.thread, pinned, closing: false, watch: undefined, graph: undefined,
      value: undefined, seq: 0, acked: 0, waiters: [], base: 0, shown: new Map(),
      tee: undefined,
    };
    this.#live.set(connection.id, live);
    try {
      if (!live.pinned) {
        await this.#parts.pin(state.thread);
        live.pinned = true;
      }
      const watch = await this.#parts.watch(state.thread);
      if (live.closing) {
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
    const refused = await this.#parts.check(state);
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
    if (live.closing) {
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
        const refused = await this.#parts.check(state);
        if (refused !== null) {
          this.#close(connection, refused);
        } else if (!this.#live.has(connection.id)) {
          await this.#attach(connection, state, false);
        }
      })().catch(() => this.#close(connection, "lost"));
    }
  }

  /** Close `thread`'s sockets: its drop or wipe (`thread_dropped`), or another end. */
  closeThread(thread: string, kind: CloseKind = "dropped"): void {
    for (const connection of this.#select(() => true, threadTag(thread))) {
      this.#close(connection, kind);
    }
  }

  /** Close every socket of `client`'s threads: its grant revoked or its generation moved. */
  closeClient(client: string, kind: CloseKind = "authority"): void {
    for (const connection of this.#select((state) => state.client === client)) {
      this.#close(connection, kind);
    }
  }

  /** Close the owner's own sockets (no outside client): its session closed. */
  closeOwner(kind: CloseKind = "session"): void {
    for (const connection of this.#select((state) => state.client === null)) {
      this.#close(connection, kind);
    }
  }

  /** Close every socket: an admission that refuses reads, or the owner's erasure. */
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
